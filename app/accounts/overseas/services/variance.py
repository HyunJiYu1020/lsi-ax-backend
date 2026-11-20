"""해외연구소 증감분석 도메인 로직 — 비교 기준 해석 · 증감 표 · 증감사유 · 증감 동인 · 실적 1차/2차.

왜 여기로 옮겼나 (PLAN.md P1-1, 5.1): 집계 엔드포인트가 증감분석의 라우트 핸들러
check_variance_reasons를 함수로 직접 호출하고, 결산 엔드포인트는 _active_labs · _load_cost_version
같은 밑줄 이름을 꺼내 썼다. 증감 계산은 증감분석 화면·집계 사전점검·결산이 모두 같은 숫자를 봐야
하는 공용 도메인이다 — 판정을 두 군데서 따로 하면 같은 상황에서 다른 건수를 보여준다.

가른 기준은 단순하다: **@router가 붙은 라우트 핸들러만 엔드포인트에 남기고 나머지 전부 여기로.**
의존을 하나씩 쫓아 옮기면 끝이 안 나서(실제로 그렇게 하다 꼬였다) 원칙으로 한 번에 갈랐다.

이 화면이 지켜야 하는 규칙:
- 비교 기준은 이름만으로 뜻이 통하지 않는다. "전실행"은 1월 속보에서는 전년 12월 실적이고, 실적
  버전에서는 같은 달 속보다. 그래서 라벨을 서버가 만들어 내려준다(resolve_bases).
- 환차는 USD 외 통화 연구소 전부를 현지→$ / $→원으로 가른다. USD 연구소는 현지→$ 단계가 없어
  fx_local이 0이다 — 통화가 섞인 전체 합계에서도 두 칸의 합이 환차 계와 맞는다.
- 증감사유는 "회신 원문 → 번역 + AI 초안 → 담당자 수정 → [저장]"이고, [저장] 시점 증감액을 같이
  박아 둔다. 재업로드로 금액이 바뀌면 사유를 지우지 않고 "재확인 필요"만 켠다.
- 실적은 1차(ERP 약식, 연구소 총액만)와 2차(PL, 속보와 같은 구성)가 저장소부터 다르다.
"""

from datetime import datetime, timezone

from fastapi import HTTPException
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.accounts.overseas.cost_groups import (
    AGGREGATE_ROWS,
    CATEGORY_LABELS,
    CATEGORY_LABELS_EN,
    VARIANCE_ROWS,
    resolve_leaves,
)
from app.shared.core.periods import PERIOD_IX, month_value, yyyymm
from app.shared.core.values import n, source_hash
from app.accounts.overseas.services.entries import (
    current_basis_month,
    entry_months_bulk,
    pick_plan_no,
    plan_entry_months_bulk,
)
from app.accounts.overseas.services.fxcalc import rate_arrays
from app.accounts.overseas.services.labor_split import (
    prepare as labor_split_prepare,
    split_for as labor_split_for,
)
from app.accounts.overseas.services.labs import ACCOUNT_KEY, LabView, all_labs, lab_views
# 버전·비교 기준 해석은 services/versions.py에 있다 — 집계표·인건비율·인건비 분해가 같은 해석을
# 쓰는데, 여기 두면 그쪽이 증감분석을 import해야 하고 labor_split과 순환이 된다.
from app.accounts.overseas.services.versions import (
    BASE_LABELS,
    KIND_LABELS,
    active_labs,
    check_kind,
    default_plan_no,
    fx_specs,
    has_cost_data,
    latest_cost_yyyymm,
    load_basis,
    load_cost_version,
    load_plan_version,
    plan_revisions,
    plan_creation_prev,
    prev_spec,
    prior_spec,
    resolve_bases,
    scope_pks,
    ym_label,
)
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
    LaborSplitTotals,
    VarianceTable,
    VarianceRow,
)

# 쿼리 파라미터로 받고 기본값만 여기 둔다(화면도 이 값을 받아서 표의 경고 표시 기준으로 쓴다).
DEFAULT_REASON_THRESHOLD_PCT = 10.0
# 합계·집계 행은 하위 계정의 합이라 사유를 따로 쓰지 않는다 — 경고 목록에서 뺀다
# (개발비(매출)에 사유를 쓰라고 하면 담당자는 같은 말을 두 번 쓰게 된다).
GROUP_KEYS = {key for key, _, _ in VARIANCE_ROWS if len(resolve_leaves(key)) > 1}


