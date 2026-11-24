"""취합(해외연구소 편성) 엔드포인트 — 화면 상태 저장/조회, 기준정보(연구소·시트), 세분화 CRUD,
업로드 이력, 번역·요약, 이상 감지, 파생 행·열, 행 이름 맞추기/합치기.

라우트는 요청을 받고 검증한 뒤 services/를 부르는 얇은 층이다 — 계산·저장 규칙은 전부
services/(state·summary·vendor·anomaly_rules·row_alias·row_merge·carry·translate·notes·uploads·
extra_rows·labs)에 있다. 쓰기 규칙이 두 벌이 되면 "같은 데이터, 두 가지 쓰기 규칙"이 되므로,
일괄 저장(PUT /overseas/state)과 전용 저장 API는 같은 서비스 함수를 부른다.
"""

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.shared.core import row_match
from app.shared.core.reason_translate import (
    MAX_LEN,
    MAX_TEXTS,
    engine_label,
    normalize,
    resolve_provider,
    summarize_reasons,
)
from app.shared.core.storage import upload_bytes
from app.db.session import get_db
from app.accounts.overseas.models import OverseasLab, OverseasLabSheet, OverseasVendorRow, OverseasVendorRowDetail
from app.shared.models.account import AccountProgress
from app.shared.models.common import CostEntry, CostEntryDetail, ExtraCol, Note, SheetRow, Upload
from app.shared.models.generic import AnomalyRuleLog, CarryDef, RowAlias, TextTranslation
from app.shared.models.system import CommonCode
from app.accounts.overseas.cost_groups import CATEGORY_LABELS
from app.shared.core.enums import CodeGroup
from app.shared.core.periods import PERIOD_IX, ym_year, yyyymm
from app.shared.core.values import n
from app.accounts.overseas.services import anomaly_rules as anomaly_rules_service
from app.accounts.overseas.services import translate as translate_service
from app.accounts.overseas.services.carry import carry_forward
from app.accounts.overseas.services.cost_headers import check_labels, read_cost_headers, replace_cost_headers
from app.accounts.overseas.services.cp import (
    contract_at,
    cp_rows_at,
    guard_cp_shrink,
    replace_cp_rows,
    rescale_rows,
)
from app.accounts.overseas.services.entries import (
    entry_months_bulk,
    get_or_create_entry,
    upsert_entry_months,
)
from app.accounts.overseas.services.extra_rows import (
    block_duplicate_label,
    check_col_formula,
    check_delete_step,
    check_lab_pk,
    check_row_formula,
    check_version,
    classify_extra_value,
    eval_col_formula,
    eval_row_formula,
    extra_cell_value,
    months_by_category,
    read_extra_rows,
)
from app.accounts.overseas.services.fxcalc import lab_currency_warning
from app.accounts.overseas.services.labor_split import (
    prepare as labor_split_prepare,
    split_for as labor_split_for,
)
from app.accounts.overseas.services.labs import (
    ACCOUNT_KEY,
    LabView,
    all_labs,
    check_category,
    check_effective_from,
    lab_attrs,
    lab_views,
    overseas_account,
    record_lab_revision,
)
from app.accounts.overseas.services.notes import upsert_note as upsert_note_row
from app.accounts.overseas.services.reason_rows import prewarm_in_background, upsert_reason_rows
from app.accounts.overseas.services.row_alias import alias_rows, match_candidates
from app.accounts.overseas.services.row_merge import merge_rows as merge_rows_service
from app.accounts.overseas.services.state import build_state, save_state, state_token
from app.accounts.overseas.services.summary import build_summary
from app.accounts.overseas.services.uploads import next_revision
from app.accounts.overseas.services.vendor import replace_vendor_rows, vendor_by_project
from app.accounts.overseas.services.versions import check_kind
from app.accounts.overseas.schemas import (
    AnomalyRuleIn,
    AnomalyRuleLogOut,
    AnomalyRuleOut,
    AnomalyScanIn,
    AnomalyScanResult,
    CarryDefIn,
    CarryDefOut,
    CostHeaderIn,
    CostHeaderOut,
    CarryForwardIn,
    CarryForwardResult,
    CostEntryIn,
    CostEntryOut,
    CpContractAmountIn,
    CpContractChangeOut,
    CpRowsIn,
    ExtraColIn,
    ExtraColOut,
    ExtraRowIn,
    ExtraRowOut,
    ExtraValueIn,
    ExtraValueOut,
    LabIn,
    LabOneOut,
    LabOut,
    LabSheetIn,
    LabSheetOut,
    LaborSplitResult,
    NoteIn,
    NoteOut,
    OverseasState,
    OverseasSummary,
    ReasonRowsIn,
    ReasonSummaryIn,
    ReasonSummaryResult,
    ReasonTranslateIn,
    ReasonTranslateItem,
    ReasonTranslateResult,
    RowAliasIn,
    RowAliasOut,
    RowMatchIn,
    RowMatchItem,
    RowMatchResult,
    RowMatchSuggestion,
    RowMergeIn,
    RowMergeResult,
    SheetRowIn,
    SheetRowOut,
    StepUpdate,
    UploadIn,
    UploadOut,
    VendorRow,
    VendorRowsIn,
    VendorByProjectResult,
)

router = APIRouter()


def check_state_token(db: Session, token: str | None) -> None:
    """전용 저장의 동시 편집 검사 — vendor-rows·cp-rows·PUT /overseas/state와 같은 규칙.

    전용 저장(추가 행·추가 열 칸)도 지문(state_token)에 잡히는 테이블을 쓴다. 그래서 두 가지가
    필요하다: ① 낡은 지문이면 거부(남의 저장을 모르고 덮지 않게) ② 성공 응답에 새 지문을 실어
    화면이 갈아끼우게 — 안 그러면 칸 하나 고친 화면이 낡은 지문을 들고 있다가 다음 전체 저장에서
    **자기 저장과** 충돌한다(409). 토큰이 없으면(챗봇) 검사하지 않는다."""
    if token and token != state_token(db):
        raise HTTPException(
            status_code=409,
            detail=(
                "다른 저장과 충돌했습니다 — 이 화면을 불러온 뒤 누군가 같은 화면을 저장했습니다. "
                "화면을 다시 불러온 뒤 다시 입력해 주세요."
            ),
        )


