from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Account, AccountProgress, CostEntry, CustomRow, FxRate, Memo, MemoCol, Note, OverseasLab, Upload
from app.schemas.overseas import (
    ActualSnapshot,
    CostEntryIn,
    CostEntryOut,
    CustomRowIn,
    CustomRowOut,
    FileInfo,
    FxRateIn,
    FxRateOut,
    FxVersion,
    LabOut,
    MemoColIn,
    MemoColOut,
    MemoIn,
    MemoOut,
    NoteIn,
    NoteOut,
    OverseasState,
    StepUpdate,
    UploadIn,
    UploadOut,
    VersionData,
)

router = APIRouter()

# cost_entries 등 공용 테이블은 account_key로 계정을 구분한다. 이 라우터는 해외연구소 전용이라 고정.
ACCOUNT_KEY = "overseas"

FX_FIELDS = {
    "end": ("USD", "end"),
    "act": ("USD", "act"),
    "cp": ("USD", "cp"),
    "ilsE": ("ILS", "end"),
    "ilsA": ("ILS", "act"),
    "inrE": ("INR", "end"),
    "inrA": ("INR", "act"),
}


def _overseas_account(db: Session) -> Account:
    acct = db.scalar(select(Account).where(Account.key == ACCOUNT_KEY))
    if not acct:
        raise HTTPException(status_code=500, detail="overseas 계정이 시드되지 않았습니다 (app.db.seed 먼저 실행하세요).")
    return acct


def _labs(db: Session) -> list[OverseasLab]:
    return list(db.scalars(select(OverseasLab)))


def _check_lab_pk(db: Session, pk: str) -> str:
    """scope_key(=연구소 pk)가 overseas_labs에 실재하는지 검증한다. DB가 FK로 못 막아주는 부분이라 여기서 확인."""
    if not db.scalar(select(OverseasLab.id).where(OverseasLab.pk == pk)):
        raise HTTPException(status_code=400, detail=f"알 수 없는 연구소 코드: {pk}")
    return pk


@router.get("/overseas/state", response_model=OverseasState)
def get_overseas_state(db: Session = Depends(get_db)) -> OverseasState:
    acct = _overseas_account(db)
    lab_pks = {lab.pk for lab in _labs(db)}

    progress = db.scalar(select(AccountProgress).where(AccountProgress.account_id == acct.id))
    step = progress.current_step_order if progress else 0

    V: dict[str, VersionData] = {}
    for version in ("cur", "prev", "prior", "plan"):
        V[version] = VersionData(d={}, etc=[0.0] * 12)

    # 연구소가 직접 추가한 행(custom row)의 값은 category_key=row_key로 같이 저장돼 있지만,
    # 여기서는 custom-rows 엔드포인트가 따로 내려주므로 d[pk]에서는 제외한다(두 군데서 중복·drift 방지).
    custom_keys: set[tuple[str, str, str]] = {
        (r.scope_key, r.version, r.row_key)
        for r in db.scalars(select(CustomRow).where(CustomRow.account_key == ACCOUNT_KEY))
    }

    # cur 버전은 "속보"(가장 큰 actual_thru_month)와 "실적"(그보다 작은 actual_thru_month)이
    # 동시에 존재할 수 있다. 어느 쪽이 "속보"인지는 저장된 값 중 가장 큰 actual_thru_month로 판단한다.
    cur_thrus = {
        t
        for t in db.scalars(
            select(CostEntry.actual_thru_month)
            .where(CostEntry.account_key == ACCOUNT_KEY, CostEntry.version == "cur", CostEntry.actual_thru_month > 0)
            .distinct()
        )
    }
    basis_month = max(cur_thrus) if cur_thrus else 0

    actualV: dict[str, ActualSnapshot] = {}

    for entry in db.scalars(select(CostEntry).where(CostEntry.account_key == ACCOUNT_KEY)):
        pk = entry.scope_key
        if pk not in lab_pks or entry.version not in V:
            continue
        if (pk, entry.version, entry.category_key) in custom_keys:
            continue
        months = _entry_months(entry)
        if entry.version == "cur" and entry.actual_thru_month > 0 and entry.actual_thru_month != basis_month:
            snap = actualV.setdefault(
                entry.version, ActualSnapshot(thru_month=entry.actual_thru_month, d={}, etc=[0.0] * 12)
            )
            if pk == "_ETC":
                snap.etc = months
            else:
                snap.d.setdefault(pk, {})[entry.category_key] = months
            continue
        if pk == "_ETC":
            V[entry.version].etc = months
            continue
        V[entry.version].d.setdefault(pk, {})[entry.category_key] = months

    files: dict[str, dict[str, FileInfo]] = {v: {} for v in ("cur", "prev", "prior", "plan")}
    latest_upload: dict[tuple[str, str], Upload] = {}
    for up in db.scalars(
        select(Upload).where(Upload.account_key == ACCOUNT_KEY).order_by(Upload.uploaded_at)
    ):
        if up.scope_key not in lab_pks or up.version not in files:
            continue
        latest_upload[(up.version, up.scope_key)] = up  # 마지막에 덮어써서 최신 것만 남음
    for (version, pk), up in latest_upload.items():
        files[version][pk] = FileInfo(file=up.filename, at=up.uploaded_at.strftime("%m.%d %H:%M"))

    note: dict[str, dict[str, str]] = {}
    for n in db.scalars(select(Note).where(Note.account_key == ACCOUNT_KEY)):
        key = f"{n.base_version}|{n.period}|{n.scope}"
        note.setdefault(key, {})[n.category_key] = n.note_text

    fx: dict[str, FxVersion] = {}
    fx_raw: dict[str, dict[str, list[float]]] = {
        v: {f: [0.0] * 12 for f in FX_FIELDS} for v in ("cur", "prev", "prior", "plan")
    }
    rate_to_field = {(cur, rt): f for f, (cur, rt) in FX_FIELDS.items()}
    for r in db.scalars(select(FxRate)):
        f = rate_to_field.get((r.currency, r.rate_type))
        if f is None or r.version not in fx_raw:
            continue
        fx_raw[r.version][f] = _fx_months(r)
    for version, fields in fx_raw.items():
        fx[version] = FxVersion(**fields)

    return OverseasState(step=step, basis_month=basis_month, V=V, actualV=actualV, files=files, note=note, fx=fx)


