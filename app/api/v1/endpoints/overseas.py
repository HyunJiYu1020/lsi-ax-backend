import uuid
from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.storage import upload_bytes
from app.db.session import get_db
from app.models import (
    Account,
    AccountProgress,
    CommonCode,
    FxRate,
    Note,
    OverseasCostEntry,
    OverseasCpRow,
    OverseasLab,
    OverseasLabSheet,
    OverseasReasonRow,
    SheetRow,
    Upload,
)
from app.core.enums import CodeGroup
from app.schemas.overseas import (
    ActualSnapshot,
    CostEntryIn,
    CostEntryOut,
    CpRow,
    CustomRowIn,
    CustomRowOut,
    ExtraColOut,
    ExtraValueIn,
    ExtraValueOut,
    FileInfo,
    FxRateIn,
    FxRateOut,
    FxVersion,
    LabIn,
    LabOut,
    LabSheetOut,
    NoteIn,
    NoteOut,
    OverseasState,
    ReasonRow,
    SheetRowIn,
    SheetRowOut,
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


def _current_user_id() -> int | None:
    """로그인 인증이 아직 없어 항상 None을 돌려준다 — 인증이 붙으면 이 함수 하나만 요청의 로그인
    사용자 id를 읽어오도록 바꾸면, 호출부(created_by_id/updated_by_id를 쓰는 곳)는 그대로 둬도 된다."""
    return None


def _year_for(version: str, basis_year: int) -> int:
    """편성 기준연도(basis_year)로부터 이 version이 가리키는 실제 연도를 계산한다.
    cur · prev · plan은 기준연도 그대로, prior(전년 실적)만 기준연도-1."""
    return basis_year - 1 if version == "prior" else basis_year


def _resolve_thru(db: Session, version: str | None, actual_thru_month: int) -> int:
    """추가 행·추가 열(custom-rows/extra-cols/cost-extras)의 actual_thru_month 파라미터를 보정한다.
    cur가 아닌 버전은 항상 0이 맞아 그대로 둔다. cur에서 0(파라미터 기본값, MCP 도구처럼 호출자가
    이 값을 아예 안 보내는 경우 포함)은 "지정 안 함"과 같으므로, 실제 기준월(cur 행들의 최댓값)로
    바꿔준다 — 안 그러면 9월 기준월에 넣은 추가 행이 그리드가 찾는 9월 자리에 없어서 안 보인다."""
    if version != "cur" or actual_thru_month != 0:
        return actual_thru_month
    basis = db.scalar(
        select(func.max(OverseasCostEntry.actual_thru_month)).where(
            OverseasCostEntry.version == "cur", OverseasCostEntry.actual_thru_month > 0
        )
    )
    return basis or 0


def _quarters_and_annual(months: list[float]) -> dict[str, float]:
    return {
        "q1": sum(months[0:3]),
        "q2": sum(months[3:6]),
        "q3": sum(months[6:9]),
        "q4": sum(months[9:12]),
        "annual": sum(months),
    }


def _get_or_create_entry(
    db: Session, scope_key: str, version: str, category_key: str, year: int, actual_thru_month: int = 0
) -> int:
    """비용 행(overseas_cost_entries)을 scope_key×version×category_key×actual_thru_month×year로 upsert하고
    id를 돌려준다. year는 항상 있어야 한다(금액이든 custom_fields만 있는 행이든 예외 없음) — 호출하는
    쪽(저장 로직)이 항상 같은 year를 넘기므로, 금액이 먼저 저장되든 커스텀 열이 먼저 저장되든 같은
    행으로 모인다."""
    stmt = pg_insert(OverseasCostEntry).values(
        scope_key=scope_key, version=version, category_key=category_key, actual_thru_month=actual_thru_month, year=year
    ).on_conflict_do_nothing(index_elements=["scope_key", "version", "category_key", "actual_thru_month", "year"])
    db.execute(stmt)
    return db.scalar(
        select(OverseasCostEntry.id).where(
            OverseasCostEntry.scope_key == scope_key,
            OverseasCostEntry.version == version,
            OverseasCostEntry.category_key == category_key,
            OverseasCostEntry.actual_thru_month == actual_thru_month,
            OverseasCostEntry.year == year,
        )
    )


@router.get("/overseas/state", response_model=OverseasState)
def get_overseas_state(basis_year: int, db: Session = Depends(get_db)) -> OverseasState:
    acct = _overseas_account(db)
    lab_pks = {lab.pk for lab in _labs(db)}
    lab_codes = {lab.lab_code for lab in _labs(db)}

    progress = db.scalar(select(AccountProgress).where(AccountProgress.account_id == acct.id))
    step = progress.current_step_order if progress else 1

    V: dict[str, VersionData] = {}
    for version in ("cur", "prev", "prior", "plan"):
        V[version] = VersionData(d={}, etc=[0.0] * 12)

    # 연구소가 직접 추가한 행(label이 있는 overseas_cost_entries 행)은 custom-rows 엔드포인트가
    # 따로 내려주므로 d[pk]에서는 제외한다(두 군데서 중복·drift 방지).
    custom_keys: set[tuple[str, str, str]] = {
        (r.scope_key, r.version, r.category_key)
        for r in db.scalars(select(OverseasCostEntry).where(OverseasCostEntry.label.is_not(None)))
    }

    # cur 버전은 "속보"(가장 큰 actual_thru_month)와 "실적"(그보다 작은 actual_thru_month)이
    # 동시에 존재할 수 있다. 어느 쪽이 "속보"인지는 저장된 값 중 가장 큰 actual_thru_month로 판단한다.
    cur_thrus = set(
        db.scalars(
            select(OverseasCostEntry.actual_thru_month).where(
                OverseasCostEntry.version == "cur", OverseasCostEntry.actual_thru_month > 0
            )
        )
    )
    basis_month = max(cur_thrus) if cur_thrus else 0

    actualV: dict[str, ActualSnapshot] = {}

    # prev(전실행)·prior(전년 실적)는 독립 슬롯이 아니다 — 취합 화면에 그 둘을 따로 업로드하는 경로가
    # 없어서(O.upVer가 항상 "cur") 실제로는 cur를 그대로 복사해 넣는 꼴이었던 걸, cur의 과거 스냅샷에서
    # 바로 유도하도록 바꿨다. 아래 루프는 실제로 저장된 cur·plan만 읽는다.
    for version in ("cur", "plan"):
        year = _year_for(version, basis_year)
        entries = db.scalars(
            select(OverseasCostEntry).where(OverseasCostEntry.version == version, OverseasCostEntry.year == year)
        )
        for entry in entries:
            months = [float(getattr(entry, f"month{m}")) for m in range(1, 13)]
            pk = entry.scope_key
            if pk not in lab_pks:
                continue
            if (pk, version, entry.category_key) in custom_keys:
                continue
            athru = entry.actual_thru_month
            if version == "cur" and athru > 0 and athru != basis_month:
                snap = actualV.setdefault(version, ActualSnapshot(thru_month=athru, d={}, etc=[0.0] * 12))
                if pk == "_ETC":
                    snap.etc = months
                else:
                    snap.d.setdefault(pk, {})[entry.category_key] = months
                continue
            if pk == "_ETC":
                V[version].etc = months
                continue
            V[version].d.setdefault(pk, {})[entry.category_key] = months

    # prev(전실행) = cur의 "실적" 스냅샷(가장 최근에 마감된 달) 그대로.
    if "cur" in actualV:
        snap = actualV["cur"]
        V["prev"].d = {pk: dict(cats) for pk, cats in snap.d.items()}
        V["prev"].etc = list(snap.etc)

    # prior(전년 실적) = 작년(basis_year - 1) cur 중 actual_thru_month가 가장 큰(그 해 마감 시점) 스냅샷.
    prior_year = basis_year - 1
    prior_thrus = set(
        db.scalars(
            select(OverseasCostEntry.actual_thru_month).where(
                OverseasCostEntry.version == "cur",
                OverseasCostEntry.year == prior_year,
                OverseasCostEntry.actual_thru_month > 0,
            )
        )
    )
    if prior_thrus:
        prior_thru = max(prior_thrus)
        for entry in db.scalars(
            select(OverseasCostEntry).where(
                OverseasCostEntry.version == "cur",
                OverseasCostEntry.year == prior_year,
                OverseasCostEntry.actual_thru_month == prior_thru,
            )
        ):
            pk = entry.scope_key
            if pk not in lab_pks or (pk, "cur", entry.category_key) in custom_keys:
                continue
            months = [float(getattr(entry, f"month{m}")) for m in range(1, 13)]
            if pk == "_ETC":
                V["prior"].etc = months
            else:
                V["prior"].d.setdefault(pk, {})[entry.category_key] = months

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

    reason = [
        ReasonRow(
            lab_code=r.lab_code,
            version=r.version,
            category_key=r.category_key,
            block=r.block,
            mon_label=r.mon_label,
            plan_value=float(r.plan_value),
            prev_value=float(r.prev_value),
            cur_value=float(r.cur_value),
            diff_plan=float(r.diff_plan),
            diff_prev=float(r.diff_prev),
            remark_plan=r.remark_plan,
            remark_prev=r.remark_prev,
            prev_label=r.prev_label,
            cur_label=r.cur_label,
            source_file=r.source_file,
        )
        for r in db.scalars(select(OverseasReasonRow))
        if r.lab_code in lab_codes
    ]

    cp = [
        CpRow(
            year=r.year,
            poc_type=r.poc_type,
            poc_proj=r.poc_proj,
            poc_desc=r.poc_desc,
            title=r.title,
            dedicated=float(r.dedicated),
            common=float(r.common),
            man_month=float(r.man_month),
            cost_year=float(r.cost_year),
            poc_cost_sum=float(r.poc_cost_sum),
            poc_mm_sum=float(r.poc_mm_sum),
            poc_direct=float(r.poc_direct),
            poc_ovh=float(r.poc_ovh),
            poc_total=float(r.poc_total),
            source_file=r.source_file,
        )
        for r in db.scalars(select(OverseasCpRow))
    ]

    return OverseasState(
        step=step, basis_year=basis_year, basis_month=basis_month, V=V, actualV=actualV, files=files, note=note, fx=fx,
        reason=reason, cp=cp,
    )


@router.put("/overseas/state")
def save_overseas_state(state: OverseasState, db: Session = Depends(get_db)) -> dict[str, str]:
    acct = _overseas_account(db)
    lab_pks = {lab.pk for lab in _labs(db)}
    lab_codes = {lab.lab_code for lab in _labs(db)}

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
    # 이미 더 나중 달(athru)로 넘어간 행(=마감된 속보)은 재업로드·수정으로 덮어쓰지 않는다 — "실적"(1-1)은
    # 마감 후에도 정정이 들어올 수 있어 그쪽은 이 잠금 대상이 아니다.
    closed_athru = {
        (scope_key, category_key, year): max_athru
        for scope_key, category_key, year, max_athru in db.execute(
            select(
                OverseasCostEntry.scope_key, OverseasCostEntry.category_key, OverseasCostEntry.year,
                func.max(OverseasCostEntry.actual_thru_month),
            )
            .where(OverseasCostEntry.version == "cur")
            .group_by(OverseasCostEntry.scope_key, OverseasCostEntry.category_key, OverseasCostEntry.year)
        )
    }
    for version, vd in state.V.items():
        if version in ("prev", "prior"):
            continue  # 전실행·전년 실적은 cur에서 유도하는 값이라 더 이상 따로 저장하지 않는다
        thru = state.basis_month if version == "cur" else 0
        year = _year_for(version, state.basis_year)
        for pk, cats in vd.d.items():
            for category_key, months in cats.items():
                if version == "cur" and thru > 0 and closed_athru.get((pk, category_key, year), 0) > thru:
                    continue
                _upsert_cost_entry_row(db, pk, version, category_key, months, year, actual_thru_month=thru)
        if "_ETC" in lab_pks:
            if not (version == "cur" and thru > 0 and closed_athru.get(("_ETC", "etc", year), 0) > thru):
                _upsert_cost_entry_row(db, "_ETC", version, "etc", vd.etc, year, actual_thru_month=thru)

    # 1-1) "N월실적" 헤더로 업로드된 확정 실적 스냅샷 (있을 때만, 지금은 cur만 사용)
    for version, snap in state.actualV.items():
        year = _year_for(version, state.basis_year)
        for pk, cats in snap.d.items():
            for category_key, months in cats.items():
                _upsert_cost_entry_row(db, pk, version, category_key, months, year, actual_thru_month=snap.thru_month)
        if "_ETC" in lab_pks:
            _upsert_cost_entry_row(db, "_ETC", version, "etc", snap.etc, year, actual_thru_month=snap.thru_month)

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

    # 4-1) 증감사유 (연구소코드×버전×항목×구간 키로 upsert — cost처럼 보낸 것만 누적 반영)
    unknown_reason_codes = {r.lab_code for r in state.reason if r.lab_code not in lab_codes}
    if unknown_reason_codes:
        raise HTTPException(status_code=400, detail=f"알 수 없는 연구소 코드(증감사유): {sorted(unknown_reason_codes)}")
    for r in state.reason:
        _upsert_reason_row(db, r)

    # 4-2) CP 계약 (연도 단위 전체 교체 — 연 1회 올리는 파일이라 사라진 POC·직급은 지운다)
    cp_years = {r.year for r in state.cp}
    for year in cp_years:
        db.execute(delete(OverseasCpRow).where(OverseasCpRow.year == year))
    for r in state.cp:
        db.add(OverseasCpRow(
            year=r.year, poc_type=r.poc_type, poc_proj=r.poc_proj, poc_desc=r.poc_desc, title=r.title,
            dedicated=r.dedicated, common=r.common, man_month=r.man_month, cost_year=r.cost_year,
            poc_cost_sum=r.poc_cost_sum, poc_mm_sum=r.poc_mm_sum, poc_direct=r.poc_direct, poc_ovh=r.poc_ovh,
            poc_total=r.poc_total, source_file=r.source_file,
        ))

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
    return StepUpdate(step=progress.current_step_order if progress else 1)


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
    """연구소·파트 기준정보. 프론트는 이 목록을 lab_code로 묶어 탭을 그리므로,
    여기 행만 추가하면(배포 없이) 새 연구소 탭이 화면에 나타난다."""
    return [
        LabOut(
            pk=lab.pk,
            lab_code=lab.lab_code,
            part_code=lab.part_code,
            name=lab.name,
            country=lab.country,
            sort_order=lab.sort_order,
            currency=lab.currency,
            is_active=lab.is_active,
        )
        for lab in sorted(_labs(db), key=lambda lab: (lab.sort_order, lab.pk))
        if lab.pk != "_ETC"
    ]


@router.put("/overseas/labs", response_model=list[LabOut])
def save_labs(body: list[LabIn], db: Session = Depends(get_db)) -> list[LabOut]:
    """연구소 기준정보 편집 화면(ovLabPage)의 "저장" — 화면에 보이는 전체 목록을 통째로 받아
    upsert하고, 목록에서 빠진 lab_code는 삭제한다(단 _ETC 가상 행은 화면에 없으니 건드리지 않음).
    overseas_cost_entries 등에 남은 데이터는 FK가 없어 지워지지 않고, 집계에서만 조용히 빠진다."""
    by_code: dict[str, list[OverseasLab]] = {}
    for lab in _labs(db):
        by_code.setdefault(lab.lab_code, []).append(lab)

    submitted_codes = {row.lab_code for row in body}
    for lab_code, rows in by_code.items():
        if lab_code == "_ETC" or lab_code in submitted_codes:
            continue
        for row in rows:
            db.delete(row)

    for order, row in enumerate(body):
        existing = by_code.get(row.lab_code, [])
        if not existing:
            db.add(
                OverseasLab(
                    pk=row.lab_code,
                    lab_code=row.lab_code,
                    part_code=None,
                    name=row.name,
                    country="",
                    sort_order=order * 10,
                    currency=row.currency,
                    is_active=row.is_active,
                )
            )
            continue
        # SARC처럼 lab_code 하나에 파트(pk)가 여러 개면 이름은 파트별로 다르므로
        # 화면에서 준 단일 이름으로 덮어쓰지 않는다(통화·사용여부·순서만 공통 적용).
        for lab in existing:
            lab.currency = row.currency
            lab.is_active = row.is_active
            lab.sort_order = order * 10
            if len(existing) == 1:
                lab.name = row.name

    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"저장 실패 (제약조건 위반): {e.orig}") from e

    return [
        LabOut(
            pk=lab.pk,
            lab_code=lab.lab_code,
            part_code=lab.part_code,
            name=lab.name,
            country=lab.country,
            sort_order=lab.sort_order,
            currency=lab.currency,
            is_active=lab.is_active,
        )
        for lab in sorted(_labs(db), key=lambda lab: (lab.sort_order, lab.pk))
        if lab.pk != "_ETC"
    ]


