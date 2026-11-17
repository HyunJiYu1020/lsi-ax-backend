import re
from bisect import bisect_right
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.shared.services.fx_rates import (
    FX_INV,
    FxRateCache,
    OVERSEAS_FX_RULE_DEFAULT_CODE,
    OVERSEAS_FX_RULE_FALLBACK,
    OVERSEAS_FX_RULE_GROUP,
    applied_rows,
    avg_krw,
    currency_coverage,
    materialize_applied_rates,
    write_applied_rows,
    compute_applied_rows,
    eom_krw,
    forecast_krw,
    fx_dir,
    overseas_fx_default,
    overseas_fx_default_row,
    rate_unit,
    upsert_rate,
)
from app.db.session import get_db
from app.shared.models.fx import FxRate, OverseasFxRuleOverride
from app.shared.models.system import CommonCode
from app.shared.schemas.fx import (
    FxActualUpload,
    FxCurrencyCoverage,
    FxAppliedRecomputeRequest,
    FxAppliedRow,
    FxForecastUpload,
    FxRateIn,
    FxPlanRow,
    FxPlanUpload,
    FxState,
    OverseasFxRuleDefaultIn,
    OverseasFxRuleOverrideIn,
    OverseasFxRuleOverrideRow,
    OverseasFxRuleState,
)

router = APIRouter()

@router.get("/fx/state", response_model=FxState)
def get_fx_state(db: Session = Depends(get_db)) -> FxState:
    actual: dict[str, FxActualUpload] = {}
    forecast: dict[str, FxForecastUpload] = {}
    plan_months: dict[str, dict[str, list[float]]] = {}  # year -> currency -> [12 floats]
    plan_meta: dict[str, tuple[str, str]] = {}  # year -> (file, at)

    for r in db.scalars(select(FxRate).order_by(FxRate.year_month)):
        at = r.uploaded_at.strftime("%m.%d %H:%M") if r.uploaded_at else ""
        if r.kind == "actual_avg":
            up = actual.setdefault(r.year_month, FxActualUpload())
            up.avg[r.currency_code] = float(r.rate)
            if r.source_file:
                up.avg_file, up.avg_at = r.source_file, at
        elif r.kind == "actual_eom":
            up = actual.setdefault(r.year_month, FxActualUpload())
            up.eom[r.currency_code] = float(r.rate)
            if r.source_file:
                up.eom_file, up.eom_at = r.source_file, at
        elif r.kind == "forecast":
            up = forecast.setdefault(r.year_month, FxForecastUpload())
            up.rows[r.currency_code] = float(r.rate)
            if r.source_file:
                up.file, up.at = r.source_file, at
        elif r.kind == "plan":
            year, month = r.year_month[:4], int(r.year_month[5:7])
            months = plan_months.setdefault(year, {}).setdefault(r.currency_code, [0.0] * 12)
            months[month - 1] = float(r.rate)
            if r.source_file:
                plan_meta[year] = (r.source_file, at)

    plan: dict[str, FxPlanUpload] = {
        year: FxPlanUpload(
            rows=[FxPlanRow(c=c, m=m) for c, m in currencies.items()],
            file=plan_meta.get(year, ("", ""))[0], at=plan_meta.get(year, ("", ""))[1],
        )
        for year, currencies in plan_months.items()
    }

    return FxState(actual=actual, forecast=forecast, plan=plan)


@router.put("/fx/state")
def save_fx_state(state: FxState, db: Session = Depends(get_db)) -> dict[str, str]:
    now = datetime.now(timezone.utc)

    # 실적: avg와 eom은 서로 다른 파일에서 다른 시점에 올라올 수 있다 — 각자 자기 kind 행만
    # upsert하므로 한쪽을 안 보내도 다른 kind 행엔 영향이 없다.
    for ym, upload in state.actual.items():
        for currency, value in upload.avg.items():
            upsert_rate(db, ym, currency, "actual_avg", value, upload.avg_file, now)
        for currency, value in upload.eom.items():
            upsert_rate(db, ym, currency, "actual_eom", value, upload.eom_file, now)

    for ym, upload in state.forecast.items():
        for currency, value in upload.rows.items():
            upsert_rate(db, ym, currency, "forecast", value, upload.file, now)

    # 경영: 연 1회 12개월치를 한 번에 올리지만, 저장은 다른 kind와 똑같이 연월 단위 행으로 쪼갠다.
    for year, upload in state.plan.items():
        for row in upload.rows:
            months = row.m[:12] + [0.0] * (12 - len(row.m))
            for i, v in enumerate(months, start=1):
                upsert_rate(db, f"{year}-{i:02d}", row.c, "plan", v, upload.file, now)

    # 원본이 바뀌었으니 적용 환율 표를 전 조합(연도×기준월×속보실적×룰) 다시 만든다 —
    # applied_rows는 이 표만 읽으므로(없으면 404), 여기서 전부 만들어 둬야 화면이 멈추지 않는다.
    materialize_applied_rates(db)
    db.commit()
    return {"status": "ok"}