@router.put("/overseas/state")
def save_overseas_state(state: OverseasState, db: Session = Depends(get_db)) -> dict[str, str]:
    acct = _overseas_account(db)
    lab_pks = {lab.pk for lab in _labs(db)}

    unknown_pks: set[str] = set()
    for version, vd in state.V.items():
        for pk in vd.d:
            if pk not in lab_pks:
                unknown_pks.add(pk)
    for version, pk_files in state.files.items():
        unknown_pks.update(pk for pk in pk_files if pk not in lab_pks)
    if unknown_pks:
        raise HTTPException(status_code=400, detail=f"알 수 없는 연구소 코드: {sorted(unknown_pks)}")

    # 1) 비용 데이터 (연구소별 + etc) — "속보" 스냅샷. cur만 basis_month로 actual_thru_month를 채운다.
    for version, vd in state.V.items():
        thru = state.basis_month if version == "cur" else 0
        for pk, cats in vd.d.items():
            for category_key, months in cats.items():
                _upsert_cost_entry_row(db, pk, version, category_key, months, actual_thru_month=thru)
        if "_ETC" in lab_pks:
            _upsert_cost_entry_row(db, "_ETC", version, "etc", vd.etc, actual_thru_month=thru)

    # 1-1) "N월실적" 헤더로 업로드된 확정 실적 스냅샷 (있을 때만, 지금은 cur만 사용)
    for version, snap in state.actualV.items():
        for pk, cats in snap.d.items():
            for category_key, months in cats.items():
                _upsert_cost_entry_row(db, pk, version, category_key, months, actual_thru_month=snap.thru_month)
        if "_ETC" in lab_pks:
            _upsert_cost_entry_row(db, "_ETC", version, "etc", snap.etc, actual_thru_month=snap.thru_month)

    # 2) 업로드 이력 (연구소×버전당 최신 파일명만 의미 있으므로, 바뀐 것만 새로 기록)
    for version, pk_files in state.files.items():
        for pk, info in pk_files.items():
            last = db.scalar(
                select(Upload)
                .where(Upload.account_key == ACCOUNT_KEY, Upload.scope_key == pk, Upload.version == version)
                .order_by(Upload.uploaded_at.desc())
            )
            if last is None or last.filename != info.file:
                db.add(Upload(account_key=ACCOUNT_KEY, scope_key=pk, version=version, filename=info.file))

    # 3) 증감 사유 메모
    for key, cats in state.note.items():
        parts = key.split("|")
        if len(parts) != 3:
            continue
        base_version, period, scope = parts
        for category_key, text in cats.items():
            _upsert_note(db, base_version, period, scope, category_key, text)

    # 4) 환율
    for version, fxv in state.fx.items():
        for field, (currency, rate_type) in FX_FIELDS.items():
            _upsert_fx_row(db, version, currency, rate_type, getattr(fxv, field))

    # 5) 진행 단계
    progress = db.scalar(select(AccountProgress).where(AccountProgress.account_id == acct.id))
    if progress is None:
        db.add(AccountProgress(account_id=acct.id, current_step_order=state.step))
    else:
        progress.current_step_order = state.step

    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"저장 실패 (제약조건 위반): {e.orig}") from e

    return {"status": "ok"}


