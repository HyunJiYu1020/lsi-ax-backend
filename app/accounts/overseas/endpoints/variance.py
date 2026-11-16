"""해외연구소 증감분석 화면의 HTTP 엔드포인트 — 파라미터 검증 → services 호출 → 응답.

계산·판정은 app/services/overseas/variance.py에 있다(PLAN.md P1-1). 여기 남은 건 라우트 핸들러
14개뿐이다 — 다른 화면(집계·결산)이 이 모듈의 내부 이름을 꺼내 쓰던 걸 끊기 위해 갈랐다.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.accounts.overseas.cost_groups import CATEGORY_LABELS, CATEGORY_LABELS_EN, VARIANCE_ROWS, resolve_leaves
from app.shared.core.periods import PERIOD_IX, month_value, yyyymm
from app.shared.core.values import n, source_hash
from app.accounts.overseas.services.entries import current_basis_month, entry_months_bulk, plan_entry_months_bulk
from app.accounts.overseas.services.fxcalc import rate_arrays
from app.accounts.overseas.services.labs import ACCOUNT_KEY, LabView, all_labs, lab_views
from app.shared.core.reason_translate import (
    MAX_LEN,
    draft_reason,
    engine_label,
    normalize,
    resolve_provider,
    translate_batch,
)
from app.db.session import get_db
from app.accounts.overseas.models import OverseasActualFirstRow
from app.shared.models.close import CloseAccountStage
from app.shared.models.common import CostEntry
from app.shared.models.generic import TextTranslation, VarianceDriver, VarianceReason
from app.shared.models.plan import ManagementPlanEntry, ManagementPlanVersion
from app.shared.models.version import Version
from app.accounts.overseas.schemas import (
    ActualFirstIn,
    ActualFirstResult,
    ActualFirstRow,
    ActualStageIn,
    ActualStageOut,
    ActualStageShift,
    ActualStageShiftResult,
    PlanRevisionOut,
    VarianceBasisOut,
    VarianceBasisResult,
    VarianceDriverFactor,
    VarianceDriverIn,
    VarianceDriverOut,
    VarianceDriverRow,
    VarianceReasonCheck,
    VarianceReasonDraftIn,
    VarianceReasonDraftResult,
    VarianceReasonGap,
    VarianceReasonIn,
    VarianceReasonOut,
    VarianceResult,
    VarianceRow,
)
from app.accounts.overseas.services.variance import (
    compute_reason_check,
    BASE_LABELS,
    DEFAULT_REASON_THRESHOLD_PCT,
    DRIVER_OPS,
    GROUP_KEYS,
    KIND_LABELS,
    RECHECK_TOLERANCE_KRW,
    active_labs,
    apply_recheck,
    cache_translation,
    check_factors,
    check_kind,
    check_period,
    check_stage,
    compute_variance,
    default_plan_no,
    diff_by_category,
    driver_out,
    driver_rows,
    driver_value,
    first_rows,
    fx_specs,
    has_cost_data,
    lab_by_code,
    latest_cost_yyyymm,
    load_basis,
    load_cost_version,
    load_plan_version,
    pct_of,
    plan_creation_prev,
    plan_revisions,
    prev_spec,
    prior_spec,
    reason_filters,
    reason_out,
    require_first_unlocked,
    resolve_bases,
    scope_pks,
    stage_out,
    stage_row,
    to_kdollar,
    variance_cells,
    variance_rows,
    visible_drivers,
    ym_label,
)

router = APIRouter()


@router.get("/overseas/variance-basis", response_model=VarianceBasisResult)
def get_variance_basis(
    basis_year: int,
    basis_month: int | None = None,
    kind: str = "forecast",
    plan_no: str | None = None,
    db: Session = Depends(get_db),
) -> VarianceBasisResult:
    """증감분석 화면의 비교 기준 세 칸이 "실제로 무엇과 비교하는지".

    화면이 직접 계산하지 않고 서버에 묻는 이유: 직전 버전이 무엇인지는 DB에 어떤 버전이 열려
    있고 어디에 데이터가 들어왔는지에 달려 있다. 브라우저가 "기준월 - 1"로 추측하면 1월(연도 넘어감)
    과 데이터가 비어 있는 달에서 틀린 라벨을 보여준다."""
    check_kind(kind)
    month = basis_month or current_basis_month(db) or 1
    if not 1 <= month <= 12:
        raise HTTPException(400, "basis_month는 1~12여야 한다.")
    return resolve_bases(db, basis_year, month, kind, plan_no)


@router.get("/overseas/variance", response_model=VarianceResult)
def get_variance(
    basis_year: int,
    base_version: str = "prev",
    basis_month: int | None = None,
    kind: str = "forecast",
    period: str = "y",
    lab_scope: str = "ALL",
    plan_no: str | None = None,
    stage: str = "",
    db: Session = Depends(get_db),
) -> VarianceResult:
    """계정별 증감분석 표 — 화면이 보여주는 것과 같은 값. 챗봇이 "SARC 인건비 왜 늘었어?"에 답할 때
    쓰는 근거 데이터이기도 하다.

    왜 챗봇이 list_cost_entries로 직접 계산하면 안 되는가: 그 값에는 환율이 움직여서 변한 몫이 섞여
    있다. 두 버전의 금액을 각자의 환율로 환산해 빼면 "실제로 비용이 늘어난 몫"과 "환율 때문에 늘어난
    몫"이 뭉개져서, 사용자가 보는 표와 다른 답이 나온다. 이 엔드포인트는 그 둘을 갈라서 준다."""
    check_kind(kind)
    month = basis_month or current_basis_month(db) or 1
    check_stage(stage, kind)
    return compute_variance(
        db, basis_year, month, kind, base_version, period, lab_scope, plan_no, stage
    )


