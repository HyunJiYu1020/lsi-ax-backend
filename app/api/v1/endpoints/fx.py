from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import FxActualRate, FxAppliedRate, FxForecastRate, FxPlanRate
from app.schemas.fx import (
    FxActualUpload,
    FxAppliedRecomputeRequest,
    FxAppliedRow,
    FxForecastUpload,
    FxPlanRow,
    FxPlanUpload,
    FxState,
)

router = APIRouter()

# "적용 환율" 계산 규칙(프론트 fxSeries/fxAvgKRW/fxForecastKRW와 동일) — USD만 원/통화 그 자체고,
# 나머지 통화는 그 달 USD 값과 결합해서 원/통화로 바꾼다. EUR·GBP·AUD·NZD는 "$/통화"(역방향)라
# USD와 곱하고, 나머지는 "통화/$"라 USD를 나눈다.
FX_INV = {"EUR", "GBP", "AUD", "NZD"}


def _fx_dir(c: str) -> str:
    if c == "USD":
        return "krw"
    return "inv" if c in FX_INV else "per"


@router.get("/fx/state", response_model=FxState)
def get_fx_state(db: Session = Depends(get_db)) -> FxState:
    actual: dict[str, FxActualUpload] = {}
    for r in db.scalars(select(FxActualRate).order_by(FxActualRate.year_month)):
        up = actual.setdefault(r.year_month, FxActualUpload())
        if r.avg_rate:
            up.avg[r.currency_code] = float(r.avg_rate)
        if r.avg_source_file:
            up.avg_file = r.avg_source_file
            up.avg_at = r.avg_uploaded_at.strftime("%m.%d %H:%M") if r.avg_uploaded_at else ""
        if r.eom_rate:
            up.eom[r.currency_code] = float(r.eom_rate)
        if r.eom_source_file:
            up.eom_file = r.eom_source_file
            up.eom_at = r.eom_uploaded_at.strftime("%m.%d %H:%M") if r.eom_uploaded_at else ""

    forecast: dict[str, FxForecastUpload] = {}
    forecast_meta: dict[str, tuple[str, str]] = {}  # ym -> (file, at)
    for r in db.scalars(select(FxForecastRate).order_by(FxForecastRate.uploaded_at)):
        up = forecast.setdefault(r.year_month, FxForecastUpload())
        up.rows[r.currency_code] = float(r.rate)
        forecast_meta[r.year_month] = (r.source_file, r.uploaded_at.strftime("%m.%d %H:%M"))
    for ym, (file, at) in forecast_meta.items():
        forecast[ym].file, forecast[ym].at = file, at

    plan: dict[str, FxPlanUpload] = {}
    plan_meta: dict[str, tuple[str, str]] = {}
    for r in db.scalars(select(FxPlanRate).order_by(FxPlanRate.uploaded_at)):
        up = plan.setdefault(r.year, FxPlanUpload(rows=[], file="", at=""))
        up.rows.append(FxPlanRow(c=r.currency_code, m=[float(getattr(r, f"month{m}")) for m in range(1, 13)]))
        plan_meta[r.year] = (r.source_file, r.uploaded_at.strftime("%m.%d %H:%M"))
    for year, (file, at) in plan_meta.items():
        plan[year].file, plan[year].at = file, at

    return FxState(actual=actual, forecast=forecast, plan=plan)


