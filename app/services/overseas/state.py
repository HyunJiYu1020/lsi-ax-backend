"""취합 화면 상태 조립 — GET /overseas/state가 돌려주는 전체 상태를 만든다.

왜 여기로 옮겼나 (PLAN.md P1-1): 집계 엔드포인트가 이 라우트 핸들러를 **함수로 직접 호출**하고
있었다(`get_overseas_state(basis_year=..., db=db)`). 그러면 집계 화면이 다른 화면의 응답 모델
전체에 결합되고, 확정 사전점검 한 번에 화면 전체 상태를 로드하는 비용을 낸다. 라우트 핸들러는
HTTP 입출력만 맡고, 조립은 이 모듈이 한다 — 집계는 서비스를 부른다.

이 상태 모양은 도메인이 아니라 프런트 화면 구조(V/actualV/files/note/reason/vendor/cp)를
모델링한 것이다(PLAN.md P1-2). 그 점은 여기서 고치지 않는다 — Phase 5에서 도메인별 저장 API로
점진 분해할 때 다룬다. 지금은 "어디에 사는가"만 바로잡는다.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.periods import ym_month, yyyymm
from app.models import (
    AccountProgress,
    CostEntry,
    ManagementPlanEntry,
    Note,
    OverseasCpRow,
    OverseasReasonRow,
    OverseasVendorRow,
    OverseasVendorRowDetail,
    Upload,
)
from app.schemas.overseas import (
    ActualSnapshot,
    CpRow,
    FileInfo,
    OverseasState,
    ReasonRow,
    VendorRow,
    VersionData,
)
from app.services.overseas.entries import (
    PLAN_NO,
    current_basis_month,
    entry_months_bulk,
    plan_entry_months_bulk,
)
from app.services.overseas.labs import ACCOUNT_KEY, all_labs, overseas_account


def latest_uploads_stmt(lab_pks: set[str], versions: list[str]):
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


def build_state(db: Session, basis_year: int) -> OverseasState:
    acct = overseas_account(db)
    labs = all_labs(db)
    lab_pks = {lab.pk for lab in labs}
    lab_codes = {lab.lab_code for lab in labs}

    progress = db.scalar(select(AccountProgress).where(AccountProgress.account_id == acct.id))
    step = progress.current_step_order if progress else 1

    V: dict[str, VersionData] = {v: VersionData(d={}, etc=[0.0] * 12) for v in ("cur", "prev", "prior", "plan")}
    basis_month = current_basis_month(db)

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
        forecast_yyyymm = yyyymm(basis_year, basis_month)
        forecast_entries = [
            entry
            for entry in db.scalars(
                select(CostEntry).where(
                    CostEntry.account_code == ACCOUNT_KEY, CostEntry.version == "forecast", CostEntry.yyyymm == forecast_yyyymm,
                )
            )
            if entry.scope_code in lab_pks and (entry.scope_code, entry.category_code) not in custom_keys
        ]
        forecast_months = entry_months_bulk(db, forecast_entries)
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
        athru = ym_month(latest_yyyymm)
        snap = ActualSnapshot(thru_month=athru, d={}, etc=[0.0] * 12)
        latest_actual_entries = [
            entry
            for entry in actual_rows
            if entry.yyyymm == latest_yyyymm
            and entry.scope_code in lab_pks
            and (entry.scope_code, entry.category_code) not in custom_keys
        ]
        latest_actual_months = entry_months_bulk(db, latest_actual_entries)
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
        latest_prior_months = entry_months_bulk(db, latest_prior_entries)
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
    plan_months = plan_entry_months_bulk(db, [e.id for e in plan_entries])
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
        for up in db.scalars(latest_uploads_stmt(lab_pks, list(files)))
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
        # 정렬 키는 유니크 키(lab_code, version, category_key, block)와 같게 둔다 — 정렬을 안 하면
        # 같은 요청이 매번 다른 순서를 돌려준다(upsert가 행을 건드리면 물리 순서가 바뀐다). 화면의
        # 증감사유 표가 새로 고칠 때마다 섞이고, 챗봇이 "위에서 세 번째 줄"처럼 위치로 말할 때도
        # 어긋난다. 외주용역(아래)은 처음부터 order_by가 있었다.
        for r in db.scalars(
            select(OverseasReasonRow)
            .where(OverseasReasonRow.lab_code.in_(lab_codes))
            .order_by(
                OverseasReasonRow.lab_code,
                OverseasReasonRow.version,
                OverseasReasonRow.category_key,
                OverseasReasonRow.block,
            )
        )
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
        # 화면은 기준연도의 CP만 그리므로(core.js가 r.year === 기준연도로 거른다) 그 연도만 내려보낸다.
        # 저장은 보낸 연도만 교체하므로 다른 연도 행은 영향받지 않는다.
        # 정렬은 증감사유와 같은 이유로 유니크 키 순서로 고정한다 — 연도 단위로 전체를 지우고 다시
        # 넣는 경로라(아래 4-3) 순서가 보장되지 않으면 POC·직급 표가 저장할 때마다 섞인다.
        for r in db.scalars(
            select(OverseasCpRow)
            .where(OverseasCpRow.year == f"{basis_year:04d}")
            .order_by(
                OverseasCpRow.year, OverseasCpRow.poc_type, OverseasCpRow.poc_proj, OverseasCpRow.title
            )
        )
    ]

    return OverseasState(
        step=step, basis_year=basis_year, basis_month=basis_month, V=V, actualV=actualV, files=files, note=note,
        reason=reason, vendor=vendor, cp=cp,
    )
