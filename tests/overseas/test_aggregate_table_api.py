"""집계 ① 전체 계정 표 조회 API — PLAN.md Phase 6(수식 백엔드 단일화).

같은 수식이 core.js · calc.js · 백엔드 세 곳에 복제돼 있어서 한 곳만 고치면 화면 숫자와 챗봇
숫자가 달라졌다. 이 API는 백엔드가 계산해서 표를 그대로 내려주므로 프런트는 표시만 하면 된다.

수식 자체가 프런트와 같은지는 test_formula_crosscheck.py가 45행 × 17칸을 전수 대조한다.
여기서는 **엔드포인트 계약**(행 구성·단위·범위·거부)을 고정한다.
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.aggregate import get_aggregate_table
from app.accounts.overseas.services.labs import lab_views

BASIS_YEAR = 2026


def test_returns_the_full_pl_table(db):
    """45행이 손익계산서 순서로 내려온다 — 한 행만 빠져도 화면이 다른 표를 그린다."""
    t = get_aggregate_table(basis_year=BASIS_YEAR, db=db)

    assert len(t.rows) == 45
    keys = [r.key for r in t.rows]
    assert keys[:5] == ["hc", "tot", "A", "hr", "labor"], "손익계산서 순서가 아니다"
    assert keys[-1] == "outs" and "pct" in keys
    for r in t.rows:
        assert len(r.values) == 17, f"{r.key}: 17칸(월 12 + 분기 4 + 연간)이 아니다"
        assert r.label, f"{r.key}: 라벨이 비었다"


def test_units_are_per_row_not_per_table(db):
    """같은 표 안에서 행마다 단위가 다르다 — 인력은 명, 비율은 %, 나머지는 금액.
    표 단위 하나로 뭉치면 인원에 환율을 곱하는 사고가 난다."""
    t = get_aggregate_table(basis_year=BASIS_YEAR, mode="krw", db=db)
    fmt = {r.key: r.format for r in t.rows}

    assert fmt["hc"] == "hc"
    assert fmt["pct"] == "pct"
    assert fmt["tot"] == "krw" and fmt["labor"] == "krw"


def test_usd_mode_skips_the_exchange_rate(db):
    """usd는 저장 단위(K$) 그대로다 — 환율을 안 거치므로 환율이 안 들어간 달도 값이 보인다."""
    usd = get_aggregate_table(basis_year=BASIS_YEAR, mode="usd", db=db)
    krw = get_aggregate_table(basis_year=BASIS_YEAR, mode="krw", db=db)

    assert {r.format for r in usd.rows if r.key == "tot"} == {"usd"}
    u = next(r for r in usd.rows if r.key == "tot").values[16]
    k = next(r for r in krw.rows if r.key == "tot").values[16]
    if u and k:
        assert k > u, "원화가 K$보다 작다 — 환산이 안 됐다"


def test_memo_rows_are_marked(db):
    """참고 행(IT 관련 비용·외주용역비)은 memo=True다 — 화면이 합계와 섞어 그리지 않게."""
    t = get_aggregate_table(basis_year=BASIS_YEAR, db=db)
    memo = {r.key for r in t.rows if r.memo}

    assert memo == {"itx", "eda", "emu", "swl", "svr", "itoth", "outs"}


def test_total_rows_and_etc_are_excluded_from_the_sum(db):
    """SARC의 'TOTAL' 파트와 원화 기타(_ETC)는 합계에서 빠진다 —
    넣으면 SARC가 CPU+GPU+TOTAL로 두 번 세어져 금액이 두 배가 된다."""
    t = get_aggregate_table(basis_year=BASIS_YEAR, mode="usd", db=db)
    labs = lab_views(db)
    n_counted = len([
        l for l in labs
        if l.is_active and l.pk != "_ETC" and (l.part_code or "").upper() != "TOTAL"
    ])

    assert f"연구소 {n_counted}곳" in t.caption
    assert "_ETC" not in t.caption


def test_caption_and_note_explain_the_table(db):
    """캡션·안내를 서버가 만든다 — 화면과 챗봇이 같은 설명을 쓰게 하려는 것이다
    (/overseas/summary의 coverage_note와 같은 방식)."""
    t = get_aggregate_table(basis_year=BASIS_YEAR, db=db)

    assert t.caption.startswith("①")
    assert "빈칸" in t.note and "0" in t.note, "빈칸과 0의 차이를 설명해야 한다"
    assert "개발비(매출)" in t.note, "MARK UP 비율의 분모를 밝혀야 한다"


@pytest.mark.parametrize("bad", [{"mode": "eur"}, {"version": "draft"}])
def test_bad_arguments_are_rejected(db, bad):
    with pytest.raises(HTTPException) as e:
        get_aggregate_table(basis_year=BASIS_YEAR, db=db, **bad)
    assert e.value.status_code == 400
