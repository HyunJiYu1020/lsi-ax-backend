import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.storage import upload_bytes
from app.db.session import get_db
from app.models import (
    Account,
    AccountProgress,
    CommonCode,
    CostEntry,
    CostEntryDetail,
    ExtraCol,
    ManagementPlanEntry,
    ManagementPlanEntryDetail,
    ManagementPlanVersion,
    Memo,
    MemoCol,
    Note,
    OverseasCpRow,
    OverseasLab,
    OverseasLabSheet,
    OverseasReasonRow,
    OverseasVendorRow,
    OverseasVendorRowDetail,
    OverseasFxRuleOverride,
    SheetRow,
    Upload,
)
from app.core.cost_groups import category_label, resolve_leaves
# 환산 기준(말일 vs 월평균)의 기본값을 읽는 헬퍼. fx.py는 overseas.py를 import하지 않으므로 순환이
# 생기지 않고, 기본값 폴백 규칙을 여기에 두 번 적어 두 곳이 어긋나는 걸 피하려고 그대로 가져다 쓴다.
from app.api.v1.endpoints.fx import _compute_applied_rows, _FxRateCache, _overseas_fx_default
from app.core.enums import CodeGroup
from app.schemas.overseas import (
    ActualSnapshot,
    LaborSplitLab,
    LaborSplitResult,
    VendorByProjectResult,
    VendorProjectRow,
    CostEntryIn,
    CostEntryOut,
    CpRow,
    ExtraRowIn,
    ExtraRowOut,
    ExtraColIn,
    ExtraColOut,
    ExtraValueIn,
    ExtraValueOut,
    FileInfo,
    LabIn,
    LabOut,
    LabSheetIn,
    LabSheetOut,
    MemoColIn,
    MemoColOut,
    MemoIn,
    MemoOut,
    NoteIn,
    NoteOut,
    OverseasState,
    ReasonRow,
    SheetRowIn,
    SheetRowOut,
    StepUpdate,
    UploadIn,
    UploadOut,
    VendorRow,
    VersionData,
)

router = APIRouter()

# cost_entries 등 공용 테이블은 account_code로 계정을 구분한다. 이 라우터는 해외연구소 전용이라 고정.
ACCOUNT_KEY = "overseas"

# 경영계획(plan) 역할태그는 cost_entries에 저장하지 않고 management_plan_entries가 담당한다
# (plan_no별로 영구 보관·비교해야 해서 cost_entries의 forecast/actual과는 다른 테이블).
# 해외연구소 화면엔 plan_no 선택 UI가 아직 없어 "P00" 고정 버전 하나만 쓴다.
PLAN_NO = "P00"


def _overseas_account(db: Session) -> Account:
    acct = db.scalar(select(Account).where(Account.key == ACCOUNT_KEY))
    if not acct:
        raise HTTPException(status_code=500, detail="overseas 계정이 시드되지 않았습니다 (app.db.seed 먼저 실행하세요).")
    return acct


def _labs(db: Session) -> list[OverseasLab]:
    return list(db.scalars(select(OverseasLab)))


# 집계 화면의 파생 행이 쓰는 "전체 연구소 합산" 범위. cost_entries.scope_code 주석에 이미 적혀 있는
# 관례("없는 계정은 상수 ALL")를 그대로 쓴다 — 연구소 하나가 아니라 전체를 가리키는 행이라는 뜻이다.
# 비율 파생 행(인당인건비 등)은 연구소별로 계산해서 더하면 뜻이 없어지므로, 집계에서는 재료를 전체
# 합산한 뒤 계산해야 해서 이 범위가 필요하다.
ALL_SCOPE = "ALL"


def _check_lab_pk(db: Session, pk: str, allow_all: bool = False) -> str:
    """scope_code(=연구소 pk)가 overseas_labs에 실재하는지 검증한다. DB가 FK로 못 막아주는 부분이라 여기서 확인.
    allow_all=True면 전체 합산 범위("ALL")도 받아준다(집계 화면의 파생 행 전용)."""
    if allow_all and pk == ALL_SCOPE:
        return pk
    if not db.scalar(select(OverseasLab.id).where(OverseasLab.pk == pk)):
        raise HTTPException(status_code=400, detail=f"알 수 없는 연구소 코드: {pk}")
    return pk


def _check_category(db: Session, category_key: str) -> str:
    """category_code가 고정 항목(common_codes group=OVERSEAS_CATEGORY) 이거나, 이미 등록된 커스텀
    행(cost_entries.label이 채워진 행)이어야 한다 — 둘 다 아니면 오타·존재하지 않는 코드로 보고 막는다.
    고정 항목 중에서도 "그룹"(A·hr·labor·asset·direct·gen·mgr·itx처럼 common_codes에서 다른 코드의
    parent_code로 가리켜지는 집계 전용 행)은 직접 금액을 못 넣는다 — 하위 항목들의 합계로만 존재해야
    하는 행에 실제 비용을 꽂으면 집계가 깨진다."""
    is_fixed = db.scalar(
        select(CommonCode.id).where(CommonCode.group_code == CodeGroup.OVERSEAS_CATEGORY.value, CommonCode.code == category_key)
    )
    is_registered_custom = db.scalar(
        select(CostEntry.id).where(
            CostEntry.account_code == ACCOUNT_KEY, CostEntry.category_code == category_key, CostEntry.label.is_not(None)
        )
    )
    if not is_fixed and not is_registered_custom:
        raise HTTPException(status_code=400, detail=f"알 수 없는 비용항목 코드: {category_key}")
    is_group = db.scalar(
        select(CommonCode.id).where(CommonCode.group_code == CodeGroup.OVERSEAS_CATEGORY.value, CommonCode.parent_code == category_key)
    )
    if is_group:
        raise HTTPException(status_code=400, detail=f"'{category_key}'는 하위 항목의 합계로만 쓰이는 그룹이라 직접 금액을 넣을 수 없습니다.")
    return category_key


def _current_user_id() -> int | None:
    """로그인 인증이 아직 없어 항상 None을 돌려준다 — 인증이 붙으면 이 함수 하나만 요청의 로그인
    사용자 id를 읽어오도록 바꾸면, 호출부(created_by_id/updated_by_id를 쓰는 곳)는 그대로 둬도 된다."""
    return None


def _yyyymm(year: int, month: int) -> str:
    return f"{year:04d}{month:02d}"


def _ym_year(yyyymm: str) -> int:
    return int(yyyymm[:4])


def _ym_month(yyyymm: str) -> int:
    return int(yyyymm[4:6])


def _basis_month(db: Session) -> int:
    """forecast(속보) 버전 중 가장 최근(yyyymm 최댓값)의 월 — 지금 편성 기준월.
    yyyymm이 "YYYYMM" 6자리 고정폭이라 문자열 최댓값이 곧 날짜순 최댓값과 같다."""
    latest = db.scalar(
        select(func.max(CostEntry.yyyymm)).where(CostEntry.account_code == ACCOUNT_KEY, CostEntry.version == "forecast")
    )
    return _ym_month(latest) if latest else 0


