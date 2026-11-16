"""적용 환율 계산 — fx_rates(실적·속보 원본)를 기준(연도·월·속보실적)과 룰(평균/말일)에 맞춰
12개월치로 조합한다. 전 계정 공용.

왜 여기로 옮겼나: 원래 app/api/v1/endpoints/fx.py 안에 `_FxRateCache` · `_compute_applied_rows`
같은 밑줄 이름으로 있었고, 해외연구소 엔드포인트가 그걸 직접 import해서 썼다(PLAN.md P1-1).
엔드포인트가 다른 엔드포인트의 내부 이름을 꺼내 쓰는 구조여서, 환율 규칙을 고치면 어디가 깨지는지
import를 따라가 봐야만 알 수 있었다. 계산은 화면(프론트 fxSeries)·챗봇·각 계정이 모두 같은 값을
봐야 하는 공용 규칙이라 services로 올리고 공개 이름으로 바꿨다.

주의: 이 모듈은 저장된 fx_applied_rates를 읽지 않는다 — 그 표에는 지금 기준월 조합만 채워져 있어서
비교 버전(직전 실적 등)의 기준이 없고, 없는 걸 읽으면 환율 0으로 조용히 넘어가 금액이 전부 0이 된다
(실제로 그렇게 나왔다). 원본에서 매번 계산한다. PLAN.md P2-7 참조.
"""

from bisect import bisect_right

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.core.enums import CodeGroup
from app.shared.models.fx import FxRate
from app.shared.models.system import CommonCode
from app.shared.schemas.fx import FxAppliedRow

# "적용 환율" 계산 규칙(프론트 fxSeries/fxAvgKRW/fxForecastKRW와 동일) — USD만 원/통화 그 자체고,
# 나머지 통화는 그 달 USD 값과 결합해서 원/통화로 바꾼다. EUR·GBP·AUD·NZD는 "$/통화"(역방향)라
# USD와 곱하고, 나머지는 "통화/$"라 USD를 나눈다.
FX_INV = {"EUR", "GBP", "AUD", "NZD"}

OVERSEAS_FX_RULE_GROUP = CodeGroup.OVERSEAS_FX_RULE.value
OVERSEAS_FX_RULE_DEFAULT_CODE = "default"
OVERSEAS_FX_RULE_FALLBACK = "last"


def fx_dir(c: str) -> str:
    if c == "USD":
        return "krw"
    return "inv" if c in FX_INV else "per"


class FxRateCache:
    """fx_rates를 한 번에 읽어서 메모리에서 조회한다. 적용 환율 계산은 통화×12개월(+기준 조합 수)만큼
    반복되는데, 반복마다 DB를 따로 조회하면 호출 하나에 수백~수천 번 왕복이 생긴다.

    basis_year로 연도 범위를 좁혀서 읽는다 — actual_avg/actual_eom은 basis_year 안에서만
    조회되고(아래 compute_applied_rows가 그 해 1~12월만 ym으로 만듦), forecast는 "그 달 속보가
    없으면 이전 속보를 쓴다"는 규칙 때문에 전년도까지만 보면 된다(매달 연속으로 올라오는 데이터라
    그 이상 과거까지 봐야 하는 경우는 없다고 가정). kind='plan'은 이 계산에 아예 안 쓰여 제외한다."""

    def __init__(self, db: Session, basis_year: int) -> None:
        self._by_key: dict[tuple[str, str, str], float] = {}
        for r in db.scalars(
            select(FxRate).where(
                FxRate.kind.in_(("actual_avg", "actual_eom", "forecast")),
                FxRate.year_month >= f"{basis_year - 1}-01",
                FxRate.year_month <= f"{basis_year}-12",
            )
        ):
            self._by_key[(r.year_month, r.currency_code, r.kind)] = float(r.rate) if r.rate else 0.0
        self.currencies = sorted({c for _, c, _ in self._by_key})
        self._forecast_yms = sorted({ym for ym, _, kind in self._by_key if kind == "forecast"})

    def rate_of(self, ym: str, c: str, kind: str) -> float:
        return self._by_key.get((ym, c, kind), 0.0)

    def forecast_of(self, ym: str) -> str | None:
        """ym 이하 중 가장 최근 속보 연월(그 달 속보가 없으면 이전 속보를 그대로 쓴다)."""
        idx = bisect_right(self._forecast_yms, ym)
        return self._forecast_yms[idx - 1] if idx else None


def avg_krw(cache: FxRateCache, ym: str, c: str) -> float:
    x = cache.rate_of(ym, c, "actual_avg")
    if not x:
        return 0.0
    if c == "USD":
        return round(x, 2)
    u = cache.rate_of(ym, "USD", "actual_avg")
    return round(u * x, 2) if fx_dir(c) == "inv" else (round(u / x, 2) if x else 0.0)


def eom_krw(cache: FxRateCache, ym: str, c: str) -> float:
    return cache.rate_of(ym, c, "actual_eom")


def forecast_krw(cache: FxRateCache, forecast_ym: str | None, c: str) -> float:
    if not forecast_ym:
        return 0.0
    x = cache.rate_of(forecast_ym, c, "forecast")
    if not x:
        return 0.0
    if c == "USD":
        return round(x, 2)
    u = cache.rate_of(forecast_ym, "USD", "forecast")
    return round(u * x, 2) if fx_dir(c) == "inv" else (round(u / x, 2) if x else 0.0)


def compute_applied_rows(
    cache: FxRateCache, basis_year: int, basis_month: int, basis_kind: str, rule: str
) -> list[FxAppliedRow]:
    """실적·속보(fx_rates)를 기준(연도·월·속보실적)과 룰(평균/말일)에 맞춰 12개월치로 조합한다."""
    th = basis_month + 1 if basis_kind == "actual" else basis_month
    out: list[FxAppliedRow] = []
    for c in cache.currencies:
        for mo in range(1, 13):
            ym = f"{basis_year}-{mo:02d}"
            if mo < th:
                v = eom_krw(cache, ym, c) if rule == "last" else 0.0
                source = "actual"
                if not v:
                    v = avg_krw(cache, ym, c)
            else:
                forecast_ym = cache.forecast_of(ym)
                v = forecast_krw(cache, forecast_ym, c)
                source = "forecast"
            out.append(FxAppliedRow(target_month=mo, currency_code=c, rate=v, source=source))
    return out


def overseas_fx_default_row(db: Session) -> CommonCode | None:
    return db.scalar(
        select(CommonCode).where(
            CommonCode.group_code == OVERSEAS_FX_RULE_GROUP,
            CommonCode.code == OVERSEAS_FX_RULE_DEFAULT_CODE,
        )
    )


def overseas_fx_default(db: Session) -> str:
    """해외연구소 실적 구간의 기본 환산 기준 — avg(월평균) | last(말일).

    달 단위 예외(overseas_fx_rule_overrides)가 없는 모든 달이 이 값을 쓴다(2층 구조의 아래층).
    """
    row = overseas_fx_default_row(db)
    return row.name if row and row.name in ("avg", "last") else OVERSEAS_FX_RULE_FALLBACK
