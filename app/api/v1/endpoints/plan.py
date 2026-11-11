from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import ManagementPlanEntry, ManagementPlanEntryDetail, ManagementPlanVersion
from app.schemas.plan import PlanEntryIn, PlanEntryOut, PlanVersionIn, PlanVersionOut, PlanVersionUpdate

router = APIRouter()


def _version_out(v: ManagementPlanVersion) -> PlanVersionOut:
    return PlanVersionOut(
        year=v.year, plan_no=v.plan_no, is_confirmed=v.is_confirmed, is_final=v.is_final, finalized_at=v.finalized_at
    )


def _entry_months_bulk(db: Session, entry_ids: list[int]) -> dict[int, list[float]]:
    """엔트리별 월 값을 한 번의 쿼리로 모아 돌려준다 (호출부가 엔트리마다 따로 조회하지 않도록)."""
    if not entry_ids:
        return {}
    by_entry: dict[int, dict[int, float]] = {}
    for r in db.scalars(
        select(ManagementPlanEntryDetail).where(
            ManagementPlanEntryDetail.plan_entry_id.in_(entry_ids), ManagementPlanEntryDetail.period_type == "MONTH"
        )
    ):
        by_entry.setdefault(r.plan_entry_id, {})[r.period] = float(r.amount)
    return {eid: [by_entry.get(eid, {}).get(m, 0.0) for m in range(1, 13)] for eid in entry_ids}


@router.get("/management-plan/versions", response_model=list[PlanVersionOut])
def list_plan_versions(year: int | None = None, db: Session = Depends(get_db)) -> list[PlanVersionOut]:
    stmt = select(ManagementPlanVersion).order_by(ManagementPlanVersion.year, ManagementPlanVersion.plan_no)
    if year is not None:
        stmt = stmt.where(ManagementPlanVersion.year == year)
    return [_version_out(v) for v in db.scalars(stmt)]


@router.post("/management-plan/versions", response_model=PlanVersionOut)
def create_plan_version(body: PlanVersionIn, db: Session = Depends(get_db)) -> PlanVersionOut:
    if db.get(ManagementPlanVersion, (body.year, body.plan_no)):
        raise HTTPException(status_code=400, detail=f"이미 있는 버전입니다: {body.year}-{body.plan_no}")
    v = ManagementPlanVersion(year=body.year, plan_no=body.plan_no)
    db.add(v)
    db.commit()
    return _version_out(v)


@router.put("/management-plan/versions/{year}/{plan_no}", response_model=PlanVersionOut)
def update_plan_version(
    year: int, plan_no: str, body: PlanVersionUpdate, db: Session = Depends(get_db)
) -> PlanVersionOut:
    """확정/확정취소, 공식버전(is_final) 표시. 확정되는 순간 확정일을 오늘로 찍고,
    확정 취소하면 확정일을 지운다(다시 확정하면 새 날짜로). 한 연도엔 공식버전이 하나뿐이라
    is_final=true로 들어오면 그 연도의 다른 버전들은 서버에서 자동으로 꺼준다."""
    v = db.get(ManagementPlanVersion, (year, plan_no))
    if not v:
        raise HTTPException(status_code=404, detail=f"버전을 찾을 수 없습니다: {year}-{plan_no}")
    if body.is_confirmed and not v.is_confirmed:
        v.finalized_at = date.today()
    elif not body.is_confirmed:
        v.finalized_at = None
    v.is_confirmed = body.is_confirmed
    v.is_final = body.is_final
    if body.is_final:
        db.execute(
            update(ManagementPlanVersion)
            .where(ManagementPlanVersion.year == year, ManagementPlanVersion.plan_no != plan_no)
            .values(is_final=False)
        )
    db.commit()
    return _version_out(v)


@router.delete("/management-plan/versions/{year}/{plan_no}")
def delete_plan_version(year: int, plan_no: str, db: Session = Depends(get_db)) -> dict[str, str]:
    v = db.get(ManagementPlanVersion, (year, plan_no))
    if not v:
        raise HTTPException(status_code=404, detail=f"버전을 찾을 수 없습니다: {year}-{plan_no}")
    if v.is_confirmed:
        raise HTTPException(status_code=400, detail="확정된 버전은 삭제할 수 없습니다. 먼저 확정을 취소하세요.")
    db.execute(
        delete(ManagementPlanEntry).where(
            ManagementPlanEntry.year == year, ManagementPlanEntry.plan_no == plan_no
        )
    )
    db.delete(v)
    db.commit()
    return {"status": "ok"}


@router.get("/management-plan/entries", response_model=list[PlanEntryOut])
def list_plan_entries(
    year: int | None = None,
    plan_no: str | None = None,
    account_key: str | None = None,
    scope_key: str | None = None,
    category_key: str | None = None,
    db: Session = Depends(get_db),
) -> list[PlanEntryOut]:
    stmt = select(ManagementPlanEntry)
    if year is not None:
        stmt = stmt.where(ManagementPlanEntry.year == year)
    if plan_no:
        stmt = stmt.where(ManagementPlanEntry.plan_no == plan_no)
    if account_key:
        stmt = stmt.where(ManagementPlanEntry.account_code == account_key)
    if scope_key:
        stmt = stmt.where(ManagementPlanEntry.scope_code == scope_key)
    if category_key:
        stmt = stmt.where(ManagementPlanEntry.category_code == category_key)
    entries = list(db.scalars(stmt))
    months_by_entry = _entry_months_bulk(db, [e.id for e in entries])
    return [
        PlanEntryOut(
            account_key=e.account_code,
            scope_key=e.scope_code,
            year=e.year,
            plan_no=e.plan_no,
            category_key=e.category_code,
            month=m,
            amount=amount,
        )
        for e in entries
        for m, amount in enumerate(months_by_entry[e.id], start=1)
    ]


@router.put("/management-plan/entries", response_model=PlanEntryOut)
def upsert_plan_entry(body: PlanEntryIn, db: Session = Depends(get_db)) -> PlanEntryOut:
    v = db.get(ManagementPlanVersion, (body.year, body.plan_no))
    if not v:
        raise HTTPException(status_code=400, detail=f"알 수 없는 경영계획 버전입니다: {body.year}-{body.plan_no}")
    if v.is_confirmed:
        raise HTTPException(status_code=400, detail="확정된 버전은 수정할 수 없습니다. 먼저 확정을 취소하세요.")
    stmt = pg_insert(ManagementPlanEntry).values(
        account_code=body.account_key, scope_code=body.scope_key, year=body.year, plan_no=body.plan_no,
        category_code=body.category_key,
    ).on_conflict_do_nothing(
        index_elements=["account_code", "scope_code", "category_code", "year", "plan_no"]
    )
    db.execute(stmt)
    entry_id = db.scalar(
        select(ManagementPlanEntry.id).where(
            ManagementPlanEntry.account_code == body.account_key, ManagementPlanEntry.scope_code == body.scope_key,
            ManagementPlanEntry.category_code == body.category_key, ManagementPlanEntry.year == body.year,
            ManagementPlanEntry.plan_no == body.plan_no,
        )
    )
    s2 = pg_insert(ManagementPlanEntryDetail).values(
        plan_entry_id=entry_id, year=body.year, period_type="MONTH", period=body.month, col_name="", amount=body.amount,
    )
    s2 = s2.on_conflict_do_update(
        index_elements=["plan_entry_id", "year", "period_type", "period", "col_name"],
        set_={"amount": s2.excluded.amount},
    )
    db.execute(s2)
    db.commit()
    return PlanEntryOut(**body.model_dump())
