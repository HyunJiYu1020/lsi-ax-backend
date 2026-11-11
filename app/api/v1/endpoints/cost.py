from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import CostEntry, CostEntryDetail
from app.schemas.cost import CostEntryOut, CostEntryValueIn

router = APIRouter()

# 분기(q1~q4)·연간(annual) 합계는 QUARTER/YEAR period_type 행에 저장한다 — 표준 월 값을 쓸 때마다
# 그 해 12개월로 자동 재계산해 같이 채워서 "12개(월)+5개(분기·연간)"가 기본으로 다 저장되게 한다.
# 조회 시 다시 합산할 필요가 없다.
QUARTERS: dict[str, tuple[int, int, int]] = {"q1": (1, 2, 3), "q2": (4, 5, 6), "q3": (7, 8, 9), "q4": (10, 11, 12)}
QUARTER_PERIOD = {"q1": 1, "q2": 2, "q3": 3, "q4": 4}
PERIOD_QUARTER = {v: k for k, v in QUARTER_PERIOD.items()}


def _resync_summary(db: Session, cost_entry_id: int, year: int) -> None:
    """그 헤더의 그 해 표준 월(MONTH) 12개 값으로 QUARTER·YEAR 행을 다시 계산해 덮어쓴다."""
    months = {
        r.period: float(r.amount)
        for r in db.scalars(
            select(CostEntryDetail).where(
                CostEntryDetail.cost_entry_id == cost_entry_id,
                CostEntryDetail.year == year,
                CostEntryDetail.period_type == "MONTH",
            )
        )
    }
    summary = {q: sum(months.get(m, 0) for m in mos) for q, mos in QUARTERS.items()}
    rows = [("QUARTER", QUARTER_PERIOD[q], amount) for q, amount in summary.items()]
    rows.append(("YEAR", 0, sum(summary.values())))
    for period_type, period, amount in rows:
        stmt = pg_insert(CostEntryDetail).values(
            cost_entry_id=cost_entry_id, year=year, period_type=period_type, period=period, col_name="", amount=amount
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["cost_entry_id", "year", "period_type", "period", "col_name"],
            set_={"amount": stmt.excluded.amount},
        )
        db.execute(stmt)


def _out_rows(db: Session, entries: list[CostEntry]) -> list[CostEntryOut]:
    if not entries:
        return []
    by_id = {e.id: e for e in entries}
    details = db.scalars(select(CostEntryDetail).where(CostEntryDetail.cost_entry_id.in_(by_id.keys())))
    out = {
        e.id: CostEntryOut(
            account_code=e.account_code,
            scope_code=e.scope_code,
            category_code=e.category_code,
            yyyymm=e.yyyymm,
            version=e.version,
            label=e.label,
            label_en=e.label_en,
            anchor_key=e.anchor_key,
        )
        for e in entries
    }
    for d in details:
        o = out[d.cost_entry_id]
        if d.period_type == "MONTH":
            o.months[f"{d.year}-{d.period:02d}"] = float(d.amount)
        elif d.period_type == "QUARTER":
            o.summary[PERIOD_QUARTER[d.period]] = float(d.amount)
        elif d.period_type == "YEAR":
            o.summary["annual"] = float(d.amount)
        else:  # CUSTOM
            o.cols[d.col_name] = float(d.amount)
    return [out[e.id] for e in entries]


@router.get("/cost-entries", response_model=list[CostEntryOut])
def list_cost_entries(
    account_code: str,
    scope_code: str | None = None,
    category_code: str | None = None,
    version: str | None = None,
    yyyymm: str | None = None,
    db: Session = Depends(get_db),
) -> list[CostEntryOut]:
    stmt = select(CostEntry).where(CostEntry.account_code == account_code)
    if scope_code is not None:
        stmt = stmt.where(CostEntry.scope_code == scope_code)
    if category_code is not None:
        stmt = stmt.where(CostEntry.category_code == category_code)
    if version is not None:
        stmt = stmt.where(CostEntry.version == version)
    if yyyymm is not None:
        stmt = stmt.where(CostEntry.yyyymm == yyyymm)
    entries = list(db.scalars(stmt.order_by(CostEntry.scope_code, CostEntry.category_code, CostEntry.yyyymm)))
    return _out_rows(db, entries)


