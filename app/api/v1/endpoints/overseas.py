import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import anomaly, row_match
from app.core.reason_translate import MAX_LEN, MAX_TEXTS, engine_label, normalize, resolve_provider, translate_batch
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
    AnomalyRule,
    AnomalyRuleLog,
    CarryDef,
    OverseasCpRow,
    OverseasLab,
    OverseasLabRevision,
    OverseasLabSheet,
    OverseasReasonRow,
    TextTranslation,
    RowAlias,
    OverseasVendorRow,
    OverseasVendorRowDetail,
    OverseasFxRuleOverride,
    SheetRow,
    Upload,
)
from app.core.cost_groups import CATEGORY_LABELS, CATEGORY_LABELS_EN, GROUP_CALC, category_label, resolve_leaves
from app.core.periods import PERIOD_IX, QUARTER_END_IX, ym_month, ym_year, yyyymm
from app.core.values import n, source_hash
from app.services.overseas.aggregate import (
    INPUT_KEYS,
    LABOR_TREE,
    STEP_COUNT,
    TOT_LEAVES,
    TOT_TREE,
    cp_quarter_amounts,
    extra_row_months,
    group_tree,
    krw_months,
)
from app.services.overseas.entries import (
    current_basis_month,
    bulk_upsert_cost_entries,
    bulk_upsert_plan_entries,
    ensure_plan_version,
    entry_months_bulk,
    get_or_create_entry,
    plan_entry_months_bulk,
    upsert_entry_months,
)
from app.services.overseas.fxcalc import rate_arrays
from app.services.overseas.extra_rows import (
    OP_SYMBOL,
    check_lab_pk,
    check_version,
    eval_row_formula,
    months_by_category,
    read_extra_rows,
)
from app.services.overseas.state import build_state, latest_uploads_stmt, state_token
from app.services.overseas.labs import ACCOUNT_KEY, ALL_SCOPE, LAB_EPOCH, LabView, all_labs, lab_views, overseas_account
# 환산 기준(말일 vs 월평균)의 기본값을 읽는 헬퍼. fx.py는 overseas.py를 import하지 않으므로 순환이
# 생기지 않고, 기본값 폴백 규칙을 여기에 두 번 적어 두 곳이 어긋나는 걸 피하려고 그대로 가져다 쓴다.
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
    OverseasSummary,
    OverseasSummaryLab,
    ReasonRow,
    ReasonTranslateIn,
    ReasonTranslateItem,
    AnomalyFinding,
    AnomalyRuleIn,
    AnomalyRuleLogOut,
    AnomalyRuleOut,
    AnomalyScanIn,
    AnomalyScanResult,
    CarryDefIn,
    CarryDefOut,
    CarryForwardIn,
    CarryForwardResult,
    ReasonTranslateResult,
    RowAliasIn,
    RowAliasOut,
    RowMatchIn,
    RowMatchItem,
    RowMatchResult,
    RowMatchSuggestion,
    SheetRowIn,
    SheetRowOut,
    StepUpdate,
    UploadIn,
    UploadOut,
    VendorRow,
    VersionData,
)

router = APIRouter()


# 경영계획(plan) 역할태그는 cost_entries에 저장하지 않고 management_plan_entries가 담당한다
# (plan_no별로 영구 보관·비교해야 해서 cost_entries의 forecast/actual과는 다른 테이블).
# 해외연구소 화면엔 plan_no 선택 UI가 아직 없어 "P00" 고정 버전 하나만 쓴다.
PLAN_NO = "P00"


def _current_basis_ym(db: Session) -> str:
    """지금 편성 중인 기준연월 "YYYY-MM" — 기준정보를 바꿀 때 기본 적용 시점(= "이 버전부터")."""
    latest = db.scalar(
        select(func.max(CostEntry.yyyymm)).where(CostEntry.account_code == ACCOUNT_KEY, CostEntry.version == "forecast")
    )
    if latest:
        return f"{ym_year(latest):04d}-{ym_month(latest):02d}"
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _record_lab_revision(db: Session, lab_code: str, before: dict, after: dict, effective_from: str) -> None:
    """기준정보가 실제로 바뀔 때만 이력 두 줄을 남긴다.

    1) 바뀌기 전 값을 '0000-01'(처음부터)로 한 번 박아서 지난 버전 전부를 옛 값으로 고정하고,
    2) 새 값을 effective_from으로 넣는다(같은 달에 또 고치면 그 줄을 덮어쓴다).
    1)은 이미 있으면 건드리지 않는다 — 처음 고정해 둔 "원래 값"이 두 번째 변경 때 덮이면
    과거가 다시 흔들린다."""
    if before == after:
        return
    db.execute(
        pg_insert(OverseasLabRevision)
        .values(lab_code=lab_code, effective_from=LAB_EPOCH, **before)
        .on_conflict_do_nothing(index_elements=["lab_code", "effective_from"])
    )
    stmt = pg_insert(OverseasLabRevision).values(lab_code=lab_code, effective_from=effective_from, **after)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["lab_code", "effective_from"],
            set_={k: stmt.excluded[k] for k in after},
        )
    )


def _check_effective_from(value: str | None, db: Session) -> str:
    """기준정보 변경이 적용되기 시작하는 연월. 안 주면 지금 편성 중인 기준월(= "이 버전부터")."""
    if not value:
        return _current_basis_ym(db)
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise HTTPException(400, f'effective_from은 "YYYY-MM" 형식이어야 한다: {value!r}')
    return value


def _lab_attrs(labs: list[OverseasLab]) -> dict:
    """이력에 남길 값 묶음. 파트가 여러 개면 이름은 파트마다 다르므로 대표(첫 파트) 이름을 쓴다 —
    되돌려 읽을 때도 파트가 여럿이면 이름은 안 덮어쓰므로 비교·복원에 영향이 없다."""
    head = labs[0]
    return {
        "name": head.name,
        "currency": head.currency,
        "is_active": head.is_active,
        "sort_order": head.sort_order,
    }






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




















@router.get("/overseas/state", response_model=OverseasState)
def get_overseas_state(basis_year: int, db: Session = Depends(get_db)) -> OverseasState:
    """취합 화면이 처음 열릴 때 받는 전체 상태. 조립은 services가 한다 —
    집계 엔드포인트가 이 핸들러를 함수로 직접 부르던 걸 끊기 위함이다(PLAN.md P1-1)."""
    return build_state(db, basis_year)


