"""적용 환율 계산 — fx_rates(실적·속보 원본)를 기준(연도·월·속보실적)과 룰(평균/말일)에 맞춰
12개월치로 조합한다. 전 계정 공용 — 화면(프론트 fxSeries)·챗봇·각 계정이 모두 같은 값을 본다.

적용 환율을 읽는 입구는 applied_rows() 하나이고, fx_applied_rates(적용 환율 표)만 읽는다 —
그래서 그 표가 곧 "집계 금액에 반영된 환율"이다. 표는 환율 저장(PUT /fx/state)이 끝날 때
materialize_applied_rates()로 미리 전부 만들어 둔다: 원본이 있는 연도마다 기준월 12 × 속보/실적 2 ×
평균/말일 2 = 48조합. 조합이 유한해서 전부 만들어 둘 수 있고, 그러면 읽는 쪽은 분기 없이 표만 보면
된다.

읽으려는 조합이 표에 없으면 0으로 넘어가지 않고 404로 멈춘다 — 없는 걸 0으로 읽으면 금액이 조용히
전부 0이 된다(실제로 그렇게 나왔다). 404 문구는 두 경우를 가른다: 그 연도 원본 자체가 없으면
"업로드해 달라", 원본은 있는데 표만 안 만들어졌으면(덤프 복원 등 PUT /fx/state를 안 거친 경우) "환율 화면에서 저장을 실행해 달라".
"""

from bisect import bisect_right
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy import insert as sa_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.shared.core.enums import CodeGroup
from app.shared.models.fx import FxAppliedRate, FxRate
from app.shared.models.system import CommonCode
from app.shared.schemas.fx import FxAppliedRow, FxCurrencyCoverage

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
            # v가 0이면 원본에 그 달 환율이 없었다는 뜻이다(avg_krw/forecast_krw가 '없음'을 0으로
            # 돌려준다). 환율 0원은 존재하지 않으므로 0을 그대로 저장하지 않고 NULL로 남겨서,
            # 읽는 쪽이 '환율이 없다'와 '환율이 0이다'를 구분할 수 있게 한다.
            out.append(FxAppliedRow(target_month=mo, currency_code=c, rate=v or None, source=source))
    return out


APPLIED_MISSING_PREFIX = "적용 환율이 아직 생성되지 않았습니다"


def applied_rows(
    db: Session, basis_year: int, basis_month: int, basis_kind: str, rule: str
) -> list[FxAppliedRow]:
    """그 기준(연도·월·속보실적·룰)의 적용 환율 12개월치 — fx_applied_rates만 읽는다. 집계·증감·
    결산이 금액을 환산할 때 쓰는 환율은 전부 이 함수를 지나므로, 그 표가 곧 "화면 금액에 반영된
    환율"이다. 표는 환율 저장(PUT /fx/state)이 materialize_applied_rates로 미리 전부 만들어 둔다.

    조합이 없으면 0으로 넘어가지 않고 404로 멈춘다 — 없는 걸 0으로 읽으면 금액이 조용히 전부 0이
    된다(실제로 그렇게 나왔다). 문구는 두 경우를 가른다: 그 연도 원본(fx_rates) 자체가 없으면
    업로드 안내, 원본은 있는데 표만 안 만들어졌으면(덤프 복원처럼 PUT /fx/state를 안 거친 경우)
    환율 화면에서 저장 실행 안내 — 이때 업로드하라고 하면 이미 올린 파일을 또 올리게 된다.
    프런트는 APPLIED_MISSING_PREFIX로 이 404를 알아보고 기준정보 › 환율 화면으로 안내한다."""
    stored = db.scalars(
        select(FxAppliedRate)
        .where(
            FxAppliedRate.basis_year == basis_year,
            FxAppliedRate.basis_month == basis_month,
            FxAppliedRate.basis_kind == basis_kind,
            FxAppliedRate.rule == rule,
        )
        .order_by(FxAppliedRate.target_month, FxAppliedRate.currency_code)
    ).all()
    if stored:
        return [
            FxAppliedRow(
                target_month=r.target_month,
                currency_code=r.currency_code,
                rate=None if r.rate is None else float(r.rate),
                source=r.source,
            )
            for r in stored
        ]

    kind_label = "실적" if basis_kind == "actual" else "속보"
    has_originals = db.scalar(
        select(FxRate.id).where(
            FxRate.kind.in_(("actual_avg", "actual_eom", "forecast")),
            FxRate.year_month >= f"{basis_year}-01",
            FxRate.year_month <= f"{basis_year}-12",
        ).limit(1)
    )
    if has_originals is None:
        raise HTTPException(
            404,
            f"{APPLIED_MISSING_PREFIX} — {basis_year}년 {basis_month}월 {kind_label} 기준의 실적·속보 "
            f"환율이 올라온 적이 없습니다. 기준정보 › 환율에서 해당 연월의 환율을 업로드해 주세요.",
        )
    raise HTTPException(
        404,
        f"{APPLIED_MISSING_PREFIX} — {basis_year}년 환율 원본은 있지만 적용 환율 표가 만들어지지 "
        f"않았습니다. 기준정보 › 환율에서 저장을 한 번 실행하면 전체 기준의 적용 환율이 생성됩니다.",
    )


