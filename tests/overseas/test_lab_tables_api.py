"""집계 ②③④ 탭 조회 API — PLAN.md Phase 6(수식 백엔드 단일화).

수식이 프런트와 같은지는 test_lab_tables_crosscheck.py가 6개 표를 전수 대조한다.
여기서는 **엔드포인트 계약**(표 구성·범위·단위·거부·재료 없을 때의 동작)을 고정한다.
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.aggregate import (
    _lab_groups,
    _table_inputs,
    get_lab_tables,
)
from app.accounts.overseas.services.labs import lab_views

BASIS_YEAR = 2026


def test_returns_all_six_tabs(db):
    """표 6장을 한 번에 준다 — 하나라도 빠지면 그 탭이 서버 값을 못 받아 로컬 계산으로 돌아간다."""
    out = get_lab_tables(basis_year=BASIS_YEAR, db=db)

    assert sorted(out.tables) == ["t2k", "t2u", "t3", "t4", "t5", "t6"]
    for key, t in out.tables.items():
        assert t.caption and t.note, f"{key}: 캡션·안내가 비었다"
        assert t.rows, f"{key}: 행이 없다"
        for r in t.rows:
            assert len(r.values) == 17, f"{key}/{r.label}: 17칸(월 12 + 분기 4 + 연간)이 아니다"


def test_every_active_lab_gets_a_row(db):
    """쓰는 연구소는 빠짐없이 한 줄씩 나온다 — 한 곳이 빠지면 합계와 줄 합이 안 맞는 표가 된다."""
    out = get_lab_tables(basis_year=BASIS_YEAR, db=db)
    inp = _table_inputs(db, BASIS_YEAR, "cur", with_rate=False)
    groups = _lab_groups(inp.labs, inp.pks)

    labels = [r.label for r in out.tables["t3"].rows]
    for g in groups:
        assert any(lab.startswith(g.name) for lab in labels), f"{g.code}: 줄이 없다"


def test_total_row_equals_the_sum_of_lab_rows(db):
    """맨 위 합계줄 = 연구소 줄 합. 집계표에서 사용자가 가장 먼저 검산하는 자리다."""
    t = get_lab_tables(basis_year=BASIS_YEAR, db=db).tables["t3"]
    total = next(r for r in t.rows if r.total)
    labs = [r for r in t.rows if not r.total and not r.bold and r.level == 1]

    for j in (0, 16):
        got = sum(r.values[j] for r in labs if r.values[j] is not None)
        if total.values[j] is None:
            assert got == 0
        else:
            assert got == pytest.approx(total.values[j], rel=1e-9), f"{j}칸: 합계 ≠ 연구소 합"


def test_parts_sum_to_their_lab(db):
    """하위 파트 줄(SARC의 CPU · GPU) 합 = 그 연구소 줄. 파트가 있는 연구소만 본다."""
    out = get_lab_tables(basis_year=BASIS_YEAR, db=db)
    inp = _table_inputs(db, BASIS_YEAR, "cur", with_rate=False)
    multi = [g for g in _lab_groups(inp.labs, inp.pks) if g.parts]
    if not multi:
        pytest.skip("파트가 있는 연구소가 없다")

    rows = {r.label: r for r in out.tables["t5"].rows}
    for g in multi:
        lab = rows.get(g.name)
        parts = [rows[lb] for _pk, lb in g.parts if lb in rows]
        assert lab is not None and parts, f"{g.code}: 연구소 줄 또는 파트 줄이 없다"
        for j in (0, 16):
            got = sum(p.values[j] for p in parts if p.values[j] is not None)
            if lab.values[j] is not None:
                assert got == pytest.approx(lab.values[j], rel=1e-9), f"{g.code}[{j}]: 파트 합 ≠ 연구소"


def test_labor_tabs_differ_only_by_cp_correction(db):
    """③(보정 없음)과 ④(CP 보정)는 CP 줄만 달라야 한다.

    둘이 완전히 같으면 "CP 보정" 탭의 존재 이유가 없고, CP 아닌 줄까지 다르면 보정이 엉뚱한
    연구소에 묻은 것이다.
    """
    out = get_lab_tables(basis_year=BASIS_YEAR, db=db)
    t4 = {r.label: r.values for r in out.tables["t4"].rows}
    t5 = {r.label: r.values for r in out.tables["t5"].rows}

    for label in set(t4) & set(t5):
        if label in ("CP", "적용 환율 (원/$)") or "CP분기보정" in label or label.startswith("인건비("):
            continue  # 합계줄·CP줄은 달라지는 게 정상이다
        assert t4[label] == t5[label], f"{label}: CP와 무관한 줄이 ③과 ④에서 다르다"


def test_rate_row_is_not_money(db):
    """적용 환율 줄은 금액이 아니라 환율이다 — 형식이 금액이면 화면이 억원으로 그려서
    1,427원이 사라진다(금액 포맷은 50만원 미만을 "–"로 숨긴다)."""
    t = get_lab_tables(basis_year=BASIS_YEAR, db=db).tables["t4"]
    fx = t.rows[0]

    assert fx.label == "적용 환율 (원/$)" and fx.format == "rate"
    assert fx.muted and fx.level == 0
    assert fx.values[0] and fx.values[0] > 100, "환율 값이 아니다"


def test_unknown_version_is_rejected(db):
    """모르는 버전은 400 — 오타로 빈 표가 내려가면 "자료가 없다"로 잘못 읽힌다."""
    with pytest.raises(HTTPException) as e:
        get_lab_tables(basis_year=BASIS_YEAR, version="nope", db=db)
    assert e.value.status_code == 400


def test_lab_groups_follow_the_screen_order(db):
    """연구소 묶기·정렬이 화면(/overseas/labs → ovLabsFromApi)과 같은 규칙이다.

    행 순서가 다르면 사용자가 보던 표와 줄 순서가 달라진다. 합계 행(TOTAL)이 섞이면 그 연구소가
    두 번 세어진다 — 그건 _table_inputs가 걸러야 한다.
    """
    inp = _table_inputs(db, BASIS_YEAR, "cur", with_rate=False)
    groups = _lab_groups(inp.labs, inp.pks)

    orders = [min(inp.labs[pk].sort_order for pk in g.pks) for g in groups]
    assert orders == sorted(orders), "sort_order 순서가 아니다"
    assert len({g.code for g in groups}) == len(groups), "같은 연구소가 두 줄로 나왔다"
    for g in groups:
        assert all((inp.labs[pk].part_code or "").upper() != "TOTAL" for pk in g.pks), (
            f"{g.code}: 합계 파트(TOTAL)가 섞여 이중 합산이 된다"
        )


def test_basis_month_and_kind_pick_the_version(db):
    """②③④도 ①과 같은 파라미터로 버전을 받는다(2026-11-17) — 한쪽만 버전을 따라가면 같은
    화면의 ① 탭과 ③ 탭이 서로 다른 버전을 그린다."""
    f = get_lab_tables(basis_year=BASIS_YEAR, basis_month=9, kind="forecast", db=db)
    a = get_lab_tables(basis_year=BASIS_YEAR, basis_month=8, kind="actual", db=db)

    assert (f.basis_month, f.kind, f.version_label) == (9, "forecast", "26.09 속보")
    assert (a.basis_month, a.kind, a.version_label) == (8, "actual", "26.08 실적")
    f_tot = next(r for r in f.tables["t3"].rows if r.total).values[16]
    a_tot = next(r for r in a.tables["t3"].rows if r.total).values[16]
    assert f_tot != a_tot, "속보와 실적이 같은 값이면 버전이 안 먹은 것이다"


def test_rejects_bad_month(db):
    """범위를 벗어난 기준월은 거부한다. 0은 거부가 아니라 "안 보냄"과 같다 — 증감분석 API와
    같은 관례이고(basis_month or 최신 속보), 둘을 다르게 두면 같은 화면의 두 호출이 다르게 떨어진다."""
    with pytest.raises(HTTPException) as e:
        get_lab_tables(basis_year=BASIS_YEAR, basis_month=13, kind="forecast", db=db)
    assert e.value.status_code == 400

    default = get_lab_tables(basis_year=BASIS_YEAR, db=db).basis_month
    assert get_lab_tables(basis_year=BASIS_YEAR, basis_month=0, kind="forecast", db=db).basis_month == default