@router.put("/overseas/state")
def save_overseas_state(state: OverseasState, db: Session = Depends(get_db)) -> dict[str, str]:
    acct = overseas_account(db)
    labs = all_labs(db)
    lab_pks = {lab.pk for lab in labs}
    lab_codes = {lab.lab_code for lab in labs}

    # 동시 편집 보호(PLAN.md P1-2) — 쓰기를 시작하기 전에 막는다. 검증이 쓰기보다 뒤에 있으면
    # 409를 받았는데 DB에는 절반만 들어간 상태가 된다.
    # 토큰이 비어 있으면 검사하지 않는다: 토큰을 모르는 기존 클라이언트를 깨지 않기 위함이고,
    # 문제가 생기면 서버에서 이 블록만 끄면 롤백된다(PLAN Phase 2의 롤백 조건).
    if state.state_token:
        now_token = state_token(db)
        if state.state_token != now_token:
            raise HTTPException(
                status_code=409,
                detail=(
                    "다른 저장과 충돌했습니다 — 이 화면을 불러온 뒤 누군가 같은 화면을 저장했습니다. "
                    "화면을 다시 불러온 뒤 수정 내용을 다시 넣어 주세요(지금 저장하면 그 사람의 "
                    "수정이 사라집니다)."
                ),
            )

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
    forecast_yyyymm = yyyymm(state.basis_year, state.basis_month) if state.basis_month else None
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
    bulk_upsert_cost_entries(db, cost_rows)
    bulk_upsert_plan_entries(db, plan_rows)

    # 1-1) "N월실적" 헤더로 업로드된 확정 실적 스냅샷 (있을 때만, 지금은 cur만 사용)
    actual_cost_rows: list[tuple[str, str, str, str, list[float]]] = []
    for version, snap in state.actualV.items():
        actual_yyyymm = yyyymm(state.basis_year, snap.thru_month)
        for pk, cats in snap.d.items():
            for category_key, months in cats.items():
                actual_cost_rows.append((pk, "actual", category_key, actual_yyyymm, months))
        if "_ETC" in lab_pks:
            actual_cost_rows.append(("_ETC", "actual", "etc", actual_yyyymm, snap.etc))
    bulk_upsert_cost_entries(db, actual_cost_rows)

    # 2) 업로드 이력 (연구소×버전당 최신 파일명만 의미 있으므로, 바뀐 것만 새로 기록)
    # pk×version 조합마다 따로 조회하지 않고, 이 계정의 업로드 이력을 한 번에 읽어 메모리에서 비교한다.
    latest_filename: dict[tuple[str, str], str] = {
        (up.version, up.scope_key): up.filename
        for up in db.scalars(latest_uploads_stmt(lab_pks, list(state.files)))
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

    # 저장 뒤 새 토큰을 같이 돌려준다 — 안 주면 화면이 들고 있는 토큰이 낡아서, 같은 화면에서
    # 두 번째로 저장할 때 자기 자신과 충돌한 것처럼 409가 난다(테스트가 그걸 잡았다).
    return {"status": "ok", "state_token": state_token(db)}


# ---------- 인건비 증감의 원인: 인원 효과 vs 단가 효과 ----------
# 화면(분석 › 증감분석 › 계정별 증감분석 › 계정별 상세)의 인건비 행 아래 줄과 같은 계산이다.
# frontend-html/shared/core.js의 ovLaborSplit / frontend/src/logic/calc.js의 ovLaborSplit와 같은 식을
# 쓴다 — 챗봇이 말하는 금액과 사용자가 보는 표가 어긋나면 안 되므로 식을 바꿀 때 세 곳을 같이 고칠 것.
_LABOR_LEAVES = tuple(resolve_leaves("labor"))




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
    ix = PERIOD_IX.get(period)
    if ix is None:
        raise HTTPException(400, "period는 y(연간) | q1~q4 | m1~m12만 쓸 수 있다.")

    state = build_state(db, basis_year)
    cur_d, base_d = state.V["cur"].d, state.V[base_version].d
    if not cur_d:
        raise HTTPException(404, f"{basis_year}년 당월 속보 데이터가 없다.")
    if not base_d:
        label = {"prev": "전실행", "prior": "전년 실적", "plan": "경영계획"}[base_version]
        raise HTTPException(404, f"{label} 데이터가 저장돼 있지 않아 인건비 증감을 나눌 수 없다.")

    basis_month = state.basis_month or 1
    # 합계 행(part_code="TOTAL")과 원화 기타(_ETC)는 다른 행을 더해 만든 값이라 빼야 한다 — 넣으면
    # SARC가 CPU+GPU+TOTAL로 두 번 세어져 인원과 금액이 정확히 두 배가 된다(실제로 그렇게 나왔다).
    # 화면도 같은 규칙이다(core.js의 ovPks는 파트 목록에 TOTAL을 두지 않는다).
    # 기준정보는 "당월 속보 시점" 값으로 읽는다 — 통화를 지금 값으로 보면 지난달에 보고한 환차 분해가
    # 소급해서 달라진다(공통 RULE: 기준정보 변경은 변경한 버전부터).
    labs = {
        lab.pk: lab for lab in lab_views(db, f"{basis_year:04d}-{basis_month:02d}")
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
    rate_cur = rate_arrays(db, *cur_spec, local_curs, ix)
    rate_base = rate_arrays(db, *base_spec, local_curs, ix)

    rows: list[LaborSplitLab] = []
    tot = {"hc_cur": 0.0, "hc_base": 0.0, "amt_cur": 0.0, "amt_base": 0.0, "unsplit": 0.0}
    for pk in sorted(pks):
        lab = labs[pk]
        cats_cur, cats_base = cur_d.get(pk, {}), base_d.get(pk, {})
        lc = lab.currency if lab.currency and lab.currency != "USD" else None
        amt_cur = amt_base = hc_cur = hc_base = 0.0
        for j in ix:
            k_cur = sum(n(cats_cur.get(leaf, [None] * 12)[j]) for leaf in _LABOR_LEAVES)
            k_base = sum(n(cats_base.get(leaf, [None] * 12)[j]) for leaf in _LABOR_LEAVES)
            rb = rate_base["USD"][j]
            lrc = rate_cur.get(lc, [0.0] * 12)[j] if lc else 0.0
            lrb = rate_base.get(lc, [0.0] * 12)[j] if lc else 0.0
            # 비교 버전 환율로 환산해야 환율이 움직인 몫이 빠진다(= 환차外 기준).
            if lc and lrc > 0 and lrb > 0:
                amt_cur += k_cur * lrc / lrb * rb * 1000
            else:
                amt_cur += k_cur * rb * 1000
            amt_base += k_base * rb * 1000
            hc_cur += n(cats_cur.get("hc", [None] * 12)[j]) / len(ix)
            hc_base += n(cats_base.get("hc", [None] * 12)[j]) / len(ix)
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


# ---------- 히어로 카드(화면 맨 위 편성 현황) ----------
# frontend-html/shared/core.js의 ovRender 히어로 블록과 같은 식이다. 그쪽이 브라우저에서 내던 숫자를
# 서버에서 그대로 재현한다 — 챗봇·MCP가 말하는 총액과 사용자가 보는 카드가 어긋나면 안 되므로 식을
# 바꿀 때는 core.js(ovAgg · ovCPc · ovCustExtra)도 같이 고칠 것.
#
# 화면과 맞추기 위해 꼭 지켜야 하는 세 가지(하나라도 빠지면 카드 숫자와 달라진다):
#   ① CP는 업로드된 PL이 아니라 기준정보 › CP 계약 금액을 쓴다(분기 정산 계약이라 연 금액 ÷ 4를
#      3·6·9·12월에 넣는다) — core.js ovCPc.
#   ② 연구소가 추가한 행(extra-rows)도 anchor_key가 가리키는 계정에 얹어서 합계에 들어간다 — ovCustExtra.
#   ③ 원화 기타(_ETC)는 히어로 합계에 넣지 않는다(OV_ALLPK에 없음). 참고용으로 etc_total만 따로 준다.










@router.get("/overseas/summary", response_model=OverseasSummary)
def get_overseas_summary(
    basis_year: int,
    version: str = "cur",
    period: str = "y",
    db: Session = Depends(get_db),
) -> OverseasSummary:
    if version not in ("cur", "prev", "prior", "plan"):
        raise HTTPException(400, "version은 cur | prev | prior | plan만 쓸 수 있다.")
    ix = PERIOD_IX.get(period)
    if ix is None:
        raise HTTPException(400, "period는 y(연간) | q1~q4 | m1~m12만 쓸 수 있다.")

    state = build_state(db, basis_year)
    vd = state.V[version]
    if not vd.d:
        label = {"cur": "당월 속보", "prev": "전실행", "prior": "전년 실적", "plan": "경영계획"}[version]
        raise HTTPException(404, f"{basis_year}년 {label} 데이터가 없다.")

    # 합계 행(part_code="TOTAL")과 원화 기타(_ETC)는 다른 행을 더해 만든 값이라 빼야 두 번 세지 않는다
    # (labor-split과 같은 규칙 — 화면 ovPks도 파트 목록에 TOTAL을 두지 않는다).
    basis_month = state.basis_month or 1
    # 환율 기준은 화면과 같이 "그 버전의 속보 시점". prior만 작년 마감 실적을 본다.
    if version == "prior":
        rate_year, rate_month, rate_kind = basis_year - 1, 12, "actual"
    else:
        rate_year, rate_month, rate_kind = basis_year, basis_month, "forecast"
    # 기준정보(통화·사용여부·이름)도 환율과 같은 시점으로 본다 — 지금 값으로 과거 버전을 다시
    # 계산하면 이미 마감해서 보고한 숫자가 바뀐다(공통 RULE: 변경한 버전부터 적용, 지난 버전 무영향).
    labs = [
        lab for lab in lab_views(db, f"{rate_year:04d}-{rate_month:02d}")
        if lab.is_active and lab.pk != "_ETC" and (lab.part_code or "").upper() != "TOTAL"
    ]
    rate = rate_arrays(db, rate_year, rate_month, rate_kind, set(), ix)["USD"]

    api_version = "actual" if version in ("prev", "prior") else "forecast"
    extras = extra_row_months(read_extra_rows(db, yyyymm=yyyymm(basis_year, basis_month), version=api_version))
    cp_total_q, cp_labor_q = cp_quarter_amounts(db, basis_year)

    total = labor = 0.0
    for lab in labs:
        if lab.lab_code == "CP":
            # CP만 PL 대신 계약 금액을 분기말에 넣는다(①).
            total += sum(cp_total_q * rate[j] * 1000 for j in ix if j in QUARTER_END_IX)
            labor += sum(cp_labor_q * rate[j] * 1000 for j in ix if j in QUARTER_END_IX)
            continue
        cats, extra = vd.d.get(lab.pk) or {}, extras.get(lab.pk) or {}
        if not cats and not extra:
            continue
        tot_m = krw_months(cats, extra, TOT_TREE, rate)
        lab_m = krw_months(cats, extra, LABOR_TREE, rate)
        total += sum(tot_m[j] for j in ix)
        labor += sum(lab_m[j] for j in ix)
    non_labor = total - labor

    # 원화 기타는 이미 원화(억원 단위 입력)라 환산하지 않는다. 화면 히어로는 이 값을 합계에 넣지
    # 않으므로(③) 여기서도 total에 더하지 않고 따로만 준다.
    etc_total = sum(n(vd.etc[j]) * 100_000_000 for j in ix)

    # 업로드 진척: 파트가 여러 개인 연구소는 파트가 모두 올라와야 1건으로 센다(화면 ovHas와 같다).
    by_code: dict[str, list[LabView]] = {}
    for lab in labs:
        by_code.setdefault(lab.lab_code, []).append(lab)
    lab_rows = [
        OverseasSummaryLab(
            lab_code=code,
            name=re.sub(r"\s*·.*$", "", parts[0].name),
            uploaded=all(
                any(
                    v is not None
                    for key, months in (vd.d.get(p.pk) or {}).items()
                    if key in INPUT_KEYS
                    for v in months
                )
                for p in parts
            ),
        )
        for code, parts in sorted(by_code.items(), key=lambda kv: kv[1][0].sort_order)
    ]

    # 공통 RULE(상단 현황): 미업로드 연구소는 빼고 "n개 중 m개 기준"이라고 밝힌다. 합계는 이미
    # 올라온 값만 더하고 있으므로(없는 달은 더할 게 없다) 숫자는 그대로고, 그 사실을 말로 붙인다.
    missing_names = [r.name for r in lab_rows if not r.uploaded]
    coverage_note = (
        f"{len(lab_rows)}개 연구소가 모두 올라온 기준이다."
        if not missing_names
        else (
            f"{len(lab_rows)}개 중 {len(lab_rows) - len(missing_names)}개 기준이다 — 아직 안 올라온 "
            f"연구소({', '.join(missing_names)})는 빼고 더했으니, 이 금액을 연구소 전체 합계라고 "
            f"말하면 안 된다."
        )
    )

    mix = total or 1.0
    return OverseasSummary(
        basis_year=basis_year,
        basis_month=basis_month,
        basis_label=f"{basis_year % 100:02d}.{basis_month:02d}",
        period=period,
        version=version,
        step=max(0, min(state.step - 1, STEP_COUNT - 1)),
        step_count=STEP_COUNT,
        total=total,
        etc_total=etc_total,
        labor=labor,
        non_labor=non_labor,
        labor_pct=labor / mix * 100,
        non_labor_pct=non_labor / mix * 100,
        uploaded=sum(1 for r in lab_rows if r.uploaded),
        lab_count=len(lab_rows),
        labs=lab_rows,
        coverage_note=coverage_note,
        note=(
            "화면 히어로 카드(해외연구소 편성 현황)와 같은 식이다. CP는 업로드 PL이 아니라 CP 계약 "
            "금액을 분기말에 넣고(분기 정산 계약), 연구소가 추가한 행도 anchor_key가 가리키는 계정에 "
            "얹어서 센다. 원화 기타(etc_total)는 화면과 같이 total에 넣지 않는다."
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
    ix = PERIOD_IX.get(period)
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
    acct = overseas_account(db)
    progress = db.scalar(select(AccountProgress).where(AccountProgress.account_id == acct.id))
    return StepUpdate(step=progress.current_step_order if progress else 1)


@router.put("/overseas/step")
def update_overseas_step(body: StepUpdate, db: Session = Depends(get_db)) -> StepUpdate:
    acct = overseas_account(db)
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
def list_labs(as_of: str | None = None, db: Session = Depends(get_db)) -> list[LabOut]:
    """연구소·파트 기준정보. 프론트는 이 목록을 lab_code로 묶어 탭을 그리므로,
    여기 행만 추가하면(배포 없이) 새 연구소 탭이 화면에 나타난다.

    as_of("YYYY-MM")를 주면 그 시점에 쓰던 기준정보를 돌려준다 — 기준정보를 바꿔도 지난 버전은
    옛 값으로 계산되므로(공통 RULE), "그때는 통화가 뭐였나"를 확인할 때 쓴다. 안 주면 지금 값."""
    if as_of is not None:
        as_of = _check_effective_from(as_of, db)
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
        for lab in sorted(lab_views(db, as_of), key=lambda lab: (lab.sort_order, lab.pk))
        if lab.pk != "_ETC"
    ]


@router.put("/overseas/labs", response_model=list[LabOut])
def save_labs(body: list[LabIn], db: Session = Depends(get_db)) -> list[LabOut]:
    """연구소 기준정보 편집 화면(ovLabPage)의 "저장" — 화면에 보이는 전체 목록을 통째로 받아
    upsert하고, 목록에서 빠진 lab_code는 삭제한다(단 _ETC 가상 행은 화면에 없으니 건드리지 않음).
    cost_entries 등에 남은 데이터는 FK가 없어 지워지지 않고, 집계에서만 조용히 빠진다."""
    by_code: dict[str, list[OverseasLab]] = {}
    for lab in all_labs(db):
        by_code.setdefault(lab.lab_code, []).append(lab)

    submitted_codes = {row.lab_code for row in body}
    for lab_code, rows in by_code.items():
        if lab_code == "_ETC" or lab_code in submitted_codes:
            continue
        for row in rows:
            db.delete(row)

    for order, row in enumerate(body):
        existing = by_code.get(row.lab_code, [])
        # 기준정보 변경은 "이 버전부터" 적용된다 — 지난 버전은 바뀌기 전 값으로 계산되도록
        # 이력을 남긴다(공통 RULE). effective_from을 안 주면 지금 편성 중인 기준월부터.
        effective_from = _check_effective_from(row.effective_from, db)
        after = {"name": row.name, "currency": row.currency, "is_active": row.is_active, "sort_order": order * 10}
        if not existing:
            # 새로 만든 연구소는 "그 전에는 없던 것"(is_active=False)으로 고정한다 — 안 그러면
            # 과거 버전의 집계·업로드 진척에 방금 만든 연구소가 끼어든다.
            _record_lab_revision(db, row.lab_code, {**after, "is_active": False}, after, effective_from)
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
        _record_lab_revision(db, row.lab_code, _lab_attrs(existing), after, effective_from)
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
        for lab in sorted(all_labs(db), key=lambda lab: (lab.sort_order, lab.pk))
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
    # save_labs와 같은 규칙 — 바뀐 값은 effective_from 버전부터만 적용되고 지난 버전은 그대로다.
    effective_from = _check_effective_from(body.effective_from, db)
    if not existing:
        next_order = (db.scalar(select(func.max(OverseasLab.sort_order))) or 0) + 10
        after = {"name": body.name, "currency": body.currency, "is_active": body.is_active, "sort_order": next_order}
        _record_lab_revision(db, body.lab_code, {**after, "is_active": False}, after, effective_from)
        db.add(
            OverseasLab(
                pk=body.lab_code, lab_code=body.lab_code, part_code=None, name=body.name,
                country="", sort_order=next_order, currency=body.currency, is_active=body.is_active,
            )
        )
    else:
        _record_lab_revision(
            db,
            body.lab_code,
            _lab_attrs(existing),
            {
                "name": body.name, "currency": body.currency, "is_active": body.is_active,
                # 순서는 이 API로 바꾸지 않으므로 지금 값을 그대로 이력에 남긴다.
                "sort_order": existing[0].sort_order,
            },
            effective_from,
        )
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
        stmt = stmt.where(CostEntry.version == check_version(version))
    if lab_pk:
        stmt = stmt.where(CostEntry.scope_code == check_lab_pk(db, lab_pk))
    if category_key:
        stmt = stmt.where(CostEntry.category_code == category_key)
    entries = list(db.scalars(stmt))
    months_by_entry = entry_months_bulk(db, entries)

    out: list[CostEntryOut] = []
    for entry in entries:
        months = months_by_entry[entry.id]
        base_year = ym_year(entry.yyyymm)
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
    check_lab_pk(db, body.lab_pk)
    check_version(body.version)
    _check_category(db, body.category_key)
    entry_id = get_or_create_entry(db, body.lab_pk, body.version, body.category_key, body.yyyymm)
    base_year = ym_year(body.yyyymm)
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
        stmt = stmt.where(Upload.scope_key == check_lab_pk(db, lab_pk))
    return [
        UploadOut(lab_pk=u.scope_key, version=u.version, filename=u.filename, uploaded_at=u.uploaded_at, s3_key=u.s3_key)
        for u in db.scalars(stmt)
    ]


@router.post("/overseas/uploads", response_model=UploadOut)
def record_upload(body: UploadIn, db: Session = Depends(get_db)) -> UploadOut:
    check_lab_pk(db, body.lab_pk)
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
    check_lab_pk(db, lab_pk)
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


# ---------- 증감사유 번역 (영문 회신 → 보고용 한국어) ----------
# 번역 자체는 app/core/reason_translate.py(프롬프트 · 공급자 호출)가 하고, 여기는 캐시만 맡는다:
# 이미 번역한 원문은 DB 값을 그대로 돌려주고, 처음 보는 원문만 LLM에 묻는다. 같은 원문이 매번
# 다르게 번역되면 보고 문구가 흔들리기 때문에 "원문 1건 = 번역 1개"를 DB로 못 박는 구조다.


@router.post("/overseas/reason-translate", response_model=ReasonTranslateResult)
async def translate_reasons(body: ReasonTranslateIn, db: Session = Depends(get_db)) -> ReasonTranslateResult:
    """연구소가 영문으로 보낸 증감사유를 한국어로 번역한다(원문은 그대로 보관).

    같은 원문이 여러 번 들어와도 LLM에는 한 번만 묻고, 한 번 번역한 원문은 다음 호출부터
    overseas_reason_translations에서 바로 꺼낸다. refresh=true면 캐시를 무시하고 다시 번역해 덮어쓴다.

    LLM 호출이 실패해도 200으로 답한다 — 증감분석 화면은 번역이 없어도 원문으로 그려져야 하고,
    여기서 502를 던지면 화면이 "사유 자체가 없는" 것처럼 보인다. 실패 이유는 error에 담아 보낸다."""
    provider = resolve_provider(body.provider)
    # 중복 제거하되 요청 순서는 지킨다(화면이 받은 순서대로 칸에 꽂는다). 길이 상한을 넘는 원문은
    # 자르지 않고 그대로 번역에 보낸다 — remark_* 컬럼이 500자라 실제로는 걸리지 않고, 혹시 더 긴
    # 값이 오면 번역을 포기하는 게 뒤가 잘린 사유를 보여주는 것보다 안전하다.
    seen: dict[str, None] = {}
    too_long: set[str] = set()
    for raw in body.texts:
        text = normalize(raw)
        if not text:
            continue
        seen.setdefault(text, None)
        if len(text) > MAX_LEN:
            too_long.add(text)
    sources = list(seen)
    if len(sources) > MAX_TEXTS:
        raise HTTPException(
            status_code=400,
            detail=f"한 번에 번역할 수 있는 원문은 {MAX_TEXTS}건까지입니다 (요청 {len(sources)}건). 나눠서 보내주세요.",
        )

    rows = {
        r.source_hash: r
        for r in db.scalars(
            select(TextTranslation).where(
                TextTranslation.source_hash.in_([source_hash(t) for t in sources])
            )
        )
    }
    hit: dict[str, TextTranslation] = {}
    todo: list[str] = []
    for text in sources:
        row = rows.get(source_hash(text))
        if row and not body.refresh:
            hit[text] = row
        elif text not in too_long:
            todo.append(text)

    fresh: dict[str, str] = {}
    error = ""
    if todo:
        try:
            result = await translate_batch(todo, provider)
        except HTTPException as e:
            # 키 없음 · 크레딧 · 연결 실패 — 번역만 못 하고 나머지(캐시된 번역)는 그대로 돌려준다.
            result = [None] * len(todo)
            error = str(e.detail)
        label = engine_label(provider)
        for text, raw_ko in zip(todo, result):
            if not raw_ko:
                continue
            # ko_text가 String(600)이라 그보다 긴 번역은 잘라서 넣는다(원문이 500자 이하라 실제로는
            # 안 걸리지만, 모델이 설명을 덧붙여 길게 내놨을 때 저장 자체가 실패하는 걸 막는다).
            ko = raw_ko[:600]
            fresh[text] = ko
            # 같은 원문이 이미 있으면(refresh) 덮어쓴다 — source_hash가 unique라 insert는 못 한다.
            db.execute(
                pg_insert(TextTranslation)
                .values(source_hash=source_hash(text), source_text=text, ko_text=ko, engine=label)
                .on_conflict_do_update(
                    index_elements=[TextTranslation.source_hash],
                    set_={"ko_text": ko, "engine": label, "updated_at": func.now()},
                )
            )
        db.commit()

    items = [
        ReasonTranslateItem(
            source=text,
            ko=fresh.get(text) or (hit[text].ko_text if text in hit else None),
            engine=engine_label(provider) if text in fresh else (hit[text].engine if text in hit else ""),
            cached=text in hit and text not in fresh,
        )
        for text in sources
    ]
    failed = sum(1 for i in items if not i.ko)
    if too_long:
        error = (error + " " if error else "") + f"{len(too_long)}건은 원문이 {MAX_LEN}자를 넘어 번역하지 않았습니다."
    return ReasonTranslateResult(
        items=items,
        provider=provider,
        translated=len(fresh),
        cached=sum(1 for i in items if i.cached),
        failed=failed,
        error=error,
    )


@router.get("/overseas/reason-translations", response_model=list[ReasonTranslateItem])
def list_reason_translations(q: str | None = None, limit: int = 200, db: Session = Depends(get_db)) -> list[ReasonTranslateItem]:
    """저장된 원문 · 번역 쌍 목록(최근 순). 오역을 찾거나 모델을 바꾼 뒤 어떤 번역이 옛 모델로
    만들어졌는지 훑어볼 때 쓴다 — q를 주면 원문 · 번역 양쪽을 부분 일치로 찾는다."""
    stmt = select(TextTranslation).order_by(TextTranslation.updated_at.desc()).limit(max(1, min(limit, 1000)))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(TextTranslation.source_text.ilike(like) | TextTranslation.ko_text.ilike(like))
    return [
        ReasonTranslateItem(source=r.source_text, ko=r.ko_text, engine=r.engine, cached=True) for r in db.scalars(stmt)
    ]


# ---------- 이상 감지 (반영 전 "이거 맞나요?") ----------
# 판정 규칙은 app/core/anomaly.py, 기준값은 overseas_anomaly_rules에 있다. 여기는 그 둘을 붙이고
# 비교 대상(전실행)을 DB에서 꺼내 오는 일을 한다. 반영을 막지 않는다 — 알리기만 한다.


def _rule_rows(db: Session) -> list[AnomalyRule]:
    # anomaly_rules는 전 계정 공용 테이블이라 계정으로 걸러야 한다 — 안 걸면 다른 계정의
    # 급변 기준이 섞여 들어온다(PLAN.md Phase 3로 account_key가 생겼다).
    return list(db.scalars(select(AnomalyRule).where(AnomalyRule.account_key == ACCOUNT_KEY)))


def _rule_out(key: str, r: dict) -> AnomalyRuleOut:
    # desc는 "지금 값이 들어간 설명"이다 — 화면·챗봇이 기준을 말로 옮길 때 숫자를 따로 조립하지
    # 않게(두 곳이 다르게 적는 걸 막으려고) 서버가 완성해서 준다.
    fmt = {
        "pct": f"{r['pct']:g}" if r.get("pct") is not None else "",
        "amount": f"${r['amount']:,.1f}K".replace(".0K", "K") if r.get("amount") is not None else "",
        "ratio": f"{r['ratio']:g}" if r.get("ratio") is not None else "",
    }
    return AnomalyRuleOut(
        rule_key=key, label=r["label"], enabled=r["enabled"], pct=r.get("pct"), amount=r.get("amount"),
        ratio=r.get("ratio"), desc=r["desc"].format(**fmt),
    )


def _rules_out(db: Session, overrides: dict | None = None) -> list[AnomalyRuleOut]:
    merged = anomaly.merged_rules(_rule_rows(db), overrides)
    return [_rule_out(k, merged[k]) for k in anomaly.RULE_KEYS]


@router.get("/overseas/anomaly-rules", response_model=list[AnomalyRuleOut])
def list_anomaly_rules(db: Session = Depends(get_db)) -> list[AnomalyRuleOut]:
    """지금 적용 중인 이상 감지 기준 4개. 해외연구소 전체에 같이 적용된다."""
    return _rules_out(db)


@router.put("/overseas/anomaly-rules", response_model=list[AnomalyRuleOut])
def upsert_anomaly_rule(body: AnomalyRuleIn, db: Session = Depends(get_db)) -> list[AnomalyRuleOut]:
    """기준 하나를 바꾼다(보낸 항목만). reset=true면 그 기준을 기본값으로 되돌린다.

    바꾼 내역은 overseas_anomaly_rule_logs에 before/after로 남긴다 — 기준이 바뀌면 경고 개수가
    달라져서, 나중에 "지난달엔 왜 이게 안 걸렸지"에 답할 수 있어야 한다."""
    key = anomaly.RULE_ALIASES.get(body.rule_key.strip(), body.rule_key.strip())
    if key not in anomaly.RULE_KEYS:
        raise HTTPException(
            status_code=400, detail=f"없는 기준입니다: {body.rule_key!r} (가능: {', '.join(anomaly.RULE_KEYS)})"
        )
    if body.pct is not None and not (0 < body.pct <= 10000):
        raise HTTPException(status_code=400, detail="비율(pct)은 0보다 크고 10000 이하여야 합니다.")
    if body.amount is not None and body.amount < 0:
        raise HTTPException(status_code=400, detail="금액(amount)은 0 이상이어야 합니다.")
    if body.ratio is not None and body.ratio <= 1:
        raise HTTPException(status_code=400, detail="배수(ratio)는 1보다 커야 합니다.")

    row = db.scalar(
        select(AnomalyRule).where(AnomalyRule.account_key == ACCOUNT_KEY, AnomalyRule.rule_key == key)
    )
    before = anomaly.merged_rules(_rule_rows(db))[key]
    before_snap = {k: before[k] for k in ("enabled", "pct", "amount", "ratio")}
    if not row:
        row = AnomalyRule(account_key=ACCOUNT_KEY, rule_key=key)
        db.add(row)
    if body.reset:
        d = anomaly.DEFAULTS[key]
        row.enabled, row.pct, row.amount, row.ratio = d["enabled"], d["pct"], d["amount"], d["ratio"]
    else:
        # None은 "안 건드림"이다 — 그 기준이 원래 안 쓰는 파라미터를 NULL로 지우는 것과 구분한다.
        if body.enabled is not None:
            row.enabled = body.enabled
        if body.pct is not None:
            row.pct = body.pct
        if body.amount is not None:
            row.amount = body.amount
        if body.ratio is not None:
            row.ratio = body.ratio
    db.flush()
    after = anomaly.merged_rules(_rule_rows(db))[key]
    after_snap = {k: after[k] for k in ("enabled", "pct", "amount", "ratio")}
    if before_snap != after_snap:
        db.add(
            AnomalyRuleLog(
                account_key=ACCOUNT_KEY, rule_key=key, before=before_snap, after=after_snap,
                source="reset" if body.reset else (body.source or "screen")[:10], note=body.note.strip()[:200],
            )
        )
    db.commit()
    return _rules_out(db)


@router.get("/overseas/anomaly-rules/history", response_model=list[AnomalyRuleLogOut])
def list_anomaly_rule_history(limit: int = 50, db: Session = Depends(get_db)) -> list[AnomalyRuleLogOut]:
    """기준 변경 이력(최근 순). "언제 누가 급변 기준을 올렸나"를 되짚을 때 본다."""
    rows = db.scalars(
        # id까지 내림차순으로 정렬한다 — 한 트랜잭션에서 두 번 바꾸면 now()가 같은 값이라
        # changed_at만으로는 어느 게 최신인지 가려지지 않는다.
        select(AnomalyRuleLog)
        .where(AnomalyRuleLog.account_key == ACCOUNT_KEY)
        .order_by(AnomalyRuleLog.changed_at.desc(), AnomalyRuleLog.id.desc())
        .limit(max(1, min(limit, 500)))
    )
    return [
        AnomalyRuleLogOut(
            rule_key=r.rule_key, before=r.before, after=r.after, source=r.source, note=r.note, changed_at=r.changed_at
        )
        for r in rows
    ]


@router.post("/overseas/anomaly-scan", response_model=AnomalyScanResult)
def scan_anomalies(body: AnomalyScanIn, db: Session = Depends(get_db)) -> AnomalyScanResult:
    """업로드 값(또는 이미 저장된 값)을 기준에 걸어 본다. 반영을 막지 않고 목록만 돌려준다.

    vals를 주면 그 값으로 검사하고(반영 대기 중인 파일), 안 주면 DB에 저장된 값으로 검사한다
    ([계정 확정] 전에 "아직 남아 있는 이상"을 세는 경로). 비교할 전실행(base_*)을 안 주면
    급변·단위(배수) 검사는 건너뛰고 그 사실을 skipped로 알린다 — 조용히 빠지면 "이상 없음"으로
    읽힌다."""
    check_lab_pk(db, body.lab_pk, allow_all=True)
    check_version(body.version)
    vals = body.vals
    if vals is None:
        vals = months_by_category(db, body.lab_pk, body.version, body.yyyymm)
    base_vals = None
    skipped: list[str] = []
    if body.base_yyyymm and body.base_version:
        check_version(body.base_version)
        base_vals = months_by_category(db, body.lab_pk, body.base_version, body.base_yyyymm)
        if not base_vals:
            base_vals = None
    if base_vals is None:
        skipped.append("급변·단위(배수) — 비교할 전실행 값이 없습니다")

    rules = anomaly.merged_rules(_rule_rows(db), body.overrides)
    findings = anomaly.detect(
        vals={k: list(v or []) for k, v in vals.items()},
        base_vals=base_vals,
        sums=body.sums,
        reasons=body.reasons,
        rules=rules,
        group_calc=GROUP_CALC,
        label_of=category_label,
        leaves_of=resolve_leaves,
    )
    if not body.sums and rules["sum_mismatch"]["enabled"]:
        skipped.append("합계 불일치 — 파일에 소계·합계 행이 없습니다")
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["rule"]] = counts.get(f["rule"], 0) + 1
    return AnomalyScanResult(
        lab_pk=body.lab_pk,
        findings=[AnomalyFinding(**f) for f in findings],
        counts=counts,
        rules=[_rule_out(k, rules[k]) for k in anomaly.RULE_KEYS],
        skipped=skipped,
    )


# ---------- 집계 파생 행·열의 "다음 버전에도 유지" ----------
# 파생 행·열은 버전×기준연월 단위로 저장돼서 새 버전을 열면 사라진다. 매달 보는 지표는 따라가야
# 하고 그 달만 보려고 만든 건 따라가면 안 되므로, 만들 때 한 번 묻고 승인한 것만 복제한다
# (overseas_carry_defs). 복제하는 건 계산식뿐이다 — 값은 새 버전 업로드 값으로 다시 계산된다.


@router.get("/overseas/carry-defs", response_model=list[CarryDefOut])
def list_carry_defs(db: Session = Depends(get_db)) -> list[CarryDefOut]:
    """다음 버전에도 유지하기로 승인한 파생 행·열 목록."""
    rows = db.scalars(
        select(CarryDef).where(CarryDef.account_key == ACCOUNT_KEY).order_by(CarryDef.kind, CarryDef.label)
    )
    return [CarryDefOut(kind=r.kind, def_key=r.def_key, label=r.label) for r in rows]


@router.put("/overseas/carry-defs", response_model=list[CarryDefOut])
def upsert_carry_def(body: CarryDefIn, db: Session = Depends(get_db)) -> list[CarryDefOut]:
    """유지 승인을 넣거나(carry=true) 뺀다(carry=false). 바뀐 뒤 전체 목록을 돌려준다 —
    화면이 "유지 중" 표시를 바로 갱신할 수 있게."""
    if body.kind not in ("row", "col"):
        raise HTTPException(status_code=400, detail="kind는 row 또는 col이어야 합니다.")
    key = body.def_key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="def_key가 비어 있습니다.")
    if body.carry:
        db.execute(
            pg_insert(CarryDef)
            .values(account_key=ACCOUNT_KEY, kind=body.kind, def_key=key, label=body.label.strip() or key)
            .on_conflict_do_update(
                # uq가 (account_key, kind, def_key)로 바뀌었으므로 충돌 판정 컬럼도 같이 맞춘다 —
                # 안 맞추면 Postgres가 "그런 제약이 없다"며 거부한다.
                index_elements=[CarryDef.account_key, CarryDef.kind, CarryDef.def_key],
                set_={"label": body.label.strip() or key},
            )
        )
    else:
        db.execute(
            delete(CarryDef).where(
                CarryDef.account_key == ACCOUNT_KEY, CarryDef.kind == body.kind, CarryDef.def_key == key
            )
        )
    db.commit()
    return list_carry_defs(db)


@router.post("/overseas/carry-forward", response_model=CarryForwardResult)
def carry_forward_defs(body: CarryForwardIn, db: Session = Depends(get_db)) -> CarryForwardResult:
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


# ---------- 업로드 행 이름 맞추기 (Travel Exp ↔ Travel Expense) ----------
# 판정 규칙은 app/core/row_match.py에 있다. 여기는 "무엇과 비교할지"(후보 목록)와 "사람이 답한
# 결과"(overseas_row_aliases)를 맡는다. 자동으로 합치지 않는다 — 후보만 돌려주고, 합칠지는 화면이
# 사람에게 묻는다. 한 번 답한 이름은 다음 업로드부터 resolved로 바로 내려가서 다시 묻지 않는다.


def _alias_rows(db: Session, norms: list[str]) -> dict[str, RowAlias]:
    if not norms:
        return {}
    return {
        a.alias_norm: a
        for a in db.scalars(
            select(RowAlias).where(RowAlias.account_key == ACCOUNT_KEY, RowAlias.alias_norm.in_(norms))
        )
    }


def _match_candidates(db: Session, yyyymm: str, version: str | None) -> list[dict]:
    """비교 대상: ① 고정 비용항목(집계로 계산되는 그룹 행은 제외 — 거기에 값을 꽂을 수 없다),
    ② 그 달에 이미 쓰이는 추가 행(연구소 전체에서 모은 이름, 어느 연구소가 쓰는지까지)."""
    out: list[dict] = [
        {"kind": "category", "key": key, "label": label, "label_en": CATEGORY_LABELS_EN.get(key, "")}
        for key, label in CATEGORY_LABELS.items()
        if key not in GROUP_CALC and key != "pct"
    ]

    stmt = select(CostEntry).where(
        CostEntry.account_code == ACCOUNT_KEY,
        CostEntry.label.is_not(None),
        CostEntry.yyyymm == yyyymm,
        CostEntry.is_derived.is_(False),
    )
    if version:
        stmt = stmt.where(CostEntry.version == check_version(version))
    by_norm: dict[str, dict] = {}
    for entry in db.scalars(stmt):
        label = (entry.label or "").strip()
        if not label:
            continue
        norm = row_match.normalize(label)
        row = by_norm.setdefault(
            norm, {"kind": "extra_row", "key": norm, "label": label, "label_en": entry.label_en or "", "labs": []}
        )
        if not row["label_en"] and entry.label_en:
            row["label_en"] = entry.label_en
        if entry.scope_code != ALL_SCOPE and entry.scope_code not in row["labs"]:
            row["labs"].append(entry.scope_code)
    return out + list(by_norm.values())


@router.post("/overseas/row-match", response_model=RowMatchResult)
def match_row_labels(body: RowMatchIn, db: Session = Depends(get_db)) -> RowMatchResult:
    """업로드 파일에서 계정 목록에 없던 행 이름들을 "이미 있는 행"과 맞춰본다.

    - 전에 사람이 답해 둔 이름이면 `resolved`로 내려간다(그 답이 "별개 행"이었으면 kind="distinct").
    - 처음 보는 이름이면 비슷한 후보를 `suggestions`로 내려주고, 묻는 건 화면이 한다.
    - 비슷한 게 없으면 둘 다 비어서 내려간다 — 그냥 새 행으로 들어갈 이름이다."""
    labels, seen = [], set()
    for raw in body.labels:
        label = str(raw or "").strip()
        norm = row_match.normalize(label)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        labels.append(label)
    if not labels:
        return RowMatchResult(items=[])

    aliases = _alias_rows(db, [row_match.normalize(x) for x in labels])
    candidates = _match_candidates(db, body.yyyymm, body.version)
    items: list[RowMatchItem] = []
    for label in labels:
        alias = aliases.get(row_match.normalize(label))
        if alias:
            items.append(
                RowMatchItem(
                    label=label,
                    resolved=RowMatchSuggestion(
                        kind=alias.target_kind,
                        key=alias.target_key,
                        label=alias.target_label,
                        # 전에 사람이 확정한 답이라 점수를 매기지 않는다(1.0 = 확정).
                        score=1.0,
                    ),
                )
            )
            continue
        # 자기 자신(이름이 같은 후보)은 후보로 올리지 않는다 — 이미 그 행으로 들어갈 이름이다.
        sugg = [s for s in row_match.rank(label, candidates) if s["score"] < 1.0]
        items.append(RowMatchItem(label=label, suggestions=[RowMatchSuggestion(**s) for s in sugg]))
    return RowMatchResult(items=items)


@router.get("/overseas/row-aliases", response_model=list[RowAliasOut])
def list_row_aliases(db: Session = Depends(get_db)) -> list[RowAliasOut]:
    """사람이 답해 둔 행 이름 매핑 전체(최근 답한 것 먼저). 잘못 합친 걸 찾을 때 본다."""
    rows = db.scalars(
        select(RowAlias).where(RowAlias.account_key == ACCOUNT_KEY).order_by(RowAlias.updated_at.desc())
    )
    return [
        RowAliasOut(
            alias_norm=a.alias_norm,
            alias_label=a.alias_label,
            target_kind=a.target_kind,
            target_key=a.target_key,
            target_label=a.target_label,
        )
        for a in rows
    ]


@router.put("/overseas/row-aliases", response_model=RowAliasOut)
def upsert_row_alias(body: RowAliasIn, db: Session = Depends(get_db)) -> RowAliasOut:
    """"같은 행"(category | extra_row) 또는 "별개 행"(distinct)이라고 답한 결과를 저장한다.
    같은 이름에 다시 답하면 덮어쓴다 — 전에 잘못 합쳤더라도 다시 답하면 그때부터 새 답을 따른다
    (이미 그 답으로 들어간 과거 데이터는 바뀌지 않는다 — 그건 업로드로 다시 올려야 한다)."""
    if body.target_kind not in ("category", "extra_row", "distinct"):
        raise HTTPException(status_code=400, detail="target_kind는 category | extra_row | distinct만 가능합니다.")
    norm = row_match.normalize(body.alias_label)
    if not norm:
        raise HTTPException(status_code=400, detail="alias_label이 비어 있습니다.")
    if body.target_kind == "category" and body.target_key not in CATEGORY_LABELS:
        raise HTTPException(status_code=400, detail=f"없는 비용항목입니다: {body.target_key!r}")
    if body.target_kind == "distinct":
        key, label = "", ""
    else:
        key = body.target_key or row_match.normalize(body.target_label)
        label = body.target_label or CATEGORY_LABELS.get(body.target_key, "")
        if not label:
            raise HTTPException(status_code=400, detail="target_label(합쳐 넣을 정식 이름)이 필요합니다.")
    # 이름을 그 자신에게 거는 건 막는다 — 매칭할 때 자기 자신을 가리켜 무한히 되돌게 된다.
    if body.target_kind == "extra_row" and key == norm:
        raise HTTPException(status_code=400, detail="같은 이름을 자기 자신에게 연결할 수 없습니다.")
    db.execute(
        pg_insert(RowAlias)
        .values(
            account_key=ACCOUNT_KEY,
            alias_norm=norm,
            alias_label=body.alias_label.strip(),
            target_kind=body.target_kind,
            target_key=key,
            target_label=label,
        )
        .on_conflict_do_update(
            index_elements=[RowAlias.account_key, RowAlias.alias_norm],
            set_={
                "alias_label": body.alias_label.strip(),
                "target_kind": body.target_kind,
                "target_key": key,
                "target_label": label,
                "updated_at": func.now(),
            },
        )
    )
    db.commit()
    return RowAliasOut(
        alias_norm=norm, alias_label=body.alias_label.strip(), target_kind=body.target_kind, target_key=key, target_label=label
    )


@router.delete("/overseas/row-aliases")
def delete_row_alias(alias_label: str, db: Session = Depends(get_db)) -> dict:
    """답을 지운다 — 다음 업로드에서 그 이름을 다시 물어본다(잘못 답한 걸 되돌릴 때)."""
    norm = row_match.normalize(alias_label)
    deleted = db.execute(
        delete(RowAlias).where(RowAlias.account_key == ACCOUNT_KEY, RowAlias.alias_norm == norm)
    ).rowcount
    db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail=f"저장된 답이 없습니다: {alias_label!r}")
    return {"deleted": deleted, "alias_label": alias_label}


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
        stmt = stmt.where(MemoCol.scope_key == check_lab_pk(db, lab_pk))
    return [
        MemoColOut(lab_pk=c.scope_key, version=c.version, col_key=c.col_key, label=c.label) for c in db.scalars(stmt)
    ]


