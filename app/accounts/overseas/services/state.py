"""취합 화면 상태 조립 — GET /overseas/state가 돌려주는 전체 상태를 만든다.

왜 여기로 옮겼나: 집계 엔드포인트가 이 라우트 핸들러를 **함수로 직접 호출**하고
있었다(`get_overseas_state(basis_year=..., db=db)`). 그러면 집계 화면이 다른 화면의 응답 모델
전체에 결합되고, 확정 사전점검 한 번에 화면 전체 상태를 로드하는 비용을 낸다. 라우트 핸들러는
HTTP 입출력만 맡고, 조립은 이 모듈이 한다 — 집계는 서비스를 부른다.

이 상태 모양은 도메인이 아니라 프런트 화면 구조(V/actualV/files/note/reason/vendor/cp)를
모델링한 것이다. 그 점은 여기서 고치지 않는다 — Phase 5에서 도메인별 저장 API로
점진 분해할 때 다룬다. 지금은 "어디에 사는가"만 바로잡는다.
"""

import hashlib

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.shared.core.periods import ym_month, ym_year, yyyymm
from app.accounts.overseas.models import OverseasReasonRow, OverseasVendorRow, OverseasVendorRowDetail
from app.shared.models.account import AccountProgress
from app.shared.models.common import CostEntry, CostEntryDetail, Note, Upload
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
from app.accounts.overseas.services.cp import cp_rows_at, guard_cp_shrink, replace_cp_rows
from app.accounts.overseas.services.entries import (
    FIRST_PLAN_NO,
    applied_plan_no,
    bulk_upsert_cost_entries,
    bulk_upsert_plan_entries,
    current_basis_month,
    entry_months_bulk,
    plan_entry_months_bulk,
    plan_version_rows,
    writable_plan_no,
)
from app.accounts.overseas.services.labs import ACCOUNT_KEY, all_labs, check_effective_from, overseas_account
from app.accounts.overseas.services.notes import bulk_upsert_notes
from app.accounts.overseas.services.reason_rows import upsert_reason_rows
from app.accounts.overseas.services.vendor import replace_vendor_rows
# 비교 기준(전실행·전년·경영계획)은 집계·증감분석이 쓰는 해석기를 그대로 쓴다 — 화면이 자기
# 규칙으로 고르면 같은 화면 안에서 위(브라우저 계산)와 아래(서버 계산)가 다른 달을 비교한다.
from app.accounts.overseas.services.versions import resolve_bases


def latest_uploads_stmt(lab_pks: set[str], yyyymms: list[str]):
    """(scope_key, yyyymm) 조합마다 가장 최근 업로드 1건만 고르는 쿼리.

    uploads는 지울 일이 없이 계속 쌓이기만 하는 이력 테이블이라, 계정 전체를 파이썬으로 끌어와
    마지막 것만 남기는 방식은 업로드가 늘수록 그대로 느려진다. DISTINCT ON으로 DB에서 바로
    최신 행만 받아 온다(연구소·달 필터도 같이 밀어 넣어 쓸모없는 행은 아예 안 읽는다).

    같은 달에 여러 번 받았으면 차수가 높은 쪽(= 나중에 받은 쪽)이 "지금 그 달의 파일"이다."""
    return (
        select(Upload)
        .where(
            Upload.account_key == ACCOUNT_KEY,
            Upload.scope_key.in_(lab_pks),
            Upload.yyyymm.in_(yyyymms),
        )
        .distinct(Upload.scope_key, Upload.yyyymm)
        .order_by(Upload.scope_key, Upload.yyyymm, Upload.uploaded_at.desc(), Upload.id.desc())
    )