@router.get("/overseas/variance-reasons", response_model=list[VarianceReasonOut])
def list_variance_reasons(
    basis_year: int,
    base_version: str,
    period: str = "y",
    basis_month: int | None = None,
    kind: str = "forecast",
    stage: str = "",
    lab_scope: str = "ALL",
    plan_no: str | None = None,
    db: Session = Depends(get_db),
) -> list[VarianceReasonOut]:
    """그 화면 조합(비교 기준 · 기간 · 연구소)의 사유 전부 + 지금 증감액.

    current_diff를 같이 내려주는 이유: 화면이 "저장 시점 ▲12억 → 지금 ▲19억"처럼 두 값을 나란히
    보여줘야 담당자가 무엇을 다시 봐야 하는지 안다. needs_recheck 플래그만으로는 얼마나 바뀐 건지
    알 수 없다."""
    check_kind(kind)
    month = basis_month or current_basis_month(db) or 1
    key = VarianceReasonIn(
        basis_year=basis_year, basis_month=month, kind=kind, stage=check_stage(stage, kind),
        base_version=base_version, period=period, lab_scope=lab_scope, category_key="",
    )
    check_period(period)
    rows = list(db.scalars(select(VarianceReason).where(*reason_filters(key))))
    diffs = diff_by_category(db, key, plan_no)
    out: list[VarianceReasonOut] = []
    for row in rows:
        diff = diffs.get(row.category_key)
        out.append(reason_out(row, diff[0] if diff else None))
    return out