@router.get("/overseas/step")
def get_overseas_step(db: Session = Depends(get_db)) -> StepUpdate:
    acct = _overseas_account(db)
    progress = db.scalar(select(AccountProgress).where(AccountProgress.account_id == acct.id))
    return StepUpdate(step=progress.current_step_order if progress else 0)


@router.put("/overseas/step")
def update_overseas_step(body: StepUpdate, db: Session = Depends(get_db)) -> StepUpdate:
    acct = _overseas_account(db)
    progress = db.scalar(select(AccountProgress).where(AccountProgress.account_id == acct.id))
    if progress is None:
        progress = AccountProgress(account_id=acct.id, current_step_order=body.step)
        db.add(progress)
    else:
        progress.current_step_order = body.step
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(
            status_code=400, detail=f"해외연구소에 정의되지 않은 단계입니다: {body.step}"
        ) from e
    return StepUpdate(step=body.step)


# ---------- 세분화 CRUD (MCP 등 외부 클라이언트용) ----------
# scope_key는 연구소 pk를 그대로 쓴다(공용 테이블이라 overseas_labs로 FK는 못 걸고, _check_lab_pk로 검증).


@router.get("/overseas/labs", response_model=list[LabOut])
def list_labs(db: Session = Depends(get_db)) -> list[LabOut]:
    return [
        LabOut(pk=lab.pk, lab_code=lab.lab_code, part_code=lab.part_code, name=lab.name, country=lab.country)
        for lab in _labs(db)
        if lab.pk != "_ETC"
    ]


@router.get("/overseas/cost-entries", response_model=list[CostEntryOut])
def list_cost_entries(
    version: str | None = None,
    lab_pk: str | None = None,
    category_key: str | None = None,
    actual_thru_month: int | None = None,
    db: Session = Depends(get_db),
) -> list[CostEntryOut]:
    stmt = select(CostEntry).where(CostEntry.account_key == ACCOUNT_KEY)
    if version:
        stmt = stmt.where(CostEntry.version == version)
    if lab_pk:
        stmt = stmt.where(CostEntry.scope_key == _check_lab_pk(db, lab_pk))
    if category_key:
        stmt = stmt.where(CostEntry.category_key == category_key)
    if actual_thru_month is not None:
        stmt = stmt.where(CostEntry.actual_thru_month == actual_thru_month)
    return [
        CostEntryOut(
            lab_pk=e.scope_key,
            version=e.version,
            category_key=e.category_key,
            month=m,
            amount=amount,
            actual_thru_month=e.actual_thru_month,
        )
        for e in db.scalars(stmt)
        for m, amount in enumerate(_entry_months(e), start=1)
    ]


@router.put("/overseas/cost-entries", response_model=CostEntryOut)
def upsert_cost_entry(body: CostEntryIn, db: Session = Depends(get_db)) -> CostEntryOut:
    _check_lab_pk(db, body.lab_pk)
    _upsert_cost_entry(
        db, body.lab_pk, body.version, body.category_key, body.month, body.amount, body.actual_thru_month
    )
    db.commit()
    return CostEntryOut(**body.model_dump())


