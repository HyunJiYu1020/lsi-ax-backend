"""CP 계약 전용 저장 API — PLAN.md Phase 5의 두 번째 조각.

CP는 연 1회 올리는 파일이라 "연도 단위 전체 교체"다 — 사라진 POC·직급은 지워야 한다.
외주용역과 같이, 전용 API와 일괄 저장(PUT /state)이 같은 서비스 함수를 부르는지가 핵심이다.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.accounts.overseas.endpoints.collect import (
    get_overseas_state,
    save_cp_rows,
    save_overseas_state,
)
from app.accounts.overseas.models import OverseasCpRow
from app.accounts.overseas.schemas import CpRow, CpRowsIn

BASIS_YEAR = 2026
YEAR = f"{BASIS_YEAR:04d}"
OTHER = "2099"  # 건드리면 안 되는 다른 연도


def _row(year: str, title: str, total: float = 100.0, proj: str = "P1") -> CpRow:
    return CpRow(
        year=year, poc_type="POC", poc_proj=proj, poc_desc="설명", title=title,
        dedicated=1.0, common=0.0, man_month=1.0, cost_year=10.0,
        poc_cost_sum=50.0, poc_mm_sum=5.0, poc_direct=20.0, poc_ovh=30.0,
        poc_total=total, source_file="cp.xlsx",
    )


def _stored(db, year: str) -> list[tuple[str, str, float]]:
    return [
        (r.poc_proj, r.title, float(r.poc_total))
        for r in db.scalars(
            select(OverseasCpRow).where(OverseasCpRow.year == year).order_by(OverseasCpRow.title)
        )
    ]


def test_saves_rows(db):
    res = save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "VP"), _row(YEAR, "CL4")]), db=db)

    assert res["status"] == "ok" and res["replaced"] == 1
    assert _stored(db, YEAR) == [("P1", "CL4", 100.0), ("P1", "VP", 100.0)]


def test_replaces_the_whole_year(db):
    """연 1회 올리는 파일이라 사라진 직급은 지워져야 한다 — upsert만 하면 작년 양식의 POC가 남는다."""
    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, t) for t in ("VP", "CL4", "CL3")]), db=db)
    assert len(_stored(db, YEAR)) == 3

    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "VP", total=777.0)]), db=db)
    assert _stored(db, YEAR) == [("P1", "VP", 777.0)]


def test_other_years_are_untouched(db):
    """보내지 않은 연도는 건드리지 않는다."""
    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "VP"), _row(OTHER, "VP", total=5.0)]), db=db)

    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "VP", total=9.0)]), db=db)

    assert _stored(db, YEAR) == [("P1", "VP", 9.0)]
    assert _stored(db, OTHER) == [("P1", "VP", 5.0)], "다른 연도가 사라졌다"


def test_matches_the_bulk_state_path(db):
    """전용 API와 일괄 저장이 같은 결과를 낸다 — 같은 서비스 함수를 부르므로 성립해야 한다."""
    rows = [_row(YEAR, "VP", 11.0), _row(YEAR, "CL4", 22.0)]

    save_cp_rows(body=CpRowsIn(rows=rows), db=db)
    via_api = _stored(db, YEAR)

    state = get_overseas_state(basis_year=BASIS_YEAR, db=db)
    state.cp = rows
    save_overseas_state(state=state, db=db)

    assert _stored(db, YEAR) == via_api, "전용 API와 일괄 저장의 결과가 다르다"


def test_empty_rows_changes_nothing(db):
    """빈 목록은 아무것도 지우지 않는다 — 어느 연도를 비울지 알 수 없으므로."""
    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "VP")]), db=db)
    before = _stored(db, YEAR)

    assert save_cp_rows(body=CpRowsIn(rows=[]), db=db)["replaced"] == 0
    assert _stored(db, YEAR) == before


def test_stale_token_is_rejected(db):
    stale = get_overseas_state(basis_year=BASIS_YEAR, db=db).state_token
    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "VP")]), db=db)  # 토큰이 낡는다

    with pytest.raises(HTTPException) as e:
        save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "CL4")], state_token=stale), db=db)
    assert e.value.status_code == 409