def check_period(period: str) -> tuple[int, ...]:
    ix = PERIOD_IX.get(period)
    if ix is None:
        raise HTTPException(400, "period는 y(연간) | q1~q4 | m1~m12만 쓸 수 있다.")
    return ix


def check_stage(stage: str, kind: str) -> str:
    """실적만 1차/2차로 갈린다. 속보에 stage를 넣으면 같은 화면 값이 두 군데에 나뉘어 저장되므로 막는다."""
    stage = stage or ""
    if stage not in ("", "first", "second"):
        raise HTTPException(400, "stage는 first(1차) | second(2차) 또는 빈 값(속보)만 쓸 수 있다.")
    if stage and kind != "actual":
        raise HTTPException(400, "1차/2차 구분은 실적 버전에만 있다 — 속보는 stage를 비워라.")
    return stage


# ---------- 증감 표 ----------

def variance_cells(
    db: Session,
    labs: dict[str, LabView],
    pks: list[str],
    cur_d: dict,
    base_d: dict,
    ix: tuple[int, ...],
    cur_fx: tuple[int, int, str],
    base_fx: tuple[int, int, str],
    split_fx: bool,
) -> dict[str, dict[str, float]]:
    """계정 키 → {cur, base, fx_local, fx_usd, fx_outside} (원). 화면 core.js의 ovAnaEl과 같은 식.

    환차 분해(현지통화 연구소):
      현지금액 L = K$ × (현지/$)
      ① 현지→$ 환차 = (L당월 ÷ 현지환율당월 − L당월 ÷ 현지환율비교) × 원/$당월
      ② $→원 환차  = (L당월 ÷ 현지환율비교) × (원/$당월 − 원/$비교)
      환차外       = (L당월 − L비교) ÷ 현지환율비교 × 원/$비교
    USD 연구소는 ①이 없어 fx_local = 0이고, 환차는 K$당월 × (원/$당월 − 원/$비교) 하나뿐이다.
    통화가 섞여 있어도 두 칸을 각각 더하면 합이 환차 계와 맞는다 — 그래서 전체 연구소로 봐도 된다.
    """
    local_curs = {
        labs[pk].currency for pk in pks if labs[pk].currency and labs[pk].currency != "USD"
    } if split_fx else set()
    rate_cur = rate_arrays(db, *cur_fx, local_curs, ix)
    rate_base = rate_arrays(db, *base_fx, local_curs, ix) if split_fx else rate_cur

    # 화면이 그리는 행 전부를 계산한다 — 참고 행(IT 관련 비용 · 외주용역비 7개)까지.
    # VARIANCE_ROWS(38행)만 돌면 그 7개가 빠지고, 증감분석 화면은 집계표와 같은 45행을 그리므로
    # 화면이 서버 값을 쓸 때 그 줄이 빈칸으로 남는다. 두 표가 같은 행 목록을 쓰는 게 맞다.
    leaves = {key: resolve_leaves(key) for key, _, _, _ in AGGREGATE_ROWS if key != "pct"}
    out = {key: {"cur": 0.0, "base": 0.0, "fx_local": 0.0, "fx_usd": 0.0, "fx_outside": 0.0} for key in leaves}

    for pk in pks:
        lab = labs[pk]
        lc = lab.currency if (split_fx and lab.currency and lab.currency != "USD") else None
        cats_cur, cats_base = cur_d.get(pk, {}), base_d.get(pk, {})
        for key, keys in leaves.items():
            cell = out[key]
            for j in ix:
                uc = sum(n(month_value(cats_cur, leaf, j)) for leaf in keys)
                ub = sum(n(month_value(cats_base, leaf, j)) for leaf in keys)
                if key == "hc":
                    # 인력은 금액이 아니라 명(月 평균)이다 — 환율을 곱하지 않고 기간 평균을 낸다.
                    cell["cur"] += uc / len(ix)
                    cell["base"] += ub / len(ix)
                    continue
                rc, rb = rate_cur["USD"][j], rate_base["USD"][j]
                cell["cur"] += uc * rc * 1000
                cell["base"] += ub * rb * 1000
                lrc = rate_cur.get(lc, [0.0] * 12)[j] if lc else 0.0
                lrb = rate_base.get(lc, [0.0] * 12)[j] if lc else 0.0
                if lc and lrc > 0 and lrb > 0:
                    local_cur = uc * lrc
                    local_base = ub * lrb
                    cell["fx_local"] += (local_cur / lrc - local_cur / lrb) * rc * 1000
                    cell["fx_usd"] += (local_cur / lrb) * (rc - rb) * 1000
                    cell["fx_outside"] += ((local_cur - local_base) / lrb) * rb * 1000
                else:
                    cell["fx_usd"] += uc * (rc - rb) * 1000
                    cell["fx_outside"] += (uc - ub) * rb * 1000
    return out


