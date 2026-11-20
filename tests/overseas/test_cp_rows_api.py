"""CP 계약 전용 저장 API — PLAN.md Phase 5의 두 번째 조각 + 시점별 스냅샷.

CP는 연 1회 올리는 파일이라 "연도 단위 전체 교체"다 — 사라진 POC·직급은 지워야 한다.
외주용역과 같이, 전용 API와 일괄 저장(PUT /state)이 같은 서비스 함수를 부르는지가 핵심이다.

여기에 적용 시점(effective_from)이 붙었다: 분기 정산액은 저장된 값이 아니라 조회할 때마다 다시
계산되는 값이라, 시점이 없으면 계약을 고치는 순간 이미 마감한 과거 버전의 분기 정산액까지 새
계약액으로 다시 계산된다. "9월에 바꾸면 9월 이후 버전만, 8월 이하는 옛 값"이 성립하는지를 못 박는다.
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.collect import (
    get_overseas_state,
    save_cp_rows,
    save_overseas_state,
)
from app.accounts.overseas.schemas import CpRow, CpRowsIn
from app.accounts.overseas.services.aggregate import cp_quarter_amounts
from app.accounts.overseas.services.cp import cp_rows_at

BASIS_YEAR = 2026
YEAR = f"{BASIS_YEAR:04d}"
OTHER = "2099"  # 건드리면 안 되는 다른 연도
# 적용 시점 테스트는 실제 데이터가 없는 가짜 연도를 쓴다 — 개발 DB의 2026년 계약('0000-01' 세트)이
# 섞이면 "그 시점 이전에는 계약이 없다"를 확인할 수 없다.
ASOF_YEAR = "2098"


def _row(year: str, title: str, total: float = 100.0, proj: str = "P1") -> CpRow:
    return CpRow(
        year=year, poc_type="POC", poc_proj=proj, poc_desc="설명", title=title,
        dedicated=1.0, common=0.0, man_month=1.0, cost_year=10.0,
        poc_cost_sum=50.0, poc_mm_sum=5.0, poc_direct=20.0, poc_ovh=30.0,
        poc_total=total, source_file="cp.xlsx",
    )


def _stored(db, year: str, as_of: str = f"{BASIS_YEAR:04d}-12") -> list[tuple[str, str, float]]:
    """그 시점 버전이 보는 계약 세트. 시점을 안 주면 기준연도 말 — 방금 저장한 세트가 보인다.

    테이블을 통째로 읽지 않는 이유: 이제 같은 연도에 시점별 세트가 여러 벌 있을 수 있어서,
    전부 읽으면 "한 버전이 보는 계약"이 아니라 "지금까지의 모든 계약"이 나온다."""
    return sorted(
        (r.poc_proj, r.title, float(r.poc_total)) for r in cp_rows_at(db, year, as_of)
    )


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


# ---------- 적용 시점: 바꾼 버전부터만 ----------


def test_change_applies_from_that_version_onward(db):
    """9월에 계약을 바꾸면 9월 이후 버전만 새 값으로 보고, 8월 이하 버전은 바뀌기 전 값 그대로다.
    공통 RULE(기준정보 변경은 변경한 버전부터 적용, 지난 버전 영향 없음)을 CP에 적용한 것."""
    save_cp_rows(
        body=CpRowsIn(rows=[_row(ASOF_YEAR, "VP", total=100.0)], effective_from=f"{ASOF_YEAR}-06"), db=db
    )
    save_cp_rows(
        body=CpRowsIn(rows=[_row(ASOF_YEAR, "VP", total=777.0)], effective_from=f"{ASOF_YEAR}-09"), db=db
    )

    assert _stored(db, ASOF_YEAR, f"{ASOF_YEAR}-06") == [("P1", "VP", 100.0)]
    assert _stored(db, ASOF_YEAR, f"{ASOF_YEAR}-08") == [("P1", "VP", 100.0)], "지난 버전이 소급해서 바뀌었다"
    assert _stored(db, ASOF_YEAR, f"{ASOF_YEAR}-09") == [("P1", "VP", 777.0)]
    assert _stored(db, ASOF_YEAR, f"{ASOF_YEAR}-12") == [("P1", "VP", 777.0)], "이후 버전에 적용되지 않았다"


def test_versions_before_the_first_contract_see_nothing(db):
    """계약이 올라오기 전 버전에는 계약이 없다 — 0원이 아니라 '없음'이어야 CP 정산 칸이 빈칸이 된다."""
    save_cp_rows(
        body=CpRowsIn(rows=[_row(ASOF_YEAR, "VP", total=100.0)], effective_from=f"{ASOF_YEAR}-09"), db=db
    )

    assert _stored(db, ASOF_YEAR, f"{ASOF_YEAR}-08") == []


def test_quarter_amount_follows_the_version(db):
    """분기 정산액은 매번 다시 계산되는 값이라, 보는 버전이 바뀌면 그 버전의 계약으로 계산된다."""
    save_cp_rows(
        body=CpRowsIn(rows=[_row(ASOF_YEAR, "VP", total=4000.0)], effective_from=f"{ASOF_YEAR}-06"), db=db
    )
    save_cp_rows(
        body=CpRowsIn(rows=[_row(ASOF_YEAR, "VP", total=8000.0)], effective_from=f"{ASOF_YEAR}-09"), db=db
    )

    before, _ = cp_quarter_amounts(db, int(ASOF_YEAR), f"{ASOF_YEAR}-08")
    after, _ = cp_quarter_amounts(db, int(ASOF_YEAR), f"{ASOF_YEAR}-09")

    assert before == pytest.approx(4000.0 / 4 / 1000)
    assert after == pytest.approx(8000.0 / 4 / 1000)


def test_same_month_change_overwrites_that_set(db):
    """같은 달에 두 번 올리면 그 달 세트만 덮인다 — 지난 버전이 보는 세트는 그대로다."""
    save_cp_rows(
        body=CpRowsIn(rows=[_row(ASOF_YEAR, "VP", total=100.0)], effective_from=f"{ASOF_YEAR}-06"), db=db
    )
    save_cp_rows(
        body=CpRowsIn(rows=[_row(ASOF_YEAR, "VP", total=777.0)], effective_from=f"{ASOF_YEAR}-09"), db=db
    )
    save_cp_rows(
        body=CpRowsIn(rows=[_row(ASOF_YEAR, "VP", total=999.0)], effective_from=f"{ASOF_YEAR}-09"), db=db
    )

    assert _stored(db, ASOF_YEAR, f"{ASOF_YEAR}-09") == [("P1", "VP", 999.0)]
    assert _stored(db, ASOF_YEAR, f"{ASOF_YEAR}-08") == [("P1", "VP", 100.0)]
