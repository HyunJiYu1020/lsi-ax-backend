"""집계 ① 전체 계정 표 조회 API.

같은 수식이 core.js와 백엔드 두 곳에 있으면 한 곳만 고쳤을 때 화면 숫자와 챗봇 숫자가
달라진다. 이 API는 백엔드가 계산해서 표를 그대로 내려주므로 프런트는 표시만 하면 된다.

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


# ---------- 어느 버전의 표인가 (2026-11-17) ----------
# 화면이 상단에서 "26.08 실적"을 골라도 집계표만 최신 속보 숫자를 그렸다 — 연도만 보내고
# 기준월·종류를 안 보내서, 서버가 기준월을 "DB에 있는 가장 최근 속보"로 혼자 정했기 때문이다.
# 이제 basis_month · kind로 받는다. 안 보내면 종전과 같이 최신 속보로 떨어진다(기존 호출 호환).
def test_basis_month_and_kind_pick_the_version(db):
    """기준월·종류를 주면 그 버전을 읽는다 — 속보와 실적은 다른 슬롯이라 값도 라벨도 달라야 한다."""
    f = get_aggregate_table(basis_year=BASIS_YEAR, basis_month=9, kind="forecast", mode="usd", db=db)
    a = get_aggregate_table(basis_year=BASIS_YEAR, basis_month=8, kind="actual", mode="usd", db=db)

    assert (f.basis_month, f.kind) == (9, "forecast")
    assert (a.basis_month, a.kind) == (8, "actual")
    assert f.version_label == "26.09 속보" and a.version_label == "26.08 실적"
    # 버전이 캡션에 적혀 있어야 받는 쪽(화면 · 엑셀)이 어느 버전 표인지 안다.
    assert a.version_label in a.caption
    f_tot = next(r for r in f.rows if r.key == "tot").values[16]
    a_tot = next(r for r in a.rows if r.key == "tot").values[16]
    assert f_tot != a_tot, "속보와 실적이 같은 값이면 버전이 안 먹은 것이다"


def test_default_is_still_the_latest_forecast(db):
    """기준월을 안 보내는 기존 호출은 종전과 같아야 한다(지금 편성 중인 속보)."""
    d = get_aggregate_table(basis_year=BASIS_YEAR, mode="usd", db=db)
    f = get_aggregate_table(
        basis_year=BASIS_YEAR, basis_month=d.basis_month, kind="forecast", mode="usd", db=db
    )

    assert d.kind == "forecast"
    assert [r.values for r in d.rows] == [r.values for r in f.rows]


def test_base_versions_resolve_like_the_variance_screen(db):
    """version=prev는 "전실행"이고, 그게 무엇인지는 증감분석과 같은 층(resolve_bases)이 푼다 —
    두 화면이 다르게 풀면 같은 자리에서 다른 숫자가 나온다."""
    # 기준월을 적어 준다 — 안 적으면 "DB의 최신 속보월"로 떨어져서, 속보가 하나 더 들어오는
    # 것만으로 이 테스트가 다른 버전을 비교하게 된다(26.10 속보가 생겼을 때 실제로 그랬다).
    prev = get_aggregate_table(
        basis_year=BASIS_YEAR, basis_month=9, kind="forecast", version="prev", mode="usd", db=db
    )
    same = get_aggregate_table(basis_year=BASIS_YEAR, basis_month=8, kind="actual", mode="usd", db=db)

    # 26.09 속보의 전실행 = 26.08 실적. kind는 "읽은 버전"의 종류라 actual이다.
    assert prev.kind == "actual" and prev.version_label == same.version_label
    assert [r.values for r in prev.rows] == [r.values for r in same.rows]


def test_rejects_bad_month_and_kind(db):
    """기준월·종류가 틀리면 조용히 최신 속보로 떨어지지 않고 거부한다 — 떨어지면 사용자는
    자기가 고른 버전의 표를 보고 있다고 믿는다."""
    with pytest.raises(HTTPException) as e:
        get_aggregate_table(basis_year=BASIS_YEAR, basis_month=13, db=db)
    assert e.value.status_code == 400

    with pytest.raises(HTTPException) as e:
        get_aggregate_table(basis_year=BASIS_YEAR, kind="sok", db=db)
    assert e.value.status_code == 400
