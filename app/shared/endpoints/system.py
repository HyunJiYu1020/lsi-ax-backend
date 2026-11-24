from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.accounts.overseas import cost_groups
from app.db.session import get_db
from app.shared.core.enums import CodeGroup
from app.shared.models.system import CommonCode, ErrorLog
from app.shared.schemas.system import CommonCodeIn, CommonCodeOut, ErrorLogOut

router = APIRouter()


def _refresh_overseas_categories(db: Session, group_code: str) -> None:
    """해외연구소 비용항목을 고쳤으면 서버가 들고 있는 계정 정의를 그 자리에서 다시 읽는다 —
    안 하면 코드를 추가·중지해도 재기동할 때까지 서버 집계·검증은 옛 목록으로 돈다(화면은
    공통코드를 직접 받아 쓰기 때문에 바로 바뀌어서, 둘이 서로 다른 계정 목록을 보게 된다)."""
    if group_code == CodeGroup.OVERSEAS_CATEGORY.value:
        cost_groups.reload_from_db(db)


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
    _refresh_overseas_categories(db, body.group_code)
    return CommonCodeOut(**body.model_dump())


@router.delete("/common-codes")
def delete_common_code(group_code: str, code: str, db: Session = Depends(get_db)) -> dict[str, str]:
    row = db.scalar(select(CommonCode).where(CommonCode.group_code == group_code, CommonCode.code == code))
    if row is None:
        raise HTTPException(status_code=404, detail=f"공통코드를 찾을 수 없습니다: {group_code}.{code}")
    db.delete(row)
    db.commit()
    _refresh_overseas_categories(db, group_code)
    return {"status": "ok"}


@router.get("/errors", response_model=list[ErrorLogOut])
def list_errors(
    status_code: int | None = None,
    path: str | None = None,
    log_level: str | None = None,
    process_status: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[ErrorLogOut]:
    """서버 로그 조회. 이제 미처리 500(log_level=ERROR)과 서버가 거절한 4xx(WARNING)가 같이
    쌓이므로, 한쪽만 보려면 log_level로 거른다. 파라미터 이름(status_code·path)은 호출하는
    쪽을 깨지 않으려고 그대로 두고 안에서 표준 컬럼(http_status·api_url)에 맞춘다."""
    stmt = select(ErrorLog).order_by(ErrorLog.occurred_at.desc()).limit(min(limit, 500))
    if status_code is not None:
        stmt = stmt.where(ErrorLog.http_status == status_code)
    if path:
        stmt = stmt.where(ErrorLog.api_url.contains(path))
    if log_level:
        stmt = stmt.where(ErrorLog.log_level == log_level.upper())
    if process_status:
        stmt = stmt.where(ErrorLog.process_status == process_status.upper())
    return [
        ErrorLogOut(
            log_id=e.log_id,
            trace_id=e.trace_id,
            user_id=e.user_id,
            session_id=e.session_id,
            occurred_at=e.occurred_at,
            client_ip=e.client_ip,
            user_agent=e.user_agent,
            api_url=e.api_url,
            http_method=e.http_method,
            params=e.params,
            log_level=e.log_level,
            http_status=e.http_status,
            error_code=e.error_code,
            error_message=e.error_message,
            stack_trace=e.stack_trace,
            server_name=e.server_name,
            response_time=e.response_time,
            process_status=e.process_status,
            handler=e.handler,
        )
        for e in db.scalars(stmt)
    ]