@router.put("/overseas/variance-reasons", response_model=VarianceReasonOut)
def upsert_variance_reason(body: VarianceReasonIn, db: Session = Depends(get_db)) -> VarianceReasonOut:
    """사유 한 칸 저장. commit=True면 [저장]을 누른 것으로 보고 저장 시점 증감액까지 박아 둔다.

    1차가 고정([1차 고정])돼 있으면 1차 사유 쓰기를 거부한다 — 보고까지 끝난 값이라서다."""
    check_kind(body.kind)
    stage = check_stage(body.stage, body.kind)
    check_period(body.period)
    if body.base_version not in BASE_LABELS:
        raise HTTPException(400, "base_version은 prev | prior | plan만 쓸 수 있다.")
    if not body.category_key:
        raise HTTPException(400, "category_key는 비울 수 없다.")
    if stage == "first":
        require_first_unlocked(db, body.basis_year, body.basis_month, "1차 사유")

    row = db.scalar(
        select(VarianceReason).where(
            *reason_filters(body), VarianceReason.category_key == body.category_key
        )
    )
    if row is None:
        row = VarianceReason(
            account_key=ACCOUNT_KEY,
            basis_year=body.basis_year, basis_month=body.basis_month, kind=body.kind, stage=stage,
            base_version=body.base_version, period=body.period, lab_scope=body.lab_scope,
            category_key=body.category_key,
        )
        db.add(row)

    for field in ("source_text", "source_ko", "ai_draft", "reason_text"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)

    current_diff = body.current_diff
    if current_diff is None:
        diffs = diff_by_category(db, body, None)
        pair = diffs.get(body.category_key)
        current_diff = pair[0] if pair else None

    if body.commit and body.reason_text is not None:
        # [저장]을 누른 순간을 기준점으로 다시 잡는다 — 재확인 딱지는 여기서 지워지고, 다음 업로드로
        # 금액이 또 바뀌면 그때 다시 켜진다.
        row.saved_diff = current_diff
        row.saved_at = datetime.now(timezone.utc)
        row.needs_recheck = False
    else:
        apply_recheck(row, current_diff)

    db.commit()
    db.refresh(row)
    return reason_out(row, current_diff)


@router.post("/overseas/variance-reason-draft", response_model=VarianceReasonDraftResult)
async def make_variance_reason_draft(
    body: VarianceReasonDraftIn, db: Session = Depends(get_db)
) -> VarianceReasonDraftResult:
    """회신 원문 → 번역 + 보고용 사유 초안. 확정 사유(reason_text)는 건드리지 않는다 — 초안은
    담당자가 고쳐 쓰는 출발점이고, 저장은 [저장]을 눌렀을 때만 일어난다.

    번역은 캐시(overseas_reason_translations)를 쓰고 초안은 캐시하지 않는다 — 같은 원문이라도
    증감액이 달라지면 초안 문장이 달라져야 한다(재업로드가 바로 그 경우다).

    실패해도 200으로 돌려주고 error에 이유를 담는다. 여기서 4xx/5xx를 던지면 사유 칸 하나 때문에
    화면이 통째로 에러로 바뀐다 — 번역이 안 되면 원문으로 그려지는 게 맞다."""
    check_kind(body.kind)
    source = normalize(body.source_text)
    if not source:
        raise HTTPException(400, "source_text(회신 원문)가 비어 있다.")
    if len(source) > MAX_LEN:
        raise HTTPException(400, f"회신 원문이 너무 길다({len(source)}자) — {MAX_LEN}자 이하로 줄여라.")
    provider = resolve_provider(body.provider)
    engine = engine_label(provider)

    cached = db.scalar(
        select(TextTranslation).where(TextTranslation.source_hash == source_hash(source))
    )
    source_ko = cached.ko_text if cached and cached.ko_text else ""
    error = ""
    if not source_ko:
        try:
            translated = await translate_batch([source], provider)
            source_ko = translated[0] or ""
        except HTTPException as e:
            error = str(e.detail)
    if source_ko and not cached:
        cache_translation(db, source, source_ko, engine)

    ai_draft = ""
    if not error:
        try:
            ai_draft = await draft_reason(source_ko, source, body.diff, body.diff_pct, provider) or ""
        except HTTPException as e:
            error = str(e.detail)

    if body.save and (source_ko or ai_draft):
        upsert_variance_reason(
            VarianceReasonIn(
                basis_year=body.basis_year, basis_month=body.basis_month, kind=body.kind, stage=body.stage,
                base_version=body.base_version, period=body.period, lab_scope=body.lab_scope,
                category_key=body.category_key, source_text=source, source_ko=source_ko, ai_draft=ai_draft,
                current_diff=body.diff, commit=False,
            ),
            db,
        )
    return VarianceReasonDraftResult(
        source_text=source, source_ko=source_ko, ai_draft=ai_draft, engine=engine, error=error
    )