# 경영계획(plan) 역할태그는 cost_entries에 저장하지 않고 management_plan_entries가 담당한다
# (plan_no별로 영구 보관·비교해야 해서 cost_entries의 forecast/actual과는 다른 테이블).
# 어느 차수에 쓰는지는 services/entries.py가 정한다 — 여기에 차수 상수를 두면 화면이 P02를 보고
# 있어도 저장이 다른 차수로 들어간다.


@router.get("/overseas/state", response_model=OverseasState)
def get_overseas_state(
    basis_year: int,
    basis_month: int | None = None,
    kind: str = "forecast",
    plan_no: str | None = None,
    db: Session = Depends(get_db),
) -> OverseasState:
    """취합 화면이 처음 열릴 때 받는 전체 상태. 조립은 services가 한다 —
    집계 엔드포인트가 이 핸들러를 함수로 직접 부르던 걸 끊기 위함이다.

    basis_month는 **보는 쪽이 어느 달 버전을 열었는지**다(다른 엔드포인트와 같은 규칙).
    화면이 속보 2026년 11월을 열었으면 11을 보내고, 그 달에 cost_entries 행이 없으면
    V["cur"]가 빈 채로 내려간다 — 업로드 현황이 전부 "미"가 되어야 맞다. 안 보내면 DB의 최신
    속보월로 짐작한다.

    kind는 **속보를 보는지 실적을 보는지**다. 이게 있어야 비교 기준(전실행·전년·경영계획)을
    집계·증감분석과 같은 규칙으로 풀 수 있다 — 속보 M의 전실행은 실적 M-1이고, 실적 M의
    전실행은 같은 달 속보 M이라 종류를 모르면 어느 쪽인지 정할 수 없다. 안 보내면 속보로 본다.
    """
    check_kind(kind)
    return build_state(db, basis_year, basis_month, kind, plan_no)


@router.put("/overseas/state")
def save_overseas_state(
    state: OverseasState,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """화면 상태 전체 저장 — 본체는 services/state.save_state에 있다."""
    out = save_state(db, state)
    # 증감사유 번역은 응답 뒤에 돌린다 — 저장(이 경로는 화면의 모든 저장이 지나간다)을 LLM이
    # 붙잡지 않게 한다. 업로드할 때 한 번 번역해 두면 조회는 항상 같은 값을 읽는다.
    prewarm_in_background(background, list(state.reason or []))
    return out


@router.put("/overseas/vendor-rows")
def save_vendor_rows(body: VendorRowsIn, db: Session = Depends(get_db)) -> dict[str, object]:
    """외주용역 상세만 저장한다 — 연구소×버전 단위 전체 교체.

    왜 따로 있나: `PUT /overseas/state`로 보내면 외주용역 한 시트를 올려도 화면이 가진 상태
    전부가 전송된다. 외주용역은 이미 "단위 교체" 의미라 떼어내기 가장 쉬운 자리다.

    `PUT /overseas/state`와 **같은 서비스 함수**를 부른다 — 쓰기 규칙이 두 벌이 되면 "같은
    데이터, 두 가지 쓰기 규칙"이 되고, 일괄·개별 두 경로의 결과가 갈린다.
    """
    if body.state_token:
        if body.state_token != state_token(db):
            raise HTTPException(
                status_code=409,
                detail=(
                    "다른 저장과 충돌했습니다 — 이 화면을 불러온 뒤 누군가 같은 화면을 저장했습니다. "
                    "화면을 다시 불러온 뒤 다시 올려 주세요."
                ),
            )
    lab_codes = {lab.lab_code for lab in all_labs(db)}
    replaced = replace_vendor_rows(
        db, body.basis_year, body.rows, lab_codes,
        yyyymm(body.basis_year, body.basis_month) if body.basis_month else "",
    )
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"저장 실패 (제약조건 위반): {e.orig}") from e
    # 저장 뒤 새 지문을 같이 준다 — 안 주면 화면이 들고 있는 토큰이 낡아서 다음 저장이 409가 된다.
    return {"status": "ok", "replaced": replaced, "state_token": state_token(db)}


@router.put("/overseas/cp-rows")
def save_cp_rows(body: CpRowsIn, db: Session = Depends(get_db)) -> dict[str, object]:
    """CP 계약만 저장한다 — 연도 단위 전체 교체.

    `PUT /overseas/state`와 같은 서비스 함수를 부른다. 구 경로와 신 경로를
    병행해 둬도 결과가 갈리지 않는다.
    """
    if body.state_token and body.state_token != state_token(db):
        raise HTTPException(
            status_code=409,
            detail=(
                "다른 저장과 충돌했습니다 — 이 화면을 불러온 뒤 누군가 같은 화면을 저장했습니다. "
                "화면을 다시 불러온 뒤 다시 올려 주세요."
            ),
        )
    effective_from = check_effective_from(body.effective_from, db)
    # 가드 1(사람이 의도한 교체인가)은 업로드 경로에만 둔다 — 금액만 바꾸는 경로
    # (change_cp_contract_amount)는 POC를 보존하고 이미 전후 비교를 사람에게 보여준 뒤 반영한다.
    guard_cp_shrink(db, body.rows, effective_from, body.confirm_shrink)
    replaced = replace_cp_rows(db, body.rows, effective_from)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"저장 실패 (제약조건 위반): {e.orig}") from e
    return {"status": "ok", "replaced": replaced, "state_token": state_token(db)}


