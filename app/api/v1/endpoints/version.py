from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Account, Version, VersionAccountStatus
from app.schemas.version import (
    VersionAccountStatusOut,
    VersionAccountStatusUpdate,
    VersionIn,
    VersionOut,
    VersionStatusUpdate,
)

router = APIRouter()


def _out(v: Version) -> VersionOut:
    return VersionOut(year=v.year, month=v.month, kind=v.kind, status=v.status, confirmed_at=v.confirmed_at)


@router.get("/versions", response_model=list[VersionOut])
def list_versions(year: int | None = None, db: Session = Depends(get_db)) -> list[VersionOut]:
    """열려 있는(행이 있는) 속보·실적 버전만 돌려준다. 열리지 않은 연월은 행이 없는 것으로 "예정"을
    나타내므로, 화면의 연간 달력은 이 목록에 없는 (연,월,kind) 칸을 예정으로 그리면 된다."""
    stmt = select(Version).order_by(Version.year, Version.month, Version.kind)
    if year is not None:
        stmt = stmt.where(Version.year == year)
    return [_out(v) for v in db.scalars(stmt)]


@router.post("/versions", response_model=VersionOut)
def open_version(body: VersionIn, db: Session = Depends(get_db)) -> VersionOut:
    """그 연월의 속보·실적 버전을 연다(작성중으로 행 생성). 이미 열려 있으면 에러."""
    if body.kind not in ("forecast", "actual"):
        raise HTTPException(status_code=400, detail="kind는 forecast 또는 actual이어야 합니다.")
    if db.scalar(
        select(Version).where(Version.year == body.year, Version.month == body.month, Version.kind == body.kind)
    ):
        raise HTTPException(status_code=400, detail=f"이미 열린 버전입니다: {body.year}-{body.month:02d}-{body.kind}")
    v = Version(year=body.year, month=body.month, kind=body.kind, status="drafting")
    db.add(v)
    db.commit()
    return _out(v)


@router.put("/versions/{year}/{month}/{kind}", response_model=VersionOut)
def update_version_status(
    year: int, month: int, kind: str, body: VersionStatusUpdate, db: Session = Depends(get_db)
) -> VersionOut:
    """작성중 ↔ 완료 전환. 완료되는 순간 확정일을 오늘로 찍고, 완료를 취소하면 확정일을 지운다."""
    v = db.scalar(select(Version).where(Version.year == year, Version.month == month, Version.kind == kind))
    if not v:
        raise HTTPException(status_code=404, detail=f"열린 버전이 없습니다: {year}-{month:02d}-{kind}")
    if body.status not in ("drafting", "done"):
        raise HTTPException(status_code=400, detail="status는 drafting 또는 done이어야 합니다.")
    if body.status == "done" and v.status != "done":
        v.confirmed_at = date.today()
    elif body.status != "done":
        v.confirmed_at = None
    v.status = body.status
    db.commit()
    return _out(v)


@router.delete("/versions/{year}/{month}/{kind}")
def delete_version(year: int, month: int, kind: str, db: Session = Depends(get_db)) -> dict[str, str]:
    v = db.scalar(select(Version).where(Version.year == year, Version.month == month, Version.kind == kind))
    if not v:
        raise HTTPException(status_code=404, detail=f"열린 버전이 없습니다: {year}-{month:02d}-{kind}")
    if v.status == "done":
        raise HTTPException(status_code=400, detail="완료된 버전은 지울 수 없습니다. 먼저 완료를 취소하세요.")
    db.delete(v)
    db.commit()
    return {"status": "ok"}


# ---------- 버전 × 계정의 "계정 확정" ----------
# 버전 전체의 확정(위 update_version_status)과는 다른 층이다 — 계정 담당자가 각자 자기 계정을 먼저
# 확정하고, 전부 완료되면 버전을 확정한다. 행이 없으면 "작성 중"이라서 미리 만들어 두지 않는다.


def _version_or_404(year: int, month: int, kind: str, db: Session) -> Version:
    v = db.scalar(select(Version).where(Version.year == year, Version.month == month, Version.kind == kind))
    if not v:
        raise HTTPException(status_code=404, detail=f"열린 버전이 없습니다: {year}-{month:02d}-{kind}")
    return v


@router.get("/versions/{year}/{month}/{kind}/accounts", response_model=list[VersionAccountStatusOut])
def list_version_accounts(year: int, month: int, kind: str, db: Session = Depends(get_db)) -> list[VersionAccountStatusOut]:
    """그 버전에서 확정 처리된 계정들. 행이 없는 계정은 아직 작성 중이라 목록에 안 나온다."""
    v = _version_or_404(year, month, kind, db)
    rows = db.scalars(
        select(VersionAccountStatus)
        .where(VersionAccountStatus.version_id == v.id)
        .order_by(VersionAccountStatus.account_code)
    )
    return [
        VersionAccountStatusOut(
            account_code=r.account_code, status=r.status, confirmed_at=r.confirmed_at, note=r.note
        )
        for r in rows
    ]


@router.put("/versions/{year}/{month}/{kind}/accounts/{account_code}", response_model=VersionAccountStatusOut)
def update_version_account(
    year: int,
    month: int,
    kind: str,
    account_code: str,
    body: VersionAccountStatusUpdate,
    db: Session = Depends(get_db),
) -> VersionAccountStatusOut:
    """계정 확정 / 확정 취소. 완료로 바꾸는 순간 확정 일시를 찍고, 작성중으로 되돌리면 일시와
    note를 같이 지운다 — 되돌렸는데 "미업로드 2개 포함 확정"이 남아 있으면 그게 지금 상태인 줄
    오해한다(공통 RULE: 완료 후 수정 시 작성중 + 일자 삭제)."""
    if body.status not in ("drafting", "done"):
        raise HTTPException(status_code=400, detail="status는 drafting 또는 done이어야 합니다.")
    if not db.scalar(select(Account).where(Account.key == account_code)):
        raise HTTPException(status_code=404, detail=f"없는 계정입니다: {account_code!r}")
    v = _version_or_404(year, month, kind, db)
    row = db.scalar(
        select(VersionAccountStatus).where(
            VersionAccountStatus.version_id == v.id, VersionAccountStatus.account_code == account_code
        )
    )
    if not row:
        row = VersionAccountStatus(version_id=v.id, account_code=account_code)
        db.add(row)
    done = body.status == "done"
    row.status = body.status
    row.confirmed_at = datetime.now(timezone.utc) if done else None
    row.note = body.note.strip()[:200] if done else ""
    db.commit()
    db.refresh(row)
    return VersionAccountStatusOut(
        account_code=row.account_code, status=row.status, confirmed_at=row.confirmed_at, note=row.note
    )