def to_kdollar(local_amount: float, local_rate: float) -> float:
    """현지통화 금액 → K$. 현지 환율(현지통화/$)이 없으면 0으로 둔다 — 환율을 모르는 채 1:1로
    넘기면 ILS 금액이 $ 금액으로 들어가 1,000배 틀린 숫자가 화면에 뜬다.

    1000으로 나누는 이유: 비용 데이터는 전부 K$ 단위라 1차 환산 결과도 거기에 맞춰야 2차와 더하거나
    비교할 수 있다. local_amount는 현지통화의 1단위(ILS 1, INR 1)로 저장한다."""
    return (local_amount / local_rate / 1000) if local_rate > 0 else 0.0


def pct_of(cur: float, base: float) -> float | None:
    """증감률. 기준이 0이면 비율을 낼 수 없다 — 0%가 아니라 null이다(무한대를 0으로 적으면 '변동
    없음'으로 읽힌다)."""
    return ((cur - base) / base) * 100 if base else None


def variance_rows(cells: dict[str, dict[str, float]]) -> list[VarianceRow]:
    """계정 행 목록. 집계표와 같은 45행을 쓴다(AGGREGATE_ROWS) — 두 화면이 같은 계정 트리를
    그리므로 행 목록이 갈리면 한쪽에만 있는 줄이 생긴다. memo(참고 행)는 개발비 합계에 들어가지
    않는다는 표시이고, 증감분석에서도 같은 뜻이다."""
    rows: list[VarianceRow] = []
    for key, level, unit, memo in AGGREGATE_ROWS:
        if unit == "pct":
            # (%) 행은 금액이 아니라 MARK UP 비율이다. 두 버전의 비율을 각각 내고 그 차이를 %p로
            # 보여주므로 환차를 가를 대상이 아니다.
            #
            # 분모는 **개발비(매출)=tot**이다(개발비 A가 아니다). 근거는 화면이 사용자에게 적어
            # 주는 문장이다 — "합계 화면은 MARK UP ÷ 개발비(매출) 계산값"(core.js 집계표 안내).
            # 집계표(aggregate.pl_rows)도 tot을 쓴다. 2026-11-16까지 여기만 A를 써서, MARK UP이
            # 0이 아닌 데이터에서는 챗봇이 화면과 다른 %를 말하게 되어 있었다(개발 데이터는 B가
            # 0이라 두 값이 같아서 드러나지 않았다).
            d, b = cells["tot"], cells["B"]
            cur = (b["cur"] / d["cur"] * 100) if d["cur"] else 0.0
            base = (b["base"] / d["base"] * 100) if d["base"] else 0.0
            rows.append(
                VarianceRow(
                    category_key=key, label=CATEGORY_LABELS[key], label_en=CATEGORY_LABELS_EN[key], level=level, unit="pct",
                    memo=memo, base=base, cur=cur, diff=cur - base,
                )
            )
            continue
        c = cells[key]
        fx_local, fx_usd = c["fx_local"], c["fx_usd"]
        rows.append(
            VarianceRow(
                category_key=key,
                label=CATEGORY_LABELS.get(key, key),
                label_en=CATEGORY_LABELS_EN.get(key, ""),
                memo=memo,
                level=level,
                unit=unit,
                base=c["base"],
                cur=c["cur"],
                diff=c["cur"] - c["base"],
                diff_pct=pct_of(c["cur"], c["base"]),
                fx=fx_local + fx_usd,
                fx_local=fx_local,
                fx_usd=fx_usd,
                fx_outside=c["fx_outside"],
            )
        )
    return rows


