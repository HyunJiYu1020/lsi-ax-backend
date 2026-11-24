"""업무 지침(AI-18) 등록·조회·삭제. 전 계정 공용.

지침은 "사유는 3줄 이내", "금액은 억원으로 써"처럼 담당자가 챗봇에 말로 알려주는 팀의 약속이다.
저장해 두고 매 요청의 시스템 프롬프트에 넣어야 "사용할수록 정확해진다"가 성립한다 — 대화 안에서만
말하면 창을 닫는 순간 사라진다. 프롬프트에 넣는 쪽은 app/shared/core/guidelines.py가 맡는다.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.shared.core.guidelines import (
    APPLIES_TO,
    duplicate_hint,
    read_guidelines,
    similar_guidelines,
)
from app.shared.models.account import Account
from app.shared.models.generic import Guideline
from app.shared.schemas.guideline import GuidelineIn, GuidelineOut

router = APIRouter()


def _check(body_account_key: str, applies_to: str, db: Session) -> None:
    if applies_to not in APPLIES_TO:
        raise HTTPException(400, f"applies_to는 {' | '.join(APPLIES_TO)}만 가능합니다.")
    # 빈 문자열은 "전 계정 공통"이라는 뜻이라 통과시킨다. 값이 있을 때만 실재하는 계정인지 본다 —
    # 모델에 FK를 못 걸었으니(빈 문자열을 FK로 표현할 수 없다) 여기가 유일한 방어선이다.
    if body_account_key and not db.scalar(select(Account.key).where(Account.key == body_account_key)):
        raise HTTPException(400, f"없는 계정입니다: {body_account_key!r}")


def _out(g: Guideline, replaced_text: str = "") -> GuidelineOut:
    return GuidelineOut(
        topic=g.topic, text=g.text, account_key=g.account_key, applies_to=g.applies_to,
        source=g.source, updated_at=g.updated_at, replaced_text=replaced_text,
    )


@router.get("/guidelines", response_model=list[GuidelineOut])
def list_guidelines(
    account_key: str = "", applies_to: str = "all", db: Session = Depends(get_db)
) -> list[GuidelineOut]:
    """그 계정·그 기능에 걸리는 지침 목록. 전 계정 공통 지침도 같이 내려간다 — 담당자가 "지금
    걸려 있는 지침 뭐야?"를 물었을 때 해외연구소 지침만 보여주면 "금액은 억원" 같은 공통 지침이
    없는 것처럼 읽힌다."""
    _check(account_key, applies_to, db)
    return [_out(g) for g in read_guidelines(db, account_key, applies_to)]


@router.put("/guidelines", response_model=GuidelineOut)
def upsert_guideline(body: GuidelineIn, db: Session = Depends(get_db)) -> GuidelineOut:
    """지침을 등록한다. 같은 (계정·적용범위·주제)면 덮어쓰고 그 전 문장을 replaced_text로 돌려준다.

    덮어쓰는 게 기본인 이유: 같은 주제 지침이 둘 이상 살아 있으면 모델이 매번 다른 걸 골라서
    지난주와 이번주 보고서 단위가 달라진다. 바꿀지 말지는 반영 전에 사람이 답한다(쓰기 도구는
    /chat/confirm 승인을 거친다).

    주제가 **다른데 뜻이 겹치는** 지침(= topic 덮어쓰기가 못 막는 중복)은 409로 막고 후보를
    돌려준다. 사용자가 "둘 다 필요하다"고 답하면 confirm_new_topic=true로 다시 호출한다."""
    topic, text = body.topic.strip(), body.text.strip()
    if not topic:
        raise HTTPException(400, "topic(지침 주제)이 비어 있습니다.")
    if not text:
        raise HTTPException(400, "text(지침 문장)가 비어 있습니다. 지침을 없애려면 DELETE를 쓰세요.")
    _check(body.account_key, body.applies_to, db)

    prev = db.scalar(
        select(Guideline).where(
            Guideline.account_key == body.account_key,
            Guideline.applies_to == body.applies_to,
            Guideline.topic == topic,
        )
    )
    replaced_text = prev.text if prev and prev.text != text else ""

    # 같은 주제가 없을 때만 중복을 본다. 있으면 그건 교체이고(위 replaced_text) 사용자가 이미
    # 그 주제를 알고 고친 것이다. 새 주제로 들어올 때가 문제다 — "금액 단위"가 걸려 있는데
    # "금액 표기"로 또 등록하면 뜻이 겹치는 두 지침이 같은 프롬프트에 실린다. 막기만 하면 모델이
    # 같은 호출을 반복하므로, 무엇을 물어야 하는지까지 적어서 돌려준다(duplicate_hint).
    #
    # 409인 이유: 입력이 틀린 게 아니라(400) **지금 저장된 것과 충돌**해서 막은 것이고, 사용자가
    # 답하면 같은 입력이 그대로 통과한다.
    if prev is None and not body.confirm_new_topic:
        dups = similar_guidelines(db, body.account_key, body.applies_to, topic, text)
        if dups:
            raise HTTPException(409, duplicate_hint(dups, topic))

    stmt = pg_insert(Guideline).values(
        account_key=body.account_key, applies_to=body.applies_to, topic=topic,
        text=text, source=body.source,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_guidelines_topic",
        set_={"text": text, "source": body.source, "updated_at": func.now()},
    ).returning(Guideline)
    saved = db.scalars(stmt).one()
    db.commit()
    return _out(saved, replaced_text)


@router.delete("/guidelines")
def delete_guideline(
    topic: str, account_key: str = "", applies_to: str = "all", db: Session = Depends(get_db)
) -> dict[str, str | int]:
    """지침을 지운다 — 다음 요청부터 프롬프트에 안 들어간다."""
    _check(account_key, applies_to, db)
    deleted = db.execute(
        delete(Guideline).where(
            Guideline.account_key == account_key,
            Guideline.applies_to == applies_to,
            Guideline.topic == topic.strip(),
        )
    ).rowcount
    db.commit()
    if not deleted:
        raise HTTPException(404, f"등록된 지침이 없습니다: {topic!r}")
    return {"deleted": deleted, "topic": topic}