@router.get("/overseas/variance-reason-check", response_model=VarianceReasonCheck)
def check_variance_reasons(
    basis_year: int,
    base_version: str = "prev",
    period: str = "y",
    basis_month: int | None = None,
    kind: str = "forecast",
    stage: str = "",
    lab_scope: str = "ALL",
    plan_no: str | None = None,
    threshold_pct: float = DEFAULT_REASON_THRESHOLD_PCT,
    db: Session = Depends(get_db),
) -> VarianceReasonCheck:
    """확정 직전 경고 — 증감률이 기준치를 넘었는데 사유가 비었거나 재확인이 필요한 칸.

    판정은 services가 한다 — 집계 화면의 확정 사전점검도 같은 함수를 부른다(PLAN.md P1-1).
    두 군데서 따로 세면 같은 상황에서 다른 건수를 보여준다."""
    return compute_reason_check(
        db, basis_year=basis_year, base_version=base_version, period=period, basis_month=basis_month,
        kind=kind, stage=stage, lab_scope=lab_scope, plan_no=plan_no, threshold_pct=threshold_pct,
    )


@router.get("/overseas/variance-drivers", response_model=list[VarianceDriverOut])
def list_variance_drivers(
    basis_year: int,
    basis_month: int | None = None,
    kind: str = "forecast",
    lab_scope: str = "ALL",
    base_version: str = "prev",
    period: str = "y",
    plan_no: str | None = None,
    with_values: bool = True,
    db: Session = Depends(get_db),
) -> list[VarianceDriverOut]:
    """이 버전에 붙은 증감 동인 표들. with_values=True면 계산 결과(기준·당월·증감)까지 채워 준다.

    값을 저장하지 않고 매번 계산하는 이유: 연구소가 파일을 다시 올리면 동인 금액도 따라 바뀌어야
    한다. 저장해 두면 원본이 바뀐 뒤에도 옛 숫자가 그대로 남아 틀린 표가 된다."""
    check_kind(kind)
    month = basis_month or current_basis_month(db) or 1
    rows = visible_drivers(db, basis_year, month, kind, lab_scope)
    out = [driver_out(r, basis_year, month, kind) for r in rows]
    if not out or not with_values:
        return out
    try:
        result = compute_variance(db, basis_year, month, kind, base_version, period, lab_scope, plan_no)
    except HTTPException:
        return out  # 비교 기준 데이터가 없으면 표 구성만 돌려준다(값은 빈칸)
    cells = {
        r.category_key: {"base": r.base, "cur": r.cur} for r in result.rows
    }
    for item, row in zip(out, rows):
        item.rows = driver_rows(cells, row.factors or [])
    return out


@router.put("/overseas/variance-drivers", response_model=VarianceDriverOut)
def upsert_variance_driver(body: VarianceDriverIn, db: Session = Depends(get_db)) -> VarianceDriverOut:
    """동인 표를 붙이거나 "다음 버전에도?" 답을 반영한다(carry_over).

    carry_over를 끌 때 이전 버전 행을 고치지 않는 이유: 이 버전에 붙인 표의 설정만 바꾼다. 지난달
    보고에 들어간 표를 지금 결정으로 되돌리면 이미 확정한 자료가 달라진다."""
    check_kind(body.kind)
    factors = check_factors(body.factors)
    if not body.driver_key or not body.title:
        raise HTTPException(400, "driver_key와 title은 비울 수 없다.")

    row = db.scalar(
        select(VarianceDriver).where(
            VarianceDriver.account_key == ACCOUNT_KEY,
            VarianceDriver.basis_year == body.basis_year,
            VarianceDriver.basis_month == body.basis_month,
            VarianceDriver.kind == body.kind,
            VarianceDriver.lab_scope == body.lab_scope,
            VarianceDriver.driver_key == body.driver_key,
        )
    )
    if row is None:
        row = VarianceDriver(
            account_key=ACCOUNT_KEY,
            basis_year=body.basis_year, basis_month=body.basis_month, kind=body.kind,
            lab_scope=body.lab_scope, driver_key=body.driver_key,
        )
        db.add(row)
    row.title = body.title
    row.target_key = body.target_key
    row.factors = factors
    row.requested_text = body.requested_text
    row.carry_over = body.carry_over
    db.commit()
    db.refresh(row)
    return driver_out(row, body.basis_year, body.basis_month, body.kind)


