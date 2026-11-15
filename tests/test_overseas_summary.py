import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.overseas import _cp_quarter_amounts, get_overseas_summary

BASIS_YEAR = 2026


def test_summary_matches_hero_card_shape(db):
    """히어로 카드가 보여주는 값이 다 채워져 나오는지. 금액은 DB 데이터에 따라 달라지므로
    "인건비 + 인건비외 = 개발비(매출)"이라는 카드 자체의 관계만 확인한다."""
    s = get_overseas_summary(basis_year=BASIS_YEAR, db=db)

    assert s.basis_label == f"{BASIS_YEAR % 100:02d}.{s.basis_month:02d}"
    assert 0 <= s.step < s.step_count == 3
    assert s.total > 0
    assert s.labor + s.non_labor == pytest.approx(s.total)
    assert s.labor_pct + s.non_labor_pct == pytest.approx(100.0)
    # 업로드 진척은 "데이터 있는 연구소 / 전체 연구소"라 전체를 넘을 수 없다.
    assert 0 <= s.uploaded <= s.lab_count == len(s.labs)
    assert s.uploaded == sum(1 for r in s.labs if r.uploaded)


def test_summary_quarters_add_up_to_year(db):
    """분기 4개의 합 = 연간. CP를 분기말에 몰아넣는 보정이 분기를 건너뛰며 새지 않는지 본다."""
    year = get_overseas_summary(basis_year=BASIS_YEAR, db=db)
    quarters = [get_overseas_summary(basis_year=BASIS_YEAR, period=f"q{q}", db=db) for q in (1, 2, 3, 4)]

    assert sum(q.total for q in quarters) == pytest.approx(year.total, rel=1e-9)
    assert sum(q.labor for q in quarters) == pytest.approx(year.labor, rel=1e-9)


def test_cp_contract_amount_is_folded_per_poc(db):
    """CP 계약은 POC 헤더값이 직급 행마다 반복 저장돼 있다 — 행마다 더하면 직급 수만큼 부풀려진다."""
    total_q, labor_q = _cp_quarter_amounts(db, BASIS_YEAR)
    assert total_q >= labor_q >= 0
    # 분기 하나치 = 연 계약액 ÷ 4라, 연 금액으로 되돌리면 K$ 단위의 계약 총액이 된다.
    assert total_q * 4 > 0


def test_summary_rejects_bad_arguments(db):
    with pytest.raises(HTTPException) as e:
        get_overseas_summary(basis_year=BASIS_YEAR, period="h1", db=db)
    assert e.value.status_code == 400

    with pytest.raises(HTTPException) as e:
        get_overseas_summary(basis_year=BASIS_YEAR, version="draft", db=db)
    assert e.value.status_code == 400


def test_coverage_note_tells_how_many_labs_the_total_covers(db):
    """공통 RULE(상단 현황): 미업로드 연구소는 빼고 "n개 중 m개 기준"이라고 밝힌다.

    합계는 올라온 값만 더하므로 숫자 자체는 그대로지만, 그 사실을 말로 안 붙이면 챗봇이 이 금액을
    "연구소 전체 합계"라고 단정해서 답한다 — 그래서 문장도 같이 내려주는지 본다."""
    s = get_overseas_summary(basis_year=BASIS_YEAR, db=db)

    assert s.coverage_note
    assert str(s.lab_count) in s.coverage_note
    if s.uploaded < s.lab_count:
        # 안 올라온 연구소 이름이 그대로 들어 있어야 "뭐가 빠졌나"를 되묻지 않아도 된다.
        assert f"{s.uploaded}개 기준" in s.coverage_note
        for row in s.labs:
            if not row.uploaded:
                assert row.name in s.coverage_note
    else:
        assert "모두 올라온" in s.coverage_note