def write_applied_rows(
    db: Session, basis_year: int, basis_month: int, basis_kind: str, rule: str, rows: list[FxAppliedRow]
) -> None:
    """계산된 적용 환율로 그 기준 조합을 덮어쓴다(기존 행은 지우고 새로 넣음). commit은 호출한 쪽이 한다."""
    db.execute(
        delete(FxAppliedRate).where(
            FxAppliedRate.basis_year == basis_year,
            FxAppliedRate.basis_month == basis_month,
            FxAppliedRate.basis_kind == basis_kind,
            FxAppliedRate.rule == rule,
        )
    )
    if not rows:
        return
    db.execute(
        sa_insert(FxAppliedRate),
        [
            {
                "basis_year": basis_year, "basis_month": basis_month, "basis_kind": basis_kind, "rule": rule,
                "target_month": row.target_month, "currency_code": row.currency_code,
                "rate": row.rate, "source": row.source,
            }
            for row in rows
        ],
    )


def materialize_applied_rates(db: Session) -> int:
    """원본(fx_rates)이 있는 모든 연도의 적용 환율 조합 전부를 다시 계산해 덮어쓴다 — 연도당
    기준월 12 × 속보/실적 2 × 평균/말일 2 = 48조합. commit은 호출한 쪽이 한다.

    환율 저장(PUT /fx/state)이 끝날 때마다 부른다. "이미 표에 있는 조합만" 갱신하면 아무도 아직
    안 본 조합(비교 버전의 기준 등)이 영원히 비어 있다 — 조합이 유한하니 전부 만들어 두면 읽는
    쪽(applied_rows)은 "없으면?" 분기가 필요 없다. 조합 수가 연도당 48개,
    행 수로 연도당 수만 행이라 저장 한 번에 다시 만들어도 부담이 없다."""
    years = sorted(
        {
            int(ym[:4])
            for ym in db.scalars(
                select(FxRate.year_month).where(FxRate.kind.in_(("actual_avg", "actual_eom", "forecast"))).distinct()
            )
        }
    )
    combos = 0
    for year in years:
        cache = FxRateCache(db, year)
        for month in range(1, 13):
            for kind in ("forecast", "actual"):
                for rule in ("avg", "last"):
                    write_applied_rows(db, year, month, kind, rule, compute_applied_rows(cache, year, month, kind, rule))
                    combos += 1
    return combos


def rate_unit(currency_code: str) -> str:
    """fx_rates.rate 칸에 들어가는 값의 단위. 업로드 엑셀과 같은 형태이고 "원/통화"가 아니다 —
    USD만 원/$이고, EUR·GBP·AUD·NZD는 $/통화, 나머지는 통화/$다(fx_dir과 같은 구분).

    사람에게 환율을 물을 때 이 단위를 그대로 보여줘야 한다. "INR 환율 얼마예요?"라고만 물으면
    원/루피(약 14)를 받을 수 있는데 표가 기대하는 값은 INR/$(약 95)라, 그대로 저장되면 환산 금액이
    7배 가까이 틀어진다."""
    direction = fx_dir(currency_code)
    if direction == "krw":
        return "원/$"
    return f"$/{currency_code}" if direction == "inv" else f"{currency_code}/$"


