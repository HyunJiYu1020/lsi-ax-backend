"""집계 ① 표의 **파생 열**(화면에서 [+ 열 추가]로 만든 계산 열) 값을 서버가 계산한다.

열 자체(이름·계산식)는 DB에 있고(extra_cols · formula), 칸 값은 저장하지 않고 볼 때마다 서버가
계산한다.

여기서 지키는 규칙 — 프런트가 쓰던 것과 같다:
  - 비율(%)·환율 행은 기간으로 묶는 뜻이 없어 **빈칸**이다(합치거나 평균 내면 거짓이 된다)
  - 인력(명) 행은 평균, 나머지는 합. op=avg_months면 전부 평균
  - 값은 순서가 아니라 **행 키**로 찾는다(화면이 45행 사이에 추가 행을 끼워 넣기 때문)
"""

import pytest

from app.accounts.overseas.endpoints.aggregate import get_aggregate_table
from app.accounts.overseas.endpoints.collect import upsert_extra_col
from app.accounts.overseas.schemas import ExtraColIn

BASIS_YEAR = 2026
BASIS_MONTH = 9
Q3 = [7, 8, 9]


def _make_col(db, label: str, op: str, months: list[int]) -> None:
    upsert_extra_col(
        ExtraColIn(
            lab_pk="ALL", version="forecast", yyyymm=BASIS_YEAR * 100 + BASIS_MONTH,
            label=label, is_derived=True, formula={"op": op, "months": months},
        ),
        db=db,
    )


def _table(db, mode: str = "usd"):
    return get_aggregate_table(BASIS_YEAR, "cur", mode, BASIS_MONTH, "forecast", None, db)


def test_no_columns_means_empty(db):
    """파생 열이 없으면 빈 목록이다 — 없는 열을 0으로 채워 보내지 않는다."""
    assert _table(db).derived_cols == []


def test_sum_months_adds_the_months_of_that_row(db):
    """합계 열 = 그 행의 해당 월 값 합. 같은 응답의 월 칸과 맞아야 한다(다른 표에서 꺼내면 어긋난다)."""
    _make_col(db, "3분기 합계", "sum_months", Q3)
    t = _table(db)
    assert len(t.derived_cols) == 1
    col = t.derived_cols[0]
    assert (col.label, col.op, col.months) == ("3분기 합계", "sum_months", Q3)

    checked = 0
    for row in t.rows:
        if not row.key or row.format not in ("usd", "krw"):
            continue
        want = sum(row.values[j] or 0 for j in (6, 7, 8))
        assert col.values["k:" + row.key] == pytest.approx(want), row.key
        checked += 1
    assert checked > 20, f"검사한 금액 행이 {checked}개뿐이다"


def test_avg_months_averages(db):
    _make_col(db, "3분기 평균", "avg_months", Q3)
    col = next(c for c in _table(db).derived_cols if c.op == "avg_months")
    row = next(r for r in _table(db).rows if r.key == "sal")
    want = sum(row.values[j] or 0 for j in (6, 7, 8)) / 3
    assert col.values["k:sal"] == pytest.approx(want)


def test_ratio_and_rate_rows_are_blank(db):
    """MARK UP 비율(%) 같은 행은 3분기를 "합"으로 묶을 수 없다 — 0이 아니라 빈칸이어야 한다."""
    _make_col(db, "3분기 합계", "sum_months", Q3)
    t = _table(db)
    col = t.derived_cols[0]
    pct_rows = [r for r in t.rows if r.format in ("pct", "rate") and r.key]
    assert pct_rows, "비율 행이 없으면 이 테스트가 지키는 게 없다"
    for r in pct_rows:
        assert col.values["k:" + r.key] is None, r.key


def test_headcount_rows_are_averaged_not_summed(db):
    """인력(명)은 더하면 안 된다 — 3개월을 더하면 인원이 3배가 된다."""
    _make_col(db, "3분기 합계", "sum_months", Q3)
    t = _table(db)
    col = t.derived_cols[0]
    hc = next((r for r in t.rows if r.format == "hc" and r.key), None)
    if hc is None:
        pytest.skip("인력 행이 없는 기준이다")
    months = [hc.values[j] or 0 for j in (6, 7, 8)]
    assert col.values["k:" + hc.key] == pytest.approx(sum(months) / 3)


def test_covers_added_rows_too(db):
    """취합에서 연구소가 추가한 행도 열에 값이 있어야 한다 — 화면이 그 줄을 45행 사이에 끼워
    넣으므로, 서버가 그 줄을 빼면 그 칸만 빈칸으로 남는다."""
    _make_col(db, "3분기 합계", "sum_months", Q3)
    keys = _table(db).derived_cols[0].values
    kinds = {k.split(":")[0] for k in keys}
    assert "k" in kinds
    assert kinds <= {"k", "l", "d"}, kinds
    # 이 기준에 추가 행이 있으면 l: 또는 d: 키가 같이 와야 한다
    assert len(keys) >= len([r for r in _table(db).rows if r.key])
