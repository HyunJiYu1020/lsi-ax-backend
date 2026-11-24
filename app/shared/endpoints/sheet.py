"""구조 미정 시트의 행 조회·저장 — 19개 계정 공용(`sheet_rows`).

위탁용역비·생소비 편성값(계약 한 줄 = 40여 칸)이 여태 브라우저에만 있었다. 그러면 담당자가
바뀌거나 브라우저를 비우면 그 달 편성안이 사라지고, 챗봇·집계는 그 계정 금액을 아예 볼 수 없다.
열 구성이 아직 바뀌는 중이라 전용 테이블 대신 JSON 행으로 받는다 — 그 선택의 이유와 조건은
`app/shared/schemas/sheet.py`와 `sheet_rows` 모델 주석에 적어 뒀다.

해외연구소 전용 경로(`/overseas/sheet-rows`)와 같은 테이블을 쓰지만 가른 이유: 그쪽은 행 단위
upsert이고 범위가 연구소(lab_pk)로 검증된다. 여기는 **표 단위 교체**이고 범위가 계정마다 다르다.
한 엔드포인트로 합치면 "연구소 검증을 건너뛰는 분기"가 생겨서, 해외연구소 행이 검증 없이 들어올
구멍이 된다.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.shared.models.account import Account
from app.shared.models.common import SheetRow
from app.shared.schemas.sheet import SheetBundleOut, SheetRowOut, SheetRowsIn, SheetRowsOut

router = APIRouter()

# 화면이 쓰는 버전 축. cost_entries의 forecast/actual과 다르다 — 이 표들은 "업데이트 버전 ·
# 전월 편성값 · 전년 실적 · 경영계획"을 나란히 올려놓고 비교하는 화면이라, 그 네 칸이 곧 버전이다.
SHEET_VERSIONS = ("cur", "prev", "prior", "plan")


def _check(db: Session, account_key: str, version: str) -> None:
    if version not in SHEET_VERSIONS:
        raise HTTPException(400, f"version은 {' | '.join(SHEET_VERSIONS)} 중 하나여야 합니다: {version!r}")
    if not db.scalar(select(Account.key).where(Account.key == account_key)):
        raise HTTPException(404, f"그런 계정이 없습니다: {account_key!r}")


def _rows(db: Session, account_key: str, scope_key: str, version: str, sheet_key: str) -> list[SheetRow]:
    return list(
        db.scalars(
            select(SheetRow)
            .where(
                SheetRow.account_key == account_key,
                SheetRow.scope_key == scope_key,
                SheetRow.version == version,
                SheetRow.sheet_key == sheet_key,
            )
            .order_by(SheetRow.sort_order, SheetRow.id)
        )
    )


@router.get("/sheet-rows", response_model=SheetRowsOut)
def get_sheet_rows(
    account_key: str,
    version: str,
    sheet_key: str,
    scope_key: str = "ALL",
    db: Session = Depends(get_db),
) -> SheetRowsOut:
    """표 하나를 순서 그대로 돌려준다. 행이 없으면 빈 목록이다 — 화면은 그때 자기 예시 데이터를
    지우지 말고 그대로 쓴다(빈 표를 꽂으면 "서버에 붙였더니 편성안이 사라졌다"가 된다)."""
    _check(db, account_key, version)
    return SheetRowsOut(
        account_key=account_key, scope_key=scope_key, version=version, sheet_key=sheet_key,
        rows=[
            SheetRowOut(row_key=r.row_key, sort_order=r.sort_order, data=r.data or {})
            for r in _rows(db, account_key, scope_key, version, sheet_key)
        ],
    )


@router.put("/sheet-rows", response_model=SheetRowsOut)
def put_sheet_rows(body: SheetRowsIn, db: Session = Depends(get_db)) -> SheetRowsOut:
    """표 하나를 통째로 교체한다(업로드 = 전체 교체, 표 수정 후 [저장]도 전체 교체).

    `row_key`가 비어 있으면 순서 번호로 채운다. 같은 키가 두 번 오면 400으로 막는다 — 테이블의
    유니크 키라서 조용히 한 줄을 버리면 담당자는 저장됐다고 믿고 그 계약 금액이 사라진다."""
    _check(db, body.account_key, body.version)
    made, seen = [], {}
    for i, r in enumerate(body.rows):
        key = (r.row_key or "").strip() or f"r{i + 1:04d}"
        if key in seen:
            raise HTTPException(400, f"{i + 1}행: 행 키 {key!r}가 {seen[key] + 1}행과 중복됩니다.")
        seen[key] = i
        made.append(
            SheetRow(
                account_key=body.account_key, scope_key=body.scope_key, version=body.version,
                sheet_key=body.sheet_key, row_key=key,
                sort_order=r.sort_order if r.sort_order is not None else i,
                data=r.data or {},
            )
        )
    db.execute(
        delete(SheetRow).where(
            SheetRow.account_key == body.account_key,
            SheetRow.scope_key == body.scope_key,
            SheetRow.version == body.version,
            SheetRow.sheet_key == body.sheet_key,
        )
    )
    db.flush()
    db.add_all(made)
    db.commit()
    return get_sheet_rows(
        account_key=body.account_key, version=body.version, sheet_key=body.sheet_key,
        scope_key=body.scope_key, db=db,
    )


@router.get("/sheet-rows/bundle", response_model=SheetBundleOut)
def get_sheet_bundle(
    account_key: str = Query(..., description="계정 key"),
    scope_key: str = "ALL",
    version: str | None = None,  # 한 버전만 받을 때. 비우면 모든 버전
    db: Session = Depends(get_db),
) -> SheetBundleOut:
    """그 계정의 표를 **한 번에** 받는다. 키는 `"<version>|<sheet_key>"`.

    화면 하나가 표 열 개 넘게 쓰기 때문이다(위탁용역비 4버전 × 2시트, 감가상각비 원천 7표 + 상태).
    표마다 따로 부르면 페이지 로드가 요청 수십 개를 동시에 던져 서버 커넥션 풀이 마른다."""
    if not db.scalar(select(Account.key).where(Account.key == account_key)):
        raise HTTPException(404, f"그런 계정이 없습니다: {account_key!r}")
    if version is not None and version not in SHEET_VERSIONS:
        raise HTTPException(400, f"version은 {' | '.join(SHEET_VERSIONS)} 중 하나여야 합니다: {version!r}")
    stmt = (
        select(SheetRow)
        .where(SheetRow.account_key == account_key, SheetRow.scope_key == scope_key)
        .order_by(SheetRow.version, SheetRow.sheet_key, SheetRow.sort_order, SheetRow.id)
    )
    if version is not None:
        stmt = stmt.where(SheetRow.version == version)
    sheets: dict[str, list[SheetRowOut]] = {}
    for r in db.scalars(stmt):
        sheets.setdefault(f"{r.version}|{r.sheet_key}", []).append(
            SheetRowOut(row_key=r.row_key, sort_order=r.sort_order, data=r.data or {})
        )
    return SheetBundleOut(account_key=account_key, scope_key=scope_key, sheets=sheets)


@router.get("/sheet-rows/index")
def list_sheet_index(
    account_key: str = Query(..., description="계정 key"),
    scope_key: str = "ALL",
    db: Session = Depends(get_db),
) -> list[dict[str, str | int]]:
    """그 계정에 어떤 표가 저장돼 있는지(버전 × 시트 × 행 수). 화면이 뭘 받아야 할지 먼저 알아야
    하고, "서버에 아직 아무것도 없다"와 "받았는데 비어 있다"를 가릴 수 있어야 한다."""
    if not db.scalar(select(Account.key).where(Account.key == account_key)):
        raise HTTPException(404, f"그런 계정이 없습니다: {account_key!r}")
    rows = db.execute(
        select(SheetRow.version, SheetRow.sheet_key, func.count(SheetRow.id))
        .where(SheetRow.account_key == account_key, SheetRow.scope_key == scope_key)
        .group_by(SheetRow.version, SheetRow.sheet_key)
        .order_by(SheetRow.version, SheetRow.sheet_key)
    ).all()
    return [{"version": v, "sheet_key": s, "rows": n} for v, s, n in rows]


@router.delete("/sheet-rows")
def delete_sheet_rows(
    account_key: str, version: str, sheet_key: str, scope_key: str = "ALL", db: Session = Depends(get_db)
) -> dict[str, int | str]:
    """표 하나를 비운다(화면에서 전체 삭제할 때)."""
    _check(db, account_key, version)
    n = db.execute(
        delete(SheetRow).where(
            SheetRow.account_key == account_key,
            SheetRow.scope_key == scope_key,
            SheetRow.version == version,
            SheetRow.sheet_key == sheet_key,
        )
    ).rowcount
    db.commit()
    return {"deleted": n, "account_key": account_key, "version": version, "sheet_key": sheet_key}