@router.get("/overseas/lab-sheets", response_model=list[LabSheetOut])
def list_lab_sheets(lab_code: str | None = None, db: Session = Depends(get_db)) -> list[LabSheetOut]:
    """연구소(lab_code)별 워크북 시트 구성(비용|증감분석|외주용역업체). 연구소마다 시트 수가
    다를 수 있어(SARC+ACL은 비용만) overseas_lab_sheets에 행으로 저장돼 있고, 시트명은
    common_codes(group_code='OVERSEAS_SHEET_TYPE')에서 가져온다."""
    names = {
        c.code: c.name
        for c in db.scalars(select(CommonCode).where(CommonCode.group_code == CodeGroup.OVERSEAS_SHEET_TYPE.value))
    }
    stmt = select(OverseasLabSheet).order_by(OverseasLabSheet.lab_code, OverseasLabSheet.sheet_order)
    if lab_code:
        stmt = stmt.where(OverseasLabSheet.lab_code == lab_code)
    return [
        LabSheetOut(
            lab_code=s.lab_code, sheet_key=s.sheet_key, name=names.get(s.sheet_key, s.sheet_key), sheet_order=s.sheet_order
        )
        for s in db.scalars(stmt)
    ]


def _resolve_cur_rows(
    db: Session, entries: list[OverseasCostEntry]
) -> list[tuple[str, str, str, int, list[float]]]:
    """cur 버전은 "속보"(basis_month, 가장 큰 actual_thru_month)와 "실적"(그보다 작은
    actual_thru_month) 두 행이 같은 scope_key×category_key에 동시에 존재할 수 있다(get_overseas_state와
    동일 규칙). 실적이 확정된 달까지는 실적값을, 그 이후 달은 속보값을 쓰도록 한 행으로 합쳐
    (scope_key, category_key, version, actual_thru_month, months) 튜플로 반환한다."""
    basis_month = (
        db.scalar(
            select(func.max(OverseasCostEntry.actual_thru_month)).where(
                OverseasCostEntry.version == "cur", OverseasCostEntry.actual_thru_month > 0
            )
        )
        or 0
    )
    cur_groups: dict[tuple[str, str], list[OverseasCostEntry]] = defaultdict(list)
    rows: list[tuple[str, str, str, int, list[float]]] = []
    for entry in entries:
        if entry.version != "cur":
            rows.append(
                (entry.scope_key, entry.category_key, entry.version, entry.actual_thru_month,
                 [float(getattr(entry, f"month{m}")) for m in range(1, 13)])
            )
            continue
        cur_groups[(entry.scope_key, entry.category_key)].append(entry)

    for (scope_key, category_key), grp in cur_groups.items():
        forecast = next((e for e in grp if e.actual_thru_month == basis_month), grp[0])
        actual = next((e for e in grp if 0 < e.actual_thru_month != basis_month), None)
        months = [float(getattr(forecast, f"month{m}")) for m in range(1, 13)]
        athru = forecast.actual_thru_month
        if actual is not None:
            for m in range(1, actual.actual_thru_month + 1):
                months[m - 1] = float(getattr(actual, f"month{m}"))
            athru = actual.actual_thru_month
        rows.append((scope_key, category_key, "cur", athru, months))
    return rows


