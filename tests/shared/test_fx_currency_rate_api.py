"""새 통화의 환율을 확인하고 한 칸 채워 넣는 경로 — 연구소 통화와 환율표를 잇는 다리.

왜 이 테스트가 필요한가: 환율이 없는 통화로 연구소를 잡아도 아무것도 막히지 않고, 집계도 멈추지
않는다. fxcalc.rate_arrays가 분모 없는 현지통화를 0으로 두고, 쓰는 쪽(services/variance.py)이
`lrc > 0`일 때만 환차를 분해하기 때문에 **조용히** 환산이 빠진다. 404로 멈추는 건 원/$가 없을
때뿐이다. 그래서 "경고가 실제로 나오는지"와 "넣은 값이 적용 환율까지 흘러가는지"를 못 박아 둔다.
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.collect import upsert_lab
from app.accounts.overseas.schemas import LabIn
from app.accounts.overseas.services.fxcalc import lab_currency_warning
from app.shared.endpoints.fx import get_fx_currency_coverage, save_fx_rate
from app.shared.models.fx import FxRate
from app.shared.schemas.fx import FxRateIn
from app.shared.services.fx_rates import applied_rows, currency_coverage, rate_unit

# 실제 데이터와 안 겹치는 미래의 가짜 연도·가짜 통화를 쓴다(XTS는 ISO가 테스트용으로 비워 둔 코드).
TEST_YEAR = 2099
TEST_CUR = "XTS"


def _seed_usd(db) -> None:
    """현지통화 적용 환율은 그 달 USD 값과 결합해서 나온다 — USD가 없으면 뭘 넣어도 결과가 비어서
    테스트가 "안 들어갔다"와 "USD가 없다"를 구분하지 못한다."""
    db.add(FxRate(year_month=f"{TEST_YEAR}-06", currency_code="USD", kind="forecast", rate=1300.0))
    db.flush()


def test_rate_unit_tells_the_direction_per_currency():
    # 이 단위를 틀리면 값이 그대로 저장되고 오류도 안 난다 — 금액만 몇 배 틀어진다.
    assert rate_unit("USD") == "원/$"
    assert rate_unit("EUR") == "$/EUR"  # 역방향 통화
    assert rate_unit("INR") == "INR/$"  # 원/루피가 아니다


def test_coverage_says_unregistered_and_shows_the_unit(db):
    cov = currency_coverage(db, TEST_CUR, TEST_YEAR)
    assert cov.registered is False
    assert cov.forecast_months == []
    assert f"{TEST_CUR}/$" in cov.note  # 입력을 요청할 때 그대로 읽을 수 있어야 한다


def test_save_fx_rate_fills_coverage_and_reaches_applied_table(db):
    _seed_usd(db)

    cov = save_fx_rate(
        FxRateIn(year_month=f"{TEST_YEAR}-06", currency_code=TEST_CUR.lower(), rate=95.3), db
    )

    assert cov.registered is True
    assert cov.forecast_months == [6]

    # 적용 환율 표까지 다시 만들어졌는지 — 여기까지 와야 집계가 그 통화를 환산한다.
    rows = applied_rows(db, TEST_YEAR, 6, "forecast", "avg")
    by_month = {r.target_month: r.rate for r in rows if r.currency_code == TEST_CUR}
    assert by_month[6] == pytest.approx(round(1300.0 / 95.3, 2))
    # 속보는 "그 달 이하의 가장 최근 값"을 이후 달에도 쓴다 — 한 달만 넣어도 연말까지 채워진다.
    assert by_month[12] == by_month[6]
    assert by_month[1] is None  # 실적 구간인데 실적이 없다 — 0이 아니라 None


@pytest.mark.parametrize(
    "body",
    [
        FxRateIn(year_month=f"{TEST_YEAR}-06", currency_code=TEST_CUR, rate=1.0, kind="월평균"),
        FxRateIn(year_month=f"{TEST_YEAR}-06", currency_code="WONS", rate=1.0),
        FxRateIn(year_month=f"{TEST_YEAR}-13", currency_code=TEST_CUR, rate=1.0),
        # 0은 "환율이 없다"와 "환율이 0이다"를 섞어버린다 — 적용 환율이 NULL과 0을 가르는 이유다.
        FxRateIn(year_month=f"{TEST_YEAR}-06", currency_code=TEST_CUR, rate=0),
    ],
)
def test_save_fx_rate_rejects_bad_input(db, body):
    with pytest.raises(HTTPException) as e:
        save_fx_rate(body, db)
    assert e.value.status_code == 400


def test_coverage_endpoint_uppercases_the_currency(db):
    cov = get_fx_currency_coverage(currency=f" {TEST_CUR.lower()} ", year=TEST_YEAR, db=db)
    assert cov.currency_code == TEST_CUR


def test_lab_currency_warning_appears_then_clears(db):
    effective_from = f"{TEST_YEAR}-06"
    warning = lab_currency_warning(db, TEST_CUR, effective_from)
    assert warning is not None
    assert TEST_CUR in warning and f"{TEST_CUR}/$" in warning  # 통화와 입력 단위를 같이 알려준다

    _seed_usd(db)
    save_fx_rate(FxRateIn(year_month=effective_from, currency_code=TEST_CUR, rate=95.3), db)
    assert lab_currency_warning(db, TEST_CUR, effective_from) is None


def test_lab_currency_warning_when_forecast_starts_after_the_change(db):
    """환율이 있긴 한데 적용 시점 이후 것뿐이면, 그 시점부터 연말까지가 환산되지 않는다 —
    적용 환율은 '그 달 이하의 가장 최근 속보'를 쓰기 때문이다."""
    db.add(FxRate(year_month=f"{TEST_YEAR}-09", currency_code=TEST_CUR, kind="forecast", rate=95.3))
    db.flush()

    warning = lab_currency_warning(db, TEST_CUR, f"{TEST_YEAR}-06")
    assert warning is not None
    assert "6~12월" in warning


def test_upsert_lab_returns_the_fx_warning_in_its_response(db):
    """경고는 따로 조회해야 보이는 게 아니라 저장 응답에 실려 온다 — 따로 두면 호출하는 쪽이
    빼먹고, 빼먹은 티도 안 난다."""
    out = upsert_lab(
        LabIn(lab_code="ZZT", name="테스트연구소", currency=TEST_CUR, effective_from=f"{TEST_YEAR}-06"),
        db,
    )
    assert out.currency == TEST_CUR  # 환율이 없어도 등록 자체는 성공한다
    assert out.fx_warning is not None and TEST_CUR in out.fx_warning


def test_krw_lab_never_asks_for_a_rate(db):
    """원화 연구소에는 환율 입력을 요구하면 안 된다 — 원/원 환율이란 건 없어서 입력할 방법도 없고,
    요구하면 꺼지지 않는 경고가 된다(챗봇으로 '한국 연구소 추가'를 하면 바로 걸린다)."""
    assert lab_currency_warning(db, "KRW", f"{TEST_YEAR}-06") is None