@router.get("/overseas/uploads", response_model=list[UploadOut])
def list_uploads(
    version: str | None = None,
    lab_pk: str | None = None,
    db: Session = Depends(get_db),
) -> list[UploadOut]:
    stmt = select(Upload).where(Upload.account_key == ACCOUNT_KEY).order_by(Upload.uploaded_at.desc())
    if version:
        stmt = stmt.where(Upload.version == version)
    if lab_pk:
        stmt = stmt.where(Upload.scope_key == _check_lab_pk(db, lab_pk))
    return [
        UploadOut(lab_pk=u.scope_key, version=u.version, filename=u.filename, uploaded_at=u.uploaded_at)
        for u in db.scalars(stmt)
    ]


@router.post("/overseas/uploads", response_model=UploadOut)
def record_upload(body: UploadIn, db: Session = Depends(get_db)) -> UploadOut:
    _check_lab_pk(db, body.lab_pk)
    up = Upload(account_key=ACCOUNT_KEY, scope_key=body.lab_pk, version=body.version, filename=body.filename)
    db.add(up)
    db.commit()
    db.refresh(up)
    return UploadOut(lab_pk=body.lab_pk, version=body.version, filename=body.filename, uploaded_at=up.uploaded_at)


@router.get("/overseas/notes", response_model=list[NoteOut])
def list_notes(
    base_version: str | None = None,
    period: str | None = None,
    lab_scope: str | None = None,
    db: Session = Depends(get_db),
) -> list[NoteOut]:
    stmt = select(Note).where(Note.account_key == ACCOUNT_KEY)
    if base_version:
        stmt = stmt.where(Note.base_version == base_version)
    if period:
        stmt = stmt.where(Note.period == period)
    if lab_scope:
        stmt = stmt.where(Note.scope == lab_scope)
    return [
        NoteOut(base_version=n.base_version, period=n.period, lab_scope=n.scope, category_key=n.category_key, note_text=n.note_text)
        for n in db.scalars(stmt)
    ]


@router.put("/overseas/notes", response_model=NoteOut)
def upsert_note(body: NoteIn, db: Session = Depends(get_db)) -> NoteOut:
    _upsert_note(db, body.base_version, body.period, body.lab_scope, body.category_key, body.note_text)
    db.commit()
    return NoteOut(**body.model_dump())


@router.get("/overseas/fx-rates", response_model=list[FxRateOut])
def list_fx_rates(version: str | None = None, db: Session = Depends(get_db)) -> list[FxRateOut]:
    stmt = select(FxRate)
    if version:
        stmt = stmt.where(FxRate.version == version)
    return [
        FxRateOut(version=r.version, currency=r.currency, rate_type=r.rate_type, month=m, value=value)
        for r in db.scalars(stmt)
        for m, value in enumerate(_fx_months(r), start=1)
    ]


@router.put("/overseas/fx-rates", response_model=FxRateOut)
def upsert_fx_rate(body: FxRateIn, db: Session = Depends(get_db)) -> FxRateOut:
    _upsert_fx(db, body.version, body.currency, body.rate_type, body.month, body.value)
    db.commit()
    return FxRateOut(**body.model_dump())


# ---------- 연구소가 직접 추가한 행 · 메모 열 · 메모 셀 ----------
# 행의 월별 값 자체는 cost_entries에 category_key=row_key로 같이 저장된다
# (기존 cost-entries CRUD를 그대로 재사용 — 저장 경로를 두 벌 두지 않기 위함).


@router.get("/overseas/custom-rows", response_model=list[CustomRowOut])
def list_custom_rows(
    version: str | None = None, lab_pk: str | None = None, db: Session = Depends(get_db)
) -> list[CustomRowOut]:
    stmt = select(CustomRow).where(CustomRow.account_key == ACCOUNT_KEY)
    if version:
        stmt = stmt.where(CustomRow.version == version)
    if lab_pk:
        stmt = stmt.where(CustomRow.scope_key == _check_lab_pk(db, lab_pk))
    rows = list(db.scalars(stmt))

    out: list[CustomRowOut] = []
    for row in rows:
        entry = db.scalar(
            select(CostEntry).where(
                CostEntry.account_key == ACCOUNT_KEY,
                CostEntry.scope_key == row.scope_key,
                CostEntry.version == row.version,
                CostEntry.category_key == row.row_key,
            )
        )
        v = _entry_months(entry) if entry else [0.0] * 12
        out.append(CustomRowOut(lab_pk=row.scope_key, version=row.version, row_key=row.row_key, label=row.label, v=v))
    return out


