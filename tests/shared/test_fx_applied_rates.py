from sqlalchemy import select

import pytest
from fastapi import HTTPException

from app.shared.schemas.fx import FxAppliedRow
from app.shared.services.fx_rates import (
    APPLIED_MISSING_PREFIX,
    FxRateCache,
    applied_rows,
    compute_applied_rows,
    materialize_applied_rates,
    write_applied_rows,
)
from app.shared.models.fx import FxAppliedRate, FxRate

# 실제 데이터와 안 겹치는 미래의 가짜 연도를 써서 테스트한다.
TEST_YEAR = 2099


def test_fx_rate_cache_scopes_to_basis_year_and_relevant_kinds(db):
    db.add_all(
        [
            FxRate(year_month=f"{TEST_YEAR - 2}-01", currency_code="USD", kind="forecast", rate=1.0),  # 범위 밖(2년 전)
            FxRate(year_month=f"{TEST_YEAR}-01", currency_code="USD", kind="forecast", rate=2.0),
            FxRate(year_month=f"{TEST_YEAR}-01", currency_code="USD", kind="plan", rate=3.0),  # 이 계산에 안 쓰이는 kind
        ]
    )
    db.flush()

    cache = FxRateCache(db, TEST_YEAR)
    assert cache.rate_of(f"{TEST_YEAR}-01", "USD", "forecast") == 2.0
    assert cache.rate_of(f"{TEST_YEAR - 2}-01", "USD", "forecast") == 0.0  # 연도 범위 밖이라 안 보임
    assert cache.rate_of(f"{TEST_YEAR}-01", "USD", "plan") == 0.0  # plan은 애초에 로드하지 않음


def test_forecast_of_picks_latest_le_target(db):
    db.add_all(
        [
            FxRate(year_month=f"{TEST_YEAR}-01", currency_code="USD", kind="forecast", rate=10.0),
            FxRate(year_month=f"{TEST_YEAR}-03", currency_code="USD", kind="forecast", rate=20.0),
        ]
    )
    db.flush()

    cache = FxRateCache(db, TEST_YEAR)
    assert cache.forecast_of(f"{TEST_YEAR}-02") == f"{TEST_YEAR}-01"  # 2월엔 속보 없어서 1월 걸 그대로 씀
    assert cache.forecast_of(f"{TEST_YEAR}-03") == f"{TEST_YEAR}-03"
    assert cache.forecast_of(f"{TEST_YEAR - 1}-12") is None  # 그보다 이전엔 아무 속보도 없음


def test_compute_applied_rows_uses_forecast_for_future_months(db):
    db.add(FxRate(year_month=f"{TEST_YEAR}-06", currency_code="USD", kind="forecast", rate=1300.0))
    db.flush()

    cache = FxRateCache(db, TEST_YEAR)
    rows = compute_applied_rows(cache, TEST_YEAR, 6, "forecast", "avg")
    row = next(r for r in rows if r.currency_code == "USD" and r.target_month == 6)
    assert row.rate == 1300.0
    assert row.source == "forecast"


def test_compute_applied_rows_leaves_missing_month_as_none(db):
    """원본에 없는 달은 0이 아니라 None이다 — 0으로 내려보내면 받는 쪽이 '환율 0원'으로 읽어
    그 달 금액을 0으로 환산해 버린다(실제로 금액이 전부 0이 된 적이 있다)."""
    db.add(FxRate(year_month=f"{TEST_YEAR}-06", currency_code="USD", kind="forecast", rate=1300.0))
    db.flush()

    rows = compute_applied_rows(FxRateCache(db, TEST_YEAR), TEST_YEAR, 6, "forecast", "avg")
    by_month = {r.target_month: r.rate for r in rows if r.currency_code == "USD"}
    assert by_month[6] == 1300.0  # 속보가 올라온 달
    assert by_month[1] is None  # 6월 이전 = 실적 구간인데 실적 환율이 없다
    assert all(v is None for m, v in by_month.items() if m < 6)


