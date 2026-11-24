"""증감사유 업로드 시트 저장 — 연구소×버전×항목×구간 키로 upsert(누적).

왜 서비스로 뺐나: 외주용역·CP와 같은 이유로 `PUT /overseas/state`에서 떼어낸다.
전용 API와 일괄 저장이 **같은 함수**를 부르게 둬서 쓰기 규칙이 두 벌이 되지 않게 한다.

외주용역·CP와 의미가 다르다: 이쪽은 **전체 교체가 아니라 보낸 것만 누적 반영**이다.
증감사유는 연구소가 보낸 회신을 항목별로 쌓아 가는 자료라, 한 연구소 시트를 올렸을 때 다른
항목의 사유가 사라지면 안 된다(비용 값을 월별로 덧쓰는 것과 같은 규칙).
"""

import asyncio

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.accounts.overseas.models import OverseasReasonRow
from app.accounts.overseas.schemas import ReasonRow
from app.accounts.overseas.services.labs import ACCOUNT_KEY
from app.accounts.overseas.services.variance import cache_translation
from app.shared.core.guidelines import guide_fingerprint, guidelines_text
from app.shared.core.reason_translate import engine_label, normalize, resolve_provider, translate_batch
from app.shared.core.values import source_hash
from app.shared.models.generic import TextTranslation

# 업로드가 덮어쓰는 칸. 키(lab_code·version·category_key·block)는 당연히 제외한다.
_SET_COLS = (
    "mon_label", "plan_value", "prev_value", "cur_value", "diff_plan", "diff_prev",
    "remark_plan", "remark_prev", "prev_label", "cur_label", "source_file",
)


def upsert_reason_rows(
    db: Session, rows: list[ReasonRow], known_lab_codes: set[str], default_yyyymm: str = ""
) -> int:
    """보낸 행을 키로 upsert한다. 반영된 행 수를 돌려준다.

    default_yyyymm은 행에 기준월이 없을 때 채워 넣을 값이다. 둘 다 없으면 400으로 멈춘다 —
    월을 모르는 채 넣으면 어느 달 회신인지 영영 알 수 없고, "9월에 올린 사유가 10월 자료로
    잡히는" 일이 생긴다.

    commit은 하지 않는다 — 호출부가 다른 쓰기와 한 트랜잭션으로 묶을 수 있어야 한다.
    """
    unknown = {r.lab_code for r in rows if r.lab_code not in known_lab_codes}
    if unknown:
        raise HTTPException(status_code=400, detail=f"알 수 없는 연구소 코드(증감사유): {sorted(unknown)}")

    ym_of = {id(r): (r.yyyymm or default_yyyymm) for r in rows}
    if rows and not all(ym_of.values()):
        raise HTTPException(status_code=400, detail="증감사유 행에 기준월(yyyymm)이 없다.")

    # 같은 키가 두 번 들어오면 마지막 값이 이긴다 — 한 문장 안에 같은 키가 두 번 있으면
    # Postgres가 "ON CONFLICT DO UPDATE command cannot affect row a second time"로 거부한다.
    # 키에 version을 넣지 않는다 — 같은 달 자료가 자리(cur·prev)마다 한 벌씩 늘어난다(모델 주석 참고).
    dedup: dict[tuple[str, str, str, str], ReasonRow] = {
        (r.lab_code, ym_of[id(r)], r.category_key, r.block): r for r in rows
    }
    if not dedup:
        return 0

    stmt = pg_insert(OverseasReasonRow).values(
        [
            {
                "lab_code": r.lab_code, "version": r.version, "yyyymm": ym_of[id(r)],
                "category_key": r.category_key, "block": r.block,
                "mon_label": r.mon_label, "plan_value": r.plan_value, "prev_value": r.prev_value,
                "cur_value": r.cur_value, "diff_plan": r.diff_plan, "diff_prev": r.diff_prev,
                "remark_plan": r.remark_plan, "remark_prev": r.remark_prev, "prev_label": r.prev_label,
                "cur_label": r.cur_label, "source_file": r.source_file,
            }
            for r in dedup.values()
        ]
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["lab_code", "yyyymm", "category_key", "block"],
            # uploaded_at도 같이 갱신한다. 두 가지 이유로 그게 맞다:
            # ① 의미 — 새 파일이 이 행의 내용을 덮어썼으면 "올라온 시각"은 그 새 업로드 시각이다.
            #    안 바꾸면 내용은 이번 달 것인데 시각만 처음 업로드 때로 남는다.
            # ② 동시 편집 보호 — state_token이 이 테이블의 max(uploaded_at)과 count(*)를 본다.
            #    내용만 바뀌고 둘 다 그대로면 토큰이 변화를 놓쳐서, 두 사람이 같은 사유를 고쳤을 때
            #    나중 저장이 조용히 덮어쓴다(테스트가 실제로 그 구멍을 잡았다).
            set_={**{k: getattr(stmt.excluded, k) for k in _SET_COLS}, "uploaded_at": func.now()},
        )
    )
    return len(dedup)