@router.put("/fx/state")
def save_fx_state(state: FxState, db: Session = Depends(get_db)) -> dict[str, str]:
    now = datetime.now(timezone.utc)

    # 실적: avg와 eom은 서로 다른 파일에서 다른 시점에 올라올 수 있어, 같은 (연월,통화) 행이라도
    # 각자 자기 칸만 upsert한다 — 한쪽을 안 보내면 그 칸은 기존 값을 그대로 둔다(0으로 덮지 않음).
    for ym, upload in state.actual.items():
        for currency, value in upload.avg.items():
            stmt = pg_insert(FxActualRate).values(
                year_month=ym, currency_code=currency, avg_rate=value,
                avg_source_file=upload.avg_file, avg_uploaded_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["year_month", "currency_code"],
                set_={"avg_rate": stmt.excluded.avg_rate, "avg_source_file": stmt.excluded.avg_source_file,
                      "avg_uploaded_at": stmt.excluded.avg_uploaded_at},
            )
            db.execute(stmt)
        for currency, value in upload.eom.items():
            stmt = pg_insert(FxActualRate).values(
                year_month=ym, currency_code=currency, eom_rate=value,
                eom_source_file=upload.eom_file, eom_uploaded_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["year_month", "currency_code"],
                set_={"eom_rate": stmt.excluded.eom_rate, "eom_source_file": stmt.excluded.eom_source_file,
                      "eom_uploaded_at": stmt.excluded.eom_uploaded_at},
            )
            db.execute(stmt)

    for ym, upload in state.forecast.items():
        for currency, value in upload.rows.items():
            stmt = pg_insert(FxForecastRate).values(
                year_month=ym, currency_code=currency, rate=value, source_file=upload.file, uploaded_at=now
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["year_month", "currency_code"],
                set_={"rate": stmt.excluded.rate, "source_file": stmt.excluded.source_file, "uploaded_at": stmt.excluded.uploaded_at},
            )
            db.execute(stmt)

    for year, upload in state.plan.items():
        for row in upload.rows:
            month_cols = {f"month{m}": v for m, v in enumerate(row.m[:12] + [0.0] * (12 - len(row.m)), start=1)}
            stmt = pg_insert(FxPlanRate).values(
                year=year, currency_code=row.c, source_file=upload.file, **month_cols
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["year", "currency_code"],
                set_={**{k: getattr(stmt.excluded, k) for k in month_cols}, "source_file": stmt.excluded.source_file,
                      "uploaded_at": datetime.now(timezone.utc)},
            )
            db.execute(stmt)

    _refresh_all_applied_rates(db)
    db.commit()
    return {"status": "ok"}


def _avg_krw(db: Session, ym: str, c: str, usd_cache: dict[str, float]) -> float:
    row = db.scalar(select(FxActualRate).where(FxActualRate.year_month == ym, FxActualRate.currency_code == c))
    if not row or not row.avg_rate:
        return 0.0
    x = float(row.avg_rate)
    if c == "USD":
        return round(x, 2)
    if ym not in usd_cache:
        urow = db.scalar(select(FxActualRate).where(FxActualRate.year_month == ym, FxActualRate.currency_code == "USD"))
        usd_cache[ym] = float(urow.avg_rate) if urow and urow.avg_rate else 0.0
    u = usd_cache[ym]
    return round(u * x, 2) if _fx_dir(c) == "inv" else (round(u / x, 2) if x else 0.0)


def _eom_krw(db: Session, ym: str, c: str) -> float:
    row = db.scalar(select(FxActualRate).where(FxActualRate.year_month == ym, FxActualRate.currency_code == c))
    return float(row.eom_rate) if row and row.eom_rate else 0.0


def _forecast_of(db: Session, ym: str) -> str | None:
    """ym 이하 중 가장 최근 속보 연월을 찾는다(프론트 fxForecastOf와 동일 규칙 — 그 달 속보가 없으면 이전 속보를 그대로 쓴다)."""
    rows = sorted(set(db.scalars(select(FxForecastRate.year_month).where(FxForecastRate.year_month <= ym))))
    return rows[-1] if rows else None


def _forecast_krw(db: Session, forecast_ym: str | None, c: str, usd_cache: dict[str, float]) -> float:
    if not forecast_ym:
        return 0.0
    row = db.scalar(select(FxForecastRate).where(FxForecastRate.year_month == forecast_ym, FxForecastRate.currency_code == c))
    if not row or not row.rate:
        return 0.0
    x = float(row.rate)
    if c == "USD":
        return round(x, 2)
    if forecast_ym not in usd_cache:
        urow = db.scalar(select(FxForecastRate).where(FxForecastRate.year_month == forecast_ym, FxForecastRate.currency_code == "USD"))
        usd_cache[forecast_ym] = float(urow.rate) if urow and urow.rate else 0.0
    u = usd_cache[forecast_ym]
    return round(u * x, 2) if _fx_dir(c) == "inv" else (round(u / x, 2) if x else 0.0)


