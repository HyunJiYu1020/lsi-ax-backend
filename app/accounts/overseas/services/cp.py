"""CP 계약 저장 — 연도 단위 전체 교체.

왜 서비스로 뺐나 (PLAN.md Phase 5): 외주용역과 같은 이유다. 저장이 `PUT /overseas/state` 하나에
뭉쳐 있었고, CP도 이미 "전체 교체" 의미라 떼어내기 쉬운 자리다. 전용 API와 일괄 저장이 **같은
함수**를 부르게 둔다 — 쓰기 규칙이 두 벌이 되면 PLAN P1-3의 "같은 데이터, 두 가지 쓰기 규칙"을
다시 만든다.

왜 연도 단위 전체 교체인가: 연 1회 올리는 파일이라 사라진 POC·직급은 지워야 한다. upsert만 하면
작년 양식에 있던 POC가 계속 남아서 집계에 섞인다. 보내지 않은 연도는 건드리지 않는다.
"""

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from app.accounts.overseas.models import OverseasCpRow
from app.accounts.overseas.schemas import CpRow


def replace_cp_rows(db: Session, rows: list[CpRow]) -> int:
    """rows에 등장하는 연도를 통째로 바꿔 넣는다. 바뀐 연도 수를 돌려준다.

    commit은 하지 않는다 — 호출부가 다른 쓰기와 한 트랜잭션으로 묶을 수 있어야 한다.
    """
    years = {r.year for r in rows}
    if years:
        db.execute(delete(OverseasCpRow).where(OverseasCpRow.year.in_(years)))
    if rows:
        db.execute(
            insert(OverseasCpRow),
            [
                {
                    "year": r.year, "poc_type": r.poc_type, "poc_proj": r.poc_proj, "poc_desc": r.poc_desc,
                    "title": r.title, "dedicated": r.dedicated, "common": r.common, "man_month": r.man_month,
                    "cost_year": r.cost_year, "poc_cost_sum": r.poc_cost_sum, "poc_mm_sum": r.poc_mm_sum,
                    "poc_direct": r.poc_direct, "poc_ovh": r.poc_ovh, "poc_total": r.poc_total,
                    "source_file": r.source_file,
                }
                for r in rows
            ],
        )
    return len(years)