@router.put("/overseas/memo-cols", response_model=MemoColOut)
def upsert_memo_col(body: MemoColIn, db: Session = Depends(get_db)) -> MemoColOut:
    check_lab_pk(db, body.lab_pk)
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
    check_lab_pk(db, lab_pk)
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
        stmt = stmt.where(Memo.scope_key == check_lab_pk(db, lab_pk))
    return [
        MemoOut(lab_pk=m.scope_key, version=m.version, row_key=m.row_key, col_key=m.col_key, text=m.text)
        for m in db.scalars(stmt)
    ]


@router.put("/overseas/memos", response_model=MemoOut)
def upsert_memo(body: MemoIn, db: Session = Depends(get_db)) -> MemoOut:
    check_lab_pk(db, body.lab_pk)
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
    check_lab_pk(db, body.lab_pk, allow_all=body.is_derived)
    check_version(body.version)
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
    entry_id = get_or_create_entry(db, body.lab_pk, body.version, body.row_key, body.yyyymm)
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
        upsert_entry_months(db, entry_id, ym_year(body.yyyymm), body.v)
    db.commit()

    result = ExtraRowOut(**body.model_dump())
    if body.is_derived:
        # 방금 저장한 계산식을 바로 평가해서 값과 근거를 함께 돌려준다 — 호출부(화면·챗봇)가
        # 저장 직후 다시 조회하지 않고도 "인건비 1,000 ÷ 인력 10 = 100"을 보여줄 수 있게.
        result.formula = formula
        result.v, result.basis = eval_row_formula(
            months_by_category(db, body.lab_pk, body.version, body.yyyymm), formula
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
    check_lab_pk(db, lab_pk, allow_all=True)
    check_version(version)
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
        stmt = stmt.where(ExtraCol.version == check_version(version))
    if lab_pk:
        # 집계 화면은 전체 합산 범위(ALL)로도 조회한다 — 그 범위에 열은 없어서 빈 목록이 맞는 답이다.
        stmt = stmt.where(ExtraCol.scope_key == check_lab_pk(db, lab_pk, allow_all=True))
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
    check_lab_pk(db, body.lab_pk, allow_all=body.is_derived)
    check_version(body.version)
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
        stmt = stmt.where(CostEntry.version == check_version(version))
    if lab_pk:
        stmt = stmt.where(CostEntry.scope_code == check_lab_pk(db, lab_pk, allow_all=True))
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
        months_by_entry = entry_months_bulk(db, entries)
        entries_by_scope: dict[tuple[str, str], list[CostEntry]] = {}
        for entry in entries:
            entries_by_scope.setdefault((entry.scope_code, entry.version), []).append(entry)
        for col in derived_cols:
            if not col.formula:
                continue
            for entry in entries_by_scope.get((col.scope_key, col.version), []):
                value, basis = _eval_col_formula(col.formula, [n(v) for v in months_by_entry[entry.id]])
                out.append(
                    ExtraValueOut(
                        lab_pk=entry.scope_code, version=entry.version, row_key=entry.category_code,
                        yyyymm=yyyymm, label=col.label, value=value, basis=basis,
                    )
                )
    return out


@router.put("/overseas/cost-extras", response_model=ExtraValueOut)
def upsert_cost_extra(body: ExtraValueIn, db: Session = Depends(get_db)) -> ExtraValueOut:
    check_lab_pk(db, body.lab_pk)
    check_version(body.version)
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
    entry_id = get_or_create_entry(db, body.lab_pk, body.version, body.row_key, body.yyyymm)
    stmt = pg_insert(CostEntryDetail).values(
        cost_entry_id=entry_id, year=ym_year(body.yyyymm), period_type="CUSTOM", period=0, col_name=body.label,
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
    check_lab_pk(db, lab_pk, allow_all=True)
    check_version(version)
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
        stmt = stmt.where(SheetRow.scope_key == check_lab_pk(db, lab_pk))
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
    check_lab_pk(db, body.lab_pk)
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
    check_lab_pk(db, lab_pk)
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
    check_lab_pk(db, lab_pk)
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


@router.get("/overseas/extra-rows", response_model=list[ExtraRowOut])
def list_extra_rows(
    yyyymm: str,
    version: str | None = None,
    lab_pk: str | None = None,
    db: Session = Depends(get_db),
) -> list[ExtraRowOut]:
    """연구소가 추가한 행. 읽기는 services가 한다 — 집계도 같은 함수를 부른다(PLAN.md P1-1)."""
    return read_extra_rows(db, yyyymm=yyyymm, version=version, lab_pk=lab_pk)