@router.get("/fx/currency-coverage", response_model=FxCurrencyCoverage)
def get_fx_currency_coverage(currency: str, year: int, db: Session = Depends(get_db)) -> FxCurrencyCoverage:
    """그 해에 그 통화의 환율 원본이 어느 달까지 들어와 있는지. 연구소 통화를 새로 잡거나 바꾸기 전에
    "환산할 환율이 실제로 있나"를 확인하는 용도다 — 없으면 금액이 멈추는 게 아니라 조용히 환산 없이
    계산되므로(services/fx_rates.currency_coverage 주석 참고), 확인은 사람이 아니라 서버가 한다."""
    return currency_coverage(db, currency.strip().upper(), year)


@router.put("/fx/rate", response_model=FxCurrencyCoverage)
def save_fx_rate(body: FxRateIn, db: Session = Depends(get_db)) -> FxCurrencyCoverage:
    """환율 원본 한 칸(연월 × 통화 × 종류)을 직접 넣는다 — 정상 경로는 파일 업로드(PUT /fx/state)고,
    이건 "연구소를 새로 만들었는데 그 통화 환율이 아직 없다"처럼 빈칸 하나를 메우는 용도다.

    저장 후 적용 환율 표를 다시 만든다(PUT /fx/state와 같은 규칙) — 그래야 집계가 바로 그 통화를
    환산한다. 응답은 저장 결과가 아니라 "이제 그 통화가 어느 달까지 채워졌는지"다. 한 칸을 넣어도
    아직 비는 달이 있을 수 있어서, 넣었다는 사실보다 남은 구멍을 보여주는 쪽이 쓸모 있다."""
    kind = body.kind.strip()
    if kind not in ("actual_avg", "actual_eom", "forecast", "plan"):
        raise HTTPException(400, "kind는 actual_avg | actual_eom | forecast | plan만 쓸 수 있다.")
    currency = body.currency_code.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise HTTPException(400, f"통화 코드는 영문 3자리여야 한다(받은 값: {body.currency_code!r}).")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", body.year_month):
        raise HTTPException(400, f'year_month는 "YYYY-MM" 형식이어야 한다(받은 값: {body.year_month!r}).')
    if body.rate <= 0:
        # 0을 허용하면 "환율이 없다"와 "환율이 0이다"가 섞인다 — 적용 환율이 0/NULL을 구분하려고
        # 애쓰는 이유가 그것이다(models/fx.py FxAppliedRate.rate 주석).
        raise HTTPException(400, f"환율은 0보다 커야 한다(받은 값: {body.rate}). 단위는 {rate_unit(currency)}다.")

    upsert_rate(db, body.year_month, currency, kind, body.rate, body.source_file)
    materialize_applied_rates(db)
    db.commit()
    return currency_coverage(db, currency, int(body.year_month[:4]))


@router.post("/fx/applied/recompute", response_model=list[FxAppliedRow])
def recompute_applied_rates(body: FxAppliedRecomputeRequest, db: Session = Depends(get_db)) -> list[FxAppliedRow]:
    """지정한 기준(연도·월·속보실적·룰)의 적용 환율을 다시 계산해 fx_applied_rates에 채운다.
    전 조합은 /fx/state 저장이 materialize_applied_rates로 자동 생성하므로 평소엔 부를 일이 없고,
    챗봇의 환산 기준 전환(recompute_applied_fx_rates 도구)이 특정 기준 하나를 다시 쓸 때 쓴다.
    dry_run=True면 계산 결과만 돌려주고 fx_applied_rates는 안 건드린다 — 실제 저장 전에 "이 기준으로
    바꾸면 숫자가 어떻게 되는지" 미리 보여줄 때 쓴다."""
    cache = FxRateCache(db, body.basis_year)
    rows = compute_applied_rows(cache, body.basis_year, body.basis_month, body.basis_kind, body.rule)
    if body.dry_run:
        return rows
    write_applied_rows(db, body.basis_year, body.basis_month, body.basis_kind, body.rule, rows)
    db.commit()
    return rows


