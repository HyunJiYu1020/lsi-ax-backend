"""해외연구소 증감분석 화면 전용 엔드포인트 — 비교 기준 해석 · 증감 표 · 증감사유 · 증감 동인 ·
실적 1차/2차.

overseas.py에 넣지 않고 모듈을 가른 이유: 그 파일은 이미 2,700줄이고 다루는 일이 "취합·집계"
(업로드·기준정보·행/열)인데, 여기 있는 건 전부 "증감분석 한 화면"의 일이다. 같은 파일에 두면 어느
함수가 어느 화면 것인지 읽어서 알 수 없어진다. 의존 방향은 한쪽이다 — 여기서 overseas.py의 헬퍼를
가져다 쓰고, overseas.py는 이 모듈을 모른다.

이 화면이 지켜야 하는 규칙(전부 여기 구현):
- 비교 기준은 이름만으로 뜻이 통하지 않는다. "전실행"은 1월 속보에서는 전년 12월 실적이고, 실적
  버전에서는 같은 달 속보다. 그래서 라벨을 서버가 만들어 내려준다(_resolve_bases).
- 환차는 USD 외 통화 연구소 전부를 현지→$ / $→원으로 가른다. USD 연구소는 현지→$ 단계가 없어
  fx_local이 0이다 — 통화가 섞인 전체 합계에서도 두 칸의 합이 환차 계와 맞는다.
- 증감사유는 "회신 원문 → 번역 + AI 초안 → 담당자 수정 → [저장]"이고, [저장] 시점 증감액을 같이
  박아 둔다. 재업로드로 금액이 바뀌면 사유를 지우지 않고 "재확인 필요"만 켠다.
- 실적은 1차(ERP 약식, 연구소 총액만)와 2차(PL, 속보와 같은 구성)가 저장소부터 다르다.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.v1.endpoints.overseas import (
    _basis_month,
    _n,
    _source_hash,
)
from app.core.cost_groups import CATEGORY_LABELS, CATEGORY_LABELS_EN, VARIANCE_ROWS, resolve_leaves
from app.core.periods import PERIOD_IX, month_value, yyyymm
from app.services.overseas.entries import entry_months_bulk, plan_entry_months_bulk
from app.services.overseas.fxcalc import rate_arrays
from app.services.overseas.labs import ACCOUNT_KEY, LabView, all_labs, lab_views
from app.core.reason_translate import (
    MAX_LEN,
    draft_reason,
    engine_label,
    normalize,
    resolve_provider,
    translate_batch,
)
from app.db.session import get_db
from app.models import (
    CloseAccountStage,
    CostEntry,
    ManagementPlanEntry,
    ManagementPlanVersion,
    OverseasActualFirstRow,
    TextTranslation,
    VarianceDriver,
    VarianceReason,
    Version,
)
from app.schemas.overseas import (
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

router = APIRouter()

BASE_LABELS = {"prev": "전실행", "prior": "전년", "plan": "경영계획"}
KIND_LABELS = {"forecast": "속보", "actual": "실적", "plan": "경영계획"}
# 증감률이 이만큼을 넘으면 사유를 쓰라고 확정 직전에 경고한다. 업무 규칙상 바뀔 수 있는 값이라
# 쿼리 파라미터로 받고 기본값만 여기 둔다(화면도 이 값을 받아서 표의 경고 표시 기준으로 쓴다).
DEFAULT_REASON_THRESHOLD_PCT = 10.0
# 합계·집계 행은 하위 계정의 합이라 사유를 따로 쓰지 않는다 — 경고 목록에서 뺀다
# (개발비(매출)에 사유를 쓰라고 하면 담당자는 같은 말을 두 번 쓰게 된다).
_GROUP_KEYS = {key for key, _, _ in VARIANCE_ROWS if len(resolve_leaves(key)) > 1}


def _ym_label(year: int, month: int, kind: str) -> str:
    """"26.09 속보" — 화면·엑셀·챗봇이 같은 문장을 쓰게 하려고 라벨을 한 군데서 만든다."""
    return f"{year % 100:02d}.{month:02d} {KIND_LABELS.get(kind, kind)}"


def _check_kind(kind: str) -> str:
    if kind not in ("forecast", "actual", "plan"):
        raise HTTPException(400, "kind는 forecast(속보) | actual(실적) | plan(경영계획)만 쓸 수 있다.")
    return kind


def _check_period(period: str) -> tuple[int, ...]:
    ix = PERIOD_IX.get(period)
    if ix is None:
        raise HTTPException(400, "period는 y(연간) | q1~q4 | m1~m12만 쓸 수 있다.")
    return ix


def _check_stage(stage: str, kind: str) -> str:
    """실적만 1차/2차로 갈린다. 속보에 stage를 넣으면 같은 화면 값이 두 군데에 나뉘어 저장되므로 막는다."""
    stage = stage or ""
    if stage not in ("", "first", "second"):
        raise HTTPException(400, "stage는 first(1차) | second(2차) 또는 빈 값(속보)만 쓸 수 있다.")
    if stage and kind != "actual":
        raise HTTPException(400, "1차/2차 구분은 실적 버전에만 있다 — 속보는 stage를 비워라.")
    return stage


# ---------- 비교 기준 해석 ----------
def _has_cost_data(db: Session, version: str, yyyymm: str) -> bool:
    return bool(
        db.scalar(
            select(CostEntry.id)
            .where(CostEntry.account_code == ACCOUNT_KEY, CostEntry.version == version, CostEntry.yyyymm == yyyymm)
            .limit(1)
        )
    )


def _latest_cost_yyyymm(db: Session, version: str, before: str | None = None) -> str | None:
    """그 버전 종류에서 데이터가 실제로 들어있는 가장 최근 편성 연월. before를 주면 그보다 앞선 것만.
    yyyymm이 "YYYYMM" 고정폭이라 문자열 최댓값이 곧 날짜순 최댓값이다."""
    stmt = select(func.max(CostEntry.yyyymm)).where(
        CostEntry.account_code == ACCOUNT_KEY, CostEntry.version == version
    )
    if before is not None:
        stmt = stmt.where(CostEntry.yyyymm < before)
    return db.scalar(stmt)


def _plan_revisions(db: Session, year: int) -> list[PlanRevisionOut]:
    rows = list(
        db.scalars(
            select(ManagementPlanVersion)
            .where(ManagementPlanVersion.year == year)
            .order_by(ManagementPlanVersion.plan_no)
        )
    )
    return [
        PlanRevisionOut(
            plan_no=r.plan_no,
            label=f"{r.plan_no} ★" if r.is_final else r.plan_no,
            is_final=r.is_final,
            is_confirmed=r.is_confirmed,
            finalized_at=r.finalized_at.isoformat() if r.finalized_at else "",
        )
        for r in rows
    ]


def _default_plan_no(revisions: list[PlanRevisionOut]) -> str:
    """기본 차수는 ★(그 연도의 공식 비교 기준). ★가 없으면 가장 최근 차수를 쓴다 — 경영계획 대비
    칸을 빈 화면으로 두지 않으려는 것이다(어느 차수를 보고 있는지는 라벨로 밝힌다)."""
    for r in revisions:
        if r.is_final:
            return r.plan_no
    return revisions[-1].plan_no if revisions else ""


def _prev_spec(db: Session, year: int, month: int, kind: str) -> tuple[int, int, str, str]:
    """전실행 = 직전 버전. (연, 월, kind, 폴백 안내) 를 돌려준다.

    버전은 "그 달 속보 → 그 달 실적 → 다음 달 속보 → …" 순서로 흐른다. 그래서
    - 속보의 직전 버전은 지난달 실적이고, 1월 속보는 전년 12월 실적이다(연도를 넘어간다).
    - 실적의 직전 버전은 같은 달 속보다 — 실적은 그 달 속보를 실행한 결과라서 그것과 맞대보는 게
      "전실행"의 뜻이다.
    그 자리에 데이터가 없으면 더 앞선 실적 → 더 앞선 속보로 물러난다. 아무 것도 없으면 그대로
    돌려주고 has_data=False로 밝힌다(숫자를 지어내지 않는다)."""
    if kind == "actual":
        target = (year, month, "forecast")
    elif month == 1:
        target = (year - 1, 12, "actual")
    else:
        target = (year, month - 1, "actual")

    ty, tm, tkind = target
    if _has_cost_data(db, tkind, yyyymm(ty, tm)):
        return ty, tm, tkind, ""

    cur_yyyymm = yyyymm(year, month)
    for fb_kind in ("actual", "forecast"):
        latest = _latest_cost_yyyymm(db, fb_kind, before=cur_yyyymm)
        if latest:
            fy, fm = int(latest[:4]), int(latest[4:6])
            return (
                fy,
                fm,
                fb_kind,
                f"{_ym_label(ty, tm, tkind)} 데이터가 없어 {_ym_label(fy, fm, fb_kind)} 기준으로 비교합니다.",
            )
    return ty, tm, tkind, f"{_ym_label(ty, tm, tkind)} 데이터가 없어 전실행 대비 증감을 낼 수 없습니다."


def _prior_spec(db: Session, year: int, month: int) -> tuple[int, int, str, str]:
    """전년 = 전년 12월 실적. 없으면 최근 실적 → 최근 속보로 물러나고 그 사실을 문장으로 밝힌다
    ("전년 12월 실적이 없어 26.11 속보 기준으로 비교합니다.")."""
    ty, tm, tkind = year - 1, 12, "actual"
    if _has_cost_data(db, tkind, yyyymm(ty, tm)):
        return ty, tm, tkind, ""

    cur_yyyymm = yyyymm(year, month)
    for fb_kind in ("actual", "forecast"):
        latest = _latest_cost_yyyymm(db, fb_kind, before=cur_yyyymm)
        if latest:
            fy, fm = int(latest[:4]), int(latest[4:6])
            return (
                fy,
                fm,
                fb_kind,
                f"전년 12월 실적이 없어 {_ym_label(fy, fm, fb_kind)} 기준으로 비교합니다.",
            )
    return ty, tm, tkind, "전년 12월 실적이 없어 전년 대비 증감을 낼 수 없습니다."


def _plan_creation_prev(db: Session, year: int, plan_no: str) -> tuple[int, int, str, str]:
    """경영계획 버전을 편성할 때의 전실행 = "생성 시점 최근 버전".

    경영계획은 특정 달에 매여 있지 않아서 "지난달 실적"이라는 말이 성립하지 않는다. 대신 그 차수를
    만든 시점에 가장 최근이던 버전과 비교한다 — 그게 그때 담당자가 실행 중이던 계획이다. 차수를
    만든 시각 이후에 열린 버전은 보지 않는다(나중에 열린 버전으로 비교 기준이 소급해서 바뀌면,
    이미 보고한 경영계획 대비 증감이 달라진다)."""
    plan = db.scalar(
        select(ManagementPlanVersion).where(
            ManagementPlanVersion.year == year, ManagementPlanVersion.plan_no == plan_no
        )
    )
    created = plan.created_at if plan else None
    stmt = select(Version).order_by(Version.year.desc(), Version.month.desc(), Version.kind.desc())
    if created is not None:
        stmt = stmt.where(Version.created_at <= created)
    row = db.scalars(stmt.limit(1)).first()
    if row is None:
        return 0, 0, "", "경영계획 생성 시점에 열린 버전이 없어 전실행 대비 증감을 낼 수 없습니다."
    note = ""
    if not _has_cost_data(db, row.kind, yyyymm(row.year, row.month)):
        note = f"{_ym_label(row.year, row.month, row.kind)}에 저장된 데이터가 없어 전실행 대비 증감을 낼 수 없습니다."
    return row.year, row.month, row.kind, note


def _resolve_bases(
    db: Session, basis_year: int, basis_month: int, kind: str, plan_no: str | None = None
) -> VarianceBasisResult:
    revisions = _plan_revisions(db, basis_year)
    default_plan = _default_plan_no(revisions)
    chosen_plan = plan_no or default_plan
    if plan_no and all(r.plan_no != plan_no for r in revisions):
        raise HTTPException(404, f"{basis_year}년에 그런 경영계획 차수가 없다: {plan_no!r}")

    if kind == "plan":
        py, pm, pkind, pnote = _plan_creation_prev(db, basis_year, chosen_plan)
    else:
        py, pm, pkind, pnote = _prev_spec(db, basis_year, basis_month, kind)
    ry, rm, rkind, rnote = _prior_spec(db, basis_year, basis_month)

    plan_has_data = bool(
        chosen_plan
        and db.scalar(
            select(ManagementPlanEntry.id)
            .where(
                ManagementPlanEntry.account_code == ACCOUNT_KEY,
                ManagementPlanEntry.year == basis_year,
                ManagementPlanEntry.plan_no == chosen_plan,
            )
            .limit(1)
        )
    )
    plan_label = next((r.label for r in revisions if r.plan_no == chosen_plan), chosen_plan)

    bases = [
        VarianceBasisOut(
            key="prev",
            label=BASE_LABELS["prev"],
            resolved_label=_ym_label(py, pm, pkind) if pkind else "—",
            year=py,
            month=pm,
            kind=pkind,
            has_data=not pnote.endswith("낼 수 없습니다."),
            fallback_note=pnote,
        ),
        VarianceBasisOut(
            key="prior",
            label=BASE_LABELS["prior"],
            resolved_label=_ym_label(ry, rm, rkind),
            year=ry,
            month=rm,
            kind=rkind,
            has_data=not rnote.endswith("낼 수 없습니다."),
            fallback_note=rnote,
        ),
        VarianceBasisOut(
            key="plan",
            label=BASE_LABELS["plan"],
            resolved_label=f"{basis_year}년 {plan_label}" if chosen_plan else "—",
            year=basis_year,
            kind="plan",
            plan_no=chosen_plan,
            has_data=plan_has_data,
            fallback_note=(
                ""
                if plan_has_data
                else f"{basis_year}년 경영계획 데이터가 없어 경영계획 대비 증감을 낼 수 없습니다."
            ),
        ),
    ]
    return VarianceBasisResult(
        basis_year=basis_year,
        basis_month=basis_month,
        kind=kind,
        basis_label=f"{basis_year}년 {plan_label}" if kind == "plan" else _ym_label(basis_year, basis_month, kind),
        bases=bases,
        plan_revisions=revisions,
        default_plan_no=default_plan,
    )


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
    _check_kind(kind)
    month = basis_month or _basis_month(db) or 1
    if not 1 <= month <= 12:
        raise HTTPException(400, "basis_month는 1~12여야 한다.")
    return _resolve_bases(db, basis_year, month, kind, plan_no)


# ---------- 버전 데이터 읽기 ----------
def _load_cost_version(db: Session, version: str, yyyymm: str, lab_pks: set[str]) -> dict[str, dict[str, list]]:
    """cost_entries 한 버전(편성 연월 1건)을 pk → 계정 → 12개월 배열로 읽는다.

    연구소가 직접 추가한 행(label이 채워진 행)은 뺀다 — 고정 계정 합계에 섞이면 같은 금액이 두 번
    세어진다(extra-rows 엔드포인트가 따로 내려주는 값이다)."""
    entries = [
        e
        for e in db.scalars(
            select(CostEntry).where(
                CostEntry.account_code == ACCOUNT_KEY,
                CostEntry.version == version,
                CostEntry.yyyymm == yyyymm,
                CostEntry.label.is_(None),
            )
        )
        if e.scope_code in lab_pks
    ]
    months = entry_months_bulk(db, entries)
    out: dict[str, dict[str, list]] = {}
    for e in entries:
        out.setdefault(e.scope_code, {})[e.category_code] = months[e.id]
    return out


def _load_plan_version(db: Session, year: int, plan_no: str, lab_pks: set[str]) -> dict[str, dict[str, list]]:
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
    months = plan_entry_months_bulk(db, [e.id for e in entries])
    out: dict[str, dict[str, list]] = {}
    for e in entries:
        out.setdefault(e.scope_code, {})[e.category_code] = months[e.id]
    return out


def _load_basis(db: Session, spec, lab_pks: set[str]) -> dict[str, dict[str, list]]:
    """비교 기준(또는 현재 버전) 한 쪽의 데이터. plan은 management_plan_entries, 나머지는 cost_entries."""
    year, month, kind, plan_no = spec
    if kind == "plan":
        return _load_plan_version(db, year, plan_no, lab_pks)
    if not kind:
        return {}
    return _load_cost_version(db, kind, yyyymm(year, month), lab_pks)


def _active_labs(db: Session, basis_year: int, basis_month: int) -> dict[str, LabView]:
    """계산에 넣을 연구소 pk → 기준정보. 기준정보는 "그 버전 시점" 값으로 읽는다 — 통화를 지금
    값으로 보면 지난달에 보고한 환차 분해가 소급해서 달라진다(공통 RULE).

    합계 행(part_code="TOTAL")과 원화 기타(_ETC)는 다른 행을 더해 만든 값이라 뺀다 — 넣으면
    SARC가 CPU+GPU+TOTAL로 두 번 세어져 금액이 정확히 두 배가 된다."""
    return {
        lab.pk: lab
        for lab in lab_views(db, f"{basis_year:04d}-{basis_month:02d}")
        if lab.is_active and lab.pk != "_ETC" and (lab.part_code or "").upper() != "TOTAL"
    }


def _scope_pks(labs: dict[str, LabView], lab_scope: str) -> list[str]:
    scope = (lab_scope or "ALL").strip()
    if scope.upper() == "ALL":
        return sorted(labs)
    pks = sorted(pk for pk, lab in labs.items() if pk == scope or lab.lab_code == scope)
    if not pks:
        raise HTTPException(404, f"그런 연구소가 없다: {scope!r}")
    return pks


# ---------- 증감 표 ----------
def _fx_specs(cur_spec, base_spec) -> tuple[tuple[int, int, str], tuple[int, int, str]]:
    """환율 기준(연·월·forecast|actual) 두 쌍. 경영계획은 속보/실적 환율 기준이 없어서, 그쪽을
    비교할 때는 "지금 버전의 환율"을 양쪽에 똑같이 써서 환차를 0으로 둔다 — 경영계획 금액은 애초에
    그때의 환율 가정으로 만든 값이라 환차를 가를 근거가 없다. 화면도 같은 제약을 둔다."""
    cy, cm, ckind, _ = cur_spec
    by, bm, bkind, _ = base_spec
    cur_fx = (cy, cm, ckind if ckind in ("forecast", "actual") else "forecast")
    base_fx = (by, bm, bkind) if bkind in ("forecast", "actual") else cur_fx
    return cur_fx, base_fx


def _variance_cells(
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
    """계정 키 → {cur, base, fx_local, fx_usd, fx_outside} (원). 화면 logic/calc.js의 ovAnaEl과 같은 식.

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

    leaves = {key: resolve_leaves(key) for key, _, _ in VARIANCE_ROWS if key != "pct"}
    out = {key: {"cur": 0.0, "base": 0.0, "fx_local": 0.0, "fx_usd": 0.0, "fx_outside": 0.0} for key in leaves}

    for pk in pks:
        lab = labs[pk]
        lc = lab.currency if (split_fx and lab.currency and lab.currency != "USD") else None
        cats_cur, cats_base = cur_d.get(pk, {}), base_d.get(pk, {})
        for key, keys in leaves.items():
            cell = out[key]
            for j in ix:
                uc = sum(_n(month_value(cats_cur, leaf, j)) for leaf in keys)
                ub = sum(_n(month_value(cats_base, leaf, j)) for leaf in keys)
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