# ---------- 업로드 시점 번역 ----------
# 왜 조회할 때가 아니라 업로드할 때 번역하나: 조회 때 번역하면 첫 번째로 화면을 연 사람은 LLM을
# 기다리는 동안 폴백 번역(core.js ovTrRule — 목업 시절 사전·정규식)을 보고, 번역이 도착하면 눈앞에서
# 값이 바뀐다. LLM이 실패하면 그 폴백이 그대로 남아서 사람마다·시점마다 다른 문장이 보인다.
# 업로드할 때 한 번 번역해 DB에 넣어 두면 조회는 항상 같은 값을 읽는다.
#
# 실패해도 업로드를 막지 않는다 — 연구소가 보낸 자료를 받는 게 먼저다. 번역이 안 됐으면 조회 때
# 기존 경로(POST /overseas/reason-translate)가 다시 시도한다(같은 캐시를 쓰므로 중복 번역이 아니다).
MAX_PREWARM = 120


def reason_texts(rows: list[ReasonRow]) -> list[str]:
    """업로드된 증감사유 행에서 번역할 원문만 골라 중복 없이 모은다.

    정규화(normalize)해서 모으는 이유: 엑셀에서 긁어온 원문은 같은 문장이 줄바꿈·연속 공백만 다르게
    여러 벌 와서, 그대로 두면 한 문장을 몇 번씩 번역하고 결과도 갈린다(캐시 키와 같은 규칙).
    영문이 아닌 것(이미 한국어)도 모델이 그대로 돌려주도록 규칙에 박혀 있어 따로 걸러내지 않는다.
    """
    seen: dict[str, None] = {}
    for r in rows:
        for raw in (r.remark_plan, r.remark_prev):
            t = normalize(raw or "")
            if t:
                seen.setdefault(t, None)
    return list(seen)[:MAX_PREWARM]


async def prewarm_reason_translations(db: Session, rows: list[ReasonRow], provider: str | None = None) -> dict:
    """업로드된 증감사유를 번역해 캐시(text_translations)에 넣는다. 돌려주는 값은 집계뿐이다.

    이미 같은 지침으로 번역해 둔 원문은 건너뛴다 — 재업로드가 흔해서(연구소가 수정본을 보낸다)
    매번 전부 다시 번역하면 비용만 든다.
    """
    texts = reason_texts(rows)
    out = {"total": len(texts), "cached": 0, "translated": 0, "error": ""}
    if not texts:
        return out

    gh = guide_fingerprint(db, ACCOUNT_KEY, "translate")
    have = {
        r.source_hash
        for r in db.scalars(
            select(TextTranslation).where(
                TextTranslation.source_hash.in_([source_hash(t) for t in texts]),
                TextTranslation.guide_hash == gh,
                TextTranslation.ko_text != "",
            )
        )
    }
    todo = [t for t in texts if source_hash(t) not in have]
    out["cached"] = len(texts) - len(todo)
    if not todo:
        return out

    # provider를 먼저 확정한다 — engine_label은 확정된 공급자 이름만 받는다(None이면 KeyError).
    # resolve_provider가 설정값(settings.chat_provider)을 적용하고 허용 목록을 검사한다.
    try:
        prov = resolve_provider(provider)
        result = await translate_batch(todo, prov, guidelines=guidelines_text(db, ACCOUNT_KEY, "translate"))
    except HTTPException as e:
        # 번역만 못 하고 업로드는 그대로 끝낸다. 조회 때 다시 시도한다.
        out["error"] = str(e.detail)
        return out

    label = engine_label(prov)
    for text, ko in zip(todo, result):
        if not ko:
            continue
        cache_translation(db, text, ko, label, gh)
        out["translated"] += 1
    return out


def prewarm_in_background(background: BackgroundTasks, rows: list[ReasonRow]) -> None:
    """번역을 응답 **뒤에** 돌리도록 예약한다. 저장은 기다리지 않는다.

    왜 저장을 막지 않나: PUT /overseas/state는 화면의 모든 저장이 지나가는 길이다(메모 한 줄을
    고쳐도 여기로 온다). 그 자리에서 LLM을 기다리면 번역과 아무 상관 없는 저장까지 수 초씩 늘어지고,
    키·네트워크 문제가 "저장이 안 된다"로 번진다. 번역은 급하지 않다 — 저장 직후 몇 초 안에 DB에
    들어가고, 그 전에 화면이 조회하면 기존 경로(POST /overseas/reason-translate)가 같은 캐시에
    채워 넣는다(중복 번역이 아니다).

    자기 세션을 새로 연다 — 요청 세션은 응답과 함께 닫히므로 그걸 들고 들어가면 쓸 수 없다.
    예외는 삼킨다: 백그라운드에서 터져도 담당자에게 보여줄 곳이 없고, 저장은 이미 끝났다.
    """
    if not rows:
        return
    texts = reason_texts(rows)
    if not texts:
        return

    def run() -> None:
        db = SessionLocal()
        try:
            asyncio.run(prewarm_reason_translations(db, rows))
        except Exception:
            pass
        finally:
            db.close()

    background.add_task(run)