@router.put("/overseas/cp-contract-amount", response_model=CpContractChangeOut)
def change_cp_contract_amount(body: CpContractAmountIn, db: Session = Depends(get_db)) -> CpContractChangeOut:
    """CP 계약의 **금액만** 바꾼다 — "2026 계약액 5.2M$로 바꿔줘" 같은 요청용.

    업로드(PUT /overseas/cp-rows)는 파일로 표 전체를 갈아끼우는 경로이고, 이건 지금 올라와 있는
    계약을 그대로 두고 금액 항목만 같은 비율로 움직인다. 인원·맨먼스는 건드리지 않는다 —
    "계약액이 얼마로 바뀌었다"는 말은 돈 이야기지 인력 구성 이야기가 아니다.

    적용 시점은 연구소 기준정보와 같은 규칙이다(공통 RULE: 변경한 버전부터 적용, 지난 버전 무영향).
    9월에 바꾸면 9월 이후 버전만 새 계약액으로 분기 정산액이 계산되고, 8월 이하 버전은 바뀌기 전
    금액 그대로다. 응답은 전·후를 같이 준다 — 이 변경의 핵심이 "얼마가 얼마로, 분기 정산액은
    어떻게"라서, 바뀐 숫자만 주면 사용자가 무엇을 승인했는지 되짚을 수 없다.
    """
    if body.state_token and body.state_token != state_token(db):
        raise HTTPException(
            status_code=409,
            detail=(
                "다른 저장과 충돌했습니다 — 이 화면을 불러온 뒤 누군가 같은 화면을 저장했습니다. "
                "화면을 다시 불러온 뒤 다시 시도해 주세요."
            ),
        )

    by_year_total = body.year_total is not None
    by_poc = body.poc_total is not None
    if by_year_total == by_poc:
        raise HTTPException(
            400,
            "연 계약 총액(year_total)이나 POC 하나의 계약액(poc_proj + poc_total) 중 **하나만** "
            "줘야 한다. 어느 쪽인지에 따라 금액이 몇 배씩 달라지므로 서버가 짐작하지 않는다.",
        )
    new_amount = body.year_total if by_year_total else body.poc_total
    if new_amount is None or new_amount <= 0:
        raise HTTPException(400, f"계약액은 0보다 커야 한다(받은 값: {new_amount}).")

    effective_from = check_effective_from(body.effective_from, db)
    before = contract_at(db, body.year, effective_from)
    if not before.registered:
        raise HTTPException(
            400,
            f"{body.year}년 CP 계약이 {effective_from} 버전 시점에 등록되어 있지 않아 금액만 바꿀 수 "
            "없다. 기준정보 › CP 계약에서 계약 파일을 먼저 올려야 한다.",
        )

    rows = cp_rows_at(db, body.year, effective_from)
    if by_year_total:
        # 연 합계를 바꾼다 — POC가 여럿이면 지금 비중대로 나눠 가진다. 비중을 모르면(합이 0) 나눌
        # 방법이 없으므로 짐작하지 않고 멈춘다.
        if before.year_total <= 0:
            raise HTTPException(
                400,
                "지금 계약액이 0이라 연 합계를 POC들에 나눌 비중이 없다. POC 하나씩 "
                "(poc_proj + poc_total) 지정해서 바꿔야 한다.",
            )
        factor = new_amount / before.year_total
        factors = {(p.poc_type, p.poc_proj): factor for p in before.pocs}
        target = f"{body.year}년 연 계약액"
    else:
        matched = [
            p for p in before.pocs
            if p.poc_proj == body.poc_proj and (body.poc_type is None or p.poc_type == body.poc_type)
        ]
        if len(matched) != 1:
            names = ", ".join(f"{p.poc_type}/{p.poc_proj}" for p in before.pocs)
            raise HTTPException(
                400,
                f"그 POC를 하나로 특정할 수 없다(찾은 개수: {len(matched)}). 등록된 POC: {names or '없음'}.",
            )
        poc = matched[0]
        if poc.total <= 0:
            raise HTTPException(400, f"POC '{poc.poc_proj}'의 지금 계약액이 0이라 비율로 바꿀 수 없다.")
        factors = {(poc.poc_type, poc.poc_proj): new_amount / poc.total}
        target = f"POC '{poc.poc_proj}' 계약액"

    replace_cp_rows(db, rescale_rows(rows, factors), effective_from)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"저장 실패 (제약조건 위반): {e.orig}") from e

    after = contract_at(db, body.year, effective_from)
    # 금액은 저장 단위인 $로 적고, 집계 표가 쓰는 K$를 괄호로 같이 보여준다 — 둘 중 하나만 적으면
    # 승인하는 사람이 자기가 보고 있는 표의 숫자와 못 맞춰 본다(두 화면의 단위가 1000배 다르다).
    note = (
        f"{target}을 {before.year_total:,.0f}$ → {after.year_total:,.0f}$로 바꿨다"
        f"({effective_from} 버전부터). 분기 정산액은 {before.quarter_total:,.0f}$ → "
        f"{after.quarter_total:,.0f}$가 되고(집계 표는 K$로 보여주니 {before.quarter_total / 1000:,.1f}K$ → "
        f"{after.quarter_total / 1000:,.1f}K$로 읽힌다), 3 · 6 · 9 · 12월에 들어간다. "
        f"인건비(분기)는 {before.quarter_labor:,.0f}$ → {after.quarter_labor:,.0f}$다. "
        f"{effective_from}보다 이전 버전은 바뀌기 전 금액 그대로다."
        + (f" POC {len(before.pocs)}개에 지금 비중대로 나눴다." if by_year_total and len(before.pocs) > 1 else "")
    )
    return CpContractChangeOut(before=before, after=after, effective_from=effective_from, note=note)