@dataclass(frozen=True)
class _VarianceSetup:
    """기간과 무관한 준비물. 17기간 표(compute_variance_table)는 이걸 한 번만 만들고 기간만
    바꿔 가며 계산한다 — 기간마다 다시 만들면 같은 DB 읽기를 17번 한다."""

    labs: dict
    pks: list[str]
    cur_d: dict
    base_d: dict
    cur_fx: tuple
    base_fx: tuple
    split_fx: bool
    basis: object
    plan_no: str
    note: str


def _variance_setup(
    db: Session, basis_year: int, basis_month: int, kind: str, base_version: str,
    lab_scope: str, plan_no: str | None,
) -> _VarianceSetup:
    if base_version not in BASE_LABELS:
        raise HTTPException(400, "base_version은 prev(전실행) | prior(전년) | plan(경영계획)만 쓸 수 있다.")
    resolved = resolve_bases(db, basis_year, basis_month, kind, plan_no)
    basis = next(b for b in resolved.bases if b.key == base_version)
    if not basis.has_data:
        raise HTTPException(404, basis.fallback_note or f"{basis.label} 데이터가 없어 증감을 낼 수 없다.")

    labs = active_labs(db, basis_year, basis_month)
    pks = scope_pks(labs, lab_scope)
    lab_pks = set(labs)

    cur_spec = (
        (basis_year, basis_month, "plan", resolved.default_plan_no)
        if kind == "plan"
        else (basis_year, basis_month, kind, "")
    )
    base_spec = (basis.year, basis.month, basis.kind, basis.plan_no)
    cur_d = load_basis(db, cur_spec, lab_pks)
    base_d = load_basis(db, base_spec, lab_pks)
    if not cur_d:
        raise HTTPException(404, f"{resolved.basis_label} 데이터가 없다.")

    # 경영계획이 한쪽에라도 끼면 환차를 가를 수 없다(환율 기준이 없다) — 그때는 환차 칸을 0으로
    # 두고 증감 전부를 환차外로 본다. 0을 "환차가 없었다"로 읽지 않도록 note에 적어 내려보낸다.
    split_fx = "plan" not in (cur_spec[2], base_spec[2])
    cur_fx, base_fx = fx_specs(cur_spec, base_spec)
    note = (
        "환차 = 환율이 움직여서 변한 몫, 환차外 = 금액 자체의 증감이다. USD가 아닌 통화를 쓰는 "
        "연구소는 환차를 현지→$(fx_local)와 $→원(fx_usd)으로 가른다 — USD 연구소는 fx_local이 0이다. "
        "금액 단위는 원이다."
    )
    if not split_fx:
        note += " 경영계획은 속보/실적 환율 기준이 없어 환차를 가를 수 없다 — 환차 칸은 0이고 증감 전부가 환차外로 들어가 있다."
    return _VarianceSetup(
        labs, pks, cur_d, base_d, cur_fx, base_fx, split_fx, basis, basis.plan_no, note
    )


def compute_variance(
    db: Session,
    basis_year: int,
    basis_month: int,
    kind: str,
    base_version: str,
    period: str,
    lab_scope: str,
    plan_no: str | None,
    stage: str = "",
) -> VarianceResult:
    ix = check_period(period)
    st = _variance_setup(db, basis_year, basis_month, kind, base_version, lab_scope, plan_no)
    cells = variance_cells(
        db, st.labs, st.pks, st.cur_d, st.base_d, ix, st.cur_fx, st.base_fx, st.split_fx
    )
    return VarianceResult(
        basis_year=basis_year,
        basis_month=basis_month,
        kind=kind,
        stage=stage,
        base_version=base_version,
        base_label=st.basis.resolved_label,
        period=period,
        lab_scope=(lab_scope or "ALL"),
        plan_no=st.plan_no,
        rows=variance_rows(cells),
        fallback_note=st.basis.fallback_note,
        note=st.note,
    )



# ---------- 증감사유 ----------
def reason_filters(key, model=VarianceReason):
    # variance_reasons·variance_drivers는 전 계정 공용 테이블이 됐다(PLAN.md Phase 3) —
    # 계정을 안 걸면 다른 계정의 증감사유가 섞인다. 이 함수가 모든 조회의 단일 관문이다.
    return (
        model.account_key == ACCOUNT_KEY,
        model.basis_year == key.basis_year,
        model.basis_month == key.basis_month,
        model.kind == key.kind,
        model.stage == key.stage,
        model.base_version == key.base_version,
        model.period == key.period,
        model.lab_scope == key.lab_scope,
    )


