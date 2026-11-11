from sqlalchemy import select

from app.api.v1.endpoints.fx import FxAppliedRow, _compute_applied_rows, _FxRateCache, _write_applied_rows
from app.models import FxAppliedRate, FxRate

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

    cache = _FxRateCache(db, TEST_YEAR)
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

    cache = _FxRateCache(db, TEST_YEAR)
    assert cache.forecast_of(f"{TEST_YEAR}-02") == f"{TEST_YEAR}-01"  # 2월엔 속보 없어서 1월 걸 그대로 씀
    assert cache.forecast_of(f"{TEST_YEAR}-03") == f"{TEST_YEAR}-03"
    assert cache.forecast_of(f"{TEST_YEAR - 1}-12") is None  # 그보다 이전엔 아무 속보도 없음


def test_compute_applied_rows_uses_forecast_for_future_months(db):
    db.add(FxRate(year_month=f"{TEST_YEAR}-06", currency_code="USD", kind="forecast", rate=1300.0))
    db.flush()

    cache = _FxRateCache(db, TEST_YEAR)
    rows = _compute_applied_rows(cache, TEST_YEAR, 6, "forecast", "avg")
    row = next(r for r in rows if r.currency_code == "USD" and r.target_month == 6)
    assert row.rate == 1300.0
    assert row.source == "forecast"


def test_write_applied_rows_replaces_existing(db):
    _write_applied_rows(
        db, TEST_YEAR, 1, "forecast", "avg", [FxAppliedRow(target_month=1, currency_code="USD", rate=100.0, source="forecast")]
    )
    db.flush()
    saved = db.scalars(select(FxAppliedRate).where(FxAppliedRate.basis_year == TEST_YEAR)).all()
    assert len(saved) == 1
    assert float(saved[0].rate) == 100.0

    _write_applied_rows(
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
    _write_applied_rows(db, TEST_YEAR, 1, "forecast", "avg", [])
    db.flush()
    saved = db.scalars(select(FxAppliedRate).where(FxAppliedRate.basis_year == TEST_YEAR)).all()
    assert saved == []