def _upsert_value(db: Session, body: CostEntryValueIn) -> tuple[int, int | None]:
    """헤더(cost_entries)를 upsert하고, 그 아래 디테일 한 칸(표준 월 · 분기/연간 요약 · 커스텀 열)을
    upsert한다. commit은 호출한 쪽이 한다. 표준 월 값을 썼으면 (cost_entry_id, year)를, 아니면
    (cost_entry_id, None)을 돌려준다 — 호출부가 month 값을 쓴 헤더만 모아서 분기·연간 재계산을
    한 번에 돌리게 하기 위함(배치에서 매 칸마다 재계산하면 낭비라 마지막에 한 번만)."""
    if body.version not in ("forecast", "actual"):
        raise HTTPException(status_code=400, detail="version은 forecast 또는 actual이어야 합니다.")
    is_month = bool(body.year and body.month and not body.col_name)
    is_summary = bool(body.col_name in QUARTER_PERIOD or body.col_name == "annual") and body.year and not body.month
    is_col = bool(body.col_name and not is_summary and not body.year and not body.month)
    if not (is_month or is_summary or is_col):
        raise HTTPException(
            status_code=400,
            detail="표준 월 값은 year·month를 채우고 col_name을 비워야 하고, "
            "분기/연간 요약 값은 year를 채우고 col_name을 q1~q4|annual 중 하나로 month=0으로 둬야 하고, "
            "커스텀 열 값은 col_name을 채우고(q1~q4·annual 제외) year·month를 비워야 합니다.",
        )
    if is_month and not (1 <= body.month <= 12):
        raise HTTPException(status_code=400, detail="month는 1~12여야 합니다.")

    header_stmt = pg_insert(CostEntry).values(
        account_code=body.account_code,
        scope_code=body.scope_code,
        category_code=body.category_code,
        yyyymm=body.yyyymm,
        version=body.version,
        label=body.label,
        label_en=body.label_en,
        anchor_key=body.anchor_key,
    )
    header_stmt = header_stmt.on_conflict_do_update(
        index_elements=["account_code", "scope_code", "category_code", "yyyymm", "version"],
        set_={
            "label": header_stmt.excluded.label,
            "label_en": header_stmt.excluded.label_en,
            "anchor_key": header_stmt.excluded.anchor_key,
        },
    ).returning(CostEntry.id)
    cost_entry_id = db.scalar(header_stmt)

    if is_month:
        period_type, period, year = "MONTH", body.month, body.year
    elif is_summary:
        if body.col_name == "annual":
            period_type, period, year = "YEAR", 0, body.year
        else:
            period_type, period, year = "QUARTER", QUARTER_PERIOD[body.col_name], body.year
    else:
        period_type, period, year = "CUSTOM", 0, 0

    detail_stmt = pg_insert(CostEntryDetail).values(
        cost_entry_id=cost_entry_id,
        year=year,
        period_type=period_type,
        period=period,
        col_name=body.col_name if is_col else "",
        amount=body.amount,
    )
    detail_stmt = detail_stmt.on_conflict_do_update(
        index_elements=["cost_entry_id", "year", "period_type", "period", "col_name"],
        set_={"amount": detail_stmt.excluded.amount},
    )
    db.execute(detail_stmt)
    return cost_entry_id, (body.year if is_month else None)


@router.put("/cost-entries", response_model=CostEntryOut)
def upsert_cost_entry_value(body: CostEntryValueIn, db: Session = Depends(get_db)) -> CostEntryOut:
    cost_entry_id, month_year = _upsert_value(db, body)
    if month_year is not None:
        _resync_summary(db, cost_entry_id, month_year)
    db.commit()
    entry = db.get(CostEntry, cost_entry_id)
    return _out_rows(db, [entry])[0]


@router.put("/cost-entries/batch")
def upsert_cost_entries_batch(body: list[CostEntryValueIn], db: Session = Depends(get_db)) -> dict[str, int | str]:
    """여러 칸을 한 트랜잭션으로 upsert한다 — 프론트가 한 번에 통째로 플러시하는 화면(해외연구소 등)용.
    표준 월 값을 쓴 헤더는 모든 항목을 다 넣은 뒤 한 번씩만 분기·연간을 재계산한다."""
    ids: set[int] = set()
    to_resync: set[tuple[int, int]] = set()
    for item in body:
        cost_entry_id, month_year = _upsert_value(db, item)
        ids.add(cost_entry_id)
        if month_year is not None:
            to_resync.add((cost_entry_id, month_year))
    for cost_entry_id, year in to_resync:
        _resync_summary(db, cost_entry_id, year)
    db.commit()
    return {"status": "ok", "count": len(body), "headers": len(ids)}


@router.delete("/cost-entries")
def delete_cost_entry(
    account_code: str, scope_code: str, category_code: str, yyyymm: str, version: str, db: Session = Depends(get_db)
) -> dict[str, str]:
    """헤더(cost_entries)를 지운다 — 디테일(cost_entry_details)은 FK CASCADE로 같이 지워진다."""
    entry = db.scalar(
        select(CostEntry).where(
            CostEntry.account_code == account_code,
            CostEntry.scope_code == scope_code,
            CostEntry.category_code == category_code,
            CostEntry.yyyymm == yyyymm,
            CostEntry.version == version,
        )
    )
    if not entry:
        raise HTTPException(status_code=404, detail="해당 cost entry가 없습니다.")
    db.delete(entry)
    db.commit()
    return {"status": "ok"}