def test_write_applied_rows_keeps_none_as_null(db):
    write_applied_rows(
        db, TEST_YEAR, 1, "forecast", "avg",
        [
            FxAppliedRow(target_month=1, currency_code="USD", rate=None, source="actual"),
            FxAppliedRow(target_month=2, currency_code="USD", rate=1300.0, source="forecast"),
        ],
    )
    db.flush()
    saved = {s.target_month: s.rate for s in db.scalars(
        select(FxAppliedRate).where(FxAppliedRate.basis_year == TEST_YEAR)
    )}
    assert saved[1] is None  # 0으로 눌러쓰지 않는다
    assert float(saved[2]) == 1300.0


def test_write_applied_rows_replaces_existing(db):
    write_applied_rows(
        db, TEST_YEAR, 1, "forecast", "avg", [FxAppliedRow(target_month=1, currency_code="USD", rate=100.0, source="forecast")]
    )
    db.flush()
    saved = db.scalars(select(FxAppliedRate).where(FxAppliedRate.basis_year == TEST_YEAR)).all()
    assert len(saved) == 1
    assert float(saved[0].rate) == 100.0

    write_applied_rows(
        db,
        TEST_YEAR,
        1,
        "forecast",
        "avg",
        [
            FxAppliedRow(target_month=1, currency_code="USD", rate=200.0, source="forecast"),
            FxAppliedRow(target_month=2, currency_code="USD", rate=300.0, source="forecast"),
        ],
    )
    db.flush()
    saved2 = db.scalars(select(FxAppliedRate).where(FxAppliedRate.basis_year == TEST_YEAR)).all()
    assert {(s.target_month, float(s.rate)) for s in saved2} == {(1, 200.0), (2, 300.0)}


def test_write_applied_rows_empty_is_noop(db):
    write_applied_rows(db, TEST_YEAR, 1, "forecast", "avg", [])
    db.flush()
    saved = db.scalars(select(FxAppliedRate).where(FxAppliedRate.basis_year == TEST_YEAR)).all()
    assert saved == []


def test_applied_rows_missing_combo_404_upload_guide(db):
    """원본도 표도 없는 연도 — 업로드 안내로 404."""
    with pytest.raises(HTTPException) as e:
        applied_rows(db, TEST_YEAR, 1, "forecast", "avg")
    assert e.value.status_code == 404
    assert e.value.detail.startswith(APPLIED_MISSING_PREFIX)
    assert "업로드" in e.value.detail


def test_applied_rows_missing_combo_404_save_guide(db):
    """원본은 있는데 표만 없는 경우(덤프 복원 등) — 업로드가 아니라 '저장 실행' 안내로 404.
    업로드하라고 하면 이미 올린 파일을 또 올리게 된다."""
    db.add(FxRate(year_month=f"{TEST_YEAR}-06", currency_code="USD", kind="forecast", rate=1300.0))
    db.flush()
    with pytest.raises(HTTPException) as e:
        applied_rows(db, TEST_YEAR, 6, "forecast", "avg")
    assert e.value.status_code == 404
    assert e.value.detail.startswith(APPLIED_MISSING_PREFIX)
    assert "저장" in e.value.detail and "업로드해 주세요" not in e.value.detail


def test_materialize_builds_all_48_combos_then_applied_rows_reads(db):
    db.add(FxRate(year_month=f"{TEST_YEAR}-06", currency_code="USD", kind="forecast", rate=1300.0))
    db.flush()
    materialize_applied_rates(db)
    db.flush()

    rows = applied_rows(db, TEST_YEAR, 6, "forecast", "avg")  # 더는 404가 아니다
    by_month = {r.target_month: r.rate for r in rows if r.currency_code == "USD"}
    assert by_month[6] == 1300.0
    assert by_month[1] is None  # 실적 구간인데 실적 환율이 없다 — 0이 아니라 None

    # 기준월 12 × 속보/실적 2 × 룰 2 = 48조합이 전부 생겼는지
    combos = db.execute(
        select(FxAppliedRate.basis_month, FxAppliedRate.basis_kind, FxAppliedRate.rule)
        .where(FxAppliedRate.basis_year == TEST_YEAR)
        .distinct()
    ).all()
    assert len(combos) == 48
