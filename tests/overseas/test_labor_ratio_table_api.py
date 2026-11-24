"""GET /overseas/labor-ratio-table · GET /overseas/lab-monthly

둘 다 서버가 계산한다 — 브라우저가 localStorage 값으로 계산하면, 다른 담당자가 올려 저장한 뒤
새로고침하지 않은 화면에서 같은 화면의 집계표와 다른 숫자가 나온다.

여기서 지키는 것:
- 인건비율 팝업은 달러(K$) · CP 미보정 · 17칸 · 파트까지. 비율은 **기간 합계끼리** 나눈 값이다
- 마감 화면 금액은 원화 · CP 분기 보정 · 파트 단위. 줄 합이 합계와 같아야 한다
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.aggregate import get_lab_monthly, get_labor_ratio_table

BASIS_YEAR = 2026
BASIS_MONTH = 9
YEAR_IX = 16  # 17칸 중 연간


@pytest.fixture
def ratio(db):
    return get_labor_ratio_table(BASIS_YEAR, "cur", BASIS_MONTH, "forecast", None, db)


@pytest.fixture
def monthly(db):
    return get_lab_monthly(BASIS_YEAR, "cur", BASIS_MONTH, "forecast", None, db)


# ---------- 인건비율 팝업 ----------


def test_blocks_go_all_then_labs_then_parts(ratio):
    """화면 줄 순서 그대로 온다 — 전체(0) → 연구소(1) → 그 연구소의 파트(2)."""
    assert ratio.blocks[0].key == "ALL" and ratio.blocks[0].level == 0
    assert ratio.unit == "K$"
    levels = [b.level for b in ratio.blocks[1:]]
    assert levels and set(levels) <= {1, 2}
    # 파트 줄은 바로 앞 연구소 줄에 딸린다(앞이 level 2거나 1이어야 한다)
    for i, b in enumerate(ratio.blocks):
        if b.level == 2:
            assert ratio.blocks[i - 1].level in (1, 2)


def test_every_block_has_17_cells(ratio):
    for b in ratio.blocks:
        assert len(b.labor) == len(b.dev) == len(b.ratio) == 17, b.key


def test_ratio_divides_period_totals(ratio):
    """분기·연간 비율 = 기간 합계끼리 나눈 값. 월별 비율의 평균으로 내면 금액이 작은 달이 왜곡한다."""
    for b in ratio.blocks:
        for i in (12, 13, 14, 15, 16):  # 1Q~4Q · 연간
            if b.dev[i]:
                assert b.ratio[i] == pytest.approx(b.labor[i] / b.dev[i] * 100)


def test_ratio_is_null_when_there_is_no_denominator(ratio):
    """분모(개발비)가 0이거나 미등록이면 null이다 — 0%로 말하면 안 된다."""
    for b in ratio.blocks:
        for i in range(17):
            if not b.dev[i]:
                assert b.ratio[i] is None, (b.key, i)


def test_parts_add_up_to_their_lab(ratio):
    """SARC의 CPU + GPU = SARC. 파트 줄이 연구소 줄과 안 맞으면 펼친 숫자가 거짓이 된다."""
    by_key = {b.key: b for b in ratio.blocks}
    parts = [b for b in ratio.blocks if b.level == 2]
    assert parts, "파트 줄이 없다(SARC가 CPU · GPU로 갈려 있어야 한다)"
    by_lab: dict[str, list] = {}
    for b in parts:
        by_lab.setdefault(b.key.split(":")[0], []).append(b)
    for code, group in by_lab.items():
        lab = by_key[code]
        assert lab.labor[YEAR_IX] == pytest.approx(sum(p.labor[YEAR_IX] or 0 for p in group))
        assert lab.dev[YEAR_IX] == pytest.approx(sum(p.dev[YEAR_IX] or 0 for p in group))


def test_needs_no_applied_rate(db):
    """달러 표라 환율을 안 쓴다 — 적용 환율이 없는 달이 있어도 비율은 나와야 한다."""
    t = get_labor_ratio_table(BASIS_YEAR, "cur", BASIS_MONTH, "forecast", None, db)
    assert any(b.ratio[YEAR_IX] is not None for b in t.blocks)


# ---------- 마감 화면 금액 ----------


def test_rows_are_parts_not_labs(monthly):
    """매핑 단위가 파트다 — SARC는 CPU · GPU로 갈려 온다(pk에 ':'가 있다)."""
    assert monthly.unit == "원"
    assert any(":" in r.pk for r in monthly.rows), [r.pk for r in monthly.rows]
    assert len({r.pk for r in monthly.rows}) == len(monthly.rows)


def test_total_is_the_sum_of_rows(monthly):
    for j in range(12):
        s = [r.months[j] for r in monthly.rows if r.months[j] is not None]
        if not s:
            assert monthly.total[j] is None, j
        else:
            assert monthly.total[j] == pytest.approx(sum(s)), j


def test_unuploaded_labs_stay_blank(monthly):
    """안 올라온 연구소는 0원이 아니라 빈칸이다(공통 RULE)."""
    blanks = [r for r in monthly.rows if all(m is None for m in r.months)]
    assert blanks, "미업로드 연구소가 하나도 없으면 이 테스트가 지키는 게 없다"


def test_cp_is_quarter_end_only(monthly):
    """CP는 분기 정산 계약이라 3 · 6 · 9 · 12월에만 금액이 선다(올라온 PL을 쓰지 않는다)."""
    cp = next((r for r in monthly.rows if r.lab_code == "CP"), None)
    if cp is None or all(m is None for m in cp.months):
        pytest.skip("CP 계약이 등록되지 않은 기준이다")
    for j in range(12):
        if j not in (2, 5, 8, 11):
            assert not cp.months[j], (j, cp.months[j])


def test_rejects_bad_month(db):
    """1~12 밖은 막는다. 0은 "안 줬다"와 같아서 최신 속보 월로 떨어진다(기존 호출 호환)."""
    with pytest.raises(HTTPException) as e:
        get_lab_monthly(BASIS_YEAR, "cur", 13, "forecast", None, db)
    assert e.value.status_code == 400
    assert get_lab_monthly(BASIS_YEAR, "cur", 0, "forecast", None, db).basis_month in range(1, 13)