def _compute_applied_rows(db: Session, basis_year: int, basis_month: int, basis_kind: str, rule: str) -> list[FxAppliedRow]:
    """실적(fx_actual_rates)·속보(fx_forecast_rates)를 기준(연도·월·속보실적)과 룰(평균/말일)에 맞춰 12개월치로 조합한다."""
    currencies = sorted(
        set(db.scalars(select(FxActualRate.currency_code).distinct()))
        | set(db.scalars(select(FxForecastRate.currency_code).distinct()))
        | set(db.scalars(select(FxPlanRate.currency_code).distinct()))
    )
    th = basis_month + 1 if basis_kind == "act" else basis_month
    usd_avg_cache: dict[str, float] = {}
    usd_forecast_cache: dict[str, float] = {}
    out: list[FxAppliedRow] = []
    for c in currencies:
        for mo in range(1, 13):
            ym = f"{basis_year}-{mo:02d}"
            if mo < th:
                v = _eom_krw(db, ym, c) if rule == "last" else 0.0
                source = "actual"
                if not v:
                    v = _avg_krw(db, ym, c, usd_avg_cache)
            else:
                forecast_ym = _forecast_of(db, ym)
                v = _forecast_krw(db, forecast_ym, c, usd_forecast_cache)
                source = "forecast"
            out.append(FxAppliedRow(target_month=mo, currency_code=c, rate=v, source=source))
    return out


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
    for row in rows:
        db.add(FxAppliedRate(
            basis_year=basis_year, basis_month=basis_month, basis_kind=basis_kind, rule=rule,
            target_month=row.target_month, currency_code=row.currency_code, rate=row.rate, source=row.source,
        ))


def _refresh_all_applied_rates(db: Session) -> None:
    """fx_applied_rates에 이미 캐시된 모든 (기준연도·기준월·속보실적·룰) 조합을 다시 계산해 덮어쓴다.
    실적·속보·경영 환율(원본 3개 테이블) 중 하나라도 바뀌면 이 함수로 캐시를 최신화한다. commit은 호출한 쪽이 한다."""
    bases = db.execute(
        select(
            FxAppliedRate.basis_year, FxAppliedRate.basis_month, FxAppliedRate.basis_kind, FxAppliedRate.rule
        ).distinct()
    ).all()
    for basis_year, basis_month, basis_kind, rule in bases:
        rows = _compute_applied_rows(db, basis_year, basis_month, basis_kind, rule)
        _write_applied_rows(db, basis_year, basis_month, basis_kind, rule, rows)


@router.post("/fx/applied/recompute", response_model=list[FxAppliedRow])
def recompute_applied_rates(body: FxAppliedRecomputeRequest, db: Session = Depends(get_db)) -> list[FxAppliedRow]:
    """지정한 기준(연도·월·속보실적·룰)의 적용 환율을 다시 계산해 fx_applied_rates에 채운다.
    실적·속보·경영 원본이 바뀌었을 때 특정 기준을 처음 만들거나 수동으로 갱신할 때 쓴다. 이미 캐시된
    기준들은 /fx/state 저장 시 _refresh_all_applied_rates로 자동 갱신된다."""
    rows = _compute_applied_rows(db, body.basis_year, body.basis_month, body.basis_kind, body.rule)
    _write_applied_rows(db, body.basis_year, body.basis_month, body.basis_kind, body.rule, rows)
    db.commit()
    return rows


@router.get("/fx/applied", response_model=list[FxAppliedRow])
def get_applied_rates(
    basis_year: int, basis_month: int, basis_kind: str = "sok", rule: str = "avg", db: Session = Depends(get_db)
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