@router.put("/overseas/reason-rows")
def save_reason_rows(
    body: ReasonRowsIn,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """증감사유 업로드 시트만 저장한다 — 보낸 것만 누적 반영(전체 교체가 아니다).

    `PUT /overseas/state`와 같은 서비스 함수를 부른다.
    """
    if body.state_token and body.state_token != state_token(db):
        raise HTTPException(
            status_code=409,
            detail=(
                "다른 저장과 충돌했습니다 — 이 화면을 불러온 뒤 누군가 같은 화면을 저장했습니다. "
                "화면을 다시 불러온 뒤 다시 올려 주세요."
            ),
        )
    lab_codes = {lab.lab_code for lab in all_labs(db)}
    saved = upsert_reason_rows(
        db, body.rows, lab_codes,
        yyyymm(body.basis_year, body.basis_month) if (body.basis_year and body.basis_month) else "",
    )
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"저장 실패 (제약조건 위반): {e.orig}") from e
    # 저장 직후(응답 뒤) 번역해서 캐시에 넣는다 — 조회할 때마다 같은 번역이 나오게 하려는 것이다.
    prewarm_in_background(background, body.rows)
    return {"status": "ok", "saved": saved, "state_token": state_token(db)}


# ---------- 인건비 증감의 원인: 인원 효과 vs 단가 효과 ----------
@router.get("/overseas/labor-split", response_model=LaborSplitResult)
def get_labor_split(
    basis_year: int,
    base_version: str = "prev",
    period: str = "y",
    lab_scope: str = "ALL",
    basis_month: int | None = None,
    kind: str = "forecast",
    db: Session = Depends(get_db),
) -> LaborSplitResult:
    """인건비 증감을 인원 효과 · 단가 효과로 나눈다(한 기간).

    계산은 services/labor_split.py에 있다 — 증감분석 표(17기간)와 **같은 함수**를 쓴다.
    식이 두 벌이 되면 화면과 챗봇이 다른 답을 한다.

    basis_month · kind는 "어느 버전을 보고 있는가"다(집계표·증감 표와 같은 파라미터). 안 주면
    지금 편성 중인 속보로 떨어진다 — 과거 버전의 분해를 보려면 같이 보내야 한다.
    """
    ix = PERIOD_IX.get(period)
    if ix is None:
        raise HTTPException(400, "period는 y(연간) | q1~q4 | m1~m12만 쓸 수 있다.")
    inp = labor_split_prepare(db, basis_year, base_version, lab_scope, basis_month, kind)
    return labor_split_for(db, inp, period, ix)


# ---------- 히어로 카드(화면 맨 위 편성 현황) ----------
@router.get("/overseas/summary", response_model=OverseasSummary)
def get_overseas_summary(
    basis_year: int,
    version: str = "cur",
    period: str = "y",
    basis_month: int | None = None,
    kind: str = "forecast",
    plan_no: str | None = None,
    db: Session = Depends(get_db),
) -> OverseasSummary:
    """화면 맨 위 히어로 카드와 ③집계 단계 KPI 카드에 들어가는 금액 — 계산은 services/summary.py."""
    ix = PERIOD_IX.get(period)
    if ix is None:
        raise HTTPException(400, "period는 y(연간) | q1~q4 | m1~m12만 쓸 수 있다.")
    return build_summary(db, basis_year, version, period, ix, basis_month, kind, plan_no)


# ---------- 외주용역비 과제별 집계 ----------
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
    return vendor_by_project(db, version, base_version, lab_code, period, ix)


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
# scope_code는 연구소 pk를 그대로 쓴다(공용 테이블이라 overseas_labs로 FK는 못 걸고, check_lab_pk로 검증).
# version은 forecast(속보) | actual(실적) 두 값만 받는다 — plan(경영계획)은 /management-plan 쪽을 쓸 것.


@router.get("/overseas/labs", response_model=list[LabOut])
def list_labs(as_of: str | None = None, db: Session = Depends(get_db)) -> list[LabOut]:
    """연구소·파트 기준정보. 프론트는 이 목록을 lab_code로 묶어 탭을 그리므로,
    여기 행만 추가하면(배포 없이) 새 연구소 탭이 화면에 나타난다.

    as_of("YYYY-MM")를 주면 그 시점에 쓰던 기준정보를 돌려준다 — 기준정보를 바꿔도 지난 버전은
    옛 값으로 계산되므로(공통 RULE), "그때는 통화가 뭐였나"를 확인할 때 쓴다. 안 주면 지금 값."""
    if as_of is not None:
        as_of = check_effective_from(as_of, db)
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
        effective_from = check_effective_from(row.effective_from, db)
        after = {"name": row.name, "currency": row.currency, "is_active": row.is_active, "sort_order": order * 10}
        if not existing:
            # 새로 만든 연구소는 "그 전에는 없던 것"(is_active=False)으로 고정한다 — 안 그러면
            # 과거 버전의 집계·업로드 진척에 방금 만든 연구소가 끼어든다.
            record_lab_revision(db, row.lab_code, {**after, "is_active": False}, after, effective_from)
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
        record_lab_revision(db, row.lab_code, lab_attrs(existing), after, effective_from)
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