def _to_kdollar(local_amount: float, local_rate: float) -> float:
    """현지통화 금액 → K$. 현지 환율(현지통화/$)이 없으면 0으로 둔다 — 환율을 모르는 채 1:1로
    넘기면 ILS 금액이 $ 금액으로 들어가 1,000배 틀린 숫자가 화면에 뜬다.

    1000으로 나누는 이유: 비용 데이터는 전부 K$ 단위라 1차 환산 결과도 거기에 맞춰야 2차와 더하거나
    비교할 수 있다. local_amount는 현지통화의 1단위(ILS 1, INR 1)로 저장한다."""
    return (local_amount / local_rate / 1000) if local_rate > 0 else 0.0


def _pct(cur: float, base: float) -> float | None:
    """증감률. 기준이 0이면 비율을 낼 수 없다 — 0%가 아니라 null이다(무한대를 0으로 적으면 '변동
    없음'으로 읽힌다)."""
    return ((cur - base) / base) * 100 if base else None


def _variance_rows(cells: dict[str, dict[str, float]]) -> list[VarianceRow]:
    rows: list[VarianceRow] = []
    for key, level, unit in VARIANCE_ROWS:
        if unit == "pct":
            # (%) 행은 금액이 아니라 MARK UP 비율(B ÷ A)이다. 두 버전의 비율을 각각 내고 그 차이를
            # %p로 보여주므로, 환차를 가를 대상이 아니다.
            a, b = cells["A"], cells["B"]
            cur = (b["cur"] / a["cur"] * 100) if a["cur"] else 0.0
            base = (b["base"] / a["base"] * 100) if a["base"] else 0.0
            rows.append(
                VarianceRow(
                    category_key=key, label="(%)", label_en="(%)", level=level, unit="pct",
                    base=base, cur=cur, diff=cur - base,
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
                level=level,
                unit=unit,
                base=c["base"],
                cur=c["cur"],
                diff=c["cur"] - c["base"],
                diff_pct=_pct(c["cur"], c["base"]),
                fx=fx_local + fx_usd,
                fx_local=fx_local,
                fx_usd=fx_usd,
                fx_outside=c["fx_outside"],
            )
        )
    return rows


