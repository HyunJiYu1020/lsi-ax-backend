"""증감사유 업로드 시트 저장 — 연구소×버전×항목×구간 키로 upsert(누적).

왜 서비스로 뺐나 (PLAN.md Phase 5): 외주용역·CP와 같은 이유로 `PUT /overseas/state`에서 떼어낸다.
전용 API와 일괄 저장이 **같은 함수**를 부르게 둬서 쓰기 규칙이 두 벌이 되지 않게 한다.

외주용역·CP와 의미가 다르다: 이쪽은 **전체 교체가 아니라 보낸 것만 누적 반영**이다.
증감사유는 연구소가 보낸 회신을 항목별로 쌓아 가는 자료라, 한 연구소 시트를 올렸을 때 다른
항목의 사유가 사라지면 안 된다(비용 값을 월별로 덧쓰는 것과 같은 규칙).
"""

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.accounts.overseas.models import OverseasReasonRow
from app.accounts.overseas.schemas import ReasonRow

# 업로드가 덮어쓰는 칸. 키(lab_code·version·category_key·block)는 당연히 제외한다.
_SET_COLS = (
    "mon_label", "plan_value", "prev_value", "cur_value", "diff_plan", "diff_prev",
    "remark_plan", "remark_prev", "prev_label", "cur_label", "source_file",
)


def upsert_reason_rows(db: Session, rows: list[ReasonRow], known_lab_codes: set[str]) -> int:
    """보낸 행을 키로 upsert한다. 반영된 행 수를 돌려준다.

    commit은 하지 않는다 — 호출부가 다른 쓰기와 한 트랜잭션으로 묶을 수 있어야 한다.
    """
    unknown = {r.lab_code for r in rows if r.lab_code not in known_lab_codes}
    if unknown:
        raise HTTPException(status_code=400, detail=f"알 수 없는 연구소 코드(증감사유): {sorted(unknown)}")

    # 같은 키가 두 번 들어오면 마지막 값이 이긴다 — 한 문장 안에 같은 키가 두 번 있으면
    # Postgres가 "ON CONFLICT DO UPDATE command cannot affect row a second time"로 거부한다.
    dedup: dict[tuple[str, str, str, str], ReasonRow] = {
        (r.lab_code, r.version, r.category_key, r.block): r for r in rows
    }
    if not dedup:
        return 0

    stmt = pg_insert(OverseasReasonRow).values(
        [
            {
                "lab_code": r.lab_code, "version": r.version, "category_key": r.category_key, "block": r.block,
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
            index_elements=["lab_code", "version", "category_key", "block"],
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