@router.put("/overseas/custom-rows", response_model=CustomRowOut)
def upsert_custom_row(body: CustomRowIn, db: Session = Depends(get_db)) -> CustomRowOut:
    if len(body.v) != 12:
        raise HTTPException(status_code=400, detail="v는 12개월치 값이어야 합니다.")
    _check_lab_pk(db, body.lab_pk)
    stmt = pg_insert(CustomRow).values(
        account_key=ACCOUNT_KEY, scope_key=body.lab_pk, version=body.version, row_key=body.row_key, label=body.label
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_key", "scope_key", "version", "row_key"], set_={"label": stmt.excluded.label}
    )
    db.execute(stmt)
    _upsert_cost_entry_row(db, body.lab_pk, body.version, body.row_key, body.v)
    db.commit()
    return CustomRowOut(**body.model_dump())


@router.delete("/overseas/custom-rows")
def delete_custom_row(lab_pk: str, version: str, row_key: str, db: Session = Depends(get_db)) -> dict[str, str]:
    _check_lab_pk(db, lab_pk)
    db.execute(
        delete(CustomRow).where(
            CustomRow.account_key == ACCOUNT_KEY,
            CustomRow.scope_key == lab_pk,
            CustomRow.version == version,
            CustomRow.row_key == row_key,
        )
    )
    db.execute(
        delete(CostEntry).where(
            CostEntry.account_key == ACCOUNT_KEY,
            CostEntry.scope_key == lab_pk,
            CostEntry.version == version,
            CostEntry.category_key == row_key,
        )
    )
    db.execute(
        delete(Memo).where(
            Memo.account_key == ACCOUNT_KEY, Memo.scope_key == lab_pk, Memo.version == version, Memo.row_key == row_key
        )
    )
    db.commit()
    return {"status": "ok"}


@router.get("/overseas/memo-cols", response_model=list[MemoColOut])
def list_memo_cols(
    version: str | None = None, lab_pk: str | None = None, db: Session = Depends(get_db)
) -> list[MemoColOut]:
    stmt = select(MemoCol).where(MemoCol.account_key == ACCOUNT_KEY)
    if version:
        stmt = stmt.where(MemoCol.version == version)
    if lab_pk:
        stmt = stmt.where(MemoCol.scope_key == _check_lab_pk(db, lab_pk))
    return [MemoColOut(lab_pk=c.scope_key, version=c.version, col_key=c.col_key, label=c.label) for c in db.scalars(stmt)]


@router.put("/overseas/memo-cols", response_model=MemoColOut)
def upsert_memo_col(body: MemoColIn, db: Session = Depends(get_db)) -> MemoColOut:
    _check_lab_pk(db, body.lab_pk)
    stmt = pg_insert(MemoCol).values(
        account_key=ACCOUNT_KEY, scope_key=body.lab_pk, version=body.version, col_key=body.col_key, label=body.label
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_key", "scope_key", "version", "col_key"], set_={"label": stmt.excluded.label}
    )
    db.execute(stmt)
    db.commit()
    return MemoColOut(**body.model_dump())


@router.delete("/overseas/memo-cols")
def delete_memo_col(lab_pk: str, version: str, col_key: str, db: Session = Depends(get_db)) -> dict[str, str]:
    _check_lab_pk(db, lab_pk)
    db.execute(
        delete(MemoCol).where(
            MemoCol.account_key == ACCOUNT_KEY,
            MemoCol.scope_key == lab_pk,
            MemoCol.version == version,
            MemoCol.col_key == col_key,
        )
    )
    db.execute(
        delete(Memo).where(
            Memo.account_key == ACCOUNT_KEY, Memo.scope_key == lab_pk, Memo.version == version, Memo.col_key == col_key
        )
    )
    db.commit()
    return {"status": "ok"}


@router.get("/overseas/memos", response_model=list[MemoOut])
def list_memos(
    version: str | None = None, lab_pk: str | None = None, db: Session = Depends(get_db)
) -> list[MemoOut]:
    stmt = select(Memo).where(Memo.account_key == ACCOUNT_KEY)
    if version:
        stmt = stmt.where(Memo.version == version)
    if lab_pk:
        stmt = stmt.where(Memo.scope_key == _check_lab_pk(db, lab_pk))
    return [MemoOut(lab_pk=m.scope_key, version=m.version, row_key=m.row_key, col_key=m.col_key, text=m.text) for m in db.scalars(stmt)]