def currency_coverage(db: Session, currency_code: str, year: int) -> FxCurrencyCoverage:
    """그 해에 그 통화의 환율 원본이 어느 달까지 들어와 있는지 — kind별 월 목록으로 돌려준다.

    왜 필요한가: 연구소 통화는 환율표와 따로 관리돼서, 환율이 없는 통화로 연구소를 잡아도 아무도
    막지 않는다. 그러면 집계가 멈추는 게 아니라 **조용히** 현지통화 분리를 건너뛴다
    (fxcalc.rate_arrays가 분모 없는 통화를 0으로 두고, 쓰는 쪽은 0이면 환차 분해를 생략한다).
    숫자는 서지만 환산이 빠진 숫자라서, 사람이 알아채지 못한다. 그래서 통화를 잡는 순간 확인해서
    없으면 입력을 요청할 근거를 만든다."""
    rows = db.execute(
        select(FxRate.kind, FxRate.year_month).where(
            FxRate.currency_code == currency_code,
            FxRate.year_month >= f"{year}-01",
            FxRate.year_month <= f"{year}-12",
        )
    ).all()
    months: dict[str, list[int]] = {k: [] for k in ("actual_avg", "actual_eom", "forecast", "plan")}
    for kind, ym in rows:
        if kind in months:
            months[kind].append(int(ym[5:7]))
    for v in months.values():
        v.sort()

    registered = bool(months["actual_avg"] or months["actual_eom"] or months["forecast"])
    unit = rate_unit(currency_code)
    if not registered:
        note = (
            f"{year}년 {currency_code} 환율이 환율표에 없습니다(실적·속보 모두). 이 통화를 쓰는 "
            f"연구소 금액은 환산 없이 계산됩니다 — 입력값 단위는 {unit}입니다."
        )
    else:
        parts = []
        if months["forecast"]:
            parts.append(f"속보 {', '.join(f'{m}월' for m in months['forecast'])}")
        if months["actual_avg"]:
            parts.append(f"실적(월평균) {', '.join(f'{m}월' for m in months['actual_avg'])}")
        note = f"{year}년 {currency_code} 환율이 있는 달: {' · '.join(parts)}. 입력값 단위는 {unit}입니다."

    return FxCurrencyCoverage(
        currency_code=currency_code,
        year=year,
        rate_unit=unit,
        registered=registered,
        forecast_months=months["forecast"],
        actual_avg_months=months["actual_avg"],
        actual_eom_months=months["actual_eom"],
        plan_months=months["plan"],
        note=note,
    )


def upsert_rate(
    db: Session,
    year_month: str,
    currency_code: str,
    kind: str,
    rate: float,
    source_file: str,
    uploaded_at: datetime | None = None,
) -> None:
    """환율 원본 한 칸을 넣거나 덮어쓴다. 파일 업로드(PUT /fx/state)와 한 칸 직접 입력
    (PUT /fx/rate)이 **같은 함수**를 쓴다 — 쓰기 규칙이 두 벌이 되면 한쪽만 고치고 다른 쪽을
    빼먹는다. uploaded_at을 주면 그 시각으로 박는다(업로드 한 번에 수백 칸이 들어가므로 호출부가
    시각을 한 번만 만들어 넘긴다).

    commit은 호출한 쪽이 한다 — 적용 환율 재생성(materialize_applied_rates)과 한 트랜잭션으로
    묶어야 "원본은 들어갔는데 적용 환율은 옛날 것"인 중간 상태가 안 생긴다."""
    stmt = pg_insert(FxRate).values(
        year_month=year_month, currency_code=currency_code, kind=kind, rate=rate,
        source_file=source_file, uploaded_at=uploaded_at or datetime.now(timezone.utc),
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["year_month", "currency_code", "kind"],
            set_={
                "rate": stmt.excluded.rate,
                "source_file": stmt.excluded.source_file,
                "uploaded_at": stmt.excluded.uploaded_at,
            },
        )
    )


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
