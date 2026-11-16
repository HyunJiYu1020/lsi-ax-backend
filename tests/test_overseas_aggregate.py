import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.aggregate import check_overseas_confirm, get_cp_contract, get_labor_ratio

BASIS_YEAR = 2026


def test_cp_contract_folds_poc_rows_and_quarters(db):
    """CP 계약은 POC 헤더값이 직급 행마다 반복 저장돼 있다 — 행마다 더하면 직급 수만큼 부풀려진다.
    분기 하나치는 연 금액 ÷ 4다(분기 정산 계약)."""
    c = get_cp_contract(year=BASIS_YEAR, db=db)

    assert c.poc_count == len(c.pocs)
    assert c.year_total == pytest.approx(sum(p.total for p in c.pocs))
    assert c.year_labor == pytest.approx(sum(p.cost_sum for p in c.pocs))
    assert c.quarter_total * 4 == pytest.approx(c.year_total)
    assert c.quarter_labor * 4 == pytest.approx(c.year_labor)
    assert c.registered == bool(c.pocs)


def test_cp_contract_missing_year_is_not_zero_but_unregistered(db):
    """계약이 없는 연도는 404도, "0원"도 아니다 — registered=False로 알려야 화면이 CP 정산 칸을
    빈칸으로 둘 수 있다(0으로 채우면 "CP 비용이 0원"으로 읽힌다)."""
    c = get_cp_contract(year=1999, db=db)

    assert c.registered is False
    assert c.poc_count == 0 and c.pocs == []
    assert c.quarter_total == 0 and c.quarter_labor == 0
    assert "빈칸" in c.note


def test_confirm_check_blocks_only_on_missing_fx(db):
    """공통 RULE: 환율 미등록 월이 있으면 확정 불가, 미업로드·사유 미작성은 경고 후 확정 가능.
    그래서 can_confirm은 blocking이 비었는지와 정확히 같아야 하고, 경고만으로는 막히지 않는다."""
    r = check_overseas_confirm(basis_year=BASIS_YEAR, kind="forecast", db=db)

    assert r.can_confirm == (not r.blocking)
    assert bool(r.fx_missing_months) == bool(r.blocking)
    assert r.uploaded == r.lab_count - len(r.missing_labs)
    # 경고가 있으면 "무엇을 알고도 확정했는지"가 단서로 남아야 한다 — 빈 문장이면 버전 화면에
    # "완료"만 남아서 미업로드 상태가 사라진다.
    if r.missing_labs:
        assert f"미업로드 {len(r.missing_labs)}개 포함" in r.note
    if not r.warnings:
        assert r.note == ""


def test_confirm_check_blocks_year_without_fx_rates(db):
    """환율이 아예 없는 연도는 12개월 모두 미등록이라 확정할 수 없다."""
    r = check_overseas_confirm(basis_year=1999, basis_month=9, kind="forecast", db=db)

    assert r.can_confirm is False
    assert r.fx_missing_months == list(range(1, 13))
    assert "기준정보 › 환율" in r.blocking[0]


def test_confirm_check_rejects_bad_kind(db):
    with pytest.raises(HTTPException) as e:
        check_overseas_confirm(basis_year=BASIS_YEAR, kind="sokbo", db=db)
    assert e.value.status_code == 400


def test_labor_ratio_divides_period_sums_not_monthly_ratios(db):
    """공통 RULE(인건비율): 분기·연간은 월별 비율을 평균 내지 않고 합계끼리 나눈다.
    그래서 연간 비율은 분기 4개의 (인건비 합 ÷ 개발비 합)과 같아야 한다."""
    year = get_labor_ratio(basis_year=BASIS_YEAR, db=db)
    quarters = [get_labor_ratio(basis_year=BASIS_YEAR, period=f"q{q}", db=db) for q in (1, 2, 3, 4)]

    a = year.rows[0]
    assert a.lab_code == "ALL"
    assert a.ratio == pytest.approx(a.labor / a.total * 100)
    qt = sum(q.rows[0].total for q in quarters)
    ql = sum(q.rows[0].labor for q in quarters)
    assert qt == pytest.approx(a.total, rel=1e-9)
    assert ql / qt * 100 == pytest.approx(a.ratio, rel=1e-9)


def test_labor_ratio_all_row_is_the_sum_of_labs(db):
    """전체 줄은 연구소 줄의 합이다 — 따로 계산해서 어긋나면 "전체 인건비율"이 어느 범위인지 알 수 없다."""
    r = get_labor_ratio(basis_year=BASIS_YEAR, db=db)
    labs = r.rows[1:]

    assert sum(x.total for x in labs) == pytest.approx(r.rows[0].total)
    assert sum(x.labor for x in labs) == pytest.approx(r.rows[0].labor)
    # 분모가 0인 줄은 0%가 아니라 null이어야 한다(화면 '—').
    for x in r.rows:
        assert (x.ratio is None) == (x.total == 0)


def test_labor_ratio_rejects_bad_arguments(db):
    with pytest.raises(HTTPException) as e:
        get_labor_ratio(basis_year=BASIS_YEAR, period="h1", db=db)
    assert e.value.status_code == 400

    with pytest.raises(HTTPException) as e:
        get_labor_ratio(basis_year=BASIS_YEAR, version="draft", db=db)
    assert e.value.status_code == 400
