"""집계 파생 행(계산 행)의 사칙연산과 원화 환산.

지키는 규칙:
  - 연산은 사칙연산 네 개다(+ − × ÷). 재료가 2개여야 하는 연산은 개수를 검증한다.
  - 연간 근거는 연산마다 다르다 — 합·차는 재료의 연간합으로, 곱은 월별 결과의 합으로,
    나누기는 "분자 연간합 ÷ 분모 연간합"으로 낸다.
  - 원화 토글은 **재료를 환산한 뒤 계산**한다(계산 결과에 환율을 곱하지 않는다). 그래서 금액
    파생 행은 원화가 되고, 금액÷금액인 비율 행은 환율이 약분돼 두 모드에서 같은 값이다.
  - 재료는 집계표와 같은 연구소 집합을 쓴다 — 합계 시트(SARC:TOTAL)를 빼지 않으면 그 연구소가
    두 번 세어져 같은 급여인데 표와 파생 행 숫자가 달라진다.
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.aggregate import get_aggregate_table
from app.accounts.overseas.endpoints.collect import upsert_extra_row
from app.accounts.overseas.schemas import ExtraRowIn
from app.accounts.overseas.services.extra_rows import (
    check_row_formula,
    eval_row_formula,
    months_by_category,
    scale_materials,
)
from app.accounts.overseas.services.labs import ALL_SCOPE

BASIS_YEAR, BASIS_MONTH = 2026, 9
YYYYMM = BASIS_YEAR * 100 + BASIS_MONTH

# 재료 세 개 — 금액 둘(급여·상여)과 수량 하나(인력).
MATERIALS = {"sal": [100.0] * 12, "bonus": [20.0] * 12, "hc": [10.0] * 12}


def _eval(op: str, operands: list[str], materials: dict | None = None):
    return eval_row_formula(materials or MATERIALS, {"op": op, "operands": operands})


# ---------- 연산 네 개 ----------
def test_sum_adds_the_same_month():
    values, basis = _eval("sum_elementwise", ["sal", "bonus"])
    assert values[:12] == [120.0] * 12
    assert basis["annual"]["result"] == pytest.approx(1440.0)  # 1200 + 240


def test_sub_subtracts_in_the_given_order():
    """[피감수, 감수] 순서다 — 뒤집히면 부호가 반대인 값이 조용히 저장된다."""
    values, basis = _eval("sub_elementwise", ["sal", "bonus"])
    assert values[:12] == [80.0] * 12
    assert basis["annual"]["result"] == pytest.approx(960.0)  # 1200 − 240
    assert basis["text"] == "급여 − 상여"


def test_mul_annual_is_the_sum_of_monthly_products():
    """곱하기의 연간은 **월별 결과의 합**이다(연간합끼리 곱하면 12배 규모가 된다)."""
    values, basis = _eval("mul_elementwise", ["sal", "bonus"])
    assert values[:12] == [2000.0] * 12
    assert basis["annual"]["result"] == pytest.approx(24000.0)
    assert basis["annual"]["result"] != pytest.approx(1200.0 * 240.0)


def test_div_annual_is_sum_over_sum():
    """나누기의 연간은 분자·분모의 연간합끼리 나눈다(월별 비율 12개를 더하면 뜻이 없다)."""
    values, basis = _eval("div_elementwise", ["sal", "hc"])
    assert values[:12] == [10.0] * 12
    assert basis["annual"]["result"] == pytest.approx(1200.0 / 120.0)


def test_div_by_zero_month_is_zero_and_shows_in_the_basis():
    materials = dict(MATERIALS, hc=[0.0] + [10.0] * 11)
    values, basis = _eval("div_elementwise", ["sal", "hc"], materials)
    assert values[0] == 0.0 and values[1] == 10.0
    assert basis["months"][0]["values"][1] == 0.0


# ---------- 계산식 검증 ----------
@pytest.mark.parametrize("op", ["sub_elementwise", "mul_elementwise", "div_elementwise"])
def test_two_operand_ops_reject_wrong_count(op):
    """빼기·곱하기·나누기는 재료가 정확히 2개다 — 3개를 받으면 어느 둘을 쓸지 아무도 모른다."""
    for operands in (["sal"], ["sal", "bonus", "hc"]):
        with pytest.raises(HTTPException) as e:
            check_row_formula({"op": op, "operands": operands})
        assert e.value.status_code == 400 and op in str(e.value.detail)


def test_sum_needs_at_least_two():
    with pytest.raises(HTTPException):
        check_row_formula({"op": "sum_elementwise", "operands": ["sal"]})


def test_unknown_op_is_rejected_with_the_allowed_list():
    with pytest.raises(HTTPException) as e:
        check_row_formula({"op": "pow_elementwise", "operands": ["sal", "bonus"]})
    assert "sub_elementwise" in str(e.value.detail) and "mul_elementwise" in str(e.value.detail)


# ---------- 환산 후 계산 ----------
def test_scale_materials_converts_money_but_not_headcount():
    """환율은 금액 재료에만 곱한다 — 인력(명)에 원/$를 곱하면 뜻이 깨진다."""
    rate = [1300.0] * 12
    scaled = scale_materials(MATERIALS, rate)
    assert scaled["sal"] == [130000.0] * 12
    assert scaled["hc"] == [10.0] * 12


def test_ratio_is_the_same_in_both_units_but_money_is_converted():
    """환산 후 계산이면 금액÷금액 비율은 환율이 약분돼 그대로고, 합계는 환율만큼 커진다."""
    rate = [1300.0] * 12
    scaled = scale_materials(MATERIALS, rate)

    usd_ratio, _ = _eval("div_elementwise", ["sal", "bonus"])
    krw_ratio, _ = _eval("div_elementwise", ["sal", "bonus"], scaled)
    assert krw_ratio == usd_ratio

    usd_sum, _ = _eval("sum_elementwise", ["sal", "bonus"])
    krw_sum, _ = _eval("sum_elementwise", ["sal", "bonus"], scaled)
    assert krw_sum[0] == pytest.approx(usd_sum[0] * 1300.0)

    # 금액÷인력(인당 금액)은 분모에 환율이 없어 결과에 환율이 남는다 — 원/명이 된다.
    usd_per, _ = _eval("div_elementwise", ["sal", "hc"])
    krw_per, _ = _eval("div_elementwise", ["sal", "hc"], scaled)
    assert krw_per[0] == pytest.approx(usd_per[0] * 1300.0)


# ---------- 재료 집합(합계 시트 제외) ----------
def test_materials_match_the_aggregate_table(db):
    """파생 행의 재료 연간합 = 집계표 그 계정의 연간 칸. 합계 시트를 빼지 않으면 SARC가 두 번
    세어져 어긋난다(tests/overseas/test_total_slot.py의 규칙을 파생 행도 따라야 한다)."""
    materials = months_by_category(db, ALL_SCOPE, "forecast", YYYYMM)
    if not materials.get("sal"):
        pytest.skip(f"{YYYYMM} 속보에 급여 데이터가 없다")
    table = get_aggregate_table(BASIS_YEAR, "cur", "usd", BASIS_MONTH, "forecast", None, db)
    row = next(r for r in table.rows if r.key == "sal")
    assert sum(materials["sal"]) == pytest.approx(row.values[16])


# ---------- 집계표가 내려주는 파생 행 ----------
def _add_derived(db, row_key: str, label: str, op: str, operands: list[str]):
    return upsert_extra_row(
        ExtraRowIn(
            lab_pk=ALL_SCOPE, version="forecast", yyyymm=YYYYMM, row_key=row_key, label=label,
            anchor_key="labor", is_derived=True, formula={"op": op, "operands": operands},
        ),
        db=db,
    )


def _derived(table, row_key: str):
    return next((r for r in table.derived_rows if r.row_key == row_key), None)


def test_table_returns_derived_rows_in_its_own_unit(db):
    """같은 파생 행이 달러 표와 원화 표에서 각자 단위로 온다 — 금액 행은 환율만큼 커지고,
    금액÷금액 비율 행은 같은 값이다. 적용 환율은 같은 응답의 고정 금액 행에서 읽는다."""
    _add_derived(db, "der_t_money", "급여+상여", "sum_elementwise", ["sal", "bonus"])
    _add_derived(db, "der_t_ratio", "상여 비중", "div_elementwise", ["bonus", "sal"])

    usd = get_aggregate_table(BASIS_YEAR, "cur", "usd", BASIS_MONTH, "forecast", None, db)
    krw = get_aggregate_table(BASIS_YEAR, "cur", "krw", BASIS_MONTH, "forecast", None, db)

    u_money, k_money = _derived(usd, "der_t_money"), _derived(krw, "der_t_money")
    u_ratio, k_ratio = _derived(usd, "der_t_ratio"), _derived(krw, "der_t_ratio")
    assert u_money and k_money and u_ratio and k_ratio, "파생 행이 응답에 없다"

    u_sal = next(r for r in usd.rows if r.key == "sal").values
    k_sal = next(r for r in krw.rows if r.key == "sal").values
    month = next((j for j in range(12) if u_sal[j] and u_money.v[j]), None)
    if month is None:
        pytest.skip(f"{YYYYMM} 속보에 급여가 들어온 달이 없다")

    rate = k_sal[month] / u_sal[month]
    assert k_money.v[month] == pytest.approx(u_money.v[month] * rate, rel=1e-6)
    assert k_ratio.v[month] == pytest.approx(u_ratio.v[month], rel=1e-9)


def test_derived_rows_are_only_the_derived_ones(db):
    """원시 행(취합에서 올라온 값 행)은 이 칸에 섞이지 않는다 — 화면이 두 번 그린다."""
    _add_derived(db, "der_t_only", "급여+상여", "sum_elementwise", ["sal", "bonus"])
    table = get_aggregate_table(BASIS_YEAR, "cur", "usd", BASIS_MONTH, "forecast", None, db)
    assert table.derived_rows, "파생 행이 비어서 왔다"
    assert all(r.is_derived for r in table.derived_rows)
    assert all(r.lab_pk == ALL_SCOPE for r in table.derived_rows)
