"""취합 화면 상태 조립 — GET /overseas/state가 돌려주는 전체 상태를 만든다.

왜 여기로 옮겼나 (PLAN.md P1-1): 집계 엔드포인트가 이 라우트 핸들러를 **함수로 직접 호출**하고
있었다(`get_overseas_state(basis_year=..., db=db)`). 그러면 집계 화면이 다른 화면의 응답 모델
전체에 결합되고, 확정 사전점검 한 번에 화면 전체 상태를 로드하는 비용을 낸다. 라우트 핸들러는
HTTP 입출력만 맡고, 조립은 이 모듈이 한다 — 집계는 서비스를 부른다.

이 상태 모양은 도메인이 아니라 프런트 화면 구조(V/actualV/files/note/reason/vendor/cp)를
모델링한 것이다(PLAN.md P1-2). 그 점은 여기서 고치지 않는다 — Phase 5에서 도메인별 저장 API로
점진 분해할 때 다룬다. 지금은 "어디에 사는가"만 바로잡는다.
"""

import hashlib

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.shared.core.periods import ym_month, yyyymm
from app.accounts.overseas.models import OverseasCpRow, OverseasReasonRow, OverseasVendorRow, OverseasVendorRowDetail
from app.shared.models.account import AccountProgress
from app.shared.models.common import CostEntry, Note, Upload
from app.shared.models.plan import ManagementPlanEntry
from app.accounts.overseas.schemas import (
    ActualSnapshot,
    CpRow,
    FileInfo,
    OverseasState,
    ReasonRow,
    VendorRow,
    VersionData,
)
from app.accounts.overseas.services.entries import (
    PLAN_NO,
    current_basis_month,
    entry_months_bulk,
    plan_entry_months_bulk,
)
from app.accounts.overseas.services.labs import ACCOUNT_KEY, all_labs, overseas_account


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
        # 이 응답을 만든 시점의 지문. PUT이 이걸 돌려보내면 그 사이 다른 저장이 있었는지 알 수 있다.
        state_token=state_token(db),
    )


# ---------- 동시 편집 보호 (낙관적 잠금) ----------
# PLAN.md P1-2: 같은 화면을 띄운 두 세션(같은 사람의 탭 2개 포함)이 저장하면 나중 것이 전체를
# 덮었다(last-write-wins). 덮인 쪽은 자기 수정이 사라진 걸 알 방법이 없었다.
#
# 해결: GET이 "지금 상태의 지문"(state_token)을 같이 내려주고, PUT이 그걸 그대로 돌려보낸다.
# 그 사이 누가 저장했으면 지문이 달라져 있으므로 409로 막고 "다시 불러오세요"라고 한다.
# 스키마 변경이 없다(PLAN 5.2(e)) — 기존 테이블의 타임스탬프만 읽는다.
#
# max(타임스탬프)만 쓰지 않고 count(*)도 섞는 이유: 외주용역·CP는 "단위 교체"(delete+insert)라
# 행을 지우기만 하면 max는 그대로일 수 있다. 개수가 같이 들어가면 그 경우도 지문이 바뀐다.
#
# 타임스탬프가 내용 변경을 따라와야 한다: 증감사유는 upsert라 같은 키를 고쳐도 행 수가 안 변하는데,
# uploaded_at까지 그대로면 지문이 변화를 놓쳐 나중 저장이 조용히 덮어쓴다. 그래서 그 upsert는
# uploaded_at을 같이 갱신한다(services/reason_rows.py) — 테스트가 실제로 이 구멍을 잡았다.
#
# 한계: Postgres의 now()는 트랜잭션 시작 시각이라 **한 트랜잭션 안에서 두 번 저장하면** 지문이
# 같다. HTTP 요청마다 트랜잭션이 하나씩이라 운영에서는 문제가 없지만(요청별 now()는 마이크로초
# 단위로 다르다), 한 요청 안에서 두 번 쓰고 그 사이 지문을 비교하는 코드를 쓰면 안 된다.
_TOKEN_PARTS: tuple[tuple[str, str], str] = (
    ("cost_entries", "updated_at", "account_code = :acct"),
    ("management_plan_entries", "updated_at", "account_code = :acct"),
    ("notes", "updated_at", "account_key = :acct"),
    ("uploads", "uploaded_at", "account_key = :acct"),
    ("overseas_reason_rows", "uploaded_at", ""),
    ("overseas_vendor_rows", "uploaded_at", ""),
    ("overseas_cp_rows", "uploaded_at", ""),
)


def state_token(db: Session) -> str:
    """이 화면이 저장하는 행들의 "지금 모습"을 요약한 짧은 지문.

    값(월별 금액)은 cost_entry_details·management_plan_entry_details에 들어 있어서 헤더 테이블만
    보면 금액 수정을 놓친다 — 그래서 디테일도 같이 센다(해당 계정 헤더에 달린 것만).
    """
    parts: list[str] = []
    for table, ts, cond in _TOKEN_PARTS:
        where = f" WHERE {cond}" if cond else ""
        row = db.execute(
            text(f"SELECT coalesce(max({ts})::text, '-'), count(*) FROM {table}{where}"),
            {"acct": ACCOUNT_KEY} if cond else {},
        ).one()
        parts.append(f"{table}:{row[0]}:{row[1]}")

    # 디테일(월 값)은 해당 계정 헤더에 달린 것만 본다.
    for detail, header, fk, acct_col in (
        ("cost_entry_details", "cost_entries", "cost_entry_id", "account_code"),
        ("management_plan_entry_details", "management_plan_entries", "plan_entry_id", "account_code"),
    ):
        row = db.execute(
            text(
                f"SELECT coalesce(max(d.updated_at)::text, '-'), count(*) FROM {detail} d "
                f"JOIN {header} h ON h.id = d.{fk} WHERE h.{acct_col} = :acct"
            ),
            {"acct": ACCOUNT_KEY},
        ).one()
        parts.append(f"{detail}:{row[0]}:{row[1]}")

    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
