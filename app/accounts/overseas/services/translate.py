"""증감사유 번역 캐시 (영문 회신 → 보고용 한국어).

번역 자체는 app/shared/core/reason_translate.py(프롬프트 · 공급자 호출)가 하고, 여기는 캐시만
맡는다: 이미 번역한 원문은 DB 값을 그대로 돌려주고, 처음 보는 원문만 LLM에 묻는다. 같은 원문이
매번 다르게 번역되면 보고 문구가 흔들리기 때문에 "원문 1건 = 번역 1개"를 DB로 못 박는 구조다.
"""

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.shared.core.guidelines import guide_fingerprint, guidelines_text
from app.shared.core.reason_translate import (
    MAX_LEN,
    MAX_TEXTS,
    engine_label,
    normalize,
    resolve_provider,
    translate_batch,
)
from app.shared.core.values import source_hash
from app.shared.models.generic import TextTranslation
from app.accounts.overseas.schemas import ReasonTranslateIn, ReasonTranslateItem, ReasonTranslateResult
from app.accounts.overseas.services.labs import ACCOUNT_KEY


async def translate_reasons(db: Session, body: ReasonTranslateIn) -> ReasonTranslateResult:
    """연구소가 영문으로 보낸 증감사유를 한국어로 번역한다(원문은 그대로 보관).

    같은 원문이 여러 번 들어와도 LLM에는 한 번만 묻고, 한 번 번역한 원문은 다음 호출부터
    overseas_reason_translations에서 바로 꺼낸다. refresh=true면 캐시를 무시하고 다시 번역해 덮어쓴다.

    LLM 호출이 실패해도 200으로 답한다 — 증감분석 화면은 번역이 없어도 원문으로 그려져야 하고,
    여기서 502를 던지면 화면이 "사유 자체가 없는" 것처럼 보인다. 실패 이유는 error에 담아 보낸다."""
    provider = resolve_provider(body.provider)
    # 중복 제거하되 요청 순서는 지킨다(화면이 받은 순서대로 칸에 꽂는다). 길이 상한을 넘는 원문은
    # 자르지 않고 그대로 번역에 보낸다 — remark_* 컬럼이 500자라 실제로는 걸리지 않고, 혹시 더 긴
    # 값이 오면 번역을 포기하는 게 뒤가 잘린 사유를 보여주는 것보다 안전하다.
    seen: dict[str, None] = {}
    too_long: set[str] = set()
    for raw in body.texts:
        text = normalize(raw)
        if not text:
            continue
        seen.setdefault(text, None)
        if len(text) > MAX_LEN:
            too_long.add(text)
    sources = list(seen)
    if len(sources) > MAX_TEXTS:
        raise HTTPException(
            status_code=400,
            detail=f"한 번에 번역할 수 있는 원문은 {MAX_TEXTS}건까지입니다 (요청 {len(sources)}건). 나눠서 보내주세요.",
        )

    rows = {
        r.source_hash: r
        for r in db.scalars(
            select(TextTranslation).where(
                TextTranslation.source_hash.in_([source_hash(t) for t in sources])
            )
        )
    }
    # 지금 걸린 번역 지침의 지문. 캐시된 번역이 다른 지침으로 만들어진 것이면 캐시를 쓰지 않고
    # 다시 번역한다 — 담당자가 "Headcount는 인원으로 써"를 등록했는데 옛 번역이 계속 나오면
    # 기능이 안 되는 것으로 읽는다. 지침이 없으면 지문이 빈 문자열이라 기존 캐시가 그대로 유효하다.
    guide = guidelines_text(db, ACCOUNT_KEY, "translate")
    gh = guide_fingerprint(db, ACCOUNT_KEY, "translate")
    hit: dict[str, TextTranslation] = {}
    todo: list[str] = []
    for text in sources:
        row = rows.get(source_hash(text))
        if row and not body.refresh and row.guide_hash == gh:
            hit[text] = row
        elif text not in too_long:
            todo.append(text)

    fresh: dict[str, str] = {}
    error = ""
    if todo:
        try:
            result = await translate_batch(todo, provider, guidelines=guide)
        except HTTPException as e:
            # 키 없음 · 크레딧 · 연결 실패 — 번역만 못 하고 나머지(캐시된 번역)는 그대로 돌려준다.
            result = [None] * len(todo)
            error = str(e.detail)
        label = engine_label(provider)
        for text, raw_ko in zip(todo, result):
            if not raw_ko:
                continue
            # ko_text가 String(600)이라 그보다 긴 번역은 잘라서 넣는다(원문이 500자 이하라 실제로는
            # 안 걸리지만, 모델이 설명을 덧붙여 길게 내놨을 때 저장 자체가 실패하는 걸 막는다).
            ko = raw_ko[:600]
            fresh[text] = ko
            # 같은 원문이 이미 있으면(refresh) 덮어쓴다 — source_hash가 unique라 insert는 못 한다.
            db.execute(
                pg_insert(TextTranslation)
                .values(
                    source_hash=source_hash(text), source_text=text, ko_text=ko,
                    engine=label, guide_hash=gh,
                )
                .on_conflict_do_update(
                    index_elements=[TextTranslation.source_hash],
                    set_={"ko_text": ko, "engine": label, "guide_hash": gh, "updated_at": func.now()},
                )
            )
        db.commit()

    items = [
        ReasonTranslateItem(
            source=text,
            ko=fresh.get(text) or (hit[text].ko_text if text in hit else None),
            engine=engine_label(provider) if text in fresh else (hit[text].engine if text in hit else ""),
            cached=text in hit and text not in fresh,
        )
        for text in sources
    ]
    failed = sum(1 for i in items if not i.ko)
    if too_long:
        error = (error + " " if error else "") + f"{len(too_long)}건은 원문이 {MAX_LEN}자를 넘어 번역하지 않았습니다."
    return ReasonTranslateResult(
        items=items,
        provider=provider,
        translated=len(fresh),
        cached=sum(1 for i in items if i.cached),
        failed=failed,
        error=error,
    )
