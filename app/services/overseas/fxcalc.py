"""해외연구소 환율 배열 — 월별 적용 환율을 계산해서 돌려준다.

왜 여기로 옮겼나: 원래 overseas.py의 `_rate_arrays`였고 증감분석·집계·결산 엔드포인트가 그 밑줄
이름을 직접 import해서 썼다(PLAN.md P1-1). 환율은 네 화면이 같은 숫자를 봐야 하는 공용 계산이라
services로 올리고 공개 이름으로 바꿨다.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OverseasFxRuleOverride
from app.services.fx_rates import FxRateCache, compute_applied_rows, overseas_fx_default


def rate_arrays(
    db: Session, year: int, month: int, kind: str, currencies: set[str], ix: tuple[int, ...]
) -> dict[str, list[float]]:
    """월별 적용 환율. 'USD'는 원/$이고(달마다 말일/월평균 기준을 따른다 — core.js ovRateOV와 같다),
    현지통화는 '현지통화/$'로 돌려준다(원/$ ÷ 원/현지, 항상 월평균 기준 — core.js ovLRate와 같다).

    저장된 fx_applied_rates를 읽지 않고 원본(fx_rates)에서 매번 계산한다 — 그 표에는 지금 기준월
    조합만 채워져 있어서 비교 버전(직전 실적 등)의 기준은 들어있지 않고, 없는 걸 읽으면 환율 0으로
    조용히 넘어가 금액이 전부 0이 되기 때문이다(실제로 그렇게 나왔다). 계산 함수는 화면의 fxSeries와
    같은 규칙을 이미 구현해 둔 fx.py의 것을 그대로 쓴다."""
    cache = FxRateCache(db, year)
    rows = {
        rule: {(r.target_month, r.currency_code): float(r.rate)
               for r in compute_applied_rows(cache, year, month, kind, rule)}
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