def reason_out(row: VarianceReason, current_diff: float | None = None) -> VarianceReasonOut:
    return VarianceReasonOut(
        basis_year=row.basis_year,
        basis_month=row.basis_month,
        kind=row.kind,
        stage=row.stage,
        base_version=row.base_version,
        period=row.period,
        lab_scope=row.lab_scope,
        category_key=row.category_key,
        source_text=row.source_text,
        source_ko=row.source_ko,
        ai_draft=row.ai_draft,
        reason_text=row.reason_text,
        saved_diff=float(row.saved_diff) if row.saved_diff is not None else None,
        needs_recheck=row.needs_recheck,
        saved_at=row.saved_at.isoformat() if row.saved_at else "",
        current_diff=current_diff,
    )


# 저장 시점 증감액과 지금 증감액이 이 금액 이상 벌어지면 "재확인 필요"로 본다. 환율 소수점 때문에
# 몇 원씩 흔들리는 걸 변동으로 보면 모든 사유에 재확인 딱지가 붙어 경고가 무의미해진다.
RECHECK_TOLERANCE_KRW = 1_000_000.0


def diff_by_category(
    db: Session, key, plan_no: str | None
) -> dict[str, tuple[float, float | None]]:
    """그 화면 조합의 계정별 (증감액, 증감률). 사유의 재확인 판정과 확정 경고에 쓴다.

    데이터가 없어 증감을 낼 수 없는 조합(비교 기준 미존재 등)에서는 빈 dict를 돌려준다 — 사유 조회
    자체는 막지 않는다(담당자가 써 둔 글은 비교 기준과 무관하게 보여야 한다)."""
    try:
        result = compute_variance(
            db, key.basis_year, key.basis_month, key.kind, key.base_version, key.period,
            key.lab_scope, plan_no, key.stage,
        )
    except HTTPException:
        return {}
    return {r.category_key: (r.diff, r.diff_pct) for r in result.rows}


def apply_recheck(row: VarianceReason, current_diff: float | None) -> None:
    """저장된 사유는 그대로 두고 재확인 플래그만 갱신한다.

    왜 사유를 지우지 않는가: 담당자가 쓴 문장은 서버가 버릴 값이 아니다. 금액이 바뀌었다고 사유를
    비우면 매달 재업로드마다 사유를 처음부터 다시 써야 한다. 바뀐 사실만 알려주고, 그대로 쓸지
    고칠지는 사람이 정한다(공통 RULE: 재업로드로 금액이 바뀌면 사유 유지 + "재확인 필요")."""
    if not row.reason_text or row.saved_diff is None or current_diff is None:
        return
    row.needs_recheck = abs(float(row.saved_diff) - current_diff) >= RECHECK_TOLERANCE_KRW


def cache_translation(db: Session, source: str, ko: str, engine: str) -> None:
    """번역 1건을 /overseas/reason-translate와 같은 자리에 캐시한다 — 두 경로가 따로 캐시하면 같은
    원문이 두 번역을 갖게 되어 "원문 1건 = 번역 1개"가 깨진다. ko_text가 String(600)이라 길면 자른다."""
    text = ko[:600]
    stmt = pg_insert(TextTranslation).values(
        source_hash=source_hash(source), source_text=source, ko_text=text, engine=engine
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[TextTranslation.source_hash],
            set_={"ko_text": text, "engine": engine, "updated_at": func.now()},
        )
    )
    db.commit()


# ---------- 증감 동인 표 ----------
DRIVER_OPS = {"ref", "div_elementwise", "sub", "sum"}


def check_factors(factors: list[VarianceDriverFactor]) -> list[dict]:
    if not factors:
        raise HTTPException(400, "factors(동인 행)가 비어 있다.")
    out: list[dict] = []
    for f in factors:
        if f.op not in DRIVER_OPS:
            raise HTTPException(400, f"알 수 없는 op: {f.op!r} ({' | '.join(sorted(DRIVER_OPS))}만 가능)")
        if not f.operands:
            raise HTTPException(400, f"{f.label!r}의 operands(계산 재료)가 비어 있다.")
        if f.op == "div_elementwise" and len(f.operands) != 2:
            raise HTTPException(400, f"{f.label!r}: div_elementwise는 operands가 정확히 2개여야 한다(분자, 분모).")
        out.append(f.model_dump())
    return out