def _entry_periods_bulk(db: Session, entries) -> dict[int, list[float | None]]:
    """엔트리별 [1Q,2Q,3Q,4Q,연간]. 저장된 행이 없는 칸은 None(미등록)이다.

    헤더의 yyyymm 연도 것만 읽는다 — 기간을 늘려 받은 자료(24·36개월)의 분기·연간은 화면이 아직
    연도별로 나눠 보여주지 않아서, 여기서 섞어 내리면 2년치가 1년 칸에 겹친다."""
    ids = [e.id for e in entries]
    if not ids:
        return {}
    year_by_id = {e.id: ym_year(e.yyyymm) for e in entries}
    out: dict[int, list[float | None]] = {}
    for r in db.scalars(
        select(CostEntryDetail).where(
            CostEntryDetail.cost_entry_id.in_(ids),
            CostEntryDetail.period_type.in_(("QUARTER", "YEAR")),
            CostEntryDetail.col_name == "",
        )
    ):
        if r.year != year_by_id.get(r.cost_entry_id):
            continue
        cells = out.setdefault(r.cost_entry_id, [None] * 5)
        cells[r.period - 1 if r.period_type == "QUARTER" else 4] = float(r.amount)
    return out


def _slot_entries(db: Session, spec, lab_pks, custom_keys, slot) -> None:
    """버전 슬롯 하나(전실행·전년·경영계획·당월)의 월 값을 slot에 채운다.

    spec은 resolve_bases가 푼 (연, 월, 종류, 차수)다. 추가 행(label 있는 행)은 빼고 담는다 —
    extra-rows 엔드포인트가 따로 내려주므로 여기 넣으면 화면에서 두 번 세어진다.
    """
    year, month, kind, plan_no = spec
    if not kind:
        return
    if kind == "plan":
        entries = [
            e
            for e in db.scalars(
                select(ManagementPlanEntry).where(
                    ManagementPlanEntry.account_code == ACCOUNT_KEY,
                    ManagementPlanEntry.year == year,
                    ManagementPlanEntry.plan_no == plan_no,
                )
            )
            if e.scope_code in lab_pks
        ]
        months_by_id = plan_entry_months_bulk(db, [e.id for e in entries])
    else:
        entries = [
            e
            for e in db.scalars(
                select(CostEntry).where(
                    CostEntry.account_code == ACCOUNT_KEY,
                    CostEntry.version == kind,
                    CostEntry.yyyymm == yyyymm(year, month),
                )
            )
            if e.scope_code in lab_pks and (e.scope_code, e.category_code) not in custom_keys
        ]
        months_by_id = entry_months_bulk(db, entries)
    # 저장된 분기·연간(QUARTER/YEAR)도 같이 돌려준다 — 연구소가 적어 보낸 값이 그대로 들어 있는
    # 자리라, 화면이 월 합으로 다시 만들면 보고서와 다른 숫자가 된다. 월 합과 같은 경우에도
    # 그대로 내려준다(같은지 다른지는 받는 쪽이 판단할 일이 아니다).
    periods_by_id = _entry_periods_bulk(db, entries) if kind != "plan" else {}
    for e in entries:
        months = months_by_id[e.id]
        if e.scope_code == "_ETC":
            slot.etc = months
        else:
            slot.d.setdefault(e.scope_code, {})[e.category_code] = months
            cells = periods_by_id.get(e.id)
            if cells:
                slot.per.setdefault(e.scope_code, {})[e.category_code] = cells