@router.delete("/overseas/variance-drivers")
def delete_variance_driver(
    basis_year: int,
    driver_key: str,
    basis_month: int | None = None,
    kind: str = "forecast",
    lab_scope: str = "ALL",
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """이 버전에 붙인 동인 표를 뗀다. 이전 버전에서 carry_over로 따라온 표는 여기서 지울 수 없다 —
    그건 그 버전의 자료라서, 끊고 싶으면 그 버전에서 carry_over를 끄는 게 맞다."""
    check_kind(kind)
    month = basis_month or current_basis_month(db) or 1
    row = db.scalar(
        select(VarianceDriver).where(
            VarianceDriver.account_key == ACCOUNT_KEY,
            VarianceDriver.basis_year == basis_year,
            VarianceDriver.basis_month == month,
            VarianceDriver.kind == kind,
            VarianceDriver.lab_scope == lab_scope,
            VarianceDriver.driver_key == driver_key,
        )
    )
    if row is None:
        return {"status": "ok"}  # 이미 없음 — 멱등하게 성공
    db.delete(row)
    db.commit()
    return {"status": "ok"}


@router.get("/overseas/actual-stage", response_model=ActualStageOut)
def get_actual_stage(basis_year: int, basis_month: int, db: Session = Depends(get_db)) -> ActualStageOut:
    """실적 한 달의 1차 상태. 화면은 close_first_done으로 [1차 분석] 탭을 열지 말지를, first_locked로
    입력칸을 잠글지를 정한다."""
    return stage_out(db, basis_year, basis_month)


@router.put("/overseas/actual-stage", response_model=ActualStageOut)
def update_actual_stage(body: ActualStageIn, db: Session = Depends(get_db)) -> ActualStageOut:
    """[1차 고정] 토글과 실적결산 1차 완료 표시.

    versions.status는 건드리지 않는다 — 1차 고정은 버전 확정과 다른 층의 결정이다(2차 작업이
    남아 있으므로 버전은 작성중 그대로여야 한다)."""
    row = stage_row(db, body.basis_year, body.basis_month)
    if row is None:
        row = CloseAccountStage(
            account_key=ACCOUNT_KEY, basis_year=body.basis_year, basis_month=body.basis_month
        )
        db.add(row)
    if body.close_first_done is not None:
        # API 이름(close_first_done)은 그대로 두고 저장만 mapping_done으로 간다 — 프런트·챗봇
        # 계약을 바꾸지 않으려는 것이다(PLAN Phase 3·4는 API 모양 유지가 원칙).
        row.mapping_done = body.close_first_done
        row.first_done_at = datetime.now(timezone.utc) if body.close_first_done else None
    if body.first_locked is not None:
        if body.first_locked and not (row.mapping_done or body.close_first_done):
            raise HTTPException(400, "실적결산 1차가 끝나지 않아 고정할 1차 값이 없다.")
        row.first_locked = body.first_locked
        row.first_locked_at = datetime.now(timezone.utc) if body.first_locked else None
    db.commit()
    return stage_out(db, body.basis_year, body.basis_month)


@router.get("/overseas/actual-first", response_model=ActualFirstResult)
def get_actual_first(
    basis_year: int,
    basis_month: int,
    base_version: str = "prev",
    plan_no: str | None = None,
    db: Session = Depends(get_db),
) -> ActualFirstResult:
    """1차(ERP 약식) 분석 표 — 연구소 총액만. 계정 상세도 외주용역도 없다(ERP가 그것까지 주지 않는다).

    환산 사슬이 1차의 핵심이다: ERP가 준 현지통화 총액을 현지/$ 환율로 $로, 원/$ 환율로 원화로
    바꾼다. 그 두 단계가 환차를 현지→$ / $→원으로 가르는 자리이기도 하다 — USD 연구소는 첫 단계가
    없어 fx_local이 0이다.

    비교 기준 금액은 2차와 같은 데이터(cost_entries)에서 가져온다 — 1차 값을 비교할 상대는 직전
    버전의 PL이고, 그쪽에는 1차라는 개념이 없다."""
    month = basis_month
    if not 1 <= month <= 12:
        raise HTTPException(400, "basis_month는 1~12여야 한다.")
    resolved = resolve_bases(db, basis_year, month, "actual", plan_no)
    basis = next((b for b in resolved.bases if b.key == base_version), None)
    if basis is None:
        raise HTTPException(400, "base_version은 prev | prior | plan만 쓸 수 있다.")

    labs = active_labs(db, basis_year, month)
    by_code = lab_by_code(labs)
    stored = first_rows(db, basis_year, month)
    ix = (month - 1,)
    local_curs = {lab.currency for lab in by_code.values() if lab.currency and lab.currency != "USD"}

    cur_fx = (basis_year, month, "actual")
    base_is_fx = basis.kind in ("forecast", "actual")
    base_fx = (basis.year, basis.month, basis.kind) if base_is_fx else cur_fx
    rate_cur = rate_arrays(db, *cur_fx, local_curs, ix)
    rate_base = rate_arrays(db, *base_fx, local_curs, ix) if base_is_fx else rate_cur

    base_d = load_basis(db, (basis.year, basis.month, basis.kind, basis.plan_no), set(labs))
    tot_leaves = resolve_leaves("tot")
    j = month - 1

    rows: list[ActualFirstRow] = []
    agg = {"usd": 0.0, "krw": 0.0, "base": 0.0, "fx_local": 0.0, "fx_usd": 0.0, "fx_outside": 0.0}
    for code, lab in by_code.items():
        row = stored.get(code)
        currency = lab.currency or "USD"
        lc = currency if currency != "USD" else None
        lrc = rate_cur.get(lc, [0.0] * 12)[j] if lc else 0.0
        lrb = rate_base.get(lc, [0.0] * 12)[j] if lc else 0.0
        rc, rb = rate_cur["USD"][j], rate_base["USD"][j]

        local = float(row.local_amount) if row else 0.0
        usd_from_source = bool(row and row.usd_amount is not None)
        # 저장된 금액은 현지통화 "그 통화의 1단위"(ILS 1 등)인데, 비용 데이터는 전부 K$라서 환산
        # 결과도 K$로 맞춘다 — 여기서 1000으로 안 나누면 1차 금액만 1000배로 보인다.
        k_cur = to_kdollar(local, lrc) if lc else local / 1000
        k_base = to_kdollar(local, lrb) if lc else k_cur
        if usd_from_source:
            # ERP가 $ 환산액을 같이 줬으면 그 값을 이긴다(환율로 다시 계산하면 ERP 보고서와 어긋난다).
            # 그때는 현지→$ 단계를 ERP가 이미 밟은 것이므로 환차 현지→$를 가를 근거가 없다.
            k_cur = k_base = float(row.usd_amount)
        krw = k_cur * rc * 1000

        # 비교 기준은 그 연구소의 모든 파트를 합친 개발비(매출) 총액이다(1차는 파트 구분이 없다).
        base_k = 0.0
        for pk, view in labs.items():
            if view.lab_code != code:
                continue
            cats = base_d.get(pk, {})
            base_k += sum(n(month_value(cats, leaf, j)) for leaf in tot_leaves)
        base_krw = base_k * rb * 1000

        # 세 칸의 합이 반드시 증감(krw - base_krw)과 같아야 한다 — 환차를 가른 뒤 남는 몫이 환차外다.
        #   현지→$ = (당월 현지환율로 본 $ − 비교 현지환율로 본 $) × 당월 원/$
        #   $→원   = 비교 현지환율로 본 $ × (당월 원/$ − 비교 원/$)
        #   환차外  = 비교 환율로 환산한 금액 − 비교 기준 금액
        fx_local = (k_cur - k_base) * rc * 1000
        fx_usd = k_base * (rc - rb) * 1000
        fx_outside = k_base * rb * 1000 - base_krw

        rows.append(
            ActualFirstRow(
                lab_code=code, lab_name=lab.name, currency=currency, local_amount=local, usd_amount=k_cur,
                krw_amount=krw, base_krw=base_krw, diff=krw - base_krw, diff_pct=pct_of(krw, base_krw),
                fx=fx_local + fx_usd, fx_local=fx_local, fx_usd=fx_usd, fx_outside=fx_outside,
                reason_text=row.reason_text if row else "", usd_from_source=usd_from_source,
            )
        )
        agg["usd"] += k_cur
        agg["krw"] += krw
        agg["base"] += base_krw
        agg["fx_local"] += fx_local
        agg["fx_usd"] += fx_usd
        agg["fx_outside"] += fx_outside

    # 합계의 현지통화 칸은 비워 둔다(0으로 둔다) — 통화가 서로 다른 금액을 더한 수는 뜻이 없다.
    total = ActualFirstRow(
        lab_code="TOTAL", lab_name="합계", currency="", local_amount=0.0, usd_amount=agg["usd"],
        krw_amount=agg["krw"], base_krw=agg["base"], diff=agg["krw"] - agg["base"],
        diff_pct=pct_of(agg["krw"], agg["base"]), fx=agg["fx_local"] + agg["fx_usd"],
        fx_local=agg["fx_local"], fx_usd=agg["fx_usd"], fx_outside=agg["fx_outside"],
    )
    return ActualFirstResult(
        basis_year=basis_year,
        basis_month=month,
        base_version=base_version,
        base_label=basis.resolved_label,
        stage=stage_out(db, basis_year, month),
        rows=rows,
        total=total,
        fallback_note=(
            basis.fallback_note
            or (
                ""
                if basis.has_data
                else f"{basis.resolved_label}에 저장된 데이터가 없어 비교 기준 금액이 0이다 — 증감을 0으로 읽지 마라."
            )
        ),
        note=(
            "1차(ERP 약식)는 연구소 총액만 있다 — 계정 상세·외주용역은 2차(PL)에서 본다. 현지통화 "
            "총액을 현지/$ → 원/$ 순서로 환산했고, 그 두 단계가 환차의 현지→$ · $→원 칸이다. "
            "합계의 현지통화 칸은 통화가 섞여 뜻이 없어 비워 두었다."
        ),
    )


@router.put("/overseas/actual-first", response_model=ActualFirstRow)
def upsert_actual_first(body: ActualFirstIn, db: Session = Depends(get_db)) -> ActualFirstRow:
    """1차 값·사유 저장. [1차 고정] 상태면 409로 거부한다 — 보고까지 끝난 값이라 해제 전에는 못 고친다."""
    if not 1 <= body.basis_month <= 12:
        raise HTTPException(400, "basis_month는 1~12여야 한다.")
    require_first_unlocked(db, body.basis_year, body.basis_month, "1차 값")
    labs = active_labs(db, body.basis_year, body.basis_month)
    if body.lab_code not in {lab.lab_code for lab in labs.values()}:
        raise HTTPException(400, f"알 수 없는 연구소 코드: {body.lab_code}")

    row = db.scalar(
        select(OverseasActualFirstRow).where(
            OverseasActualFirstRow.basis_year == body.basis_year,
            OverseasActualFirstRow.basis_month == body.basis_month,
            OverseasActualFirstRow.lab_code == body.lab_code,
        )
    )
    if row is None:
        row = OverseasActualFirstRow(
            basis_year=body.basis_year, basis_month=body.basis_month, lab_code=body.lab_code
        )
        db.add(row)
    if body.local_amount is not None:
        row.local_amount = body.local_amount
    if body.usd_amount is not None:
        row.usd_amount = body.usd_amount
    if body.reason_text is not None:
        row.reason_text = body.reason_text
    if body.source_file:
        row.source_file = body.source_file
    db.commit()

    result = get_actual_first(body.basis_year, body.basis_month, "prev", None, db)
    return next((r for r in result.rows if r.lab_code == body.lab_code), result.total)


@router.get("/overseas/actual-stage-shift", response_model=ActualStageShiftResult)
def get_actual_stage_shift(
    basis_year: int, basis_month: int, db: Session = Depends(get_db)
) -> ActualStageShiftResult:
    """2차 화면 맨 위의 "1차 → 2차 변동".

    1차는 ERP 약식이고 2차는 PL이라 금액이 달라지는 게 정상이다. 그 차이를 2차 화면에서 먼저
    보여주지 않으면, 1차로 이미 보고한 숫자와 2차 숫자가 왜 다른지 설명할 수 없다. 1차 사유도 같이
    붙여 준다 — 1차에 "채용 지연"이라고 썼는데 2차에서 금액이 또 움직였으면 그걸 같이 봐야 한다."""
    if not 1 <= basis_month <= 12:
        raise HTTPException(400, "basis_month는 1~12여야 한다.")
    labs = active_labs(db, basis_year, basis_month)
    by_code = lab_by_code(labs)
    stored = first_rows(db, basis_year, basis_month)
    j = basis_month - 1
    ix = (j,)
    local_curs = {lab.currency for lab in by_code.values() if lab.currency and lab.currency != "USD"}
    rate = rate_arrays(db, basis_year, basis_month, "actual", local_curs, ix)
    second_d = load_cost_version(db, "actual", yyyymm(basis_year, basis_month), set(labs))
    tot_leaves = resolve_leaves("tot")

    rows: list[ActualStageShift] = []
    tot_first = tot_second = 0.0
    for code, lab in by_code.items():
        first = stored.get(code)
        currency = lab.currency or "USD"
        lc = currency if currency != "USD" else None
        lr = rate.get(lc, [0.0] * 12)[j] if lc else 0.0
        local = float(first.local_amount) if first else 0.0
        if first and first.usd_amount is not None:
            usd = float(first.usd_amount)
        else:
            usd = to_kdollar(local, lr) if lc else local / 1000
        first_krw = usd * rate["USD"][j] * 1000

        second_k = 0.0
        for pk, view in labs.items():
            if view.lab_code != code:
                continue
            cats = second_d.get(pk, {})
            second_k += sum(n(month_value(cats, leaf, j)) for leaf in tot_leaves)
        second_krw = second_k * rate["USD"][j] * 1000

        rows.append(
            ActualStageShift(
                lab_code=code, lab_name=lab.name, first_krw=first_krw, second_krw=second_krw,
                diff=second_krw - first_krw, diff_pct=pct_of(second_krw, first_krw),
                first_reason=first.reason_text if first else "",
            )
        )
        tot_first += first_krw
        tot_second += second_krw

    total = ActualStageShift(
        lab_code="TOTAL", lab_name="합계", first_krw=tot_first, second_krw=tot_second,
        diff=tot_second - tot_first, diff_pct=pct_of(tot_second, tot_first),
    )
    return ActualStageShiftResult(
        basis_year=basis_year,
        basis_month=basis_month,
        rows=rows,
        total=total,
        has_first=bool(stored),
        note=(
            "1차(ERP 약식) → 2차(PL) 변동이다. 두 값은 출처가 달라 차이가 나는 게 정상이고, 이 줄은 "
            "'이미 보고한 1차와 지금 2차가 얼마나 다른지'를 보여준다. 환산은 양쪽 모두 그 달 실적 환율을 "
            "썼다 — 환율 차이가 아니라 금액 차이만 남기려는 것이다."
        )
        if stored
        else "1차 데이터가 없어 1차 → 2차 변동을 낼 수 없다(실적결산 1차 미완료).",
    )
