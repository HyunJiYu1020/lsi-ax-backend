"""집계 파생 행·열의 "다음 버전에도 유지" — 승인한 정의를 새 버전으로 복제한다.

파생 행·열은 버전×기준연월 단위로 저장돼서 새 버전을 열면 사라진다. 매달 보는 지표는 따라가야
하고 그 달만 보려고 만든 건 따라가면 안 되므로, 만들 때 한 번 묻고 승인한 것만 복제한다
(overseas_carry_defs). 복제하는 건 계산식뿐이다 — 값은 새 버전 업로드 값으로 다시 계산된다.
"""

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.shared.models.common import CostEntry, ExtraCol
from app.shared.models.generic import CarryDef
from app.accounts.overseas.schemas import CarryForwardIn, CarryForwardResult
from app.accounts.overseas.services.entries import get_or_create_entry
from app.accounts.overseas.services.extra_rows import check_version
from app.accounts.overseas.services.labs import ACCOUNT_KEY, ALL_SCOPE


def carry_forward(db: Session, body: CarryForwardIn) -> CarryForwardResult:
    """유지 승인한 파생 행·열의 정의를 from 버전 → to 버전으로 복제한다(새 버전을 열 때 호출).

    이미 to 버전에 같은 행·열이 있으면 건너뛴다 — 덮어쓰면 새 버전에서 사람이 고친 계산식이
    지난 버전 것으로 되돌아간다. 두 번 호출해도 결과가 같다(새 버전을 여러 번 열어도 안전)."""
    check_version(body.from_version)
    check_version(body.to_version)
    if (body.from_yyyymm, body.from_version) == (body.to_yyyymm, body.to_version):
        raise HTTPException(status_code=400, detail="같은 버전으로는 복제할 수 없습니다.")
    defs = list(db.scalars(select(CarryDef).where(CarryDef.account_key == ACCOUNT_KEY)))
    row_keys = {d.def_key for d in defs if d.kind == "row"}
    col_labels = {d.def_key for d in defs if d.kind == "col"}
    rows = cols = 0
    skipped: list[str] = []

    if row_keys:
        src_rows = db.scalars(
            select(CostEntry).where(
                CostEntry.account_code == ACCOUNT_KEY,
                CostEntry.scope_code == ALL_SCOPE,
                CostEntry.version == body.from_version,
                CostEntry.yyyymm == body.from_yyyymm,
                CostEntry.is_derived.is_(True),
                CostEntry.category_code.in_(row_keys),
            )
        )
        for src in src_rows:
            exists = db.scalar(
                select(CostEntry).where(
                    CostEntry.account_code == ACCOUNT_KEY, CostEntry.scope_code == ALL_SCOPE,
                    CostEntry.version == body.to_version, CostEntry.yyyymm == body.to_yyyymm,
                    CostEntry.category_code == src.category_code,
                )
            )
            if exists is not None:
                skipped.append(src.label or src.category_code)
                continue
            entry_id = get_or_create_entry(db, ALL_SCOPE, body.to_version, src.category_code, body.to_yyyymm)
            db.execute(
                update(CostEntry).where(CostEntry.id == entry_id)
                .values(
                    label=src.label, label_en=src.label_en, anchor_key=src.anchor_key,
                    is_derived=True, formula=src.formula,
                )
            )
            rows += 1

    if col_labels:
        src_cols = db.scalars(
            select(ExtraCol).where(
                ExtraCol.account_key == ACCOUNT_KEY,
                ExtraCol.scope_key == ALL_SCOPE,
                ExtraCol.version == body.from_version,
                ExtraCol.yyyymm == body.from_yyyymm,
                ExtraCol.is_derived.is_(True),
                ExtraCol.label.in_(col_labels),
            )
        )
        for src in src_cols:
            exists = db.scalar(
                select(ExtraCol).where(
                    ExtraCol.account_key == ACCOUNT_KEY, ExtraCol.scope_key == ALL_SCOPE,
                    ExtraCol.version == body.to_version, ExtraCol.yyyymm == body.to_yyyymm,
                    ExtraCol.label == src.label,
                )
            )
            if exists is not None:
                skipped.append(src.label)
                continue
            db.add(
                ExtraCol(
                    account_key=ACCOUNT_KEY, scope_key=ALL_SCOPE, version=body.to_version,
                    yyyymm=body.to_yyyymm, label=src.label, is_derived=True, formula=src.formula,
                )
            )
            cols += 1

    db.commit()
    return CarryForwardResult(rows=rows, cols=cols, skipped=skipped)