def build_state(
    db: Session,
    basis_year: int,
    basis_month: int | None = None,
    kind: str = "forecast",
    plan_no: str | None = None,
) -> OverseasState:
    acct = overseas_account(db)
    labs = all_labs(db)
    lab_pks = {lab.pk for lab in labs}
    lab_codes = {lab.lab_code for lab in labs}

    progress = db.scalar(select(AccountProgress).where(AccountProgress.account_id == acct.id))
    step = progress.current_step_order if progress else 1

    V: dict[str, VersionData] = {v: VersionData(d={}, etc=[0.0] * 12) for v in ("cur", "prev", "prior", "plan")}
    # 기준월은 **호출하는 쪽이 정한다** — 보낸 월이 있으면 그 달을 그대로 쓴다. 그 달에 행이 하나도
    # 없어도 DB에 들어 있는 다른 달로 대체하지 않는다: 그게 이 함수가 유일하게 남아 있던 "월을
    # 짐작하는 자리"였고, 화면은 그 짐작 때문에 아직 아무것도 안 올린 달을 열어도 직전 달 데이터를
    # 받아 업로드 현황에 ✓가 켜졌다(202611 행이 없는데 속보 11월이 업로드 완료로 보임).
    # 월을 안 보내는 호출(옛 클라이언트·내부 도구)만 종전처럼 DB의 최신 속보월로 짐작한다.
    # versions.py:385 · labor_split.py:70이 "build_state는 월을 짐작한다"며 이 함수를 피해 간
    # 이유도 이것이었다 — 이제 월을 받으니 그 우회가 필요하지 않다.
    if basis_month is None:
        basis_month = current_basis_month(db)
    # (예전 필드) 증감사유·외주용역이 yyyymm을 갖게 되어 더 이상 짐작하지 않는다 — 늘 보고 있는
    # 달과 같다. 업로드 이력(uploads)만 아직 월이 없는데, 그쪽은 아래에서 "그 달에 값이 있는
    # 연구소에만 파일명을 붙이는" 방식으로 따로 막고 있다.
    monthless_month = basis_month

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

    # cur(보고 있는 버전) · prev(전실행) · prior(전년) · plan(경영계획)을 **집계·증감분석과 같은
    # 해석기**로 푼다(resolve_bases). 여기서 따로 고르면 — 예컨대 "그 해 actual 중 최신"으로
    # 집으면 — 실적 버전이 둘 이상일 때 이 응답의 전실행과 집계·증감분석의 전실행이 다른 달이 된다.
    #
    # 규칙: 속보 M → 전실행 = 실적 M-1, 실적 M → 전실행 = 같은 달 속보 M.
    # V["plan"]이 실제로 어느 차수인지(화면이 "P02 기준"이라고 밝히는 값). 기준월을 모르면
    # 비교 기준 자체를 풀 수 없어 빈 문자열로 둔다.
    resolved_plan_no = ""
    # 자리(cur · prev · prior · plan)마다 "그 자리가 가리키는 달". 증감사유 · 외주용역을 어느 자리로
    # 내려줄지 고르는 데 쓴다 — 두 테이블이 yyyymm을 갖게 되면서 월을 짐작할 필요가 없어졌다.
    slot_ym: dict[str, str] = {}
    slots: dict[str, str] = {}
    if basis_month:
        resolved = resolve_bases(db, basis_year, basis_month, kind, plan_no)
        resolved_plan_no = next(
            (b.plan_no for b in resolved.bases if b.key == "plan" and b.plan_no), ""
                )
        cur_spec = (
            (basis_year, basis_month, "plan", resolved.default_plan_no)
            if kind == "plan"
            else (basis_year, basis_month, kind, "")
            )
        _slot_entries(db, cur_spec, lab_pks, custom_keys, V["cur"])
        slot_ym["cur"] = yyyymm(basis_year, basis_month)
        slots["cur"] = f"{slot_ym['cur']}|{'forecast' if kind != 'actual' else 'actual'}"
        for base in resolved.bases:
            if base.key in V and base.month:
                slot_ym[base.key] = yyyymm(base.year, base.month)
                slots[base.key] = f"{slot_ym[base.key]}|{base.kind}"
            elif base.key == "plan":
                slots["plan"] = f"{basis_year}|{base.plan_no or resolved.default_plan_no}"
            if base.key in V and base.has_data:
                _slot_entries(
                    db, (base.year, base.month, base.kind, base.plan_no),
                    lab_pks, custom_keys, V[base.key],
                    )

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
                # 이 스냅샷은 "올해 어디까지 실적으로 마감됐나"(thru_month)를 화면에 알려주는 값이다.
                # 전실행(V["prev"])은 여기서 정하지 않는다 — 위에서 resolve_bases가 버전 순서대로 푼다.
        actualV["cur"] = snap

        # prior(전년 실적)·plan(경영계획)도 위 resolve_bases 결과로 이미 채워져 있다.
        # 전에는 여기서 "작년 actual 중 최신"·"applied_plan_no"로 따로 골랐는데, 그 둘도 집계·증감분석이
        # 쓰는 해석과 어긋날 수 있는 자리였다(전년 = 전년 12월 실적, 경영계획 = ★ 차수).

    # 어느 달이 어느 자리인지 뒤집은 표. 한 달이 두 자리를 겸할 수도 있다(드물지만 막을 이유는 없다).
    roles_of_ym: dict[str, list[str]] = {}
    for role, ym in slot_ym.items():
        roles_of_ym.setdefault(ym, []).append(role)

    # 업로드 이력도 기준월로 읽는다 — 자리(cur·prev…)는 저장돼 있지 않고 **그 달이 어느 자리인지**로
    # 정한다(증감사유·외주용역과 같은 규칙). 전에는 이력에 달이 없어서 "그 달에 비용 값이 실제로 있는
    # 연구소에만 파일명을 붙인다"는 우회를 뒀는데, 이제 파일이 자기 달을 들고 있어 그럴 필요가 없다.
    files: dict[str, dict[str, FileInfo]] = {v: {} for v in ("cur", "prev", "prior", "plan")}
    # 경영계획은 달이 없는 자료라 yyyymm이 빈 문자열이다(그때 version이 P02 같은 계획 차수다).
    ym_wanted = {ym for ym in slot_ym.values() if ym} | {""}
    for up in db.scalars(latest_uploads_stmt(lab_pks, sorted(ym_wanted))):
        for role in (roles_of_ym.get(up.yyyymm) or (["plan"] if not up.yyyymm else [])):
            files[role][up.scope_key] = FileInfo(
                file=up.filename, at=up.uploaded_at.strftime("%m.%d %H:%M"), yyyymm=up.yyyymm
            )

    note: dict[str, dict[str, str]] = {}
    for n in db.scalars(select(Note).where(Note.account_key == ACCOUNT_KEY)):
        key = f"{n.base_version}|{n.period}|{n.scope}"
        note.setdefault(key, {})[n.category_key] = n.note_text

    reason = [
        ReasonRow(
            lab_code=r.lab_code,
            version=r.version,
            slot=role,
            yyyymm=r.yyyymm,
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
            .where(
                OverseasReasonRow.lab_code.in_(lab_codes),
                OverseasReasonRow.yyyymm.in_(roles_of_ym or [""]),
            )
            .order_by(
                OverseasReasonRow.lab_code,
                OverseasReasonRow.version,
                OverseasReasonRow.category_key,
                OverseasReasonRow.block,
            )
        )
        for role in roles_of_ym.get(r.yyyymm, [])
    ]

    # 외주용역 상세 — 마스터(행) + 디테일(월 금액)을 12개월 배열로 합쳐 내려준다.
    vendor_rows = list(
        db.scalars(
            select(OverseasVendorRow)
            .where(
                OverseasVendorRow.lab_code.in_(lab_codes),
                OverseasVendorRow.yyyymm.in_(roles_of_ym or [""]),
            )
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
            lab_code=r.lab_code, version=r.version, slot=role, yyyymm=r.yyyymm, row_no=r.row_no, category=r.category,
            project=r.project, company=r.company, description=r.description,
            m=vendor_months.get(r.id, [0.0] * 12), source_file=r.source_file,
        )
        for r in vendor_rows
        for role in roles_of_ym.get(r.yyyymm, [])
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
        #
        # 그 연도의 **모든** 세트가 아니라 이 버전 시점의 세트 하나만 보낸다 — 시점별 스냅샷이라
        # 전부 보내면 화면이 같은 POC를 여러 벌 받아 계약액이 배로 보인다(services/cp.py 참고).
        # 정렬은 cp_rows_at이 유니크 키 순서로 고정해 준다(순서가 흔들리면 POC·직급 표가 저장할
        # 때마다 섞인다 — 증감사유와 같은 이유).
        for r in cp_rows_at(db, basis_year, f"{basis_year:04d}-{(basis_month or 12):02d}")
    ]

    return OverseasState(
        step=step, basis_year=basis_year, basis_month=basis_month, V=V, actualV=actualV, files=files, note=note,
        reason=reason, vendor=vendor, cp=cp,
        # V["plan"]이 어느 차수인지 — 화면이 "P02 기준"이라고 밝힐 수 있어야 한다. 빈 문자열은
        # 그 해 경영계획 차수가 아예 없다는 뜻이다(0원이 아니라 데이터 없음).
        plan_no=resolved_plan_no,
        slots=slots,
        monthless_month=monthless_month,
        # 이 응답을 만든 시점의 지문. PUT이 이걸 돌려보내면 그 사이 다른 저장이 있었는지 알 수 있다.
        state_token=state_token(db),
    )


