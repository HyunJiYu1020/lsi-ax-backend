"""해외연구소 증감사유 · 외주용역 예시 데이터 생성기(app/db/seed_overseas_sheets.py) 테스트.

DB 없이 순수 함수만 본다 — 외주용역은 "상세 합계 = 비용 시트 외주용역비"(화면의 대사)가 깨지면
안 되고, 난수는 같은 시드에서 항상 같은 값이 나와야 한다(재적재해도 화면이 안 흔들리도록)."""

from app.db.seed_overseas_sheets import JsRandom, _k_usd, _reason_rows, _vendor_rows

OUTS = [120.4, 98.7, 110.0, 0.0, 87.3, 95.1, 103.9, 88.8, 91.2, 99.5, 101.1, 94.6]


def _monthly_totals(rows: list[dict]) -> list[float]:
    return [round(sum(r["m"][j] for r in rows), 2) for j in range(12)]


def test_vendor_rows_reconcile_with_cost_sheet():
    rows = _vendor_rows("DSRA", "202609", OUTS)
    assert 30 <= len(rows) <= 40
    assert _monthly_totals(rows) == [round(v, 2) for v in OUTS]


def test_vendor_rows_keep_sscr_mismatch_example():
    """SSCR 3월은 화면에 "불일치"로 보여주려고 일부러 5K 틀리게 둔 예시다."""
    rows = _vendor_rows("SSCR", "202609", OUTS)
    totals = _monthly_totals(rows)
    assert totals[2] == round(OUTS[2] + 5, 2)
    assert [totals[j] for j in range(12) if j != 2] == [round(OUTS[j], 2) for j in range(12) if j != 2]


def test_vendor_rows_are_deterministic():
    assert _vendor_rows("CP", "202609", OUTS) == _vendor_rows("CP", "202609", OUTS)


def test_js_random_matches_browser_sequence():
    """화면 core.js의 rnd("ovouts|SARC")가 내는 첫 값들과 같아야 한다 (FNV-1a + xorshift32)."""
    rand = JsRandom("ovouts|SARC")
    assert [rand() for _ in range(3)] == [0.1943, 0.0549, 0.4996]


def test_k_usd_formats_like_js():
    assert _k_usd(1234.6) == "$1,235K"  # 10 이상은 정수 + 천단위 쉼표
    assert _k_usd(3.0) == "$3K"  # 10 미만은 소수 1자리, 정수면 소수점 없이
    assert _k_usd(3.46) == "$3.5K"


def test_reason_rows_cover_all_categories_and_blocks():
    el = {
        "cur": {"sal": [100.0] * 12, "hc": [10.0] * 12, "outs": OUTS},
        "prev": {"sal": [90.0] * 12, "hc": [9.0] * 12},
        "plan": {"sal": [80.0] * 12, "hc": [8.0] * 12},
    }
    rows = _reason_rows("DSRA", "202609", "202608", el)
    assert len(rows) == 88  # 44개 항목 × (연간 y · 당월 m)
    assert {r["block"] for r in rows} == {"y", "m"}
    assert {r["prev_label"] for r in rows} == {"M2608A"}
    assert {r["cur_label"] for r in rows} == {"M2609A"}
    assert {r["mon_label"] for r in rows if r["block"] == "m"} == {"SEP"}

    annual_sal = next(r for r in rows if r["category_key"] == "sal" and r["block"] == "y")
    assert annual_sal["cur_value"] == 1200  # 연간 블록은 12개월 합
    assert annual_sal["diff_prev"] == 120
    annual_hc = next(r for r in rows if r["category_key"] == "hc" and r["block"] == "y")
    assert annual_hc["cur_value"] == 10  # 인력은 합이 아니라 12개월 평균
    assert "more engineers were hired than planned" in annual_hc["remark_plan"]


def test_vendor_rows_carry_master_detail_shape():
    """적재 payload는 마스터 컬럼 + 월 금액 배열(m)로 나오고, 디테일은 이 m을 월 1행씩 펼쳐 넣는다."""
    rows = _vendor_rows("DSRA", "202609", OUTS)
    first = rows[0]
    assert first["lab_code"] == "DSRA"
    assert first["version"] == "cur"
    assert first["row_no"] == 1
    assert first["source_file"] == "OV_DSRA_2609.xlsx"
    assert len(first["m"]) == 12
    assert [r["row_no"] for r in rows] == list(range(1, len(rows) + 1))
    assert set(first) == {"lab_code", "version", "row_no", "category", "project", "company", "description", "source_file", "m"}