def _entry_months_bulk(db: Session, entries: list[CostEntry]) -> dict[int, list[float]]:
    """엔트리별 월 값을 한 번의 쿼리로 모아 돌려준다 (호출부가 엔트리마다 따로 조회하지 않도록).
    기본은 12개월(그 헤더의 yyyymm 연도)이지만, 다음 연도치(year+1)까지 저장돼 있으면 24개월(그다음도
    저장돼 있으면 36개월…, 항상 12의 배수)로 이어 붙여 돌려준다 — 기간을 12개월 너머로 늘려 받을 때
    쓰는 확장 지점."""
    entry_ids = [e.id for e in entries]
    if not entry_ids:
        return {}
    base_year_by_id = {e.id: _ym_year(e.yyyymm) for e in entries}
    by_entry: dict[int, dict[int, float]] = {}
    max_idx_by_entry: dict[int, int] = {}
    for r in db.scalars(
        select(CostEntryDetail).where(
            CostEntryDetail.cost_entry_id.in_(entry_ids), CostEntryDetail.period_type == "MONTH"
        )
    ):
        idx = (r.year - base_year_by_id[r.cost_entry_id]) * 12 + (r.period - 1)  # 0-based: 0~11=1년차, 12~23=2년차…
        by_entry.setdefault(r.cost_entry_id, {})[idx] = float(r.amount)
        max_idx_by_entry[r.cost_entry_id] = max(max_idx_by_entry.get(r.cost_entry_id, -1), idx)
    out: dict[int, list[float]] = {}
    for eid in entry_ids:
        n = ((max_idx_by_entry.get(eid, -1) // 12) + 1) * 12
        vals = by_entry.get(eid, {})
        out[eid] = [vals.get(i, 0.0) for i in range(n)]
    return out


def _plan_entry_months_bulk(db: Session, entry_ids: list[int]) -> dict[int, list[float]]:
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


def _latest_uploads_stmt(lab_pks: set[str], versions: list[str]):
    """(version, scope_key) 조합마다 가장 최근 업로드 1건만 고르는 쿼리.

    uploads는 지울 일이 없이 계속 쌓이기만 하는 이력 테이블이라, 계정 전체를 파이썬으로 끌어와
    마지막 것만 남기는 방식은 업로드가 늘수록 그대로 느려진다. DISTINCT ON으로 DB에서 바로
    최신 행만 받아 온다(연구소·버전 필터도 같이 밀어 넣어 쓸모없는 행은 아예 안 읽는다)."""
    return (
        select(Upload)
        .where(
            Upload.account_key == ACCOUNT_KEY,
            Upload.scope_key.in_(lab_pks),
            Upload.version.in_(versions),
        )
        .distinct(Upload.version, Upload.scope_key)
        .order_by(Upload.version, Upload.scope_key, Upload.uploaded_at.desc(), Upload.id.desc())
    )


def _get_or_create_entry(db: Session, scope_code: str, version: str, category_code: str, yyyymm: str) -> int:
    """비용 헤더(cost_entries)를 account_code×scope_code×category_code×yyyymm×version으로 upsert하고
    id를 돌려준다."""
    stmt = pg_insert(CostEntry).values(
        yyyymm=yyyymm, account_code=ACCOUNT_KEY, scope_code=scope_code, version=version, category_code=category_code,
    ).on_conflict_do_nothing(index_elements=["account_code", "scope_code", "category_code", "yyyymm", "version"])
    db.execute(stmt)
    return db.scalar(
        select(CostEntry.id).where(
            CostEntry.account_code == ACCOUNT_KEY,
            CostEntry.scope_code == scope_code,
            CostEntry.category_code == category_code,
            CostEntry.yyyymm == yyyymm,
            CostEntry.version == version,
        )
    )


def _upsert_entry_months(db: Session, cost_entry_id: int, base_year: int, months: list[float]) -> None:
    """months는 12개(1년치)가 기본이지만 12의 배수(24·36…)면 2년차부터 base_year+1, base_year+2…로
    이어서 저장한다 — 기간을 12개월 너머로 늘려 받을 때 쓰는 확장 지점.

    월마다 따로 실행하면 행 하나 저장에 12번(24개월이면 24번) DB 왕복이 나서, 디테일 전체를
    한 문장으로 모아 upsert한다(_bulk_upsert_cost_entries와 같은 이유)."""
    if not months:
        return
    stmt = pg_insert(CostEntryDetail).values(
        [
            {
                "cost_entry_id": cost_entry_id, "year": base_year + i // 12, "period_type": "MONTH",
                "period": i % 12 + 1, "col_name": "", "amount": amount,
            }
            for i, amount in enumerate(months)
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["cost_entry_id", "year", "period_type", "period", "col_name"],
        set_={"amount": stmt.excluded.amount, "updated_at": datetime.now(timezone.utc)},
    )
    db.execute(stmt)


def _bulk_upsert_cost_entries(db: Session, rows: list[tuple[str, str, str, str, list[float]]]) -> None:
    """(scope_code, version, category_code, yyyymm, months) 목록을 헤더·디테일 각각 단일 upsert
    문으로 한 번에 처리한다 — save_overseas_state가 행마다 _get_or_create_entry+월별 insert를
    따로 호출하면 (lab×category 조합 수)×14번 왕복이 나서, 그걸 피하려고 모아서 처리한다.
    같은 키가 두 번 들어오면 (호출부가 겹쳐 보낸 경우) 마지막 값이 이긴다 — 기존의 순차 upsert와 동일한 규칙."""
    dedup: dict[tuple[str, str, str, str], list[float]] = {
        (scope_code, version, category_code, yyyymm): months
        for scope_code, version, category_code, yyyymm, months in rows
    }
    if not dedup:
        return

    header_stmt = pg_insert(CostEntry).values(
        [
            {
                "yyyymm": yyyymm, "account_code": ACCOUNT_KEY, "scope_code": scope_code,
                "version": version, "category_code": category_code,
            }
            for scope_code, version, category_code, yyyymm in dedup
        ]
    )
    header_stmt = header_stmt.on_conflict_do_update(
        index_elements=["account_code", "scope_code", "category_code", "yyyymm", "version"],
        set_={"yyyymm": header_stmt.excluded.yyyymm},  # no-op 갱신 — DO NOTHING은 충돌 행을 RETURNING 안 해줘서 못 씀
    ).returning(CostEntry.id, CostEntry.scope_code, CostEntry.category_code, CostEntry.yyyymm, CostEntry.version)
    id_by_key = {(r.scope_code, r.category_code, r.yyyymm, r.version): r.id for r in db.execute(header_stmt)}

    detail_stmt = pg_insert(CostEntryDetail).values(
        [
            {
                "cost_entry_id": id_by_key[(scope_code, category_code, yyyymm, version)],
                # months가 12개(기본)를 넘으면(12의 배수, 기간을 늘려 받는 경우) 12개씩 다음 연도로
                # 넘겨 저장한다 — upsert_cost_entry/_upsert_entry_months와 동일한 규칙.
                "year": _ym_year(yyyymm) + i // 12, "period_type": "MONTH", "period": i % 12 + 1,
                "col_name": "", "amount": amount,
            }
            for (scope_code, version, category_code, yyyymm), months in dedup.items()
            for i, amount in enumerate(months)
        ]
    )
    detail_stmt = detail_stmt.on_conflict_do_update(
        index_elements=["cost_entry_id", "year", "period_type", "period", "col_name"],
        set_={"amount": detail_stmt.excluded.amount, "updated_at": datetime.now(timezone.utc)},
    )
    db.execute(detail_stmt)


def _ensure_plan_version(db: Session, year: int) -> None:
    if not db.scalar(
        select(ManagementPlanVersion.year).where(
            ManagementPlanVersion.year == year, ManagementPlanVersion.plan_no == PLAN_NO
        )
    ):
        db.add(ManagementPlanVersion(year=year, plan_no=PLAN_NO))
        db.flush()


def _bulk_upsert_plan_entries(db: Session, rows: list[tuple[str, str, int, list[float]]]) -> None:
    """(scope_code, category_code, year, months) 목록을 헤더·디테일 각각 단일 upsert 문으로 처리한다
    (_bulk_upsert_cost_entries와 같은 이유)."""
    dedup = {(r[0], r[1], r[2]): r[3] for r in rows}
    if not dedup:
        return
    for year in {year for _, _, year in dedup}:
        _ensure_plan_version(db, year)

    header_stmt = pg_insert(ManagementPlanEntry).values(
        [
            {
                "account_code": ACCOUNT_KEY, "scope_code": scope_code, "year": year,
                "plan_no": PLAN_NO, "category_code": category_code,
            }
            for scope_code, category_code, year in dedup
        ]
    )
    header_stmt = header_stmt.on_conflict_do_update(
        index_elements=["account_code", "scope_code", "category_code", "year", "plan_no"],
        set_={"year": header_stmt.excluded.year},
    ).returning(ManagementPlanEntry.id, ManagementPlanEntry.scope_code, ManagementPlanEntry.category_code, ManagementPlanEntry.year)
    id_by_key = {(r.scope_code, r.category_code, r.year): r.id for r in db.execute(header_stmt)}

    detail_stmt = pg_insert(ManagementPlanEntryDetail).values(
        [
            {
                "plan_entry_id": id_by_key[(scope_code, category_code, year)],
                "year": year, "period_type": "MONTH", "period": m, "col_name": "", "amount": amount,
            }
            for (scope_code, category_code, year), months in dedup.items()
            for m, amount in enumerate(months, start=1)
        ]
    )
    detail_stmt = detail_stmt.on_conflict_do_update(
        index_elements=["plan_entry_id", "year", "period_type", "period", "col_name"],
        set_={"amount": detail_stmt.excluded.amount},
    )
    db.execute(detail_stmt)


@router.get("/overseas/state", response_model=OverseasState)
def get_overseas_state(basis_year: int, db: Session = Depends(get_db)) -> OverseasState:
    acct = _overseas_account(db)
    labs = _labs(db)
    lab_pks = {lab.pk for lab in labs}
    lab_codes = {lab.lab_code for lab in labs}

    progress = db.scalar(select(AccountProgress).where(AccountProgress.account_id == acct.id))
    step = progress.current_step_order if progress else 1

    V: dict[str, VersionData] = {v: VersionData(d={}, etc=[0.0] * 12) for v in ("cur", "prev", "prior", "plan")}
    basis_month = _basis_month(db)

    # 연구소가 직접 추가한 행(label이 있는 cost_entries 행)은 extra-rows 엔드포인트가 따로 내려주므로
    # d[pk]에서는 제외한다(두 군데서 중복·drift 방지).
    custom_keys: set[tuple[str, str]] = set(
        db.execute(
            select(CostEntry.scope_code, CostEntry.category_code).where(
                CostEntry.account_code == ACCOUNT_KEY, CostEntry.label.is_not(None)
            )
        ).all()
    )

    actualV: dict[str, ActualSnapshot] = {}

    # cur("속보") = forecast 버전 중 지금 기준월(basis_month) 1건
    if basis_month:
        forecast_yyyymm = _yyyymm(basis_year, basis_month)
        forecast_entries = [
            entry
            for entry in db.scalars(
                select(CostEntry).where(
                    CostEntry.account_code == ACCOUNT_KEY, CostEntry.version == "forecast", CostEntry.yyyymm == forecast_yyyymm,
                )
            )
            if entry.scope_code in lab_pks and (entry.scope_code, entry.category_code) not in custom_keys
        ]
        forecast_months = _entry_months_bulk(db, forecast_entries)
        for entry in forecast_entries:
            pk = entry.scope_code
            months = forecast_months[entry.id]
            if pk == "_ETC":
                V["cur"].etc = months
            else:
                V["cur"].d.setdefault(pk, {})[entry.category_code] = months

    # cur의 "실적" 스냅샷 = actual 버전 중 올해(basis_year) 안에서 가장 최근(마감월 최댓값) 확정분
    actual_rows = list(
        db.scalars(
            select(CostEntry).where(
                CostEntry.account_code == ACCOUNT_KEY,
                CostEntry.version == "actual",
                CostEntry.yyyymm.like(f"{basis_year:04d}%"),
            )
        )
    )
    if actual_rows:
        latest_yyyymm = max(e.yyyymm for e in actual_rows)
        athru = _ym_month(latest_yyyymm)
        snap = ActualSnapshot(thru_month=athru, d={}, etc=[0.0] * 12)
        latest_actual_entries = [
            entry
            for entry in actual_rows
            if entry.yyyymm == latest_yyyymm
            and entry.scope_code in lab_pks
            and (entry.scope_code, entry.category_code) not in custom_keys
        ]
        latest_actual_months = _entry_months_bulk(db, latest_actual_entries)
        for entry in latest_actual_entries:
            pk = entry.scope_code
            months = latest_actual_months[entry.id]
            if pk == "_ETC":
                snap.etc = months
            else:
                snap.d.setdefault(pk, {})[entry.category_code] = months
        actualV["cur"] = snap
        # prev(전실행) = cur의 "실적" 스냅샷(가장 최근에 마감된 달) 그대로.
        V["prev"].d = {pk: dict(cats) for pk, cats in snap.d.items()}
        V["prev"].etc = list(snap.etc)

    # prior(전년 실적) = 작년(basis_year - 1) actual 중 마감월이 가장 큰(그 해 마감 시점) 스냅샷.
    prior_year = basis_year - 1
    prior_rows = list(
        db.scalars(
            select(CostEntry).where(
                CostEntry.account_code == ACCOUNT_KEY,
                CostEntry.version == "actual",
                CostEntry.yyyymm.like(f"{prior_year:04d}%"),
            )
        )
    )
    if prior_rows:
        latest_prior_yyyymm = max(e.yyyymm for e in prior_rows)
        latest_prior_entries = [
            entry
            for entry in prior_rows
            if entry.yyyymm == latest_prior_yyyymm
            and entry.scope_code in lab_pks
            and (entry.scope_code, entry.category_code) not in custom_keys
        ]
        latest_prior_months = _entry_months_bulk(db, latest_prior_entries)
        for entry in latest_prior_entries:
            pk = entry.scope_code
            months = latest_prior_months[entry.id]
            if pk == "_ETC":
                V["prior"].etc = months
            else:
                V["prior"].d.setdefault(pk, {})[entry.category_code] = months

    # plan(경영계획) = management_plan_entries(plan_no="P00")에서.
    plan_entries = [
        entry
        for entry in db.scalars(
            select(ManagementPlanEntry).where(
                ManagementPlanEntry.account_code == ACCOUNT_KEY,
                ManagementPlanEntry.year == basis_year,
                ManagementPlanEntry.plan_no == PLAN_NO,
            )
        )
        if entry.scope_code in lab_pks
    ]
    plan_months = _plan_entry_months_bulk(db, [e.id for e in plan_entries])
    for entry in plan_entries:
        pk = entry.scope_code
        months = plan_months[entry.id]
        if pk == "_ETC":
            V["plan"].etc = months
        else:
            V["plan"].d.setdefault(pk, {})[entry.category_code] = months

    files: dict[str, dict[str, FileInfo]] = {v: {} for v in ("cur", "prev", "prior", "plan")}
    latest_upload: dict[tuple[str, str], Upload] = {
        (up.version, up.scope_key): up
        for up in db.scalars(_latest_uploads_stmt(lab_pks, list(files)))
    }
    for (version, pk), up in latest_upload.items():
        files[version][pk] = FileInfo(file=up.filename, at=up.uploaded_at.strftime("%m.%d %H:%M"))

    note: dict[str, dict[str, str]] = {}
    for n in db.scalars(select(Note).where(Note.account_key == ACCOUNT_KEY)):
        key = f"{n.base_version}|{n.period}|{n.scope}"
        note.setdefault(key, {})[n.category_key] = n.note_text

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
        for r in db.scalars(select(OverseasReasonRow).where(OverseasReasonRow.lab_code.in_(lab_codes)))
    ]

    # 외주용역 상세 — 마스터(행) + 디테일(월 금액)을 12개월 배열로 합쳐 내려준다.
    vendor_rows = list(
        db.scalars(
            select(OverseasVendorRow)
            .where(OverseasVendorRow.lab_code.in_(lab_codes))
            .order_by(OverseasVendorRow.lab_code, OverseasVendorRow.row_no)
        )
    )
    vendor_months: dict[int, list[float]] = {}
    if vendor_rows:
        for d in db.scalars(
            select(OverseasVendorRowDetail).where(
                OverseasVendorRowDetail.vendor_row_id.in_([r.id for r in vendor_rows]),
                OverseasVendorRowDetail.year == basis_year,
            )
        ):
            vendor_months.setdefault(d.vendor_row_id, [0.0] * 12)[d.month - 1] = float(d.amount)
    vendor = [
        VendorRow(
            lab_code=r.lab_code, version=r.version, row_no=r.row_no, category=r.category, project=r.project,
            company=r.company, description=r.description, m=vendor_months.get(r.id, [0.0] * 12),
            source_file=r.source_file,
        )
        for r in vendor_rows
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
        # NOTE: 연도 필터가 없어 모든 연도의 CP 행을 내려보낸다. basis_year로 좁히면 쿼리가 가벼워지지만
        # 화면이 다른 연도 CP를 같이 보는지 확인이 필요해 그대로 둔다(저장은 보낸 연도만 교체한다).
        for r in db.scalars(select(OverseasCpRow))
    ]

    return OverseasState(
        step=step, basis_year=basis_year, basis_month=basis_month, V=V, actualV=actualV, files=files, note=note,
        reason=reason, vendor=vendor, cp=cp,
    )


@router.put("/overseas/state")
def save_overseas_state(state: OverseasState, db: Session = Depends(get_db)) -> dict[str, str]:
    acct = _overseas_account(db)
    labs = _labs(db)
    lab_pks = {lab.pk for lab in labs}
    lab_codes = {lab.lab_code for lab in labs}

    unknown_pks: set[str] = set()
    for version, vd in state.V.items():
        for pk in vd.d:
            if pk not in lab_pks:
                unknown_pks.add(pk)
    for version, pk_files in state.files.items():
        unknown_pks.update(pk for pk in pk_files if pk not in lab_pks)
    if unknown_pks:
        raise HTTPException(status_code=400, detail=f"알 수 없는 연구소 코드: {sorted(unknown_pks)}")

    # 1) 비용 데이터 — "속보"(forecast) 스냅샷. cur만 지금 기준월(basis_month)의 yyyymm으로 저장한다.
    #    prev·prior는 actual 스냅샷에서 유도하는 값이라 따로 저장하지 않는다.
    # 행을 모아서 _bulk_upsert_*에 한 번에 넘긴다 — lab×category 조합마다 따로 upsert하면
    # 조합 수만큼 DB 왕복이 느는데, 해외연구소는 연구소×비용항목 조합이 많아서 영향이 크다.
    forecast_yyyymm = _yyyymm(state.basis_year, state.basis_month) if state.basis_month else None
    cost_rows: list[tuple[str, str, str, str, list[float]]] = []
    plan_rows: list[tuple[str, str, int, list[float]]] = []
    for version, vd in state.V.items():
        if version in ("prev", "prior"):
            continue
        if version == "cur":
            if not forecast_yyyymm:
                continue
            for pk, cats in vd.d.items():
                for category_key, months in cats.items():
                    cost_rows.append((pk, "forecast", category_key, forecast_yyyymm, months))
            if "_ETC" in lab_pks:
                cost_rows.append(("_ETC", "forecast", "etc", forecast_yyyymm, vd.etc))
        elif version == "plan":
            for pk, cats in vd.d.items():
                for category_key, months in cats.items():
                    plan_rows.append((pk, category_key, state.basis_year, months))
            if "_ETC" in lab_pks:
                plan_rows.append(("_ETC", "etc", state.basis_year, vd.etc))
    _bulk_upsert_cost_entries(db, cost_rows)
    _bulk_upsert_plan_entries(db, plan_rows)

    # 1-1) "N월실적" 헤더로 업로드된 확정 실적 스냅샷 (있을 때만, 지금은 cur만 사용)
    actual_cost_rows: list[tuple[str, str, str, str, list[float]]] = []
    for version, snap in state.actualV.items():
        actual_yyyymm = _yyyymm(state.basis_year, snap.thru_month)
        for pk, cats in snap.d.items():
            for category_key, months in cats.items():
                actual_cost_rows.append((pk, "actual", category_key, actual_yyyymm, months))
        if "_ETC" in lab_pks:
            actual_cost_rows.append(("_ETC", "actual", "etc", actual_yyyymm, snap.etc))
    _bulk_upsert_cost_entries(db, actual_cost_rows)

    # 2) 업로드 이력 (연구소×버전당 최신 파일명만 의미 있으므로, 바뀐 것만 새로 기록)
    # pk×version 조합마다 따로 조회하지 않고, 이 계정의 업로드 이력을 한 번에 읽어 메모리에서 비교한다.
    latest_filename: dict[tuple[str, str], str] = {
        (up.version, up.scope_key): up.filename
        for up in db.scalars(_latest_uploads_stmt(lab_pks, list(state.files)))
    }
    for version, pk_files in state.files.items():
        for pk, info in pk_files.items():
            if latest_filename.get((version, pk)) != info.file:
                db.add(Upload(account_key=ACCOUNT_KEY, scope_key=pk, version=version, filename=info.file))

    # 3) 증감 사유 메모
    note_rows: list[tuple[str, str, str, str, str]] = []
    for key, cats in state.note.items():
        parts = key.split("|")
        if len(parts) != 3:
            continue
        base_version, period, scope = parts
        for category_key, text in cats.items():
            note_rows.append((base_version, period, scope, category_key, text))
    _bulk_upsert_notes(db, note_rows)

    # 4-1) 증감사유 (연구소코드×버전×항목×구간 키로 upsert — cost처럼 보낸 것만 누적 반영)
    unknown_reason_codes = {r.lab_code for r in state.reason if r.lab_code not in lab_codes}
    if unknown_reason_codes:
        raise HTTPException(status_code=400, detail=f"알 수 없는 연구소 코드(증감사유): {sorted(unknown_reason_codes)}")
    _bulk_upsert_reason_rows(db, state.reason)

    # 4-2) 외주용역 상세 (연구소×버전 단위 전체 교체 — 행 수가 업로드마다 달라서 upsert만 하면
    # 지워진 행이 남는다. 보내지 않은 연구소·버전은 그대로 둔다. 월 금액은 디테일에 월 1행씩.)
    unknown_vendor_codes = {r.lab_code for r in state.vendor if r.lab_code not in lab_codes}
    if unknown_vendor_codes:
        raise HTTPException(status_code=400, detail=f"알 수 없는 연구소 코드(외주용역): {sorted(unknown_vendor_codes)}")
    for lab_code, version in {(r.lab_code, r.version) for r in state.vendor}:
        db.execute(
            delete(OverseasVendorRow).where(
                OverseasVendorRow.lab_code == lab_code, OverseasVendorRow.version == version
            )
        )
    if state.vendor:
        new_ids = (
            db.execute(
                insert(OverseasVendorRow).returning(OverseasVendorRow.id, sort_by_parameter_order=True),
                [
                    {
                        "lab_code": r.lab_code, "version": r.version, "row_no": r.row_no, "category": r.category,
                        "project": r.project, "company": r.company, "description": r.description,
                        "source_file": r.source_file,
                    }
                    for r in state.vendor
                ],
            )
            .scalars()
            .all()
        )
        vendor_details = [
            {"vendor_row_id": new_id, "year": state.basis_year, "month": month, "amount": float(amount)}
            for new_id, r in zip(new_ids, state.vendor)
            for month, amount in enumerate(r.m[:12], start=1)
        ]
        if vendor_details:
            db.execute(insert(OverseasVendorRowDetail), vendor_details)

    # 4-3) CP 계약 (연도 단위 전체 교체 — 연 1회 올리는 파일이라 사라진 POC·직급은 지운다)
    cp_years = {r.year for r in state.cp}
    if cp_years:
        db.execute(delete(OverseasCpRow).where(OverseasCpRow.year.in_(cp_years)))
    if state.cp:
        db.execute(
            insert(OverseasCpRow),
            [
                {
                    "year": r.year, "poc_type": r.poc_type, "poc_proj": r.poc_proj, "poc_desc": r.poc_desc, "title": r.title,
                    "dedicated": r.dedicated, "common": r.common, "man_month": r.man_month, "cost_year": r.cost_year,
                    "poc_cost_sum": r.poc_cost_sum, "poc_mm_sum": r.poc_mm_sum, "poc_direct": r.poc_direct,
                    "poc_ovh": r.poc_ovh, "poc_total": r.poc_total, "source_file": r.source_file,
                }
                for r in state.cp
            ],
        )

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


# ---------- 인건비 증감의 원인: 인원 효과 vs 단가 효과 ----------
# 화면(분석 › 증감분석 › 계정별 증감분석 › 계정별 상세)의 인건비 행 아래 줄과 같은 계산이다.
# frontend-html/shared/core.js의 ovLaborSplit / frontend/src/logic/calc.js의 ovLaborSplit와 같은 식을
# 쓴다 — 챗봇이 말하는 금액과 사용자가 보는 표가 어긋나면 안 되므로 식을 바꿀 때 세 곳을 같이 고칠 것.
_PERIOD_IX: dict[str, tuple[int, ...]] = {
    "y": tuple(range(12)),
    "q1": (0, 1, 2), "q2": (3, 4, 5), "q3": (6, 7, 8), "q4": (9, 10, 11),
    **{f"m{m}": (m - 1,) for m in range(1, 13)},
}
_LABOR_LEAVES = tuple(resolve_leaves("labor"))


def _rate_arrays(
    db: Session, year: int, month: int, kind: str, currencies: set[str], ix: tuple[int, ...]
) -> dict[str, list[float]]:
    """월별 적용 환율. 'USD'는 원/$이고(달마다 말일/월평균 기준을 따른다 — core.js ovRateOV와 같다),
    현지통화는 '현지통화/$'로 돌려준다(원/$ ÷ 원/현지, 항상 월평균 기준 — core.js ovLRate와 같다).

    저장된 fx_applied_rates를 읽지 않고 원본(fx_rates)에서 매번 계산한다 — 그 표에는 지금 기준월
    조합만 채워져 있어서 비교 버전(직전 실적 등)의 기준은 들어있지 않고, 없는 걸 읽으면 환율 0으로
    조용히 넘어가 금액이 전부 0이 되기 때문이다(실제로 그렇게 나왔다). 계산 함수는 화면의 fxSeries와
    같은 규칙을 이미 구현해 둔 fx.py의 것을 그대로 쓴다."""
    cache = _FxRateCache(db, year)
    rows = {
        rule: {(r.target_month, r.currency_code): float(r.rate)
               for r in _compute_applied_rows(cache, year, month, kind, rule)}
        for rule in ("avg", "last")
    }
    # 월 단위 예외는 한 번에 읽는다(달마다 조회하면 12번 왕복한다).
    default = _overseas_fx_default(db)
    overrides = {o.year_month: o.rule for o in db.scalars(select(OverseasFxRuleOverride))}
    usd: list[float] = []
    for m in range(1, 13):
        rule = overrides.get(f"{year:04d}-{m:02d}", default)
        usd.append(rows[rule].get((m, "USD"), 0.0))
    # 조회 기간에 쓰이는 달의 원/$가 비어 있으면 금액을 0으로 답하지 말고 실패시킨다 — 0은 "환율이
    # 0원"이 아니라 "환율을 못 찾았다"는 뜻이고, 그대로 답하면 챗봇이 틀린 금액을 말한다.
    missing = [j + 1 for j in ix if not usd[j]]
    if missing:
        raise HTTPException(
            404,
            f"{year}년 {month}월 {kind} 기준의 원/$ 환율이 없는 달이 있다(해당 월: "
            f"{', '.join(str(m) for m in missing)}). 기준정보 › 환율에 그 달 데이터를 먼저 넣어야 한다.",
        )
    out = {"USD": usd}
    for cur in currencies:
        # 현지/$ = (원/$) ÷ (원/현지). 분모가 없으면 0으로 두고, 호출부는 화면과 똑같이 "현지통화
        # 분리 없음"(= $ 연구소와 같은 계산)으로 처리한다.
        out[cur] = [
            (rows["avg"].get((m, "USD"), 0.0) / rows["avg"][(m, cur)]) if rows["avg"].get((m, cur)) else 0.0
            for m in range(1, 13)
        ]
    return out


@router.get("/overseas/labor-split", response_model=LaborSplitResult)
def get_labor_split(
    basis_year: int,
    base_version: str = "prev",
    period: str = "y",
    lab_scope: str = "ALL",
    db: Session = Depends(get_db),
) -> LaborSplitResult:
    if base_version not in ("prev", "prior", "plan"):
        raise HTTPException(400, "base_version은 prev(전실행) | prior(전년 실적) | plan(경영계획)만 쓸 수 있다.")
    ix = _PERIOD_IX.get(period)
    if ix is None:
        raise HTTPException(400, "period는 y(연간) | q1~q4 | m1~m12만 쓸 수 있다.")

    state = get_overseas_state(basis_year=basis_year, db=db)
    cur_d, base_d = state.V["cur"].d, state.V[base_version].d
    if not cur_d:
        raise HTTPException(404, f"{basis_year}년 당월 속보 데이터가 없다.")
    if not base_d:
        label = {"prev": "전실행", "prior": "전년 실적", "plan": "경영계획"}[base_version]
        raise HTTPException(404, f"{label} 데이터가 저장돼 있지 않아 인건비 증감을 나눌 수 없다.")

    # 합계 행(part_code="TOTAL")과 원화 기타(_ETC)는 다른 행을 더해 만든 값이라 빼야 한다 — 넣으면
    # SARC가 CPU+GPU+TOTAL로 두 번 세어져 인원과 금액이 정확히 두 배가 된다(실제로 그렇게 나왔다).
    # 화면도 같은 규칙이다(core.js의 ovPks는 파트 목록에 TOTAL을 두지 않는다).
    labs = {
        lab.pk: lab for lab in _labs(db)
        if lab.is_active and lab.pk != "_ETC" and (lab.part_code or "").upper() != "TOTAL"
    }
    scope = (lab_scope or "ALL").strip()
    if scope.upper() == "ALL":
        pks = [pk for pk in labs if pk in cur_d or pk in base_d]
    else:
        # "SARC"처럼 파트 없는 코드를 주면 그 연구소의 파트를 모두 더한다.
        pks = [pk for pk, lab in labs.items() if pk == scope or lab.lab_code == scope]
        if not pks:
            raise HTTPException(404, f"그런 연구소가 없다: {scope!r}")

    basis_month = state.basis_month or 1
    cur_spec = (basis_year, basis_month, "forecast")
    if base_version == "prev":
        snap = state.actualV.get("cur")
        if not snap:
            raise HTTPException(404, "전실행(직전 실적) 스냅샷이 없어 비교할 수 없다.")
        base_spec = (basis_year, snap.thru_month, "actual")
    elif base_version == "prior":
        base_spec = (basis_year - 1, 12, "actual")
    else:
        # 경영계획은 속보/실적 환율 기준이 없어 환차·환차外를 나눌 수 없다(화면도 같은 제약).
        raise HTTPException(
            400, "경영계획 대비는 환율 기준(속보/실적)이 없어 인원·단가로 나눌 수 없다. 화면 표를 보라."
        )

    # 현지통화 → $ 환차를 따로 나누는 통화. core.js의 ovLcOf와 똑같이 "통화가 USD가 아니면 현지통화"로
    # 본다 — CP도 통화가 ILS라 여기 포함된다.
    local_curs = {
        lab.currency for pk, lab in labs.items()
        if pk in pks and lab.currency and lab.currency != "USD"
    }
    rate_cur = _rate_arrays(db, *cur_spec, local_curs, ix)
    rate_base = _rate_arrays(db, *base_spec, local_curs, ix)

    rows: list[LaborSplitLab] = []
    tot = {"hc_cur": 0.0, "hc_base": 0.0, "amt_cur": 0.0, "amt_base": 0.0, "unsplit": 0.0}
    for pk in sorted(pks):
        lab = labs[pk]
        cats_cur, cats_base = cur_d.get(pk, {}), base_d.get(pk, {})
        lc = lab.currency if lab.currency and lab.currency != "USD" else None
        amt_cur = amt_base = hc_cur = hc_base = 0.0
        for j in ix:
            k_cur = sum(float(cats_cur.get(leaf, [0.0] * 12)[j]) for leaf in _LABOR_LEAVES)
            k_base = sum(float(cats_base.get(leaf, [0.0] * 12)[j]) for leaf in _LABOR_LEAVES)
            rb = rate_base["USD"][j]
            lrc = rate_cur.get(lc, [0.0] * 12)[j] if lc else 0.0
            lrb = rate_base.get(lc, [0.0] * 12)[j] if lc else 0.0
            # 비교 버전 환율로 환산해야 환율이 움직인 몫이 빠진다(= 환차外 기준).
            if lc and lrc > 0 and lrb > 0:
                amt_cur += k_cur * lrc / lrb * rb * 1000
            else:
                amt_cur += k_cur * rb * 1000
            amt_base += k_base * rb * 1000
            hc_cur += float(cats_cur.get("hc", [0.0] * 12)[j]) / len(ix)
            hc_base += float(cats_base.get("hc", [0.0] * 12)[j]) / len(ix)
        splittable = hc_cur > 0 and hc_base > 0
        p_base = amt_base / hc_base if splittable else 0.0
        p_cur = amt_cur / hc_cur if splittable else 0.0
        hc_eff = (hc_cur - hc_base) * p_base if splittable else 0.0
        rate_eff = hc_cur * (p_cur - p_base) if splittable else 0.0
        unsplit = 0.0 if splittable else amt_cur - amt_base
        rows.append(LaborSplitLab(
            lab_pk=pk, lab_name=lab.name, currency=lab.currency or "USD",
            hc_base=hc_base, hc_cur=hc_cur, per_head_base=p_base, per_head_cur=p_cur,
            hc_effect=hc_eff, rate_effect=rate_eff, unsplit=unsplit,
            fx_outside=hc_eff + rate_eff + unsplit,
        ))
        if splittable:
            tot["hc_cur"] += hc_cur
            tot["hc_base"] += hc_base
            tot["amt_cur"] += amt_cur
            tot["amt_base"] += amt_base
        else:
            tot["unsplit"] += unsplit

    # 합계는 연구소별 효과를 더하는 게 아니라 전체 인원·전체 금액으로 다시 계산한다 — 단가는 비율이라
    # 연구소별로 따로 낸 걸 더하면 뜻이 없다(집계 파생 행이 전체 합산만 쓰는 것과 같은 이유).
    pB = tot["amt_base"] / tot["hc_base"] if tot["hc_base"] else 0.0
    pC = tot["amt_cur"] / tot["hc_cur"] if tot["hc_cur"] else 0.0
    hc_effect = (tot["hc_cur"] - tot["hc_base"]) * pB
    rate_effect = tot["hc_cur"] * (pC - pB)
    return LaborSplitResult(
        basis_year=basis_year, period=period, base_version=base_version, lab_scope=scope,
        hc_base=tot["hc_base"], hc_cur=tot["hc_cur"], per_head_base=pB, per_head_cur=pC,
        hc_effect=hc_effect, rate_effect=rate_effect, unsplit=tot["unsplit"],
        fx_outside=hc_effect + rate_effect + tot["unsplit"],
        labor_cur=tot["amt_cur"] + tot["unsplit"], labor_base=tot["amt_base"],
        labs=rows,
        note=(
            "환율이 움직여서 변한 몫(환차)은 빼고 환차外만 인원 효과와 단가 효과로 나눈 값이다. "
            "두 효과의 합(+ 인력 없는 연구소 몫)이 fx_outside이고, 이 값은 화면 계정별 상세의 "
            "인건비 행 환차外 칸과 같아야 한다."
        ),
    )


# ---------- 외주용역비 과제별 집계 ----------
# "과제 기준으로 분석해줘"는 비용 본체로는 할 수 없다 — cost_entries의 키는 연월·계정과목·연구소·
# 버전·계정이고 과제 칸이 없다. 과제명이 붙어 있는 건 외주용역 시트(overseas_vendor_rows.project)와
# CP 계약뿐이라, 과제 기준 분석은 외주용역비 범위에서만 가능하다.
def _vendor_period_sums(
    db: Session, version: str, lab_code: str | None, ix: tuple[int, ...]
) -> dict[int, tuple[OverseasVendorRow, float]]:
    """그 버전의 외주용역 행별 (행, 조회 기간 합계)."""
    q = select(OverseasVendorRow).where(OverseasVendorRow.version == version)
    if lab_code:
        q = q.where(OverseasVendorRow.lab_code == lab_code)
    rows = list(db.scalars(q))
    if not rows:
        return {}
    months: dict[int, list[float]] = {}
    for d in db.scalars(
        select(OverseasVendorRowDetail).where(
            OverseasVendorRowDetail.vendor_row_id.in_([r.id for r in rows])
        )
    ):
        months.setdefault(d.vendor_row_id, [0.0] * 12)[d.month - 1] = float(d.amount)
    return {
        r.id: (r, sum(months.get(r.id, [0.0] * 12)[j] for j in ix))
        for r in rows
    }


@router.get("/overseas/vendor-by-project", response_model=VendorByProjectResult)
def get_vendor_by_project(
    version: str = "cur",
    base_version: str | None = None,
    lab_code: str | None = None,
    period: str = "y",
    db: Session = Depends(get_db),
) -> VendorByProjectResult:
    ix = _PERIOD_IX.get(period)
    if ix is None:
        raise HTTPException(400, "period는 y(연간) | q1~q4 | m1~m12만 쓸 수 있다.")
    for v in (version, base_version):
        if v is not None and v not in ("cur", "prev", "prior", "plan"):
            raise HTTPException(400, "version/base_version은 cur | prev | prior | plan만 쓸 수 있다.")

    cur = _vendor_period_sums(db, version, lab_code, ix)
    if not cur:
        where = f"{lab_code} 연구소의 " if lab_code else ""
        raise HTTPException(404, f"{where}{version} 외주용역 데이터가 없다.")
    base = _vendor_period_sums(db, base_version, lab_code, ix) if base_version else {}
    compared = bool(base_version) and bool(base)

    # 과제명이 빈 행은 "(과제 미지정)"으로 묶는다 — 조용히 빼면 합계가 화면과 안 맞는다.
    def group(src: dict[int, tuple[OverseasVendorRow, float]]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for row, amount in src.values():
            key = (row.project or "").strip() or "(과제 미지정)"
            g = out.setdefault(key, {"amount": 0.0, "count": 0, "labs": set(), "companies": set()})
            g["amount"] += amount
            g["count"] += 1
            g["labs"].add(row.lab_code)
            if row.company:
                g["companies"].add(row.company)
        return out

    g_cur, g_base = group(cur), group(base)
    rows: list[VendorProjectRow] = []
    for key in sorted(set(g_cur) | set(g_base)):
        c = g_cur.get(key, {"amount": 0.0, "count": 0, "labs": set(), "companies": set()})
        b = g_base.get(key)
        diff = c["amount"] - (b["amount"] if b else 0.0)
        if not compared:
            status = None
        elif not b or not b["amount"]:
            status = "신규"
        elif not c["amount"]:
            status = "종료"
        elif abs(diff) < 0.005:
            status = "동일"
        else:
            status = "변동"
        rows.append(VendorProjectRow(
            project=key, amount=c["amount"], item_count=c["count"],
            labs=sorted(c["labs"]), company_count=len(c["companies"]),
            # 비교하지 못했으면 0이 아니라 비워서 내려보낸다 — 0은 "증감 없음"으로 읽힌다.
            base_amount=(b["amount"] if b else 0.0) if compared else None,
            diff=diff if compared else None, status=status,
        ))
    rows.sort(key=lambda r: -abs(r.diff or 0.0) if compared else -r.amount)

    total = sum(a for _, a in cur.values())
    base_total = sum(a for _, a in base.values())
    base_label = {"cur": "당월 속보", "prev": "전실행", "prior": "전년 실적", "plan": "경영계획"}
    note = ""
    if base_version and not base:
        note = (
            f"{base_label.get(base_version, base_version)} 외주용역 데이터가 저장돼 있지 않아 증감을 "
            f"구할 수 없다. 그래서 base_amount·diff·status를 0이 아니라 비워서(null) 돌려준다 — "
            f"'증감이 없다'가 아니라 '증감을 알 수 없다'는 뜻이다. 사용자에게는 "
            f"{base_label.get(version, version)} 과제별 금액만 답하고, 증감은 그 데이터가 올라와야 "
            f"볼 수 있다고 설명해라. 0을 증감으로 말하지 마라."
        )
    return VendorByProjectResult(
        version=version, base_version=base_version, period=period, lab_code=lab_code,
        total=total, base_total=base_total if compared else None,
        diff=(total - base_total) if compared else None, compared=compared, projects=rows, note=note,
    )


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
# scope_code는 연구소 pk를 그대로 쓴다(공용 테이블이라 overseas_labs로 FK는 못 걸고, _check_lab_pk로 검증).
# version은 forecast(속보) | actual(실적) 두 값만 받는다 — plan(경영계획)은 /management-plan 쪽을 쓸 것.


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
    cost_entries 등에 남은 데이터는 FK가 없어 지워지지 않고, 집계에서만 조용히 빠진다."""
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


@router.put("/overseas/labs/one", response_model=LabOut)
def upsert_lab(body: LabIn, db: Session = Depends(get_db)) -> LabOut:
    """연구소 하나만 추가하거나 수정한다 — 다른 연구소는 전혀 건드리지 않는다. 연구소를 하나 추가·
    수정하는 거라면(예: 에이전트·스크립트) `PUT /overseas/labs`(전체 목록 교체, 빠뜨린 연구소는
    삭제됨) 대신 반드시 이걸 써라. 화면에 보이는 "연구소 기준정보 편집" 그리드(ovLabPage)의 저장만
    예외적으로 `PUT /overseas/labs`를 그대로 쓴다 — 그 화면은 "그리드에서 지운 행 = 삭제"라는 동작이
    필요하기 때문이다."""
    existing = list(db.scalars(select(OverseasLab).where(OverseasLab.lab_code == body.lab_code)))
    if not existing:
        next_order = (db.scalar(select(func.max(OverseasLab.sort_order))) or 0) + 10
        db.add(
            OverseasLab(
                pk=body.lab_code, lab_code=body.lab_code, part_code=None, name=body.name,
                country="", sort_order=next_order, currency=body.currency, is_active=body.is_active,
            )
        )
    else:
        # SARC처럼 lab_code 하나에 파트(pk)가 여러 개면 이름은 파트별로 다르므로
        # 단일 이름으로 덮어쓰지 않는다(통화·사용여부만 공통 적용) — save_labs와 동일한 규칙.
        for lab in existing:
            lab.currency = body.currency
            lab.is_active = body.is_active
            if len(existing) == 1:
                lab.name = body.name

    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"저장 실패 (제약조건 위반): {e.orig}") from e

    lab = db.scalars(select(OverseasLab).where(OverseasLab.lab_code == body.lab_code)).first()
    return LabOut(
        pk=lab.pk, lab_code=lab.lab_code, part_code=lab.part_code, name=lab.name, country=lab.country,
        sort_order=lab.sort_order, currency=lab.currency, is_active=lab.is_active,
    )


@router.delete("/overseas/labs/one")
def delete_lab(lab_code: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """연구소 하나(lab_code, 파트가 있으면 그 파트들 전부)를 삭제한다. cost_entries 등에 남은 데이터는
    FK가 없어 지워지지 않고, 집계에서만 조용히 빠진다(save_labs와 동일)."""
    db.execute(delete(OverseasLab).where(OverseasLab.lab_code == lab_code))
    db.commit()
    return {"status": "ok"}


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


@router.put("/overseas/lab-sheets", response_model=LabSheetOut)
def upsert_lab_sheet(body: LabSheetIn, db: Session = Depends(get_db)) -> LabSheetOut:
    """연구소(lab_code)에 시트 하나를 배정하거나(새 sheet_key) 표시 순서를 바꾼다(기존 sheet_key).
    sheet_key 자체(이름·존재 여부)는 common_codes(group_code='OVERSEAS_SHEET_TYPE')가 정하므로,
    아직 없는 종류의 시트면 먼저 upsert_common_code로 그 코드를 등록해야 한다."""
    names = {
        c.code: c.name
        for c in db.scalars(select(CommonCode).where(CommonCode.group_code == CodeGroup.OVERSEAS_SHEET_TYPE.value))
    }
    if body.sheet_key not in names:
        raise HTTPException(
            status_code=400,
            detail=f"알 수 없는 sheet_key: {body.sheet_key!r}. 먼저 common_codes(group_code='OVERSEAS_SHEET_TYPE')에 등록하세요.",
        )
    stmt = pg_insert(OverseasLabSheet).values(
        lab_code=body.lab_code, sheet_key=body.sheet_key, sheet_order=body.sheet_order,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["lab_code", "sheet_key"], set_={"sheet_order": stmt.excluded.sheet_order},
    )
    db.execute(stmt)
    db.commit()
    return LabSheetOut(lab_code=body.lab_code, sheet_key=body.sheet_key, name=names[body.sheet_key], sheet_order=body.sheet_order)


@router.delete("/overseas/lab-sheets")
def delete_lab_sheet(lab_code: str, sheet_key: str, db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(delete(OverseasLabSheet).where(OverseasLabSheet.lab_code == lab_code, OverseasLabSheet.sheet_key == sheet_key))
    db.commit()
    return {"status": "ok"}


def _check_version(version: str) -> str:
    if version not in ("forecast", "actual"):
        raise HTTPException(status_code=400, detail='version은 "forecast" 또는 "actual"이어야 합니다.')
    return version


@router.get("/overseas/cost-entries", response_model=list[CostEntryOut])
def list_cost_entries(
    yyyymm: str,
    version: str | None = None,
    lab_pk: str | None = None,
    category_key: str | None = None,
    db: Session = Depends(get_db),
) -> list[CostEntryOut]:
    """비용 헤더(cost_entries)의 월별 금액(cost_entry_details, period_type=MONTH)을 펼쳐서 내려준다.
    기본 12건(1~12월)이지만, 다음 연도치까지 저장돼 있으면 month가 13 이상(13=2년차 1월…)까지
    이어지고 year도 그만큼 넘어간다(기간을 12개월 너머로 늘려 받을 때 쓰는 확장 지점)."""
    stmt = select(CostEntry).where(CostEntry.account_code == ACCOUNT_KEY, CostEntry.yyyymm == yyyymm)
    if version:
        stmt = stmt.where(CostEntry.version == _check_version(version))
    if lab_pk:
        stmt = stmt.where(CostEntry.scope_code == _check_lab_pk(db, lab_pk))
    if category_key:
        stmt = stmt.where(CostEntry.category_code == category_key)
    entries = list(db.scalars(stmt))
    months_by_entry = _entry_months_bulk(db, entries)

    out: list[CostEntryOut] = []
    for entry in entries:
        months = months_by_entry[entry.id]
        base_year = _ym_year(entry.yyyymm)
        for i, amount in enumerate(months):
            out.append(
                CostEntryOut(
                    lab_pk=entry.scope_code, version=entry.version, category_key=entry.category_code,
                    yyyymm=entry.yyyymm, year=base_year + i // 12, month=i + 1, amount=amount,
                )
            )
    return out


@router.put("/overseas/cost-entries", response_model=CostEntryOut)
def upsert_cost_entry(body: CostEntryIn, db: Session = Depends(get_db)) -> CostEntryOut:
    _check_lab_pk(db, body.lab_pk)
    _check_version(body.version)
    _check_category(db, body.category_key)
    entry_id = _get_or_create_entry(db, body.lab_pk, body.version, body.category_key, body.yyyymm)
    base_year = _ym_year(body.yyyymm)
    year, period = base_year + (body.month - 1) // 12, (body.month - 1) % 12 + 1
    stmt = pg_insert(CostEntryDetail).values(
        cost_entry_id=entry_id, year=year, period_type="MONTH", period=period, col_name="", amount=body.amount,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["cost_entry_id", "year", "period_type", "period", "col_name"],
        set_={"amount": stmt.excluded.amount, "updated_at": datetime.now(timezone.utc)},
    )
    db.execute(stmt)
    db.commit()
    return CostEntryOut(lab_pk=body.lab_pk, version=body.version, category_key=body.category_key, yyyymm=body.yyyymm, year=year, month=body.month, amount=body.amount)


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
    보관한다(S3 설정이 있으면 S3, 없으면 로컬 폴더 — storage.upload_bytes 참고). 값이 틀어졌을 때
    되돌릴 유일한 근거이고, 양식이 바뀐 뒤 과거 파일을 그때 양식으로 다시 읽을 때도 필요하다.
    보관에 실패해도 s3_key만 NULL로 남을 뿐 업로드 이력 자체는 기록된다 — 이 엔드포인트가 막혀도
    /overseas/state 저장 흐름(파싱 결과)에는 영향이 없다."""
    _check_lab_pk(db, lab_pk)
    data = await file.read()
    # 파일명은 사용자가 준 값이라 경로 구분자나 ".."가 섞일 수 있다 — 보관 키에 그대로 쓰면 폴더
    # 밖을 가리킬 수 있어 마지막 경로 조각만 남기고 위험한 문자를 지운다(로컬 보관 시 특히 중요).
    safe_name = re.sub(r"[^\w.\-]", "_", Path(file.filename or "upload").name) or "upload"
    key = f"overseas/{lab_pk}/{version}/{datetime.now(timezone.utc):%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}_{safe_name}"
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


# ---------- 연구소가 직접 추가한 행 · 메모 열 · 메모 셀 ----------
# 행 = label이 있는 cost_entries 행(그 자체가 존재·이름·값을 다 가짐 — 별도 레지스트리 불필요).
# 열(메모 전용 열)은 비용 데이터에 home이 없어서 memo_cols에 따로 둔다.


@router.get("/overseas/memo-cols", response_model=list[MemoColOut])
def list_memo_cols(
    version: str | None = None, lab_pk: str | None = None, db: Session = Depends(get_db)
) -> list[MemoColOut]:
    stmt = select(MemoCol).where(MemoCol.account_key == ACCOUNT_KEY)
    if version:
        stmt = stmt.where(MemoCol.version == version)
    if lab_pk:
        stmt = stmt.where(MemoCol.scope_key == _check_lab_pk(db, lab_pk))
    return [
        MemoColOut(lab_pk=c.scope_key, version=c.version, col_key=c.col_key, label=c.label) for c in db.scalars(stmt)
    ]


@router.put("/overseas/memo-cols", response_model=MemoColOut)
def upsert_memo_col(body: MemoColIn, db: Session = Depends(get_db)) -> MemoColOut:
    _check_lab_pk(db, body.lab_pk)
    stmt = pg_insert(MemoCol).values(
        account_key=ACCOUNT_KEY, scope_key=body.lab_pk, version=body.version, col_key=body.col_key, label=body.label,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_key", "scope_key", "version", "col_key"],
        set_={"label": stmt.excluded.label, "updated_at": datetime.now(timezone.utc)},
    )
    db.execute(stmt)
    db.commit()
    return MemoColOut(**body.model_dump())


@router.delete("/overseas/memo-cols")
def delete_memo_col(lab_pk: str, version: str, col_key: str, db: Session = Depends(get_db)) -> dict[str, str]:
    """메모 열을 지운다 — DB가 FK로 못 묶어주므로 그 열에 달린 메모 셀(memos)도 같이 지운다."""
    _check_lab_pk(db, lab_pk)
    db.execute(
        delete(Memo).where(
            Memo.account_key == ACCOUNT_KEY, Memo.scope_key == lab_pk, Memo.version == version, Memo.col_key == col_key
        )
    )
    db.execute(
        delete(MemoCol).where(
            MemoCol.account_key == ACCOUNT_KEY,
            MemoCol.scope_key == lab_pk,
            MemoCol.version == version,
            MemoCol.col_key == col_key,
        )
    )
    db.commit()
    return {"status": "ok"}


@router.get("/overseas/memos", response_model=list[MemoOut])
def list_memos(version: str | None = None, lab_pk: str | None = None, db: Session = Depends(get_db)) -> list[MemoOut]:
    stmt = select(Memo).where(Memo.account_key == ACCOUNT_KEY)
    if version:
        stmt = stmt.where(Memo.version == version)
    if lab_pk:
        stmt = stmt.where(Memo.scope_key == _check_lab_pk(db, lab_pk))
    return [
        MemoOut(lab_pk=m.scope_key, version=m.version, row_key=m.row_key, col_key=m.col_key, text=m.text)
        for m in db.scalars(stmt)
    ]


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
        index_elements=["account_key", "scope_key", "version", "row_key", "col_key"],
        set_={"text": stmt.excluded.text, "updated_at": datetime.now(timezone.utc)},
    )
    db.execute(stmt)
    db.commit()
    return MemoOut(**body.model_dump())


# ---------- 파생 행·열의 계산식 평가 ----------
# 파생 행/열은 월별 금액을 저장하지 않고 계산식(formula)만 저장한다 — 원본(인건비 등)이 업로드로
# 바뀌면 파생값도 자동으로 따라 바뀌어야 하기 때문이다. 그래서 조회할 때마다 여기서 다시 계산한다.
# 평가를 프런트가 아니라 서버에서 하는 이유: 화면과 챗봇이 같은 API를 쓰게 해서, 두 군데가 따로
# 계산하다 서로 다른 숫자를 말하는 사고를 원천 차단하기 위함이다.
#
# 행 수식은 계정을 "세로로" 묶는다 — operands가 계정 키 목록이고 월별로(같은 달끼리) 계산한다.
#   {"op": "div_elementwise", "operands": ["labor", "hc"]}  → 인건비 ÷ 인력 (= 인당인건비)
# 열 수식은 월을 "가로로" 묶는다 — months가 월 번호 목록이고 각 행마다 그 달들을 계산한다.
#   {"op": "sum_months", "months": [7, 8, 9]}               → 3분기 합계
ROW_FORMULA_OPS = ("sum_elementwise", "div_elementwise")
COL_FORMULA_OPS = ("sum_months", "avg_months")

# 원시 행/열은 취합에서만, 파생 행/열은 집계에서만 지울 수 있다 — 추가한 화면에서만 치우게 해서
# 다른 단계 담당자가 남의 행을 모르고 지우는 걸 막는다. 단계 이름은 화면 상단 진행 표시의 짧은
# 이름(취합·집계·증감분석)과 같다. 단계는 브라우저 로컬 상태라 서버가 스스로 알 수 없고 호출부가
# 보내줘야 하므로, 이건 권한(authorization)이 아니라 실수 방지 장치다 — chat.py의 파생 행 차단과
# 같은 성격이다.
STEP_FOR_RAW, STEP_FOR_DERIVED = "취합", "집계"


def _check_delete_step(step: str | None, is_derived: bool, what: str) -> None:
    required = STEP_FOR_DERIVED if is_derived else STEP_FOR_RAW
    if step == required:
        return
    kind = "계산으로 만든 파생" if is_derived else "직접 입력한"
    raise HTTPException(
        status_code=400,
        detail=(
            f"이 {what}은 {kind} {what}이라 '{required}' 단계에서만 지울 수 있습니다"
            f"(지금 단계: '{step or '알 수 없음'}'). '{required}' 화면으로 이동한 뒤 지워주세요."
        ),
    )
# 근거 문장에 쓰는 연산 기호 — 사용자에게 "인건비 ÷ 인력"처럼 보여주기 위함.
_OP_SYMBOL = {"sum_elementwise": " + ", "div_elementwise": " ÷ "}


def _check_row_formula(formula: dict | None) -> dict:
    """파생 행의 계산식을 검증한다. 1차 범위는 '같은 연구소·버전·연월 안의 계정/행 참조'로 한정한다 —
    다른 연구소나 다른 연월을 섞는 수식은 참조 표현이 훨씬 복잡해져 아직 받지 않는다."""
    if not isinstance(formula, dict):
        raise HTTPException(status_code=400, detail="파생 행에는 formula(계산식)가 필요합니다.")
    op = formula.get("op")
    if op not in ROW_FORMULA_OPS:
        raise HTTPException(status_code=400, detail=f"알 수 없는 행 계산식 op: {op!r} ({' | '.join(ROW_FORMULA_OPS)})")
    operands = formula.get("operands")
    if not isinstance(operands, list) or not all(isinstance(k, str) and k for k in operands):
        raise HTTPException(status_code=400, detail="formula.operands는 계정 키 문자열 목록이어야 합니다.")
    if op == "div_elementwise" and len(operands) != 2:
        raise HTTPException(status_code=400, detail="div_elementwise는 operands가 정확히 2개([분자, 분모])여야 합니다.")
    if op == "sum_elementwise" and len(operands) < 2:
        raise HTTPException(status_code=400, detail="sum_elementwise는 operands가 2개 이상이어야 합니다.")
    return {"op": op, "operands": operands}


def _check_col_formula(formula: dict | None) -> dict:
    if not isinstance(formula, dict):
        raise HTTPException(status_code=400, detail="파생 열에는 formula(계산식)가 필요합니다.")
    op = formula.get("op")
    if op not in COL_FORMULA_OPS:
        raise HTTPException(status_code=400, detail=f"알 수 없는 열 계산식 op: {op!r} ({' | '.join(COL_FORMULA_OPS)})")
    months = formula.get("months")
    if not isinstance(months, list) or not months or not all(isinstance(m, int) and 1 <= m <= 12 for m in months):
        raise HTTPException(status_code=400, detail="formula.months는 1~12 사이 월 번호 목록이어야 합니다.")
    return {"op": op, "months": months}


def _months_by_category(db: Session, lab_pk: str, version: str, yyyymm: str) -> dict[str, list[float]]:
    """그 연구소·버전·연월에 저장된 모든 행의 월별 값을 category_code -> 월 배열로 모아 온다.
    파생 행(is_derived)은 제외한다 — 파생값을 다시 파생의 재료로 쓰면 평가가 꼬이고 순환할 수 있다.
    lab_pk가 ALL_SCOPE면 전체 연구소를 계정별로 합산한다(집계 화면의 파생 행이 쓰는 범위) —
    비율은 연구소별로 따로 계산해서 더하면 뜻이 없어지므로, 재료를 먼저 다 더한 뒤 계산해야 한다."""
    stmt = select(CostEntry).where(
        CostEntry.account_code == ACCOUNT_KEY,
        CostEntry.version == version,
        CostEntry.yyyymm == yyyymm,
        CostEntry.is_derived.is_(False),
    )
    if lab_pk != ALL_SCOPE:
        stmt = stmt.where(CostEntry.scope_code == lab_pk)
    entries = list(db.scalars(stmt))
    months_by_entry = _entry_months_bulk(db, entries)
    out: dict[str, list[float]] = {}
    for e in entries:
        acc = out.setdefault(e.category_code, [])
        for i, v in enumerate(months_by_entry[e.id]):
            if i < len(acc):
                acc[i] += v
            else:
                acc.append(v)
    return out


def _operand_months(key: str, by_category: dict[str, list[float]], width: int) -> list[float]:
    """계정 키 하나를 월 배열로 바꾼다. 집계 그룹(인건비 등)이면 리프까지 전개해서 월별로 더한다."""
    out = [0.0] * width
    for leaf in resolve_leaves(key):
        for i, v in enumerate(by_category.get(leaf, [])[:width]):
            out[i] += v
    return [round(v, 4) for v in out]


def _eval_row_formula(by_category: dict[str, list[float]], formula: dict) -> tuple[list[float], dict]:
    """파생 행의 계산식을 지금 저장된 원본 값으로 평가해 (월별 값, 근거)를 돌려준다.
    근거는 "인건비 1,000 ÷ 인력 10 = 100"처럼 사용자·챗봇에게 그대로 보여줄 수 있는 형태로 만든다.
    by_category는 _months_by_category 결과 — 파생 행이 여러 개일 때 매번 다시 조회하지 않도록
    호출부에서 (연구소·버전·연월)당 한 번만 구해서 넘긴다."""
    op, operand_keys = formula["op"], formula["operands"]
    # 재료들의 월 개수가 다를 수 있어(어떤 계정만 24개월치가 들어온 경우) 가장 긴 쪽에 맞춘다.
    width = max((len(v) for v in by_category.values()), default=12) or 12
    series = [_operand_months(k, by_category, width) for k in operand_keys]

    values: list[float] = []
    for i in range(width):
        if op == "sum_elementwise":
            values.append(round(sum(s[i] for s in series), 4))
        else:  # div_elementwise — 분모가 0인 달은 계산할 수 없어 0으로 둔다(근거에 그대로 드러난다)
            numerator, denominator = series[0][i], series[1][i]
            values.append(round(numerator / denominator, 4) if denominator else 0.0)

    operands_basis = [
        {"key": k, "label": category_label(k), "months": s, "sum": round(sum(s), 4)}
        for k, s in zip(operand_keys, series)
    ]
    # 연간 근거: 합이면 각 재료의 연간합을 더하고, 나누기면 "분자 연간합 ÷ 분모 연간합"으로 낸다
    # (월별 비율 12개를 더하는 건 뜻이 없다 — 연간 인당인건비는 연간 인건비 ÷ 연간 인력이다).
    sums = [o["sum"] for o in operands_basis]
    annual = round(sum(sums), 4) if op == "sum_elementwise" else (round(sums[0] / sums[1], 4) if sums[1] else 0.0)
    basis = {
        "text": _OP_SYMBOL[op].join(o["label"] for o in operands_basis),
        "op": op,
        "operands": operands_basis,
        "months": [
            {"month": i + 1, "values": [s[i] for s in series], "result": values[i]}
            for i in range(min(width, 12))
        ],
        "annual": {"values": sums, "result": annual},
    }
    return values, basis


def _eval_col_formula(formula: dict, row_months: list[float]) -> tuple[float, dict]:
    """파생 열의 계산식을 한 행의 월별 값에 적용해 (칸 값, 근거)를 돌려준다."""
    op, months = formula["op"], formula["months"]
    picked = [(m, round(row_months[m - 1], 4) if m - 1 < len(row_months) else 0.0) for m in months]
    total = round(sum(v for _, v in picked), 4)
    value = total if op == "sum_months" else round(total / len(picked), 4)
    return value, {
        "text": f"{'+'.join(f'{m}월' for m in months)}{'' if op == 'sum_months' else ' 평균'}",
        "op": op,
        "months": [{"month": m, "value": v} for m, v in picked],
        "result": value,
    }


@router.get("/overseas/extra-rows", response_model=list[ExtraRowOut])
def list_extra_rows(
    yyyymm: str,
    version: str | None = None,
    lab_pk: str | None = None,
    db: Session = Depends(get_db),
) -> list[ExtraRowOut]:
    stmt = select(CostEntry).where(
        CostEntry.account_code == ACCOUNT_KEY, CostEntry.label.is_not(None), CostEntry.yyyymm == yyyymm,
    )
    if version:
        stmt = stmt.where(CostEntry.version == _check_version(version))
    if lab_pk:
        stmt = stmt.where(CostEntry.scope_code == _check_lab_pk(db, lab_pk, allow_all=True))

    entries = list(db.scalars(stmt))
    months_by_entry = _entry_months_bulk(db, entries)
    # 파생 행은 저장된 월값이 없고 계산식으로 매번 계산한다. 재료값(원시 행 전체)은 그 행이 속한
    # (연구소·버전) 조합당 한 번만 조회해서 돌려 쓴다 — 파생 행 수만큼 다시 조회하지 않도록.
    by_category_cache: dict[tuple[str, str], dict[str, list[float]]] = {}
    out: list[ExtraRowOut] = []
    for entry in entries:
        values, basis = months_by_entry[entry.id], None
        if entry.is_derived and entry.formula:
            cache_key = (entry.scope_code, entry.version)
            if cache_key not in by_category_cache:
                by_category_cache[cache_key] = _months_by_category(db, entry.scope_code, entry.version, yyyymm)
            values, basis = _eval_row_formula(by_category_cache[cache_key], entry.formula)
        out.append(
            ExtraRowOut(
                lab_pk=entry.scope_code, version=entry.version, row_key=entry.category_code, label=entry.label,
                label_en=entry.label_en or "", yyyymm=yyyymm, anchor_key=entry.anchor_key, v=values,
                is_derived=entry.is_derived, formula=entry.formula, basis=basis,
            )
        )
    return out


@router.put("/overseas/extra-rows", response_model=ExtraRowOut)
def upsert_extra_row(body: ExtraRowIn, db: Session = Depends(get_db)) -> ExtraRowOut:
    # 파생 행은 월별 값을 저장하지 않고 계산식만 저장한다 — 원본이 업로드로 바뀌면 파생값도 자동으로
    # 따라 바뀌어야 하므로, 조회할 때마다 계산식을 다시 평가한다(저장된 숫자는 금방 낡기 때문).
    formula = None
    if body.is_derived:
        formula = _check_row_formula(body.formula)
    elif not body.v or len(body.v) % 12 != 0:
        raise HTTPException(status_code=400, detail="v는 12개월치 값이거나(기본), 12의 배수(24·36…)여야 합니다.")
    # 전체 합산 범위(ALL)는 집계 화면의 파생 행 전용이다 — 원시 행(연구소가 실제로 보고한 값)은
    # 반드시 실재하는 연구소에 붙어야 하므로 ALL을 받지 않는다.
    _check_lab_pk(db, body.lab_pk, allow_all=body.is_derived)
    _check_version(body.version)
    # row_key가 하필 고정 계정 code(예: "sal")와 같으면 _get_or_create_entry가 그 업로드된 실제
    # 행을 "찾아서" 돌려주고, 아래 update가 그 label·월별 값을 custom row 내용으로 덮어써 버린다 —
    # 업로드 데이터가 조용히 사라지는 사고를 막기 위해 미리 막는다(label이 NULL인 기존 행 = 업로드행).
    existing = db.scalar(
        select(CostEntry).where(
            CostEntry.account_code == ACCOUNT_KEY, CostEntry.scope_code == body.lab_pk,
            CostEntry.version == body.version, CostEntry.category_code == body.row_key,
            CostEntry.yyyymm == body.yyyymm,
        )
    )
    if existing is not None and existing.label is None:
        raise HTTPException(
            status_code=400,
            detail=f"row_key '{body.row_key}'는 업로드된 고정 계정과 같습니다. 다른 row_key를 쓰세요.",
        )
    entry_id = _get_or_create_entry(db, body.lab_pk, body.version, body.row_key, body.yyyymm)
    db.execute(
        update(CostEntry).where(CostEntry.id == entry_id)
        .values(
            label=body.label, label_en=body.label_en, anchor_key=body.anchor_key,
            is_derived=body.is_derived, formula=formula,
        )
    )
    if body.is_derived:
        # 원시 행 → 파생 행으로 바뀐 경우 예전에 저장돼 있던 월값이 남아 있으면 안 된다
        # (계산식 결과와 섞여 어느 쪽이 참인지 알 수 없어진다).
        db.execute(
            delete(CostEntryDetail).where(
                CostEntryDetail.cost_entry_id == entry_id, CostEntryDetail.period_type == "MONTH"
            )
        )
    else:
        _upsert_entry_months(db, entry_id, _ym_year(body.yyyymm), body.v)
    db.commit()

    result = ExtraRowOut(**body.model_dump())
    if body.is_derived:
        # 방금 저장한 계산식을 바로 평가해서 값과 근거를 함께 돌려준다 — 호출부(화면·챗봇)가
        # 저장 직후 다시 조회하지 않고도 "인건비 1,000 ÷ 인력 10 = 100"을 보여줄 수 있게.
        result.formula = formula
        result.v, result.basis = _eval_row_formula(
            _months_by_category(db, body.lab_pk, body.version, body.yyyymm), formula
        )
    return result


@router.delete("/overseas/extra-rows")
def delete_extra_row(
    lab_pk: str, version: str, row_key: str, yyyymm: str, step: str | None = None, db: Session = Depends(get_db)
) -> dict[str, str]:
    """cost_entries 행을 지운다(디테일도 FK cascade로 같이 삭제됨). label이 있는(=연구소가 직접 추가한)
    행만 지운다 — label.is_not(None) 조건이 없으면 row_key가 하필 고정 계정 code(예: "sal")와 같을 때
    업로드된 실제 데이터까지 지워버릴 수 있다.
    step: 지금 보고 있는 화면 단계(취합|집계) — 추가한 단계에서만 지울 수 있게 검사한다."""
    _check_lab_pk(db, lab_pk, allow_all=True)
    _check_version(version)
    target = db.scalar(
        select(CostEntry).where(
            CostEntry.account_code == ACCOUNT_KEY,
            CostEntry.scope_code == lab_pk,
            CostEntry.version == version,
            CostEntry.category_code == row_key,
            CostEntry.yyyymm == yyyymm,
            CostEntry.label.is_not(None),
        )
    )
    if target is None:
        return {"status": "ok"}  # 이미 없음 — 멱등하게 성공으로 둔다
    _check_delete_step(step, target.is_derived, "행")
    db.execute(delete(CostEntry).where(CostEntry.id == target.id))
    db.commit()
    return {"status": "ok"}


# 추가 열은 extra_cols 테이블이 존재·이름·파생 여부를 관리하고, 원시 열의 칸 값은 그대로
# cost_entry_details(period_type="CUSTOM", col_name=열 라벨)에 저장된다. 파생 열은 칸 값을 저장하지
# 않고 계산식(월을 가로로 묶음)으로 조회할 때마다 계산한다.
# label 자체가 열 식별자라 열 이름을 바꾸는 건 지원하지 않는다(지우고 새 이름으로 다시 넣으면 됨).
# 원시 열을 처음 만들 때는 "hc"(인력) 행을 앵커로 쓴다(모든 연구소×버전에 항상 존재하는 고정
# 계정이라, 아직 아무 칸도 안 채운 빈 열도 안정적으로 표시할 수 있다).
_EXTRA_ANCHOR_CATEGORY = "hc"


@router.get("/overseas/extra-cols", response_model=list[ExtraColOut])
def list_extra_cols(
    yyyymm: str, version: str | None = None, lab_pk: str | None = None, db: Session = Depends(get_db)
) -> list[ExtraColOut]:
    stmt = select(ExtraCol).where(ExtraCol.account_key == ACCOUNT_KEY, ExtraCol.yyyymm == yyyymm)
    if version:
        stmt = stmt.where(ExtraCol.version == _check_version(version))
    if lab_pk:
        # 집계 화면은 전체 합산 범위(ALL)로도 조회한다 — 그 범위에 열은 없어서 빈 목록이 맞는 답이다.
        stmt = stmt.where(ExtraCol.scope_key == _check_lab_pk(db, lab_pk, allow_all=True))
    return [
        ExtraColOut(
            lab_pk=c.scope_key, version=c.version, yyyymm=c.yyyymm, label=c.label,
            is_derived=c.is_derived, formula=c.formula,
        )
        for c in db.scalars(stmt)
    ]


@router.put("/overseas/extra-cols", response_model=ExtraColOut)
def upsert_extra_col(body: ExtraColIn, db: Session = Depends(get_db)) -> ExtraColOut:
    """열 자체(존재·이름·파생 여부)를 등록한다. 원시 열의 칸 값은 /overseas/cost-extras로 따로 넣고,
    파생 열은 칸 값 없이 이 등록만으로 끝난다(값은 계산식으로 매번 계산되므로)."""
    # 집계 화면의 파생 열은 연구소 하나가 아니라 전체 합산 범위(ALL)에 붙는다 — 행과 같은 규칙.
    # 원시 열(사람이 칸마다 값을 넣는 열)은 반드시 실재하는 연구소에 붙어야 하므로 ALL을 받지 않는다.
    _check_lab_pk(db, body.lab_pk, allow_all=body.is_derived)
    _check_version(body.version)
    formula = _check_col_formula(body.formula) if body.is_derived else None
    stmt = pg_insert(ExtraCol).values(
        account_key=ACCOUNT_KEY, scope_key=body.lab_pk, version=body.version, yyyymm=body.yyyymm,
        label=body.label, is_derived=body.is_derived, formula=formula,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_key", "scope_key", "version", "yyyymm", "label"],
        set_={
            "is_derived": stmt.excluded.is_derived,
            "formula": stmt.excluded.formula,
            "updated_at": datetime.now(timezone.utc),
        },
    )
    db.execute(stmt)
    db.commit()
    return ExtraColOut(**body.model_dump() | {"formula": formula})


@router.get("/overseas/cost-extras", response_model=list[ExtraValueOut])
def list_cost_extras(
    yyyymm: str, version: str | None = None, lab_pk: str | None = None, db: Session = Depends(get_db)
) -> list[ExtraValueOut]:
    stmt = select(CostEntry).where(CostEntry.account_code == ACCOUNT_KEY, CostEntry.yyyymm == yyyymm)
    if version:
        stmt = stmt.where(CostEntry.version == _check_version(version))
    if lab_pk:
        stmt = stmt.where(CostEntry.scope_code == _check_lab_pk(db, lab_pk, allow_all=True))
    entries = list(db.scalars(stmt))
    entries_by_id = {e.id: e for e in entries}
    if not entries_by_id:
        return []
    out: list[ExtraValueOut] = []
    for row in db.scalars(
        select(CostEntryDetail).where(
            CostEntryDetail.cost_entry_id.in_(list(entries_by_id)), CostEntryDetail.period_type == "CUSTOM"
        )
    ):
        entry = entries_by_id[row.cost_entry_id]
        out.append(
            ExtraValueOut(
                lab_pk=entry.scope_code, version=entry.version, row_key=entry.category_code, yyyymm=yyyymm,
                label=row.col_name, value=float(row.amount),
            )
        )

    # 파생 열은 칸 값이 저장돼 있지 않다 — 그 열의 계산식을 각 행의 월별 값에 적용해서 칸을 만들어
    # 준다(원본이 바뀌면 자동으로 따라 바뀌어야 하므로 조회 시마다 계산). 근거도 함께 담는다.
    col_stmt = select(ExtraCol).where(
        ExtraCol.account_key == ACCOUNT_KEY, ExtraCol.yyyymm == yyyymm, ExtraCol.is_derived.is_(True)
    )
    if version:
        col_stmt = col_stmt.where(ExtraCol.version == version)
    if lab_pk:
        col_stmt = col_stmt.where(ExtraCol.scope_key == lab_pk)
    derived_cols = list(db.scalars(col_stmt))
    if derived_cols:
        months_by_entry = _entry_months_bulk(db, entries)
        entries_by_scope: dict[tuple[str, str], list[CostEntry]] = {}
        for entry in entries:
            entries_by_scope.setdefault((entry.scope_code, entry.version), []).append(entry)
        for col in derived_cols:
            if not col.formula:
                continue
            for entry in entries_by_scope.get((col.scope_key, col.version), []):
                value, basis = _eval_col_formula(col.formula, months_by_entry[entry.id])
                out.append(
                    ExtraValueOut(
                        lab_pk=entry.scope_code, version=entry.version, row_key=entry.category_code,
                        yyyymm=yyyymm, label=col.label, value=value, basis=basis,
                    )
                )
    return out


@router.put("/overseas/cost-extras", response_model=ExtraValueOut)
def upsert_cost_extra(body: ExtraValueIn, db: Session = Depends(get_db)) -> ExtraValueOut:
    _check_lab_pk(db, body.lab_pk)
    _check_version(body.version)
    # 칸 값을 넣으면 그 열이 원시 열로 등록돼 있어야 한다(아직 없으면 지금 등록). 파생 열에는 칸 값을
    # 직접 넣을 수 없다 — 계산식 결과와 수동 입력값이 섞이면 어느 쪽이 참인지 알 수 없어진다.
    existing_col = db.scalar(
        select(ExtraCol).where(
            ExtraCol.account_key == ACCOUNT_KEY, ExtraCol.scope_key == body.lab_pk,
            ExtraCol.version == body.version, ExtraCol.yyyymm == body.yyyymm, ExtraCol.label == body.label,
        )
    )
    if existing_col is not None and existing_col.is_derived:
        raise HTTPException(
            status_code=400,
            detail=f"'{body.label}'은 계산식으로 값이 나오는 파생 열이라 칸에 값을 직접 넣을 수 없습니다.",
        )
    if existing_col is None:
        db.execute(
            pg_insert(ExtraCol)
            .values(
                account_key=ACCOUNT_KEY, scope_key=body.lab_pk, version=body.version, yyyymm=body.yyyymm,
                label=body.label, is_derived=False, formula=None,
            )
            .on_conflict_do_nothing(index_elements=["account_key", "scope_key", "version", "yyyymm", "label"])
        )
    entry_id = _get_or_create_entry(db, body.lab_pk, body.version, body.row_key, body.yyyymm)
    stmt = pg_insert(CostEntryDetail).values(
        cost_entry_id=entry_id, year=_ym_year(body.yyyymm), period_type="CUSTOM", period=0, col_name=body.label,
        amount=body.value,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["cost_entry_id", "year", "period_type", "period", "col_name"],
        set_={"amount": stmt.excluded.amount, "updated_at": datetime.now(timezone.utc)},
    )
    db.execute(stmt)
    db.commit()
    return ExtraValueOut(**body.model_dump())


@router.delete("/overseas/extra-cols")
def delete_extra_col(
    lab_pk: str, version: str, label: str, yyyymm: str, step: str | None = None, db: Session = Depends(get_db)
) -> dict[str, str]:
    """열 등록을 지우고, 그 열(label)이 달린 같은 yyyymm의 모든 행에서 칸 값도 같이 지운다.
    step: 지금 보고 있는 화면 단계(취합|집계) — 추가한 단계에서만 지울 수 있게 검사한다."""
    _check_lab_pk(db, lab_pk, allow_all=True)
    _check_version(version)
    target = db.scalar(
        select(ExtraCol).where(
            ExtraCol.account_key == ACCOUNT_KEY, ExtraCol.scope_key == lab_pk,
            ExtraCol.version == version, ExtraCol.yyyymm == yyyymm, ExtraCol.label == label,
        )
    )
    # 레지스트리가 생기기 전에 만들어졌는데 백필에서 빠진 열(칸 값이 하나도 없던 빈 열)도 지울 수
    # 있어야 하므로, 등록이 없으면 원시 열로 간주해서 단계만 확인하고 칸 값 삭제로 넘어간다.
    _check_delete_step(step, bool(target and target.is_derived), "열")
    if target is not None:
        db.execute(delete(ExtraCol).where(ExtraCol.id == target.id))
    entry_ids = [
        e.id
        for e in db.scalars(
            select(CostEntry).where(
                CostEntry.account_code == ACCOUNT_KEY, CostEntry.scope_code == lab_pk,
                CostEntry.version == version, CostEntry.yyyymm == yyyymm,
            )
        )
    ]
    if entry_ids:
        db.execute(
            delete(CostEntryDetail).where(
                CostEntryDetail.cost_entry_id.in_(entry_ids), CostEntryDetail.period_type == "CUSTOM",
                CostEntryDetail.col_name == label,
            )
        )
    db.commit()
    return {"status": "ok"}


# ---------- 외주용역 상세 조회 ----------
# 저장은 /overseas/state의 vendor가 한다(업로드 단위가 시트 1장이라 행 단위 쓰기 API가 필요 없다).


@router.get("/overseas/vendor-rows", response_model=list[VendorRow])
def list_vendor_rows(
    lab_code: str | None = None, version: str = "cur", year: int | None = None, db: Session = Depends(get_db)
) -> list[VendorRow]:
    stmt = select(OverseasVendorRow).where(OverseasVendorRow.version == version).order_by(OverseasVendorRow.row_no)
    if lab_code:
        stmt = stmt.where(OverseasVendorRow.lab_code == lab_code)
    rows = list(db.scalars(stmt))
    if not rows:
        return []
    detail_stmt = select(OverseasVendorRowDetail).where(
        OverseasVendorRowDetail.vendor_row_id.in_([r.id for r in rows])
    )
    if year:
        detail_stmt = detail_stmt.where(OverseasVendorRowDetail.year == year)
    months: dict[int, list[float]] = {}
    for d in db.scalars(detail_stmt):
        months.setdefault(d.vendor_row_id, [0.0] * 12)[d.month - 1] = float(d.amount)
    return [
        VendorRow(
            lab_code=r.lab_code, version=r.version, row_no=r.row_no, category=r.category, project=r.project,
            company=r.company, description=r.description, m=months.get(r.id, [0.0] * 12), source_file=r.source_file,
        )
        for r in rows
    ]


# ---------- 구조 미정 시트(열 구성이 확정 전인 새 시트)의 원본 행 ----------
# 열 구성이 아직 확정되지 않아 cost_entries 같은 정형 테이블 대신 JSON 한 덩어리(sheet_rows.data)로
# 우선 보관한다. 열이 확정되면 외주용역처럼 전용 테이블로 옮긴다(→ overseas_vendor_rows).


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
        set_={"sort_order": stmt.excluded.sort_order, "data": stmt.excluded.data, "updated_at": datetime.now(timezone.utc)},
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


def _upsert_note(db: Session, base_version: str, period: str, scope: str, category_key: str, text: str) -> None:
    """단일 메모 저장용(PUT /overseas/notes). 배치 저장(save_overseas_state)은 _bulk_upsert_notes를 쓴다."""
    stmt = pg_insert(Note).values(
        account_key=ACCOUNT_KEY, base_version=base_version, period=period, scope=scope, category_key=category_key, note_text=text
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_key", "base_version", "period", "scope", "category_key"],
        set_={"note_text": stmt.excluded.note_text, "updated_at": datetime.now(timezone.utc)},
    )
    db.execute(stmt)


def _bulk_upsert_notes(db: Session, rows: list[tuple[str, str, str, str, str]]) -> None:
    """rows: (base_version, period, scope, category_key, text). 메모 수만큼 따로 upsert하지 않고 한 번에 처리한다."""
    dedup = {(bv, p, s, ck): text for bv, p, s, ck, text in rows}
    if not dedup:
        return
    stmt = pg_insert(Note).values(
        [
            {"account_key": ACCOUNT_KEY, "base_version": bv, "period": p, "scope": s, "category_key": ck, "note_text": text}
            for (bv, p, s, ck), text in dedup.items()
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_key", "base_version", "period", "scope", "category_key"],
        set_={"note_text": stmt.excluded.note_text, "updated_at": datetime.now(timezone.utc)},
    )
    db.execute(stmt)


def _bulk_upsert_reason_rows(db: Session, reason_rows: list[ReasonRow]) -> None:
    dedup: dict[tuple[str, str, str, str], ReasonRow] = {
        (r.lab_code, r.version, r.category_key, r.block): r for r in reason_rows
    }
    if not dedup:
        return
    set_cols = (
        "mon_label", "plan_value", "prev_value", "cur_value", "diff_plan", "diff_prev",
        "remark_plan", "remark_prev", "prev_label", "cur_label", "source_file",
    )
    stmt = pg_insert(OverseasReasonRow).values(
        [
            {
                "lab_code": r.lab_code, "version": r.version, "category_key": r.category_key, "block": r.block,
                "mon_label": r.mon_label, "plan_value": r.plan_value, "prev_value": r.prev_value, "cur_value": r.cur_value,
                "diff_plan": r.diff_plan, "diff_prev": r.diff_prev, "remark_plan": r.remark_plan, "remark_prev": r.remark_prev,
                "prev_label": r.prev_label, "cur_label": r.cur_label, "source_file": r.source_file,
            }
            for r in dedup.values()
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["lab_code", "version", "category_key", "block"],
        set_={k: getattr(stmt.excluded, k) for k in set_cols},
    )
    db.execute(stmt)