@router.put("/overseas/labs/one", response_model=LabOneOut)
def upsert_lab(body: LabIn, db: Session = Depends(get_db)) -> LabOneOut:
    """연구소 하나만 추가하거나 수정한다 — 다른 연구소는 전혀 건드리지 않는다. 연구소를 하나 추가·
    수정하는 거라면(예: 에이전트·스크립트) `PUT /overseas/labs`(전체 목록 교체, 빠뜨린 연구소는
    삭제됨) 대신 반드시 이걸 써라. 화면에 보이는 "연구소 기준정보 편집" 그리드(ovLabPage)의 저장만
    예외적으로 `PUT /overseas/labs`를 그대로 쓴다 — 그 화면은 "그리드에서 지운 행 = 삭제"라는 동작이
    필요하기 때문이다."""
    existing = list(db.scalars(select(OverseasLab).where(OverseasLab.lab_code == body.lab_code)))
    # save_labs와 같은 규칙 — 바뀐 값은 effective_from 버전부터만 적용되고 지난 버전은 그대로다.
    effective_from = check_effective_from(body.effective_from, db)
    if not existing:
        next_order = (db.scalar(select(func.max(OverseasLab.sort_order))) or 0) + 10
        after = {"name": body.name, "currency": body.currency, "is_active": body.is_active, "sort_order": next_order}
        record_lab_revision(db, body.lab_code, {**after, "is_active": False}, after, effective_from)
        db.add(
            OverseasLab(
                pk=body.lab_code, lab_code=body.lab_code, part_code=None, name=body.name,
                country="", sort_order=next_order, currency=body.currency, is_active=body.is_active,
            )
        )
    else:
        record_lab_revision(
            db,
            body.lab_code,
            lab_attrs(existing),
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
    return LabOneOut(
        pk=lab.pk, lab_code=lab.lab_code, part_code=lab.part_code, name=lab.name, country=lab.country,
        sort_order=lab.sort_order, currency=lab.currency, is_active=lab.is_active,
        # 통화는 환율표와 따로 관리돼서, 환율 없는 통화를 잡아도 아무도 안 막는다 — 저장은 그대로
        # 하되(사용자가 환율보다 연구소를 먼저 등록할 수 있다) 환산이 안 된다는 사실을 같이 돌려준다.
        fx_warning=lab_currency_warning(db, lab.currency, effective_from),
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
    check_category(db, body.category_key)
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
    yyyymm: str | None = None,
    version: str | None = None,
    lab_pk: str | None = None,
    db: Session = Depends(get_db),
) -> list[UploadOut]:
    stmt = select(Upload).where(Upload.account_key == ACCOUNT_KEY).order_by(Upload.uploaded_at.desc())
    if yyyymm:
        stmt = stmt.where(Upload.yyyymm == yyyymm)
    if version:
        stmt = stmt.where(Upload.version == version)
    if lab_pk:
        stmt = stmt.where(Upload.scope_key == check_lab_pk(db, lab_pk))
    return [
        UploadOut(
            lab_pk=u.scope_key, version=u.version, yyyymm=u.yyyymm, filename=u.filename,
            uploaded_at=u.uploaded_at, s3_key=u.s3_key,
        )
        for u in db.scalars(stmt)
    ]


@router.post("/overseas/uploads", response_model=UploadOut)
def record_upload(body: UploadIn, db: Session = Depends(get_db)) -> UploadOut:
    check_lab_pk(db, body.lab_pk)
    rev = body.version or next_revision(db, body.lab_pk, body.yyyymm)
    up = Upload(
        account_key=ACCOUNT_KEY, scope_key=body.lab_pk, yyyymm=body.yyyymm, version=rev, filename=body.filename
    )
    db.add(up)
    db.commit()
    db.refresh(up)
    return UploadOut(lab_pk=body.lab_pk, version=rev, yyyymm=body.yyyymm, filename=body.filename, uploaded_at=up.uploaded_at)


@router.post("/overseas/uploads/file", response_model=UploadOut)
async def upload_raw_file(
    lab_pk: str = Form(...),
    yyyymm: str = Form(""),
    version: str = Form(""),
    client_token: str = Form("", alias="state_token"),
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
    # 보관 경로도 달 밑으로 둔다 — 자리 이름(cur)으로 나누면 같은 폴더에 서로 다른 달 파일이 섞인다.
    rev = version or next_revision(db, lab_pk, yyyymm)
    key = (
        f"overseas/{lab_pk}/{yyyymm or 'plan'}/{rev or 'v1'}/"
        f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}_{safe_name}"
    )
    s3_key = upload_bytes(data, key, content_type=file.content_type)
    # 보관도 uploads 행을 추가해 동시 편집 지문을 움직인다. 화면 지문이 보관 직전과 같았을 때만
    # 새 지문을 돌려준다 — 409는 내지 않는다(UploadOut.state_token 주석).
    token_was_fresh = bool(client_token) and client_token == state_token(db)
    up = Upload(
        account_key=ACCOUNT_KEY, scope_key=lab_pk, yyyymm=yyyymm, version=rev,
        filename=file.filename, s3_key=s3_key,
    )
    db.add(up)
    db.commit()
    db.refresh(up)
    return UploadOut(
        lab_pk=lab_pk, version=rev, yyyymm=yyyymm, filename=file.filename,
        uploaded_at=up.uploaded_at, s3_key=up.s3_key,
        state_token=state_token(db) if token_was_fresh else None,
    )


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
    upsert_note_row(db, body.base_version, body.period, body.lab_scope, body.category_key, body.note_text)
    db.commit()
    return NoteOut(**body.model_dump())


# ---------- 증감사유 번역 (영문 회신 → 보고용 한국어) ----------
@router.post("/overseas/reason-translate", response_model=ReasonTranslateResult)
async def translate_reasons(body: ReasonTranslateIn, db: Session = Depends(get_db)) -> ReasonTranslateResult:
    """연구소가 영문으로 보낸 증감사유를 한국어로 번역한다(원문은 그대로 보관).
    캐시 규칙은 services/translate.py에 있다."""
    return await translate_service.translate_reasons(db, body)


@router.post("/overseas/reason-summary", response_model=ReasonSummaryResult)
async def summarize_reason_rows(body: ReasonSummaryIn) -> ReasonSummaryResult:
    """지금 보고 있는 회신 묶음 → 담당자가 먼저 읽을 요약 3~5줄.

    번역(/overseas/reason-translate)은 한 문장을 한국어로 옮기고, 이건 여러 계정에 흩어진 회신을
    가로질러 읽는다 — 연구소 하나가 보낸 사유가 계정 20여 개로 쪼개져 있어서 담당자가 표를 훑으며
    머릿속에서 모으던 일이다.

    DB를 쓰지 않는다(Session 의존성이 없는 이유): 요약은 저장하지도 캐시하지도 않는다 — 원문·번역은
    이미 표와 캐시에 있고, 요약은 화면이 무엇을 걸러 놨느냐에 따라 매번 달라지는 읽을거리다.

    실패해도 200으로 돌려주고 error에 이유를 담는다 — 요약이 안 된다고 사유 표가 사라지면 안 된다
    (번역 엔드포인트와 같은 약속)."""
    items: list[tuple[str, str]] = []
    too_long = 0
    for it in body.items:
        text = normalize(it.text)
        if not text:
            continue
        if len(text) > MAX_LEN:
            too_long += 1
            continue
        items.append((normalize(it.label), text))
    if not items:
        raise HTTPException(400, "요약할 회신 원문이 없다.")
    if len(items) > MAX_TEXTS:
        raise HTTPException(
            400,
            f"한 번에 요약할 수 있는 회신은 {MAX_TEXTS}건까지입니다 (요청 {len(items)}건). 기간이나 연구소를 좁혀주세요.",
        )

    provider = resolve_provider(body.provider)
    lines: list[str] = []
    error = ""
    try:
        lines = await summarize_reasons(items, body.scope_label, provider)
    except HTTPException as e:
        error = str(e.detail)
    if too_long:
        error = (error + " " if error else "") + f"{too_long}건은 원문이 {MAX_LEN}자를 넘어 요약에서 뺐습니다."
    return ReasonSummaryResult(lines=lines, count=len(items), engine=engine_label(provider), error=error)


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
@router.get("/overseas/anomaly-rules", response_model=list[AnomalyRuleOut])
def list_anomaly_rules(db: Session = Depends(get_db)) -> list[AnomalyRuleOut]:
    """지금 적용 중인 이상 감지 기준 4개. 해외연구소 전체에 같이 적용된다."""
    return anomaly_rules_service.rules_out(db)


@router.put("/overseas/anomaly-rules", response_model=list[AnomalyRuleOut])
def upsert_anomaly_rule(body: AnomalyRuleIn, db: Session = Depends(get_db)) -> list[AnomalyRuleOut]:
    """기준 하나를 바꾼다(보낸 항목만). reset=true면 그 기준을 기본값으로 되돌린다."""
    return anomaly_rules_service.upsert_rule(db, body)


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
    """업로드 값(또는 이미 저장된 값)을 기준에 걸어 본다. 반영을 막지 않고 목록만 돌려준다."""
    return anomaly_rules_service.scan(db, body)


# ---------- 집계 파생 행·열의 "다음 버전에도 유지" ----------
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
    """유지 승인한 파생 행·열의 정의를 from 버전 → to 버전으로 복제한다(새 버전을 열 때 호출)."""
    return carry_forward(db, body)


# ---------- 업로드 행 이름 맞추기 (Travel Exp ↔ Travel Expense) ----------
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

    aliases = alias_rows(db, [row_match.normalize(x) for x in labels])
    candidates = match_candidates(db, body.yyyymm, body.version)
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


# ---------- 이미 적재된 행 합치기 (TC-008) ----------
@router.post("/overseas/row-merge", response_model=RowMergeResult)
def merge_rows(body: RowMergeIn, db: Session = Depends(get_db)) -> RowMergeResult:
    """이미 적재된 추가 행의 금액을 다른 행으로 합쳐 옮긴다. 원본 행은 지운다.
    규칙(지나간 칸 금지 · dry_run 기본 · 분기 재계산)은 services/row_merge.py에 있다."""
    return merge_rows_service(db, body)


# ---------- 파생 행·열 · 추가 열 ----------
@router.put("/overseas/extra-rows", response_model=ExtraRowOut)
def upsert_extra_row(body: ExtraRowIn, db: Session = Depends(get_db)) -> ExtraRowOut:
    check_state_token(db, body.state_token)
    # 파생 행은 월별 값을 저장하지 않고 계산식만 저장한다 — 원본이 업로드로 바뀌면 파생값도 자동으로
    # 따라 바뀌어야 하므로, 조회할 때마다 계산식을 다시 평가한다(저장된 숫자는 금방 낡기 때문).
    formula = None
    if body.is_derived:
        formula = check_row_formula(body.formula)
    # 전체 합산 범위(ALL)는 집계 화면의 파생 행 전용이다 — 원시 행(연구소가 실제로 보고한 값)은
    # 반드시 실재하는 연구소에 붙어야 하므로 ALL을 받지 않는다.
    check_lab_pk(db, body.lab_pk, allow_all=body.is_derived)
    check_version(body.version)
    # row_key가 하필 고정 계정 code(예: "sal")와 같으면 get_or_create_entry가 그 업로드된 실제
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
    if body.source == "chat":
        block_duplicate_label(db, body, existing)
    # v를 아예 안 보내면(None) "저장된 금액은 손대지 않는다"는 뜻이다 — 이미 있는 행을 다른 항목
    # 아래로 옮길 때 쓴다. 금액이 제자리에 남는다는 뜻이 **아니다**: 월별 금액은 이 행(cost_entry)에
    # 매달려 있어서 행과 함께 옮겨가고, 집계도 새 anchor_key 아래에서 센다(aggregate.py의
    # extra_row_months). 금액을 되보내지 않는 건 그 왕복에서 값이 상하는 걸 막기 위해서다:
    # 화면이 $ 단위면 서버가 받은 금액을 1000으로 나누는데(chat.py _to_kdollar), 조회해서 받은 K$
    # 값을 그대로 되보내면 1000배 작아진다. 옮기는 데 금액이 필요 없으니 아예 안 받는다.
    #
    # 없는 행에는 이 뜻이 성립하지 않는다 — 옮길 금액이 없으니 그냥 빈 행이 생긴다. 그래서
    # "이미 있는 행"일 때만 허용하고, 아니면 금액을 요구한다(행을 새로 만들 때는 전부 0으로 보낸다).
    keep_months = not body.is_derived and body.v is None and existing is not None
    if not body.is_derived and not keep_months and (not body.v or len(body.v) % 12 != 0):
        detail = (
            "v는 12개월치 값이거나(기본), 12의 배수(24·36…)여야 합니다."
            if body.v is not None
            else f"'{body.row_key}' 행이 아직 없습니다. 새로 만들 때는 v(월별 금액, 보통 전부 0)를 "
            "같이 보내세요 — v를 빼는 건 이미 있는 행의 자리·이름만 고칠 때만 쓸 수 있습니다."
        )
        raise HTTPException(status_code=400, detail=detail)
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
    elif not keep_months:
        upsert_entry_months(db, entry_id, ym_year(body.yyyymm), body.v)
    # 파일에 적혀 온 1Q~4Q·연간 — 고정 계정(entries.py의 periods)과 같은 이유로 월 합으로 만들지
    # 않고 그대로 저장한다. None이면 안 건드리고(이름·자리만 고치는 저장), 리스트면 전체 교체다 —
    # 분기 칸 없이 다시 올라온 파일([])이 지난 파일의 분기값을 남기면 안 된다. 추가 행은 월 저장이
    # 분기·연간을 재계산(resync)하지 않으므로, QUARTER·YEAR 디테일이 있다 = 연구소가 직접 적었다.
    if not body.is_derived and body.per is not None:
        if len(body.per) > 5:
            raise HTTPException(status_code=400, detail="per는 [1Q,2Q,3Q,4Q,연간] 최대 5칸입니다.")
        year = ym_year(body.yyyymm)
        db.execute(
            delete(CostEntryDetail).where(
                CostEntryDetail.cost_entry_id == entry_id, CostEntryDetail.year == year,
                CostEntryDetail.period_type.in_(("QUARTER", "YEAR")),
            )
        )
        period_values = [
            {
                "cost_entry_id": entry_id, "year": year,
                "period_type": "YEAR" if i == 4 else "QUARTER", "period": 0 if i == 4 else i + 1,
                "col_name": "", "amount": amount,
            }
            for i, amount in enumerate(body.per)
            if amount is not None
        ]
        if period_values:
            db.execute(pg_insert(CostEntryDetail).values(period_values))
    db.commit()

    result = ExtraRowOut(**body.model_dump())
    # 저장 뒤 새 지문 — 받은 토큰(낡은 것)이 응답에 그대로 실려 나가면 화면이 그걸 다시 들고
    # 다음 저장에서 또 충돌한다(vendor-rows와 같은 규칙).
    result.state_token = state_token(db)
    if keep_months:
        # 금액을 안 받았으니 응답에도 지금 저장돼 있는 값을 실어 보낸다 — 빈 배열을 돌려주면
        # 부르는 쪽이 "옮겼더니 금액이 지워졌다"로 읽는다.
        entry = db.get(CostEntry, entry_id)
        result.v = entry_months_bulk(db, [entry]).get(entry_id, [])
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
    lab_pk: str, version: str, row_key: str, yyyymm: str, step: str | None = None,
    confirmed: bool = False,
    client_token: str | None = Query(None, alias="state_token"),
    db: Session = Depends(get_db),
) -> dict[str, str | None]:
    """cost_entries 행을 지운다(디테일도 FK cascade로 같이 삭제됨). label이 있는(=연구소가 직접 추가한)
    행만 지운다 — label.is_not(None) 조건이 없으면 row_key가 하필 고정 계정 code(예: "sal")와 같을 때
    업로드된 실제 데이터까지 지워버릴 수 있다.
    step: 지금 보고 있는 화면 단계(취합|집계) — 추가한 단계에서만 지울 수 있게 검사한다.

    state_token은 **검사하지 않고** 응답에만 쓴다 — 삭제의 409는 "단계가 달라 정말 지울 거냐"는
    물음이라서(화면이 확인창을 띄우고 재시도한다), 지문 충돌까지 같은 409로 보내면 화면이 둘을
    구분할 수 없다. 대신 받은 지문이 삭제 직전과 같았을 때만 새 지문을 돌려줘서, 화면 지문이
    낡은 채 남지 않게 한다(다른 사람 저장이 끼었으면 새 지문을 안 주고, 다음 전체 저장의 409가
    그 사실을 알린다)."""
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
    check_delete_step(step, target.is_derived, "행", confirmed)
    token_was_fresh = bool(client_token) and client_token == state_token(db)
    db.execute(delete(CostEntry).where(CostEntry.id == target.id))
    db.commit()
    return {"status": "ok", "state_token": state_token(db) if token_was_fresh else None}


