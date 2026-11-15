from bisect import bisect_right
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.services.fx_rates import (
    FX_INV,
    FxRateCache,
    OVERSEAS_FX_RULE_DEFAULT_CODE,
    OVERSEAS_FX_RULE_FALLBACK,
    OVERSEAS_FX_RULE_GROUP,
    avg_krw,
    compute_applied_rows,
    eom_krw,
    forecast_krw,
    fx_dir,
    overseas_fx_default,
    overseas_fx_default_row,
)
from app.db.session import get_db
from app.models import CommonCode, FxAppliedRate, FxRate, OverseasFxRuleOverride
from app.schemas.fx import (
    FxActualUpload,
    FxAppliedRecomputeRequest,
    FxAppliedRow,
    FxForecastUpload,
    FxPlanRow,
    FxPlanUpload,
    FxState,
    OverseasFxRuleDefaultIn,
    OverseasFxRuleOverrideIn,
    OverseasFxRuleOverrideRow,
    OverseasFxRuleState,
)

router = APIRouter()

def _upsert_fx_input(
    db: Session, year_month: str, currency: str, kind: str, rate: float, source_file: str, uploaded_at: datetime
) -> None:
    stmt = pg_insert(FxRate).values(
        year_month=year_month, currency_code=currency, kind=kind, rate=rate,
        source_file=source_file, uploaded_at=uploaded_at,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["year_month", "currency_code", "kind"],
        set_={"rate": stmt.excluded.rate, "source_file": stmt.excluded.source_file, "uploaded_at": stmt.excluded.uploaded_at},
    )
    db.execute(stmt)


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
            _upsert_fx_input(db, ym, currency, "actual_avg", value, upload.avg_file, now)
        for currency, value in upload.eom.items():
            _upsert_fx_input(db, ym, currency, "actual_eom", value, upload.eom_file, now)

    for ym, upload in state.forecast.items():
        for currency, value in upload.rows.items():
            _upsert_fx_input(db, ym, currency, "forecast", value, upload.file, now)

    # 경영: 연 1회 12개월치를 한 번에 올리지만, 저장은 다른 kind와 똑같이 연월 단위 행으로 쪼갠다.
    for year, upload in state.plan.items():
        for row in upload.rows:
            months = row.m[:12] + [0.0] * (12 - len(row.m))
            for i, v in enumerate(months, start=1):
                _upsert_fx_input(db, f"{year}-{i:02d}", row.c, "plan", v, upload.file, now)

    _refresh_all_applied_rates(db)
    db.commit()
    return {"status": "ok"}


def _write_applied_rows(
    db: Session, basis_year: int, basis_month: int, basis_kind: str, rule: str, rows: list[FxAppliedRow]
) -> None:
    """계산된 적용 환율을 fx_applied_rates에 덮어쓴다(같은 기준의 기존 행은 지우고 새로 넣음). commit은 호출한 쪽이 한다."""
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
        insert(FxAppliedRate),
        [
            {
                "basis_year": basis_year, "basis_month": basis_month, "basis_kind": basis_kind, "rule": rule,
                "target_month": row.target_month, "currency_code": row.currency_code, "rate": row.rate, "source": row.source,
            }
            for row in rows
        ],
    )


def _refresh_all_applied_rates(db: Session) -> None:
    """fx_applied_rates에 이미 캐시된 모든 (기준연도·기준월·속보실적·룰) 조합을 다시 계산해 덮어쓴다.
    실적·속보·경영 환율(fx_rates) 중 하나라도 바뀌면 이 함수로 캐시를 최신화한다. commit은 호출한 쪽이 한다."""
    bases = db.execute(
        select(
            FxAppliedRate.basis_year, FxAppliedRate.basis_month, FxAppliedRate.basis_kind, FxAppliedRate.rule
        ).distinct()
    ).all()
    cache_by_year: dict[int, FxRateCache] = {}  # 같은 basis_year끼리는 캐시를 재사용(연도별로 1번만 로드)
    for basis_year, basis_month, basis_kind, rule in bases:
        cache = cache_by_year.setdefault(basis_year, FxRateCache(db, basis_year))
        rows = compute_applied_rows(cache, basis_year, basis_month, basis_kind, rule)
        _write_applied_rows(db, basis_year, basis_month, basis_kind, rule, rows)


@router.post("/fx/applied/recompute", response_model=list[FxAppliedRow])
def recompute_applied_rates(body: FxAppliedRecomputeRequest, db: Session = Depends(get_db)) -> list[FxAppliedRow]:
    """지정한 기준(연도·월·속보실적·룰)의 적용 환율을 다시 계산해 fx_applied_rates에 채운다.
    실적·속보·경영 원본이 바뀌었을 때 특정 기준을 처음 만들거나 수동으로 갱신할 때 쓴다. 이미 캐시된
    기준들은 /fx/state 저장 시 _refresh_all_applied_rates로 자동 갱신된다.
    dry_run=True면 계산 결과만 돌려주고 fx_applied_rates는 안 건드린다 — 실제 저장 전에 "이 기준으로
    바꾸면 숫자가 어떻게 되는지" 미리 보여줄 때 쓴다."""
    cache = FxRateCache(db, body.basis_year)
    rows = compute_applied_rows(cache, body.basis_year, body.basis_month, body.basis_kind, body.rule)
    if body.dry_run:
        return rows
    _write_applied_rows(db, body.basis_year, body.basis_month, body.basis_kind, body.rule, rows)
    db.commit()
    return rows


@router.get("/fx/applied", response_model=list[FxAppliedRow])
def get_applied_rates(
    basis_year: int, basis_month: int, basis_kind: str = "forecast", rule: str = "avg", db: Session = Depends(get_db)
) -> list[FxAppliedRow]:
    rows = db.scalars(
        select(FxAppliedRate)
        .where(
            FxAppliedRate.basis_year == basis_year,
            FxAppliedRate.basis_month == basis_month,
            FxAppliedRate.basis_kind == basis_kind,
            FxAppliedRate.rule == rule,
        )
        .order_by(FxAppliedRate.target_month, FxAppliedRate.currency_code)
    )
    return [
        FxAppliedRow(target_month=r.target_month, currency_code=r.currency_code, rate=float(r.rate), source=r.source)
        for r in rows
    ]


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