def driver_value(cells: dict[str, dict[str, float]], factor: dict, side: str) -> float | None:
    """동인 한 줄의 값. 재료가 없으면 None(빈칸)이다 — 0으로 적으면 "0원"으로 읽힌다."""
    op, operands = factor.get("op", "ref"), factor.get("operands", [])
    vals = [cells[k][side] if k in cells else None for k in operands]
    if any(v is None for v in vals):
        return None
    if op == "ref":
        return vals[0]
    if op == "sum":
        return sum(vals)
    if op == "sub":
        return vals[0] - sum(vals[1:])
    return (vals[0] / vals[1]) if vals[1] else None


def driver_rows(cells: dict[str, dict[str, float]], factors: list[dict]) -> list[VarianceDriverRow]:
    rows: list[VarianceDriverRow] = []
    for f in factors:
        base = driver_value(cells, f, "base")
        cur = driver_value(cells, f, "cur")
        names = " ÷ ".join(CATEGORY_LABELS.get(k, k) for k in f.get("operands", []))
        rows.append(
            VarianceDriverRow(
                label=f.get("label", ""),
                unit=f.get("unit", "krw"),
                base=base,
                cur=cur,
                diff=(cur - base) if (base is not None and cur is not None) else None,
                diff_pct=pct_of(cur, base) if (base is not None and cur is not None) else None,
                basis_text=(
                    f"{f.get('label', '')} = {names}"
                    + (f" = {cur:,.0f}" if cur is not None else " = (재료 없음)")
                ),
            )
        )
    return rows