@router.get("/overseas/cost-entries", response_model=list[CostEntryOut])
def list_cost_entries(
    year: int,
    version: str | None = None,
    lab_pk: str | None = None,
    category_key: str | None = None,
    actual_thru_month: int | None = None,
    resolve_cur: bool = False,
    db: Session = Depends(get_db),
) -> list[CostEntryOut]:
    """비용 행(overseas_cost_entries)의 month1~12 컬럼을 월 1~12건으로 펼쳐서 내려준다.
    resolve_cur=true면 cur 버전의 실적/속보 중복 스냅샷을 병합해 scope_key×category_key당 달마다
    값을 하나씩만 내려준다(실적이 확정된 달까지는 실적값, 그 이후는 속보값) — actual_thru_month로
    직접 걸러서 원시 행을 보고 싶을 때가 아니면 이 옵션을 쓸 것."""
    stmt = select(OverseasCostEntry).where(OverseasCostEntry.year == year)
    if version:
        stmt = stmt.where(OverseasCostEntry.version == version)
    if lab_pk:
        stmt = stmt.where(OverseasCostEntry.scope_key == _check_lab_pk(db, lab_pk))
    if category_key:
        stmt = stmt.where(OverseasCostEntry.category_key == category_key)
    if actual_thru_month is not None:
        stmt = stmt.where(OverseasCostEntry.actual_thru_month == actual_thru_month)
    entries = list(db.scalars(stmt))

    if resolve_cur:
        rows = _resolve_cur_rows(db, entries)
    else:
        rows = [
            (entry.scope_key, entry.category_key, entry.version, entry.actual_thru_month,
             [float(getattr(entry, f"month{m}")) for m in range(1, 13)])
            for entry in entries
        ]

    return [
        CostEntryOut(
            lab_pk=scope_key,
            version=entry_version,
            category_key=category_key,
            year=year,
            month=m,
            amount=months[m - 1],
            actual_thru_month=athru,
        )
        for scope_key, category_key, entry_version, athru, months in rows
        for m in range(1, 13)
    ]