# 추가 열은 extra_cols 테이블이 존재·이름·파생 여부를 관리하고, 원시 열의 칸 값은 그대로
# cost_entry_details(period_type="CUSTOM", col_name=열 라벨)에 저장된다. 파생 열은 칸 값을 저장하지
# 않고 계산식(월을 가로로 묶음)으로 조회할 때마다 계산한다.
# label 자체가 열 식별자라 열 이름을 바꾸는 건 지원하지 않는다(지우고 새 이름으로 다시 넣으면 됨).
#
# 표시 형식(fmt)은 값이 아니라 **그리는 방법**이다. 양식 밖 열에는 비율이 연구소마다 다른 모양으로
# 오는데("0.825" · "82.5"), 값만 보고는 어느 쪽인지 알 수 없다 — 0.825를 일률적으로 82.5%로 고쳐
# 그리면 이미 퍼센트로 적어 보낸 연구소는 100배가 된다. 그래서 추측하지 않고 열마다 사람이 한 번
# 골라 둔다. 저장된 칸 값은 형식과 무관하게 원문 그대로다(글자 값도 형식을 안 탄다).
COL_FMTS = ("raw", "pct_ratio", "pct_plain")


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
            is_derived=c.is_derived, formula=c.formula, fmt=c.fmt,
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
    formula = check_col_formula(body.formula) if body.is_derived else None
    if body.fmt not in COL_FMTS:
        raise HTTPException(400, f"표시 형식은 {' | '.join(COL_FMTS)}만 쓸 수 있다.")
    stmt = pg_insert(ExtraCol).values(
        account_key=ACCOUNT_KEY, scope_key=body.lab_pk, version=body.version, yyyymm=body.yyyymm,
        label=body.label, is_derived=body.is_derived, formula=formula, fmt=body.fmt,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["account_key", "scope_key", "version", "yyyymm", "label"],
        set_={
            "is_derived": stmt.excluded.is_derived,
            "formula": stmt.excluded.formula,
            "fmt": stmt.excluded.fmt,
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
                # JSON 타입이 곧 저장된 타입이다(숫자면 number, 문자열이면 text) — 화면은 typeof로
                # 가른다: 숫자는 계산에 쓰고 글자는 그대로 그린다.
                label=row.col_name, value=extra_cell_value(row.data_type, row.value, float(row.amount)),
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
                value, basis = eval_col_formula(col.formula, [n(v) for v in months_by_entry[entry.id]])
                out.append(
                    ExtraValueOut(
                        lab_pk=entry.scope_code, version=entry.version, row_key=entry.category_code,
                        yyyymm=yyyymm, label=col.label, value=value, basis=basis,
                    )
                )
    return out


@router.put("/overseas/cost-extras", response_model=ExtraValueOut)
def upsert_cost_extra(body: ExtraValueIn, db: Session = Depends(get_db)) -> ExtraValueOut:
    check_state_token(db, body.state_token)
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
    # 타입 판정(숫자인가 글자인가)은 classify_extra_value 한 곳에서만 한다 — 원문은 value에
    # 글자 그대로, data_type이 읽는 쪽의 분기 기준이다(models.CostEntryDetail 주석).
    data_type, raw, amount = classify_extra_value(body.value)
    stmt = pg_insert(CostEntryDetail).values(
        cost_entry_id=entry_id, year=ym_year(body.yyyymm), period_type="CUSTOM", period=0, col_name=body.label,
        amount=amount, data_type=data_type, value=raw,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["cost_entry_id", "year", "period_type", "period", "col_name"],
        set_={
            "amount": stmt.excluded.amount, "data_type": stmt.excluded.data_type,
            "value": stmt.excluded.value, "updated_at": datetime.now(timezone.utc),
        },
    )
    db.execute(stmt)
    db.commit()
    # 응답 값은 저장된 타입으로 돌려준다 — "1,200"을 보냈어도 number로 저장됐으면 1200.0이 온다.
    # 조회(GET)와 같은 값이어야 호출부가 저장 직후 다시 읽지 않아도 된다.
    # state_token은 저장 뒤의 새 지문으로 바꿔 싣는다(받은 낡은 토큰을 되돌려주면 안 된다).
    return ExtraValueOut(
        **body.model_dump()
        | {"value": extra_cell_value(data_type, raw, amount), "state_token": state_token(db)}
    )


@router.delete("/overseas/extra-cols")
def delete_extra_col(
    lab_pk: str, version: str, label: str, yyyymm: str, step: str | None = None,
    confirmed: bool = False,
    client_token: str | None = Query(None, alias="state_token"),
    db: Session = Depends(get_db),
) -> dict[str, str | None]:
    """열 등록을 지우고, 그 열(label)이 달린 같은 yyyymm의 모든 행에서 칸 값도 같이 지운다.
    step: 지금 보고 있는 화면 단계(취합|집계) — 추가한 단계에서만 지울 수 있게 검사한다.
    state_token은 검사하지 않고 응답에만 쓴다 — delete_extra_row와 같은 이유(409의 뜻이 겹친다)."""
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
    check_delete_step(step, bool(target and target.is_derived), "열", confirmed)
    token_was_fresh = bool(client_token) and client_token == state_token(db)
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
    return {"status": "ok", "state_token": state_token(db) if token_was_fresh else None}


# 비용 시트의 기간 열 머리글 원문(1월…12월 · 1Q~4Q · 연간). 연구소가 파일에 적어 보낸 글자를
# 그대로 들고 있다가 취합 › 비용 표가 코드 머리글("1월") 대신 그 글자를 보여 준다 —
# 왜 집계·증감분석 표에는 안 쓰는지는 models.OverseasCostHeader 주석에 적어 뒀다.
# 추가 열(extra-cols)과 달리 머리글은 사람이 화면에서 고치는 값이 아니라 **파일에서 읽은 사실**이라,
# 업로드를 반영할 때 그 시트 한 벌을 통째로 교체한다.


@router.get("/overseas/cost-headers", response_model=list[CostHeaderOut])
def list_cost_headers(
    yyyymm: str, version: str | None = None, lab_pk: str | None = None, db: Session = Depends(get_db)
) -> list[CostHeaderOut]:
    if version:
        check_version(version)
    if lab_pk:
        check_lab_pk(db, lab_pk)
    return read_cost_headers(db, yyyymm, version, lab_pk)


@router.put("/overseas/cost-headers", response_model=CostHeaderOut)
def upsert_cost_headers(body: CostHeaderIn, db: Session = Depends(get_db)) -> CostHeaderOut:
    """그 시트(연구소 · 버전 · 기준월)의 머리글 한 벌을 통째로 바꿔 넣는다.

    빈 labels는 "머리글을 지운다"는 뜻이다(= 그 표가 코드 머리글로 돌아간다) — 머리글이 없는
    파일을 다시 올렸을 때 지난 파일의 머리글이 남지 않게 하려고 지우기를 따로 두지 않았다."""
    check_lab_pk(db, body.lab_pk)
    check_version(body.version)
    # 저장한 것과 돌려주는 것이 같아야 한다 — 빈 글자를 떨어낸 뒤의 한 벌이 실제로 저장된 머리글이다.
    labels = check_labels(body.labels)
    replace_cost_headers(db, body.lab_pk, body.version, body.yyyymm, labels, body.source_file)
    db.commit()
    return CostHeaderOut(**body.model_dump() | {"labels": labels})


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


@router.get("/overseas/extra-rows", response_model=list[ExtraRowOut])
def list_extra_rows(
    yyyymm: str,
    version: str | None = None,
    lab_pk: str | None = None,
    db: Session = Depends(get_db),
) -> list[ExtraRowOut]:
    """연구소가 추가한 행. 읽기는 services가 한다 — 집계도 같은 함수를 부른다."""
    return read_extra_rows(db, yyyymm=yyyymm, version=version, lab_pk=lab_pk)