def _compute_variance(
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
    if base_version not in BASE_LABELS:
        raise HTTPException(400, "base_version은 prev(전실행) | prior(전년) | plan(경영계획)만 쓸 수 있다.")
    ix = _check_period(period)
    resolved = _resolve_bases(db, basis_year, basis_month, kind, plan_no)
    basis = next(b for b in resolved.bases if b.key == base_version)
    if not basis.has_data:
        raise HTTPException(404, basis.fallback_note or f"{basis.label} 데이터가 없어 증감을 낼 수 없다.")

    labs = _active_labs(db, basis_year, basis_month)
    pks = _scope_pks(labs, lab_scope)
    lab_pks = set(labs)

    cur_spec = (
        (basis_year, basis_month, "plan", resolved.default_plan_no)
        if kind == "plan"
        else (basis_year, basis_month, kind, "")
    )
    base_spec = (basis.year, basis.month, basis.kind, basis.plan_no)
    cur_d = _load_basis(db, cur_spec, lab_pks)
    base_d = _load_basis(db, base_spec, lab_pks)
    if not cur_d:
        raise HTTPException(404, f"{resolved.basis_label} 데이터가 없다.")

    # 경영계획이 한쪽에라도 끼면 환차를 가를 수 없다(환율 기준이 없다) — 그때는 환차 칸을 0으로
    # 두고 증감 전부를 환차外로 본다. 0을 "환차가 없었다"로 읽지 않도록 note에 적어 내려보낸다.
    split_fx = "plan" not in (cur_spec[2], base_spec[2])
    cur_fx, base_fx = _fx_specs(cur_spec, base_spec)
    cells = _variance_cells(db, labs, pks, cur_d, base_d, ix, cur_fx, base_fx, split_fx)
    note = (
        "환차 = 환율이 움직여서 변한 몫, 환차外 = 금액 자체의 증감이다. USD가 아닌 통화를 쓰는 "
        "연구소는 환차를 현지→$(fx_local)와 $→원(fx_usd)으로 가른다 — USD 연구소는 fx_local이 0이다. "
        "금액 단위는 원이다."
    )
    if not split_fx:
        note += " 경영계획은 속보/실적 환율 기준이 없어 환차를 가를 수 없다 — 환차 칸은 0이고 증감 전부가 환차外로 들어가 있다."
    return VarianceResult(
        basis_year=basis_year,
        basis_month=basis_month,
        kind=kind,
        stage=stage,
        base_version=base_version,
        base_label=basis.resolved_label,
        period=period,
        lab_scope=(lab_scope or "ALL"),
        plan_no=basis.plan_no,
        rows=_variance_rows(cells),
        fallback_note=basis.fallback_note,
        note=note,
    )


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
    _check_kind(kind)
    month = basis_month or _basis_month(db) or 1
    _check_stage(stage, kind)
    return _compute_variance(
        db, basis_year, month, kind, base_version, period, lab_scope, plan_no, stage
    )


# ---------- 증감사유 ----------
def _reason_filters(key, model=VarianceReason):
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


def _reason_out(row: VarianceReason, current_diff: float | None = None) -> VarianceReasonOut:
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


def _diff_by_category(
    db: Session, key, plan_no: str | None
) -> dict[str, tuple[float, float | None]]:
    """그 화면 조합의 계정별 (증감액, 증감률). 사유의 재확인 판정과 확정 경고에 쓴다.

    데이터가 없어 증감을 낼 수 없는 조합(비교 기준 미존재 등)에서는 빈 dict를 돌려준다 — 사유 조회
    자체는 막지 않는다(담당자가 써 둔 글은 비교 기준과 무관하게 보여야 한다)."""
    try:
        result = _compute_variance(
            db, key.basis_year, key.basis_month, key.kind, key.base_version, key.period,
            key.lab_scope, plan_no, key.stage,
        )
    except HTTPException:
        return {}
    return {r.category_key: (r.diff, r.diff_pct) for r in result.rows}


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
    _check_kind(kind)
    month = basis_month or _basis_month(db) or 1
    key = VarianceReasonIn(
        basis_year=basis_year, basis_month=month, kind=kind, stage=_check_stage(stage, kind),
        base_version=base_version, period=period, lab_scope=lab_scope, category_key="",
    )
    _check_period(period)
    rows = list(db.scalars(select(VarianceReason).where(*_reason_filters(key))))
    diffs = _diff_by_category(db, key, plan_no)
    out: list[VarianceReasonOut] = []
    for row in rows:
        diff = diffs.get(row.category_key)
        out.append(_reason_out(row, diff[0] if diff else None))
    return out


def _apply_recheck(row: VarianceReason, current_diff: float | None) -> None:
    """저장된 사유는 그대로 두고 재확인 플래그만 갱신한다.

    왜 사유를 지우지 않는가: 담당자가 쓴 문장은 서버가 버릴 값이 아니다. 금액이 바뀌었다고 사유를
    비우면 매달 재업로드마다 사유를 처음부터 다시 써야 한다. 바뀐 사실만 알려주고, 그대로 쓸지
    고칠지는 사람이 정한다(공통 RULE: 재업로드로 금액이 바뀌면 사유 유지 + "재확인 필요")."""
    if not row.reason_text or row.saved_diff is None or current_diff is None:
        return
    row.needs_recheck = abs(float(row.saved_diff) - current_diff) >= RECHECK_TOLERANCE_KRW


@router.put("/overseas/variance-reasons", response_model=VarianceReasonOut)
def upsert_variance_reason(body: VarianceReasonIn, db: Session = Depends(get_db)) -> VarianceReasonOut:
    """사유 한 칸 저장. commit=True면 [저장]을 누른 것으로 보고 저장 시점 증감액까지 박아 둔다.

    1차가 고정([1차 고정])돼 있으면 1차 사유 쓰기를 거부한다 — 보고까지 끝난 값이라서다."""
    _check_kind(body.kind)
    stage = _check_stage(body.stage, body.kind)
    _check_period(body.period)
    if body.base_version not in BASE_LABELS:
        raise HTTPException(400, "base_version은 prev | prior | plan만 쓸 수 있다.")
    if not body.category_key:
        raise HTTPException(400, "category_key는 비울 수 없다.")
    if stage == "first":
        _require_first_unlocked(db, body.basis_year, body.basis_month, "1차 사유")

    row = db.scalar(
        select(VarianceReason).where(
            *_reason_filters(body), VarianceReason.category_key == body.category_key
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
        diffs = _diff_by_category(db, body, None)
        pair = diffs.get(body.category_key)
        current_diff = pair[0] if pair else None

    if body.commit and body.reason_text is not None:
        # [저장]을 누른 순간을 기준점으로 다시 잡는다 — 재확인 딱지는 여기서 지워지고, 다음 업로드로
        # 금액이 또 바뀌면 그때 다시 켜진다.
        row.saved_diff = current_diff
        row.saved_at = datetime.now(timezone.utc)
        row.needs_recheck = False
    else:
        _apply_recheck(row, current_diff)

    db.commit()
    db.refresh(row)
    return _reason_out(row, current_diff)


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
    _check_kind(body.kind)
    source = normalize(body.source_text)
    if not source:
        raise HTTPException(400, "source_text(회신 원문)가 비어 있다.")
    if len(source) > MAX_LEN:
        raise HTTPException(400, f"회신 원문이 너무 길다({len(source)}자) — {MAX_LEN}자 이하로 줄여라.")
    provider = resolve_provider(body.provider)
    engine = engine_label(provider)

    cached = db.scalar(
        select(TextTranslation).where(TextTranslation.source_hash == _source_hash(source))
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
        _cache_translation(db, source, source_ko, engine)

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


def _cache_translation(db: Session, source: str, ko: str, engine: str) -> None:
    """번역 1건을 /overseas/reason-translate와 같은 자리에 캐시한다 — 두 경로가 따로 캐시하면 같은
    원문이 두 번역을 갖게 되어 "원문 1건 = 번역 1개"가 깨진다. ko_text가 String(600)이라 길면 자른다."""
    text = ko[:600]
    stmt = pg_insert(TextTranslation).values(
        source_hash=_source_hash(source), source_text=source, ko_text=text, engine=engine
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[TextTranslation.source_hash],
            set_={"ko_text": text, "engine": engine, "updated_at": func.now()},
        )
    )
    db.commit()


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

    서버가 확정을 막지는 않는다(공통 RULE: 미작성·미업로드가 있어도 진행은 막지 않고 무엇을 알고도
    확정하는지 밝힌다). 화면이 이 목록을 보여주고 담당자가 결정한다.

    집계 행(개발비(매출)·개발비(A) 등)은 목록에서 뺀다 — 하위 계정의 합이라 같은 사유를 두 번 쓰게
    된다. 인력(명)·(%) 행도 금액 증감이 아니라서 뺀다."""
    _check_kind(kind)
    month = basis_month or _basis_month(db) or 1
    stage = _check_stage(stage, kind)
    key = VarianceReasonIn(
        basis_year=basis_year, basis_month=month, kind=kind, stage=stage, base_version=base_version,
        period=period, lab_scope=lab_scope, category_key="",
    )
    result = _compute_variance(db, basis_year, month, kind, base_version, period, lab_scope, plan_no, stage)
    saved = {
        r.category_key: r
        for r in db.scalars(select(VarianceReason).where(*_reason_filters(key)))
    }

    missing: list[VarianceReasonGap] = []
    stale: list[VarianceReasonGap] = []
    for row in result.rows:
        if row.unit != "krw" or row.category_key in _GROUP_KEYS or row.diff_pct is None:
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


# ---------- 증감 동인 표 ----------
_DRIVER_OPS = {"ref", "div_elementwise", "sub", "sum"}


def _check_factors(factors: list[VarianceDriverFactor]) -> list[dict]:
    if not factors:
        raise HTTPException(400, "factors(동인 행)가 비어 있다.")
    out: list[dict] = []
    for f in factors:
        if f.op not in _DRIVER_OPS:
            raise HTTPException(400, f"알 수 없는 op: {f.op!r} ({' | '.join(sorted(_DRIVER_OPS))}만 가능)")
        if not f.operands:
            raise HTTPException(400, f"{f.label!r}의 operands(계산 재료)가 비어 있다.")
        if f.op == "div_elementwise" and len(f.operands) != 2:
            raise HTTPException(400, f"{f.label!r}: div_elementwise는 operands가 정확히 2개여야 한다(분자, 분모).")
        out.append(f.model_dump())
    return out


def _driver_value(cells: dict[str, dict[str, float]], factor: dict, side: str) -> float | None:
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


def _driver_rows(cells: dict[str, dict[str, float]], factors: list[dict]) -> list[VarianceDriverRow]:
    rows: list[VarianceDriverRow] = []
    for f in factors:
        base = _driver_value(cells, f, "base")
        cur = _driver_value(cells, f, "cur")
        names = " ÷ ".join(CATEGORY_LABELS.get(k, k) for k in f.get("operands", []))
        rows.append(
            VarianceDriverRow(
                label=f.get("label", ""),
                unit=f.get("unit", "krw"),
                base=base,
                cur=cur,
                diff=(cur - base) if (base is not None and cur is not None) else None,
                diff_pct=_pct(cur, base) if (base is not None and cur is not None) else None,
                basis_text=(
                    f"{f.get('label', '')} = {names}"
                    + (f" = {cur:,.0f}" if cur is not None else " = (재료 없음)")
                ),
            )
        )
    return rows


def _driver_out(row: VarianceDriver, basis_year: int, basis_month: int, kind: str) -> VarianceDriverOut:
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
        created_basis=_ym_label(row.basis_year, row.basis_month, row.kind),
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


def _visible_drivers(
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
    _check_kind(kind)
    month = basis_month or _basis_month(db) or 1
    rows = _visible_drivers(db, basis_year, month, kind, lab_scope)
    out = [_driver_out(r, basis_year, month, kind) for r in rows]
    if not out or not with_values:
        return out
    try:
        result = _compute_variance(db, basis_year, month, kind, base_version, period, lab_scope, plan_no)
    except HTTPException:
        return out  # 비교 기준 데이터가 없으면 표 구성만 돌려준다(값은 빈칸)
    cells = {
        r.category_key: {"base": r.base, "cur": r.cur} for r in result.rows
    }
    for item, row in zip(out, rows):
        item.rows = _driver_rows(cells, row.factors or [])
    return out


@router.put("/overseas/variance-drivers", response_model=VarianceDriverOut)
def upsert_variance_driver(body: VarianceDriverIn, db: Session = Depends(get_db)) -> VarianceDriverOut:
    """동인 표를 붙이거나 "다음 버전에도?" 답을 반영한다(carry_over).

    carry_over를 끌 때 이전 버전 행을 고치지 않는 이유: 이 버전에 붙인 표의 설정만 바꾼다. 지난달
    보고에 들어간 표를 지금 결정으로 되돌리면 이미 확정한 자료가 달라진다."""
    _check_kind(body.kind)
    factors = _check_factors(body.factors)
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
    return _driver_out(row, body.basis_year, body.basis_month, body.kind)


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
    _check_kind(kind)
    month = basis_month or _basis_month(db) or 1
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


# ---------- 실적 1차/2차 ----------
def _stage_row(db: Session, basis_year: int, basis_month: int) -> CloseAccountStage | None:
    """그 연월 해외연구소의 결산 단계 행. "1차 완료"는 mapping_done 하나가 진실이다 —
    예전에는 overseas_actual_stages.close_first_done이 같은 사실을 따로 들고 있었다(PLAN.md P2-6)."""
    return db.scalar(
        select(CloseAccountStage).where(
            CloseAccountStage.account_key == ACCOUNT_KEY,
            CloseAccountStage.basis_year == basis_year,
            CloseAccountStage.basis_month == basis_month,
        )
    )


def _require_first_unlocked(db: Session, basis_year: int, basis_month: int, what: str) -> None:
    row = _stage_row(db, basis_year, basis_month)
    if row is not None and row.first_locked:
        raise HTTPException(
            409,
            f"{basis_year}년 {basis_month}월 1차가 고정돼 있어 {what}를 고칠 수 없다. "
            "[1차 고정]을 해제한 뒤 다시 시도하라.",
        )


def _stage_out(db: Session, basis_year: int, basis_month: int) -> ActualStageOut:
    row = _stage_row(db, basis_year, basis_month)
    labs = _active_labs(db, basis_year, basis_month)
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


@router.get("/overseas/actual-stage", response_model=ActualStageOut)
def get_actual_stage(basis_year: int, basis_month: int, db: Session = Depends(get_db)) -> ActualStageOut:
    """실적 한 달의 1차 상태. 화면은 close_first_done으로 [1차 분석] 탭을 열지 말지를, first_locked로
    입력칸을 잠글지를 정한다."""
    return _stage_out(db, basis_year, basis_month)


@router.put("/overseas/actual-stage", response_model=ActualStageOut)
def update_actual_stage(body: ActualStageIn, db: Session = Depends(get_db)) -> ActualStageOut:
    """[1차 고정] 토글과 실적결산 1차 완료 표시.

    versions.status는 건드리지 않는다 — 1차 고정은 버전 확정과 다른 층의 결정이다(2차 작업이
    남아 있으므로 버전은 작성중 그대로여야 한다)."""
    row = _stage_row(db, body.basis_year, body.basis_month)
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
    return _stage_out(db, body.basis_year, body.basis_month)


def _first_rows(db: Session, basis_year: int, basis_month: int) -> dict[str, OverseasActualFirstRow]:
    return {
        r.lab_code: r
        for r in db.scalars(
            select(OverseasActualFirstRow).where(
                OverseasActualFirstRow.basis_year == basis_year,
                OverseasActualFirstRow.basis_month == basis_month,
            )
        )
    }


def _lab_by_code(labs: dict[str, LabView]) -> dict[str, LabView]:
    """연구소 코드 → 대표 기준정보(파트 중 첫 번째). 1차는 파트 구분 없이 연구소 총액만 올라온다."""
    out: dict[str, LabView] = {}
    for lab in sorted(labs.values(), key=lambda x: (x.sort_order, x.pk)):
        out.setdefault(lab.lab_code, lab)
    return out


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
    resolved = _resolve_bases(db, basis_year, month, "actual", plan_no)
    basis = next((b for b in resolved.bases if b.key == base_version), None)
    if basis is None:
        raise HTTPException(400, "base_version은 prev | prior | plan만 쓸 수 있다.")

    labs = _active_labs(db, basis_year, month)
    by_code = _lab_by_code(labs)
    stored = _first_rows(db, basis_year, month)
    ix = (month - 1,)
    local_curs = {lab.currency for lab in by_code.values() if lab.currency and lab.currency != "USD"}

    cur_fx = (basis_year, month, "actual")
    base_is_fx = basis.kind in ("forecast", "actual")
    base_fx = (basis.year, basis.month, basis.kind) if base_is_fx else cur_fx
    rate_cur = rate_arrays(db, *cur_fx, local_curs, ix)
    rate_base = rate_arrays(db, *base_fx, local_curs, ix) if base_is_fx else rate_cur

    base_d = _load_basis(db, (basis.year, basis.month, basis.kind, basis.plan_no), set(labs))
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
        k_cur = _to_kdollar(local, lrc) if lc else local / 1000
        k_base = _to_kdollar(local, lrb) if lc else k_cur
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
            base_k += sum(_n(month_value(cats, leaf, j)) for leaf in tot_leaves)
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
                krw_amount=krw, base_krw=base_krw, diff=krw - base_krw, diff_pct=_pct(krw, base_krw),
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
        diff_pct=_pct(agg["krw"], agg["base"]), fx=agg["fx_local"] + agg["fx_usd"],
        fx_local=agg["fx_local"], fx_usd=agg["fx_usd"], fx_outside=agg["fx_outside"],
    )
    return ActualFirstResult(
        basis_year=basis_year,
        basis_month=month,
        base_version=base_version,
        base_label=basis.resolved_label,
        stage=_stage_out(db, basis_year, month),
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
    _require_first_unlocked(db, body.basis_year, body.basis_month, "1차 값")
    labs = _active_labs(db, body.basis_year, body.basis_month)
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
    labs = _active_labs(db, basis_year, basis_month)
    by_code = _lab_by_code(labs)
    stored = _first_rows(db, basis_year, basis_month)
    j = basis_month - 1
    ix = (j,)
    local_curs = {lab.currency for lab in by_code.values() if lab.currency and lab.currency != "USD"}
    rate = rate_arrays(db, basis_year, basis_month, "actual", local_curs, ix)
    second_d = _load_cost_version(db, "actual", yyyymm(basis_year, basis_month), set(labs))
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
            usd = _to_kdollar(local, lr) if lc else local / 1000
        first_krw = usd * rate["USD"][j] * 1000

        second_k = 0.0
        for pk, view in labs.items():
            if view.lab_code != code:
                continue
            cats = second_d.get(pk, {})
            second_k += sum(_n(month_value(cats, leaf, j)) for leaf in tot_leaves)
        second_krw = second_k * rate["USD"][j] * 1000

        rows.append(
            ActualStageShift(
                lab_code=code, lab_name=lab.name, first_krw=first_krw, second_krw=second_krw,
                diff=second_krw - first_krw, diff_pct=_pct(second_krw, first_krw),
                first_reason=first.reason_text if first else "",
            )
        )
        tot_first += first_krw
        tot_second += second_krw

    total = ActualStageShift(
        lab_code="TOTAL", lab_name="합계", first_krw=tot_first, second_krw=tot_second,
        diff=tot_second - tot_first, diff_pct=_pct(tot_second, tot_first),
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