@router.put("/overseas/cost-entries", response_model=CostEntryOut)
def upsert_cost_entry(body: CostEntryIn, db: Session = Depends(get_db)) -> CostEntryOut:
    _check_lab_pk(db, body.lab_pk)
    _upsert_cost_entry(db, body.lab_pk, body.version, body.category_key, body.month, body.amount, body.year, body.actual_thru_month)
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
        UploadOut(lab_pk=u.scope_key, version=u.version, filename=u.filename, uploaded_at=u.uploaded_at, s3_key=u.s3_key)
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


@router.post("/overseas/uploads/file", response_model=UploadOut)
async def upload_raw_file(
    lab_pk: str = Form(...),
    version: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> UploadOut:
    """프론트가 엑셀을 파싱해 /overseas/state로 숫자만 보내는 것과 별개로, 원본 바이트를 그대로
    S3에 올려 감사·재검증용으로 남긴다. S3 미설정(버킷 준비 전)이거나 업로드 실패해도 s3_key만
    NULL로 남을 뿐 업로드 이력 자체는 기존과 동일하게 기록된다 — 이 엔드포인트가 막혀도 /overseas/state
    저장 흐름(파싱 결과)에는 영향이 없다."""
    _check_lab_pk(db, lab_pk)
    data = await file.read()
    key = f"overseas/{lab_pk}/{version}/{datetime.utcnow():%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}_{file.filename}"
    s3_key = upload_bytes(data, key, content_type=file.content_type)
    up = Upload(account_key=ACCOUNT_KEY, scope_key=lab_pk, version=version, filename=file.filename, s3_key=s3_key)
    db.add(up)
    db.commit()
    db.refresh(up)
    return UploadOut(lab_pk=lab_pk, version=version, filename=file.filename, uploaded_at=up.uploaded_at, s3_key=up.s3_key)


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
# 행 = label이 있는 overseas_cost_entries 행(그 자체가 존재·이름·값을 다 가짐 — 별도 레지스트리 불필요).
# 열(메모 전용 열)은 비용 데이터에 home이 없어서 memo_cols에 따로 둔다.


@router.get("/overseas/custom-rows", response_model=list[CustomRowOut])
def list_custom_rows(
    year: int,
    version: str | None = None,
    lab_pk: str | None = None,
    actual_thru_month: int = 0,
    db: Session = Depends(get_db),
) -> list[CustomRowOut]:
    actual_thru_month = _resolve_thru(db, version, actual_thru_month)
    stmt = select(OverseasCostEntry).where(
        OverseasCostEntry.label.is_not(None),
        OverseasCostEntry.actual_thru_month == actual_thru_month,
        OverseasCostEntry.year == year,
    )
    if version:
        stmt = stmt.where(OverseasCostEntry.version == version)
    if lab_pk:
        stmt = stmt.where(OverseasCostEntry.scope_key == _check_lab_pk(db, lab_pk))

    return [
        CustomRowOut(
            lab_pk=entry.scope_key, version=entry.version, row_key=entry.category_key, label=entry.label,
            label_en=entry.label_en or "", year=year, actual_thru_month=actual_thru_month,
            anchor_key=entry.anchor_key,
            v=[float(getattr(entry, f"month{m}")) for m in range(1, 13)],
        )
        for entry in db.scalars(stmt)
    ]


@router.put("/overseas/custom-rows", response_model=CustomRowOut)
def upsert_custom_row(body: CustomRowIn, db: Session = Depends(get_db)) -> CustomRowOut:
    if len(body.v) != 12:
        raise HTTPException(status_code=400, detail="v는 12개월치 값이어야 합니다.")
    _check_lab_pk(db, body.lab_pk)
    thru = _resolve_thru(db, body.version, body.actual_thru_month)
    entry_id = _get_or_create_entry(db, body.lab_pk, body.version, body.row_key, body.year, thru)
    db.execute(
        update(OverseasCostEntry).where(OverseasCostEntry.id == entry_id)
        .values(label=body.label, label_en=body.label_en, anchor_key=body.anchor_key)
    )
    _upsert_cost_entry_row(db, body.lab_pk, body.version, body.row_key, body.v, body.year, thru)
    db.commit()
    return CustomRowOut(**{**body.model_dump(), "actual_thru_month": thru})


@router.delete("/overseas/custom-rows")
def delete_custom_row(
    lab_pk: str, version: str, row_key: str, actual_thru_month: int = 0, db: Session = Depends(get_db)
) -> dict[str, str]:
    """overseas_cost_entries 행을 지운다(연도별로 쌓인 행이 있으면 전부) — 더 이상 다른 테이블이 FK로
    참조하지 않아 cascade 없이 이 테이블만 지우면 된다."""
    _check_lab_pk(db, lab_pk)
    actual_thru_month = _resolve_thru(db, version, actual_thru_month)
    db.execute(
        delete(OverseasCostEntry).where(
            OverseasCostEntry.scope_key == lab_pk,
            OverseasCostEntry.version == version,
            OverseasCostEntry.category_key == row_key,
            OverseasCostEntry.actual_thru_month == actual_thru_month,
        )
    )
    db.commit()
    return {"status": "ok"}


# 추가 열(존재·이름·값)은 overseas_cost_entries.custom_fields(JSONB 배열, [{label, value}, ...])에
# 행마다 직접 달려 있다. label 자체가 열 식별자라 별도 col_key·레지스트리가 없다 — 같은 label을 가진
# 값들의 모임이 곧 "그 열"이다. 열 이름을 바꾸는 건 지원하지 않는다(지우고 새 이름으로 다시 넣으면 됨).
# 새 열을 만들 때는 "hc"(인력) 행을 앵커로 쓴다(모든 연구소×버전에 항상 존재하는 고정 계정이라
# 빈 열도 안정적으로 표시할 수 있다).
_EXTRA_ANCHOR_CATEGORY = "hc"


@router.get("/overseas/extra-cols", response_model=list[ExtraColOut])
def list_extra_cols(
    year: int, version: str | None = None, lab_pk: str | None = None, actual_thru_month: int = 0, db: Session = Depends(get_db)
) -> list[ExtraColOut]:
    actual_thru_month = _resolve_thru(db, version, actual_thru_month)
    stmt = select(OverseasCostEntry).where(
        OverseasCostEntry.actual_thru_month == actual_thru_month,
        OverseasCostEntry.year == year,
        OverseasCostEntry.custom_fields.is_not(None),
    )
    if version:
        stmt = stmt.where(OverseasCostEntry.version == version)
    if lab_pk:
        stmt = stmt.where(OverseasCostEntry.scope_key == _check_lab_pk(db, lab_pk))
    seen: set[tuple[str, str, str]] = set()
    out: list[ExtraColOut] = []
    for entry in db.scalars(stmt):
        for field in entry.custom_fields or []:
            key = (entry.scope_key, entry.version, field["label"])
            if key in seen:
                continue
            seen.add(key)
            out.append(ExtraColOut(lab_pk=entry.scope_key, version=entry.version, label=field["label"]))
    return out


@router.get("/overseas/cost-extras", response_model=list[ExtraValueOut])
def list_cost_extras(
    year: int, version: str | None = None, lab_pk: str | None = None, actual_thru_month: int = 0, db: Session = Depends(get_db)
) -> list[ExtraValueOut]:
    actual_thru_month = _resolve_thru(db, version, actual_thru_month)
    stmt = select(OverseasCostEntry).where(
        OverseasCostEntry.actual_thru_month == actual_thru_month,
        OverseasCostEntry.year == year,
        OverseasCostEntry.custom_fields.is_not(None),
    )
    if version:
        stmt = stmt.where(OverseasCostEntry.version == version)
    if lab_pk:
        stmt = stmt.where(OverseasCostEntry.scope_key == _check_lab_pk(db, lab_pk))
    return [
        ExtraValueOut(
            lab_pk=entry.scope_key, version=entry.version, row_key=entry.category_key, year=year,
            label=field["label"], actual_thru_month=actual_thru_month, value=float(field.get("value", 0)),
        )
        for entry in db.scalars(stmt)
        for field in entry.custom_fields or []
    ]


@router.put("/overseas/cost-extras", response_model=ExtraValueOut)
def upsert_cost_extra(body: ExtraValueIn, db: Session = Depends(get_db)) -> ExtraValueOut:
    _check_lab_pk(db, body.lab_pk)
    thru = _resolve_thru(db, body.version, body.actual_thru_month)
    entry_id = _get_or_create_entry(db, body.lab_pk, body.version, body.row_key, body.year, thru)
    entry = db.get(OverseasCostEntry, entry_id)
    fields = [dict(f) for f in (entry.custom_fields or [])]
    for f in fields:
        if f["label"] == body.label:
            f["value"] = body.value
            break
    else:
        fields.append({"label": body.label, "value": body.value})
    db.execute(
        update(OverseasCostEntry).where(OverseasCostEntry.id == entry_id)
        .values(custom_fields=fields, updated_at=datetime.utcnow())
    )
    db.commit()
    return ExtraValueOut(**{**body.model_dump(), "actual_thru_month": thru})


@router.delete("/overseas/extra-cols")
def delete_extra_col(
    lab_pk: str, version: str, label: str, year: int, actual_thru_month: int = 0, db: Session = Depends(get_db)
) -> dict[str, str]:
    """이 열(label)이 달린, 같은 연도의 모든 행에서 지운다 — 사실상 그 연도의 열 자체를 지우는 것."""
    _check_lab_pk(db, lab_pk)
    actual_thru_month = _resolve_thru(db, version, actual_thru_month)
    entries = db.scalars(
        select(OverseasCostEntry).where(
            OverseasCostEntry.scope_key == lab_pk,
            OverseasCostEntry.version == version,
            OverseasCostEntry.actual_thru_month == actual_thru_month,
            OverseasCostEntry.year == year,
            OverseasCostEntry.custom_fields.is_not(None),
        )
    )
    for entry in entries:
        entry.custom_fields = [f for f in (entry.custom_fields or []) if f["label"] != label]
    db.commit()
    return {"status": "ok"}


# ---------- 구조 미정 시트(증감분석 · 외주용역업체)의 원본 행 ----------
# 열 구성이 아직 확정되지 않아 cost_entries 같은 정형 테이블 대신 JSON 한 덩어리(sheet_rows.data)로
# 우선 보관한다. 열 구조가 확정되면 이 CRUD는 그대로 두고 조회 쪽만 정형 테이블로 옮기면 된다.


@router.get("/overseas/sheet-rows", response_model=list[SheetRowOut])
def list_sheet_rows(
    version: str | None = None, lab_pk: str | None = None, sheet_key: str | None = None, db: Session = Depends(get_db)
) -> list[SheetRowOut]:
    stmt = select(SheetRow).where(SheetRow.account_key == ACCOUNT_KEY).order_by(SheetRow.sort_order)
    if version:
        stmt = stmt.where(SheetRow.version == version)
    if lab_pk:
        stmt = stmt.where(SheetRow.scope_key == _check_lab_pk(db, lab_pk))
    if sheet_key:
        stmt = stmt.where(SheetRow.sheet_key == sheet_key)
    return [
        SheetRowOut(
            lab_pk=r.scope_key, version=r.version, sheet_key=r.sheet_key, row_key=r.row_key, sort_order=r.sort_order, data=r.data
        )
        for r in db.scalars(stmt)
    ]


@router.put("/overseas/sheet-rows", response_model=SheetRowOut)
def upsert_sheet_row(body: SheetRowIn, db: Session = Depends(get_db)) -> SheetRowOut:
    _check_lab_pk(db, body.lab_pk)
    stmt = pg_insert(SheetRow).values(
        account_key=ACCOUNT_KEY,
        scope_key=body.lab_pk,
        version=body.version,
        sheet_key=body.sheet_key,
        row_key=body.row_key,
        sort_order=body.sort_order,
        data=body.data,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_key", "scope_key", "version", "sheet_key", "row_key"],
        set_={"sort_order": stmt.excluded.sort_order, "data": stmt.excluded.data, "updated_at": datetime.utcnow()},
    )
    db.execute(stmt)
    db.commit()
    return SheetRowOut(**body.model_dump())


@router.delete("/overseas/sheet-rows")
def delete_sheet_row(lab_pk: str, version: str, sheet_key: str, row_key: str, db: Session = Depends(get_db)) -> dict[str, str]:
    _check_lab_pk(db, lab_pk)
    db.execute(
        delete(SheetRow).where(
            SheetRow.account_key == ACCOUNT_KEY,
            SheetRow.scope_key == lab_pk,
            SheetRow.version == version,
            SheetRow.sheet_key == sheet_key,
            SheetRow.row_key == row_key,
        )
    )
    db.commit()
    return {"status": "ok"}


@router.delete("/overseas/sheet-rows/all")
def delete_sheet_rows_all(lab_pk: str, version: str, sheet_key: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """시트를 다시 업로드할 때 이전 행을 한 번에 지우기 위한 용도(행 개수가 매번 다를 수 있어서)."""
    _check_lab_pk(db, lab_pk)
    db.execute(
        delete(SheetRow).where(
            SheetRow.account_key == ACCOUNT_KEY,
            SheetRow.scope_key == lab_pk,
            SheetRow.version == version,
            SheetRow.sheet_key == sheet_key,
        )
    )
    db.commit()
    return {"status": "ok"}


def _fx_months(rate: FxRate) -> list[float]:
    return [float(getattr(rate, f"month{m}")) for m in range(1, 13)]


def _upsert_cost_entry_row(
    db: Session, lab_pk: str, version: str, category_key: str, months: list[float], year: int, actual_thru_month: int = 0
) -> None:
    """비용 행(overseas_cost_entries)을 scope_key×version×category_key×actual_thru_month×year로 upsert하고,
    month1~12·분기·연간 컬럼을 한꺼번에 채운다. actual_thru_month는 cur에서 같은 카테고리가 실적/속보로
    동시에 존재할 때 구분용 (그 외엔 0)."""
    month_cols = {f"month{m}": amount for m, amount in enumerate(months, start=1)}
    values = {**month_cols, **_quarters_and_annual(months)}
    stmt = pg_insert(OverseasCostEntry).values(
        scope_key=lab_pk, version=version, category_key=category_key, actual_thru_month=actual_thru_month, year=year, **values
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["scope_key", "version", "category_key", "actual_thru_month", "year"],
        set_={**{k: getattr(stmt.excluded, k) for k in values}, "updated_at": datetime.utcnow()},
    )
    db.execute(stmt)


def _upsert_cost_entry(
    db: Session,
    lab_pk: str,
    version: str,
    category_key: str,
    month: int,
    amount: float,
    year: int,
    actual_thru_month: int = 0,
) -> None:
    """세분화 CRUD(단일 셀)용. 한 달치만 바뀌므로 그 month 컬럼만 바꾸고 분기·연간은 다시 합산한다."""
    entry_id = _get_or_create_entry(db, lab_pk, version, category_key, year, actual_thru_month)
    db.execute(update(OverseasCostEntry).where(OverseasCostEntry.id == entry_id).values(**{f"month{month}": amount}))
    entry = db.get(OverseasCostEntry, entry_id)
    months = [float(getattr(entry, f"month{m}")) for m in range(1, 13)]
    db.execute(
        update(OverseasCostEntry).where(OverseasCostEntry.id == entry_id)
        .values(**_quarters_and_annual(months), updated_at=datetime.utcnow())
    )


def _upsert_note(db: Session, base_version: str, period: str, scope: str, category_key: str, text: str) -> None:
    stmt = pg_insert(Note).values(
        account_key=ACCOUNT_KEY, base_version=base_version, period=period, scope=scope, category_key=category_key, note_text=text
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_key", "base_version", "period", "scope", "category_key"],
        set_={"note_text": stmt.excluded.note_text, "updated_at": datetime.utcnow()},
    )
    db.execute(stmt)


def _upsert_reason_row(db: Session, r: ReasonRow) -> None:
    stmt = pg_insert(OverseasReasonRow).values(
        lab_code=r.lab_code, version=r.version, category_key=r.category_key, block=r.block,
        mon_label=r.mon_label, plan_value=r.plan_value, prev_value=r.prev_value, cur_value=r.cur_value,
        diff_plan=r.diff_plan, diff_prev=r.diff_prev, remark_plan=r.remark_plan, remark_prev=r.remark_prev,
        prev_label=r.prev_label, cur_label=r.cur_label, source_file=r.source_file,
    )
    set_cols = (
        "mon_label", "plan_value", "prev_value", "cur_value", "diff_plan", "diff_prev",
        "remark_plan", "remark_prev", "prev_label", "cur_label", "source_file",
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["lab_code", "version", "category_key", "block"],
        set_={k: getattr(stmt.excluded, k) for k in set_cols},
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
