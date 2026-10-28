from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import CommonCode, ErrorLog
from app.schemas.system import CommonCodeIn, CommonCodeOut, ErrorLogOut

router = APIRouter()


@router.get("/common-codes", response_model=list[CommonCodeOut])
def list_common_codes(
    group_code: str | None = None, is_active: bool | None = None, db: Session = Depends(get_db)
) -> list[CommonCodeOut]:
    stmt = select(CommonCode).order_by(CommonCode.group_code, CommonCode.sort_order)
    if group_code:
        stmt = stmt.where(CommonCode.group_code == group_code)
    if is_active is not None:
        stmt = stmt.where(CommonCode.is_active == is_active)
    return [
        CommonCodeOut(
            group_code=c.group_code,
            code=c.code,
            name=c.name,
            parent_code=c.parent_code,
            sort_order=c.sort_order,
            is_active=c.is_active,
            description=c.description,
        )
        for c in db.scalars(stmt)
    ]


@router.put("/common-codes", response_model=CommonCodeOut)
def upsert_common_code(body: CommonCodeIn, db: Session = Depends(get_db)) -> CommonCodeOut:
    stmt = pg_insert(CommonCode).values(**body.model_dump())
    stmt = stmt.on_conflict_do_update(
        index_elements=["group_code", "code"],
        set_={
            "name": stmt.excluded.name,
            "parent_code": stmt.excluded.parent_code,
            "sort_order": stmt.excluded.sort_order,
            "is_active": stmt.excluded.is_active,
            "description": stmt.excluded.description,
        },
    )
    db.execute(stmt)
    db.commit()
    return CommonCodeOut(**body.model_dump())


@router.delete("/common-codes")
def delete_common_code(group_code: str, code: str, db: Session = Depends(get_db)) -> dict[str, str]:
    row = db.scalar(select(CommonCode).where(CommonCode.group_code == group_code, CommonCode.code == code))
    if row is None:
        raise HTTPException(status_code=404, detail=f"공통코드를 찾을 수 없습니다: {group_code}.{code}")
    db.delete(row)
    db.commit()
    return {"status": "ok"}


@router.get("/errors", response_model=list[ErrorLogOut])
def list_errors(
    status_code: int | None = None,
    path: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[ErrorLogOut]:
    stmt = select(ErrorLog).order_by(ErrorLog.occurred_at.desc()).limit(min(limit, 500))
    if status_code is not None:
        stmt = stmt.where(ErrorLog.status_code == status_code)
    if path:
        stmt = stmt.where(ErrorLog.path.contains(path))
    return [
        ErrorLogOut(
            occurred_at=e.occurred_at,
            method=e.method,
            path=e.path,
            status_code=e.status_code,
            error_type=e.error_type,
            message=e.message,
            traceback=e.traceback,
        )
        for e in db.scalars(stmt)
    ]