@router.get("/fx/applied", response_model=list[FxAppliedRow])
def get_applied_rates(
    basis_year: int, basis_month: int, basis_kind: str = "forecast", rule: str = "avg", db: Session = Depends(get_db)
) -> list[FxAppliedRow]:
    # 아직 아무도 안 본 기준이면 행이 없다 — 그때 빈 배열을 주면 "환율이 없다"로 읽혀서 금액이
    # 0으로 환산된다. applied_rows가 없는 조합을 원본에서 계산해 채운 뒤 돌려주므로 그럴 일이 없고,
    # 집계가 쓰는 값과 같은 함수를 거치니 화면에 보이는 적용 환율과 금액에 쓰인 환율이 어긋나지 않는다.
    return applied_rows(db, basis_year, basis_month, basis_kind, rule)


@router.get("/fx/overseas-rule", response_model=OverseasFxRuleState)
def get_overseas_fx_rule(db: Session = Depends(get_db)) -> OverseasFxRuleState:
    """해외연구소 환산 환율 기준의 현재 상태 — 기본값(예외 없는 달이 쓰는 값)과 월 단위 예외 전체.
    화면은 이걸 한 번만 받아서 월마다 "예외 있으면 그 rule, 없으면 default"로 판정한다."""
    overrides = db.scalars(select(OverseasFxRuleOverride).order_by(OverseasFxRuleOverride.year_month))
    return OverseasFxRuleState(
        default=overseas_fx_default(db),
        overrides=[
            OverseasFxRuleOverrideRow(year_month=o.year_month, rule=o.rule, created_at=o.created_at)
            for o in overrides
        ],
    )


@router.put("/fx/overseas-rule/override", response_model=OverseasFxRuleOverrideRow)
def set_overseas_fx_rule_override(
    body: OverseasFxRuleOverrideIn, db: Session = Depends(get_db)
) -> OverseasFxRuleOverrideRow:
    """특정 달만 기본값과 다른 rule을 쓰도록 예외를 넣는다(= "이번 한 번만"). 기본값은 안 건드리므로
    다음 달부터는 그대로 기본값이 적용된다. 같은 달을 다시 저장하면 덮어쓴다."""
    stmt = pg_insert(OverseasFxRuleOverride).values(year_month=body.year_month, rule=body.rule)
    stmt = stmt.on_conflict_do_update(
        index_elements=["year_month"],
        set_={"rule": stmt.excluded.rule, "updated_at": datetime.now(timezone.utc)},
    )
    db.execute(stmt)
    db.commit()
    row = db.scalar(select(OverseasFxRuleOverride).where(OverseasFxRuleOverride.year_month == body.year_month))
    return OverseasFxRuleOverrideRow(year_month=row.year_month, rule=row.rule, created_at=row.created_at)


@router.put("/fx/overseas-rule/default", response_model=OverseasFxRuleState)
def set_overseas_fx_rule_default(body: OverseasFxRuleDefaultIn, db: Session = Depends(get_db)) -> OverseasFxRuleState:
    """기본값 자체를 바꾼다(= "앞으로 계속"). effective_from보다 이전 달들은 바뀌기 전 기본값으로
    예외를 심어서 고정한다 — 그래야 이미 마감해서 보고된 과거 달 숫자가 소급해서 바뀌지 않는다.
    고정 대상은 실제로 환율 데이터가 있는 달(fx_rates의 연월)로 한정하고, 이미 예외가 있는 달은
    사용자가 명시적으로 정한 값이라 건드리지 않는다."""
    old_default = overseas_fx_default(db)

    if body.rule != old_default:
        existing = set(db.scalars(select(OverseasFxRuleOverride.year_month)))
        past_yms = db.scalars(
            select(FxRate.year_month).where(FxRate.year_month < body.effective_from).distinct()
        )
        frozen = [
            OverseasFxRuleOverride(year_month=ym, rule=old_default)
            for ym in sorted(set(past_yms) - existing)
        ]
        db.add_all(frozen)

    row = overseas_fx_default_row(db)
    if row is None:
        db.add(
            CommonCode(
                group_code=OVERSEAS_FX_RULE_GROUP, code=OVERSEAS_FX_RULE_DEFAULT_CODE,
                name=body.rule, sort_order=10, is_active=True,
            )
        )
    else:
        row.name = body.rule
    db.commit()
    return get_overseas_fx_rule(db)