# ---------- 동시 편집 보호 (낙관적 잠금) ----------
# 같은 화면을 띄운 두 세션(같은 사람의 탭 2개 포함)이 저장하면 나중 것이 전체를
# 덮었다(last-write-wins). 덮인 쪽은 자기 수정이 사라진 걸 알 방법이 없었다.
#
# 해결: GET이 "지금 상태의 지문"(state_token)을 같이 내려주고, PUT이 그걸 그대로 돌려보낸다.
# 그 사이 누가 저장했으면 지문이 달라져 있으므로 409로 막고 "다시 불러오세요"라고 한다.
# 스키마 변경이 없다(PLAN.md P1-2) — 기존 테이블의 타임스탬프만 읽는다.
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


def save_state(db: Session, state: OverseasState) -> dict[str, object]:
    """PUT /overseas/state의 저장 본체 — 화면 상태 전체(비용·경영계획·업로드 이력·메모·증감사유·
    외주용역·CP·진행 단계)를 한 트랜잭션으로 넣는다. 응답 뒤에 돌릴 번역 예열은 호출부(엔드포인트)가
    한다 — BackgroundTasks는 HTTP 계층의 물건이라 여기로 끌고 들어오지 않는다."""
    acct = overseas_account(db)
    labs = all_labs(db)
    lab_pks = {lab.pk for lab in labs}
    lab_codes = {lab.lab_code for lab in labs}

    # 동시 편집 보호 — 쓰기를 시작하기 전에 막는다. 검증이 쓰기보다 뒤에 있으면
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
    # 행을 모아서 bulk_upsert_*에 한 번에 넘긴다 — lab×category 조합마다 따로 upsert하면
    # 조합 수만큼 DB 왕복이 느는데, 해외연구소는 연구소×비용항목 조합이 많아서 영향이 크다.
    forecast_yyyymm = yyyymm(state.basis_year, state.basis_month) if state.basis_month else None
    cost_rows: list[tuple[str, str, str, str, list[float]]] = []
    # 연구소가 파일에 적어 보낸 1Q~4Q·연간. 월 합으로 덮지 않고 그대로 저장한다.
    cost_periods: dict[tuple[str, str, str, str], list[float | None]] = {}
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
            # 업로드가 보낸 분기·연간만 쓴다(state.cost_periods). vd.per는 GET 응답용 읽기 값이라
            # 여기서 받으면 "읽은 걸 그대로 돌려보내는" 저장이 지난 분기 값을 다시 박는다.
            for pk, cats in (state.cost_periods or {}).items():
                for category_key, cells in cats.items():
                    cost_periods[(pk, "forecast", category_key, forecast_yyyymm)] = cells
            if "_ETC" in lab_pks:
                cost_rows.append(("_ETC", "forecast", "etc", forecast_yyyymm, vd.etc))
        elif version == "plan":
            for pk, cats in vd.d.items():
                for category_key, months in cats.items():
                    plan_rows.append((pk, category_key, state.basis_year, months))
            if "_ETC" in lab_pks:
                plan_rows.append(("_ETC", "etc", state.basis_year, vd.etc))
    # 화면 상태 전체를 받는 경로라 보낸 배열이 그 칸의 전부다 — 빈칸(None)은 "미제출"이라는
    # 보고이므로 저장된 행을 지운다(연구소가 칸을 비워 다시 보낸 경우가 이것이다).
    bulk_upsert_cost_entries(db, cost_rows, clear_blanks=True, periods=cost_periods)
    # 경영계획은 **확정되지 않은 차수**에만 쓴다. 확정한 차수를 화면 저장이 덮으면 "확정"이 뜻을
    # 잃고, 이미 보고한 경영계획 대비 숫자가 소급해서 바뀐다(공통 RULE: 지난 버전 무영향).
    # 보낸 차수가 확정됐거나 쓸 차수가 없으면 **막지 않고 건너뛰고** 응답에 밝힌다 — 경영계획은
    # 이 화면의 입력물이 아니라 비교용으로 들고 있는 값이라, 저장 전체를 세울 이유가 없다.
    plan_skipped = ""
    if plan_rows:
        rows = plan_version_rows(db, state.basis_year)
        # 그 해 차수가 아예 없으면 첫 차수를 만들어 쓴다(종전 동작 그대로) — 차수가 하나라도
        # 있으면 "확정되지 않은 것"만 대상이다.
        target = state.plan_no or writable_plan_no(db, state.basis_year) or (FIRST_PLAN_NO if not rows else "")
        if not target:
            plan_skipped = (
                f"{state.basis_year}년 경영계획은 저장하지 않았다 — 확정되지 않은 차수가 없다"
                "(기준정보 › 경영계획에서 새 차수를 열어야 한다)."
            )
        elif any(r.plan_no == target and r.is_confirmed for r in rows):
            plan_skipped = f"{state.basis_year}년 경영계획 {target}은 확정된 차수라 덮어쓰지 않았다."
        else:
            bulk_upsert_plan_entries(db, plan_rows, target)

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

    # 2) 업로드 이력 — 자리(cur·prev…)가 아니라 **기준월**로 적는다. 자리는 보는 달에 따라 달라지는
    # 값이라 저장하면 9월에 올린 파일이 11월 화면에서도 "이번 파일"이 된다(models.Upload 주석).
    # 화면은 아직 자리별 묶음으로 보내므로 여기서 달로 바꾼다 — cur은 이 저장의 기준월이고,
    # 나머지 자리는 그 자리가 가리키는 달이다(집계·증감분석과 같은 해석기를 쓴다).
    slot_ym = {"cur": yyyymm(state.basis_year, state.basis_month)} if state.basis_month else {}
    plan_rev = ""
    if state.basis_month:
        bases = resolve_bases(db, state.basis_year, state.basis_month, "forecast", state.plan_no)
        for base in bases.bases:
            if base.month:
                slot_ym[base.key] = yyyymm(base.year, base.month)
            elif base.key == "plan":
                plan_rev = base.plan_no or bases.default_plan_no
    # 같은 (연구소, 달)에 이미 같은 파일이 있으면 다시 적지 않는다. 다른 파일이면 차수를 올려 쌓는다 —
    # 지난 차수를 지우지 않으므로 "무엇을 받아 지금 숫자가 됐나"가 남는다.
    seen: dict[tuple[str, str], tuple[str, int]] = {}
    for up in db.scalars(latest_uploads_stmt(lab_pks, sorted({ym for ym in slot_ym.values() if ym} | {""}))):
        rev = int(up.version[1:]) if up.version[:1] == "v" and up.version[1:].isdigit() else 0
        seen[(up.scope_key, up.yyyymm)] = (up.filename, rev)
    for version, pk_files in state.files.items():
        ym = slot_ym.get(version, "")
        if version != "plan" and not ym and not any(i.yyyymm for i in pk_files.values()):
            continue  # 달을 모르는 자리는 적지 않는다 — 어느 달 파일인지 말할 수 없다
        for pk, info in pk_files.items():
            ym = info.yyyymm or ym   # 화면이 달을 실어 보냈으면 그게 맞다(자리 해석보다 우선)
            last_file, last_rev = seen.get((pk, ym), ("", 0))
            if last_file == info.file:
                continue
            # 경영계획만 계획 차수(P02)를 쓴다 — 나머지는 그 달의 다음 차수다.
            rev = (plan_rev or "P00") if version == "plan" else f"v{last_rev + 1}"
            db.add(Upload(account_key=ACCOUNT_KEY, scope_key=pk, yyyymm=ym, version=rev, filename=info.file))
            seen[(pk, ym)] = (info.file, last_rev + 1)

    # 3) 증감 사유 메모
    note_rows: list[tuple[str, str, str, str, str]] = []
    for key, cats in state.note.items():
        parts = key.split("|")
        if len(parts) != 3:
            continue
        base_version, period, scope = parts
        for category_key, text_ in cats.items():
            note_rows.append((base_version, period, scope, category_key, text_))
    bulk_upsert_notes(db, note_rows)

    # 4-1) 증감사유 — 전용 저장 API(PUT /overseas/reason-rows)와 **같은 함수**를 부른다.
    # 외주용역·CP와 달리 전체 교체가 아니라 보낸 것만 누적 반영이다(연구소 회신을 쌓아 가는 자료).
    # 행에 기준월이 없으면 이 저장의 기준월로 채운다 — 화면은 버전마다 그 버전의 달을 실어 보내고,
    # 월을 모르는 옛 클라이언트만 이 기본값으로 떨어진다.
    upsert_reason_rows(db, state.reason, lab_codes, yyyymm(state.basis_year, state.basis_month))

    # 4-2) 외주용역 상세 — 전용 저장 API(PUT /overseas/vendor-rows)와 **같은 함수**를 부른다.
    # 쓰기 규칙이 두 벌이 되면 PLAN P1-3에서 겪은 "같은 데이터, 두 가지 쓰기 규칙"을 다시 만든다.
    replace_vendor_rows(
        db, state.basis_year, state.vendor, lab_codes, yyyymm(state.basis_year, state.basis_month)
    )

    # 4-3) CP 계약 — 전용 저장 API(PUT /overseas/cp-rows)와 **같은 함수**를 부른다.
    # 적용 시점은 연구소 기준정보와 같은 규칙이다(공통 RULE: 변경한 버전부터 적용, 지난 버전 무영향) —
    # 지금 편성 중인 기준월부터 이 계약이 적용되고, 이미 마감한 버전은 바뀌기 전 세트를 계속 본다.
    cp_effective_from = check_effective_from(None, db)
    # 줄이는 교체는 확인 없이 지나가지 못한다 — 전용 API와 같은 규칙이다(services/cp.guard_cp_shrink).
    guard_cp_shrink(db, state.cp, cp_effective_from, state.confirm_cp_shrink)
    replace_cp_rows(db, state.cp, cp_effective_from)

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
    out: dict[str, object] = {"status": "ok", "state_token": state_token(db)}
    if plan_skipped:
        # 조용히 버리지 않는다 — 화면이 "저장됐다"고만 알리면 경영계획이 안 들어간 걸 아무도 모른다.
        out["plan_skipped"] = plan_skipped
    return out