def driver_out(row: VarianceDriver, basis_year: int, basis_month: int, kind: str) -> VarianceDriverOut:
    created_here = (row.basis_year, row.basis_month, row.kind) == (basis_year, basis_month, kind)
    return VarianceDriverOut(
        basis_year=row.basis_year,
        basis_month=row.basis_month,
        kind=row.kind,
        lab_scope=row.lab_scope,
        driver_key=row.driver_key,
        title=row.title,
        target_key=row.target_key,
        factors=[VarianceDriverFactor(**f) for f in (row.factors or [])],
        requested_text=row.requested_text,
        carry_over=row.carry_over,
        created_here=created_here,
        created_basis=ym_label(row.basis_year, row.basis_month, row.kind),
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


def visible_drivers(
    db: Session, basis_year: int, basis_month: int, kind: str, lab_scope: str
) -> list[VarianceDriver]:
    """이 버전에서 보이는 동인 표 = 이 버전에 붙인 것 + 이전 버전에서 carry_over로 켜 둔 것.

    같은 driver_key가 양쪽에 다 있으면 이 버전에 붙인 것을 쓴다 — 담당자가 이번에 고쳐 둔 표가
    지난달 설정에 덮이면 안 된다."""
    cur_ym = basis_year * 100 + basis_month
    rows = list(
        db.scalars(
            select(VarianceDriver)
            .where(VarianceDriver.account_key == ACCOUNT_KEY, VarianceDriver.lab_scope == lab_scope)
            .order_by(VarianceDriver.basis_year, VarianceDriver.basis_month)
        )
    )
    by_key: dict[str, VarianceDriver] = {}
    for r in rows:
        r_ym = r.basis_year * 100 + r.basis_month
        here = (r.basis_year, r.basis_month, r.kind) == (basis_year, basis_month, kind)
        if here:
            by_key[r.driver_key] = r
        elif r.carry_over and r_ym < cur_ym and r.driver_key not in by_key:
            by_key[r.driver_key] = r
    return list(by_key.values())


# ---------- 실적 1차/2차 ----------
def stage_row(db: Session, basis_year: int, basis_month: int) -> CloseAccountStage | None:
    """그 연월 해외연구소의 결산 단계 행. "1차 완료"는 mapping_done 하나가 진실이다 —
    예전에는 overseas_actual_stages.close_first_done이 같은 사실을 따로 들고 있었다(PLAN.md P2-6)."""
    return db.scalar(
        select(CloseAccountStage).where(
            CloseAccountStage.account_key == ACCOUNT_KEY,
            CloseAccountStage.basis_year == basis_year,
            CloseAccountStage.basis_month == basis_month,
        )
    )


def require_first_unlocked(db: Session, basis_year: int, basis_month: int, what: str) -> None:
    row = stage_row(db, basis_year, basis_month)
    if row is not None and row.first_locked:
        raise HTTPException(
            409,
            f"{basis_year}년 {basis_month}월 1차가 고정돼 있어 {what}를 고칠 수 없다. "
            "[1차 고정]을 해제한 뒤 다시 시도하라.",
        )


def stage_out(db: Session, basis_year: int, basis_month: int) -> ActualStageOut:
    row = stage_row(db, basis_year, basis_month)
    labs = active_labs(db, basis_year, basis_month)
    lab_codes = {lab.lab_code for lab in labs.values()}
    filled = db.scalar(
        select(func.count())
        .select_from(OverseasActualFirstRow)
        .where(
            OverseasActualFirstRow.basis_year == basis_year,
            OverseasActualFirstRow.basis_month == basis_month,
            OverseasActualFirstRow.lab_code.in_(lab_codes or {""}),
        )
    ) or 0
    close_done = bool(row and row.mapping_done)
    locked = bool(row and row.first_locked)
    note = (
        "실적결산 1차가 아직 끝나지 않아 1차 분석을 열 수 없다(가져올 ERP 약식 값이 없다)."
        if not close_done
        else "1차 값·사유가 고정돼 있다 — 고치려면 [1차 고정]을 해제해야 한다."
        if locked
        else f"1차 분석 입력: 연구소 {len(lab_codes)}개 중 {filled}개."
    )
    return ActualStageOut(
        basis_year=basis_year,
        basis_month=basis_month,
        close_first_done=close_done,
        first_locked=locked,
        first_locked_at=row.first_locked_at.isoformat() if row and row.first_locked_at else "",
        first_filled=filled,
        lab_count=len(lab_codes),
        note=note,
    )


def first_rows(db: Session, basis_year: int, basis_month: int) -> dict[str, OverseasActualFirstRow]:
    return {
        r.lab_code: r
        for r in db.scalars(
            select(OverseasActualFirstRow).where(
                OverseasActualFirstRow.basis_year == basis_year,
                OverseasActualFirstRow.basis_month == basis_month,
            )
        )
    }


def lab_by_code(labs: dict[str, LabView]) -> dict[str, LabView]:
    """연구소 코드 → 대표 기준정보(파트 중 첫 번째). 1차는 파트 구분 없이 연구소 총액만 올라온다."""
    out: dict[str, LabView] = {}
    for lab in sorted(labs.values(), key=lambda x: (x.sort_order, x.pk)):
        out.setdefault(lab.lab_code, lab)
    return out


def compute_reason_check(
    db: Session,
    basis_year: int,
    base_version: str = "prev",
    period: str = "y",
    basis_month: int | None = None,
    kind: str = "forecast",
    stage: str = "",
    lab_scope: str = "ALL",
    plan_no: str | None = None,
    threshold_pct: float = DEFAULT_REASON_THRESHOLD_PCT,
) -> VarianceReasonCheck:
    """확정 직전 경고 — 증감률이 기준치를 넘었는데 사유가 비었거나 재확인이 필요한 칸.

    서버가 확정을 막지는 않는다(공통 RULE: 미작성·미업로드가 있어도 진행은 막지 않고 무엇을 알고도
    확정하는지 밝힌다). 화면이 이 목록을 보여주고 담당자가 결정한다.

    집계 행(개발비(매출)·개발비(A) 등)은 목록에서 뺀다 — 하위 계정의 합이라 같은 사유를 두 번 쓰게
    된다. 인력(명)·(%) 행도 금액 증감이 아니라서 뺀다."""
    check_kind(kind)
    month = basis_month or current_basis_month(db) or 1
    stage = check_stage(stage, kind)
    key = VarianceReasonIn(
        basis_year=basis_year, basis_month=month, kind=kind, stage=stage, base_version=base_version,
        period=period, lab_scope=lab_scope, category_key="",
    )
    result = compute_variance(db, basis_year, month, kind, base_version, period, lab_scope, plan_no, stage)
    saved = {
        r.category_key: r
        for r in db.scalars(select(VarianceReason).where(*reason_filters(key)))
    }

    missing: list[VarianceReasonGap] = []
    stale: list[VarianceReasonGap] = []
    for row in result.rows:
        if row.unit != "krw" or row.category_key in GROUP_KEYS or row.diff_pct is None:
            continue
        if abs(row.diff_pct) < threshold_pct:
            continue
        existing = saved.get(row.category_key)
        gap = VarianceReasonGap(
            category_key=row.category_key, label=row.label, diff=row.diff, diff_pct=row.diff_pct,
            reason_text=existing.reason_text if existing else "",
            needs_recheck=bool(existing and existing.needs_recheck),
        )
        if not gap.reason_text:
            missing.append(gap)
        elif gap.needs_recheck:
            stale.append(gap)

    parts = []
    if missing:
        parts.append(f"증감률 {threshold_pct:g}% 이상인데 사유가 없는 항목 {len(missing)}건")
    if stale:
        parts.append(f"사유 저장 뒤 금액이 바뀌어 재확인이 필요한 항목 {len(stale)}건")
    note = (
        " · ".join(parts) + " — 확정을 막지는 않지만, 이대로 확정하면 사유 없이 보고된다."
        if parts
        else f"증감률 {threshold_pct:g}% 이상인 항목은 모두 사유가 적혀 있다."
    )
    return VarianceReasonCheck(threshold_pct=threshold_pct, missing=missing, stale=stale, note=note)


def compute_variance_table(
    db: Session,
    basis_year: int,
    basis_month: int,
    kind: str,
    base_version: str,
    lab_scope: str,
    plan_no: str | None,
    stage: str = "",
) -> VarianceTable:
    """증감분석 화면이 그리는 표 전체 — 17기간(월 12 + 분기 4 + 연간) × 계정 전부.

    기간을 하나씩 받는 `compute_variance`와 같은 계산이지만, 준비물(기준 해석 · 연구소 목록 ·
    두 버전 데이터 로드)을 **한 번만** 만들고 기간만 바꿔 돈다 — 화면이 17번 부르면 같은 DB
    읽기를 17번 하고, 그게 이 화면이 브라우저에서 계산되고 있던 이유다.

    왜 기간별 응답을 합치지 않고 따로 주는가: 월 금액을 더해 분기를 만들 수 없는 행이 있다
    (인력은 평균, 비율은 분자·분모를 각각 더한 뒤 나눠야 한다). 서버가 기간마다 제대로 계산해서
    주고 화면은 고르기만 한다.
    """
    st = _variance_setup(db, basis_year, basis_month, kind, base_version, lab_scope, plan_no)
    periods = {
        period: variance_rows(
            variance_cells(
                db, st.labs, st.pks, st.cur_d, st.base_d, ix, st.cur_fx, st.base_fx, st.split_fx
            )
        )
        for period, ix in PERIOD_IX.items()
    }

    # 인건비 분해도 같이 담는다 — 화면이 인건비 행을 펼칠 때 쓰는 줄들이고, 따로 부르면 기간마다
    # 한 번씩(17번) 왕복한다. 계산은 엔드포인트와 **같은 함수**를 쓴다(services/labor_split.py).
    # 경영계획 대비는 환율 기준이 없어 분해할 수 없다 — 그때는 빈 dict로 두고 화면이 그 줄을
    # 안 그리게 한다(없는 걸 0으로 주면 "분해했는데 0"으로 읽힌다).
    split: dict[str, LaborSplitTotals] = {}
    try:
        inp = labor_split_prepare(db, basis_year, base_version, lab_scope, basis_month, kind, plan_no)
        split = {
            period: LaborSplitTotals(
                **{
                    f: getattr(labor_split_for(db, inp, period, ix), f)
                    for f in LaborSplitTotals.model_fields
                }
            )
            for period, ix in PERIOD_IX.items()
        }
    except HTTPException:
        split = {}
    return VarianceTable(
        basis_year=basis_year,
        basis_month=basis_month,
        kind=kind,
        stage=stage,
        base_version=base_version,
        base_label=st.basis.resolved_label,
        lab_scope=(lab_scope or "ALL"),
        plan_no=st.plan_no,
        periods=periods,
        labor_split=split,
        fallback_note=st.basis.fallback_note,
        note=st.note,
    )