@router.put("/overseas/memos", response_model=MemoOut)
def upsert_memo(body: MemoIn, db: Session = Depends(get_db)) -> MemoOut:
    _check_lab_pk(db, body.lab_pk)
    stmt = pg_insert(Memo).values(
        account_key=ACCOUNT_KEY,
        scope_key=body.lab_pk,
        version=body.version,
        row_key=body.row_key,
        col_key=body.col_key,
        text=body.text,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_key", "scope_key", "version", "row_key", "col_key"], set_={"text": stmt.excluded.text}
    )
    db.execute(stmt)
    db.commit()
    return MemoOut(**body.model_dump())


def _entry_months(entry: CostEntry) -> list[float]:
    return [float(getattr(entry, f"month{m}")) for m in range(1, 13)]


def _fx_months(rate: FxRate) -> list[float]:
    return [float(getattr(rate, f"month{m}")) for m in range(1, 13)]


def _upsert_cost_entry_row(
    db: Session, lab_pk: str, version: str, category_key: str, months: list[float], actual_thru_month: int = 0
) -> None:
    """카테고리 하나의 12개월치를 한 행으로 upsert한다 (실제 접근 패턴: 업로드·저장은 항상 1년치가 같이 옴).
    actual_thru_month는 cur에서 같은 카테고리가 실적/속보로 동시에 존재할 때 구분용 (그 외엔 0)."""
    month_cols = {f"month{m}": v for m, v in enumerate(months, start=1)}
    stmt = pg_insert(CostEntry).values(
        account_key=ACCOUNT_KEY,
        scope_key=lab_pk,
        version=version,
        category_key=category_key,
        actual_thru_month=actual_thru_month,
        **month_cols,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_key", "scope_key", "version", "category_key", "actual_thru_month"],
        set_={**{k: getattr(stmt.excluded, k) for k in month_cols}, "updated_at": datetime.utcnow()},
    )
    db.execute(stmt)


def _upsert_cost_entry(
    db: Session, lab_pk: str, version: str, category_key: str, month: int, amount: float, actual_thru_month: int = 0
) -> None:
    """세분화 CRUD(단일 셀)용. 한 달치만 바뀌므로 그 컬럼만 업데이트한다."""
    col = f"month{month}"
    stmt = pg_insert(CostEntry).values(
        account_key=ACCOUNT_KEY,
        scope_key=lab_pk,
        version=version,
        category_key=category_key,
        actual_thru_month=actual_thru_month,
        **{col: amount},
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_key", "scope_key", "version", "category_key", "actual_thru_month"],
        set_={col: getattr(stmt.excluded, col), "updated_at": datetime.utcnow()},
    )
    db.execute(stmt)


def _upsert_note(db: Session, base_version: str, period: str, scope: str, category_key: str, text: str) -> None:
    stmt = pg_insert(Note).values(
        account_key=ACCOUNT_KEY, base_version=base_version, period=period, scope=scope, category_key=category_key, note_text=text
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_key", "base_version", "period", "scope", "category_key"],
        set_={"note_text": stmt.excluded.note_text, "updated_at": datetime.utcnow()},
    )
    db.execute(stmt)


def _upsert_fx_row(db: Session, version: str, currency: str, rate_type: str, months: list[float]) -> None:
    month_cols = {f"month{m}": v for m, v in enumerate(months, start=1)}
    stmt = pg_insert(FxRate).values(version=version, currency=currency, rate_type=rate_type, **month_cols)
    stmt = stmt.on_conflict_do_update(
        index_elements=["version", "currency", "rate_type"],
        set_={k: getattr(stmt.excluded, k) for k in month_cols},
    )
    db.execute(stmt)


def _upsert_fx(db: Session, version: str, currency: str, rate_type: str, month: int, value: float) -> None:
    """세분화 CRUD(단일 셀)용. 한 달치만 바뀌므로 그 컬럼만 업데이트한다."""
    col = f"month{month}"
    stmt = pg_insert(FxRate).values(version=version, currency=currency, rate_type=rate_type, **{col: value})
    stmt = stmt.on_conflict_do_update(
        index_elements=["version", "currency", "rate_type"],
        set_={col: getattr(stmt.excluded, col)},
    )
    db.execute(stmt)
