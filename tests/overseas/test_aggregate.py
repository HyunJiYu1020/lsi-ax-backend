import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.aggregate import check_overseas_confirm, get_cp_contract, get_labor_ratio

BASIS_YEAR = 2026
# CP 계약은 이제 시점별 스냅샷이라 "어느 버전에서 보는가"를 같이 줘야 한다(services/cp.py).
AS_OF = f"{BASIS_YEAR:04d}-12"


def test_cp_contract_folds_poc_rows_and_quarters(db):
    """CP 계약은 POC 헤더값이 직급 행마다 반복 저장돼 있다 — 행마다 더하면 직급 수만큼 부풀려진다.
    분기 하나치는 연 금액 ÷ 4다(분기 정산 계약)."""
    c = get_cp_contract(year=BASIS_YEAR, as_of=AS_OF, db=db)

    assert c.poc_count == len(c.pocs)
    assert c.year_total == pytest.approx(sum(p.total for p in c.pocs))
    assert c.year_labor == pytest.approx(sum(p.cost_sum for p in c.pocs))
    assert c.quarter_total * 4 == pytest.approx(c.year_total)
    assert c.quarter_labor * 4 == pytest.approx(c.year_labor)
    assert c.registered == bool(c.pocs)


def test_cp_contract_missing_year_is_not_zero_but_unregistered(db):
    """계약이 없는 연도는 404도, "0원"도 아니다 — registered=False로 알려야 화면이 CP 정산 칸을
    빈칸으로 둘 수 있다(0으로 채우면 "CP 비용이 0원"으로 읽힌다)."""
    c = get_cp_contract(year=1999, as_of="1999-12", db=db)

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


# ---------- 인건비율도 "어느 버전인가"를 받는다 (2026-11-17) ----------
def test_labor_ratio_takes_the_version(db):
    """집계표와 같은 파라미터(basis_month · kind)로 버전을 고른다 — 전에는 기준월을 DB 최신
    속보로 혼자 정해서 과거 버전의 인건비율을 낼 수 없었다."""
    from app.accounts.overseas.endpoints.aggregate import get_labor_ratio

    f = get_labor_ratio(basis_year=BASIS_YEAR, basis_month=9, kind="forecast", db=db)
    a = get_labor_ratio(basis_year=BASIS_YEAR, basis_month=8, kind="actual", db=db)

    assert (f.basis_month, f.kind, f.version_label) == (9, "forecast", "26.09 속보")
    assert (a.basis_month, a.kind, a.version_label) == (8, "actual", "26.08 실적")
    assert f.rows[0].lab_code == "ALL" and a.rows[0].lab_code == "ALL"
    assert f.rows[0].ratio != a.rows[0].ratio, "속보와 실적이 같은 비율이면 버전이 안 먹은 것이다"


def test_labor_ratio_default_is_the_latest_forecast(db):
    """기준월을 안 보내는 기존 호출은 종전과 같아야 한다."""
    from app.accounts.overseas.endpoints.aggregate import get_labor_ratio

    d = get_labor_ratio(basis_year=BASIS_YEAR, db=db)
    f = get_labor_ratio(basis_year=BASIS_YEAR, basis_month=d.basis_month, kind="forecast", db=db)

    assert d.kind == "forecast"
    assert [(r.lab_code, r.ratio) for r in d.rows] == [(r.lab_code, r.ratio) for r in f.rows]


def test_labor_ratio_uses_the_same_materials_as_the_aggregate_table(db):
    """인건비율과 집계표가 같은 재료(table_inputs)를 쓴다 — 연구소 추리기·추가 행·환율 규칙이
    갈리면 같은 화면의 두 숫자가 다른 분모를 쓴다."""
    from app.accounts.overseas.endpoints.aggregate import get_labor_ratio
    from app.accounts.overseas.services.versions import table_inputs

    inp = table_inputs(db, BASIS_YEAR, "cur", False, 9, "forecast")
    r = get_labor_ratio(basis_year=BASIS_YEAR, basis_month=9, kind="forecast", db=db)

    # 비율이 나온 연구소는 모두 집계표가 세는 연구소 안에 있어야 한다.
    counted = {inp.labs[pk].lab_code for pk in inp.pks}
    assert {row.lab_code for row in r.rows if row.lab_code != "ALL"} <= counted


def test_labor_split_takes_the_version(db):
    """인건비 분해(인원·단가)도 같은 파라미터를 받는다. 비교 대상 라벨을 같이 돌려줘서
    "무엇과 비교한 분해인지"가 응답만 보고 드러나야 한다."""
    from app.accounts.overseas.endpoints.collect import get_labor_split

    s = get_labor_split(basis_year=BASIS_YEAR, basis_month=9, kind="forecast", db=db)

    assert (s.basis_month, s.kind) == (9, "forecast")
    assert s.base_label and "." in s.base_label  # "26.08 실적"
    # 두 효과의 합(+ 인력 없는 연구소 몫)이 환차外와 맞아야 한다 — 분해의 정의다.
    assert abs((s.hc_effect + s.rate_effect + s.unsplit) - s.fx_outside) < 1.0


def test_labor_split_rejects_bad_month_and_kind(db):
    from app.accounts.overseas.endpoints.collect import get_labor_split

    with pytest.raises(HTTPException) as e:
        get_labor_split(basis_year=BASIS_YEAR, basis_month=13, db=db)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        get_labor_split(basis_year=BASIS_YEAR, kind="sok", db=db)
    assert e.value.status_code == 400
