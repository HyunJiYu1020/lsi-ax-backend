"""해외연구소 환율 배열 — 월별 적용 환율을 계산해서 돌려준다.

왜 여기로 옮겼나: 원래 overseas.py의 `_rate_arrays`였고 증감분석·집계·결산 엔드포인트가 그 밑줄
이름을 직접 import해서 썼다(PLAN.md P1-1). 환율은 네 화면이 같은 숫자를 봐야 하는 공용 계산이라
services로 올리고 공개 이름으로 바꿨다.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.models.fx import FxRate, OverseasFxRuleOverride
from app.shared.services.fx_rates import applied_rows, currency_coverage, overseas_fx_default, rate_unit


def rate_arrays(
    db: Session, year: int, month: int, kind: str, currencies: set[str], ix: tuple[int, ...]
) -> dict[str, list[float]]:
    """월별 적용 환율. 'USD'는 원/$이고(달마다 말일/월평균 기준을 따른다 — core.js ovRateOV와 같다),
    현지통화는 '현지통화/$'로 돌려준다(원/$ ÷ 원/현지, 항상 월평균 기준 — core.js ovLRate와 같다).

    환율은 fx_applied_rates(적용 환율 표)에서만 읽는다 — 그래서 화면 금액에 실제로 반영된 환율이
    항상 그 표에 남는다(예전에는 그 표를 건너뛰고 매번 계산해서, 어떤 환율로 환산한 금액인지가
    아무 데도 남지 않았다). 표는 환율 저장(PUT /fx/state)이 전 조합을 미리 만들어 둔다. 읽으려는
    조합이 표에 없으면 applied_rows가 404로 멈추고 "기준정보 › 환율에서 업로드/저장" 안내를 낸다 —
    0으로 조용히 넘어가 금액이 전부 0이 되는 것보다 멈추는 쪽이 낫다.

    avg·last 두 룰을 다 받아 두는 이유: 달마다 환산 기준 예외(overseas_fx_rule_overrides)가 달라서,
    어느 룰을 쓸지는 12개월을 돌면서 달 단위로 고른다."""
    rows = {
        # rate가 None인 행(그 달 원본 환율 없음)은 아예 담지 않는다 — 아래 조회부가 전부
        # .get(..., 0.0)/truthy 검사라, 담지 않는 것과 0을 담는 것이 결과가 같고 뜻은 더 분명하다.
        rule: {(r.target_month, r.currency_code): float(r.rate)
               for r in applied_rows(db, year, month, kind, rule) if r.rate is not None}
        for rule in ("avg", "last")
    }
    # 월 단위 예외는 한 번에 읽는다(달마다 조회하면 12번 왕복한다).
    default = overseas_fx_default(db)
    overrides = {o.year_month: o.rule for o in db.scalars(select(OverseasFxRuleOverride))}
    usd: list[float] = []
    for m in range(1, 13):
        rule = overrides.get(f"{year:04d}-{m:02d}", default)
        usd.append(rows[rule].get((m, "USD"), 0.0))
    # 조회 기간에 쓰이는 달의 원/$가 비어 있으면 금액을 0으로 답하지 말고 실패시킨다 — 0은 "환율이
    # 0원"이 아니라 "환율을 못 찾았다"는 뜻이고, 그대로 답하면 챗봇이 틀린 금액을 말한다.
    missing = [j + 1 for j in ix if not usd[j]]
    if missing:
        raise HTTPException(
            404,
            f"{year}년 {month}월 {kind} 기준의 원/$ 환율이 없는 달이 있다(해당 월: "
            f"{', '.join(str(m) for m in missing)}). 기준정보 › 환율에 그 달 데이터를 먼저 넣어야 한다.",
        )
    out = {"USD": usd}
    for cur in currencies:
        # 현지/$ = (원/$) ÷ (원/현지). 분모가 없으면 0으로 두고, 호출부는 화면과 똑같이 "현지통화
        # 분리 없음"(= $ 연구소와 같은 계산)으로 처리한다.
        out[cur] = [
            (rows["avg"].get((m, "USD"), 0.0) / rows["avg"][(m, cur)]) if rows["avg"].get((m, cur)) else 0.0
            for m in range(1, 13)
        ]
    return out


def lab_currency_warning(db: Session, currency: str, effective_from: str) -> str | None:
    """그 통화로 연구소를 잡아도 환산이 되는지 확인하고, 안 되면 사람에게 보여줄 한 문장을 만든다.
    문제가 없으면 None.

    왜 저장할 때 확인해야 하나: 환율이 없는 통화로 연구소를 잡아도 아무것도 막지 않는다. 집계는
    멈추지 않고 **조용히** 현지통화 분리를 건너뛴다 — 위 rate_arrays가 분모 없는 통화를 0으로 두고,
    쓰는 쪽(services/variance.py)이 `lrc > 0`일 때만 환차를 분해하기 때문이다. 404로 멈추는 건
    원/$가 없을 때뿐이라, 현지통화가 없는 경우는 화면에 아무 신호도 안 남는다. 숫자는 서는데
    환산이 빠진 숫자다. 그래서 통화를 잡는 **그 순간** 확인해서 환율 입력을 요청해야 한다.

    두 경우를 가른다:
    1) 그 해에 그 통화 환율이 아예 없다 → 그 통화 환산 자체가 안 된다.
    2) 있긴 한데 적용 시점(effective_from) 이하의 속보가 없다 → 적용 시점부터 연말까지가 안 된다.
       적용 환율은 기준월 이후 달에 "그 달 이하의 가장 최근 속보"를 쓰기 때문이다
       (services/fx_rates.FxRateCache.forecast_of).
    """
    # 원화는 환율표에 있을 수 없다 — 원/원은 1이고, fx_rates에 KRW 행을 넣는 건 뜻이 없다.
    # 빼지 않으면 "KRW 환율을 입력해 주세요(단위 KRW/$)"라는, 따를 수도 없고 따르면 안 되는 안내가
    # 나간다. (원화 연구소를 집계가 어떻게 다뤄야 하는지는 별개 문제다 — 지금은 현지통화 분리
    # 없이 $ 연구소와 같은 계산으로 간다.)
    if currency == "KRW":
        return None
    year, month = int(effective_from[:4]), int(effective_from[5:7])
    coverage = currency_coverage(db, currency, year)
    unit = rate_unit(currency)
    if not coverage.registered:
        return (
            f"{currency} 환율이 {year}년 환율표에 없습니다. 이대로 두면 이 연구소 금액은 환산 없이 "
            f"(현지통화 분리가 빠진 채) 계산되는데, 화면에는 아무 경고도 안 나옵니다. "
            f"{effective_from} 속보 {currency} 환율을 입력해 주세요 — 단위는 {unit}입니다."
        )

    # 속보는 전년도 것도 이어서 쓴다(FxRateCache가 전년도까지 읽는다) — 그래서 같은 해만 보면 안 된다.
    has_forecast_before = db.scalar(
        select(FxRate.id)
        .where(
            FxRate.currency_code == currency,
            FxRate.kind == "forecast",
            FxRate.year_month >= f"{year - 1}-01",
            FxRate.year_month <= effective_from,
        )
        .limit(1)
    )
    if has_forecast_before is None:
        return (
            f"{currency} 환율은 {year}년 환율표에 있지만, {effective_from} 이전의 속보 환율이 "
            f"없습니다. 적용 환율은 기준월 이후 달에 '그 달 이하의 가장 최근 속보'를 쓰므로 "
            f"{month}~12월이 환산되지 않습니다. {effective_from} 속보 {currency} 환율을 입력해 "
            f"주세요 — 단위는 {unit}입니다."
        )
    return None
