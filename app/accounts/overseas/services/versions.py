"""버전·비교 기준 해석 — "지금 보고 있는 버전이 무엇이고, 무엇과 비교하는가".

왜 따로 떼어냈나 (2026-11-17): 이 해석이 services/variance.py 안에 있어서, 증감분석 말고도 같은
해석이 필요한 쪽(집계표 · 인건비율 · 인건비 분해)이 variance를 import해야 했다. 그런데
variance는 labor_split을 import한다 — labor_split이 거꾸로 variance를 쓰면 순환이 된다. 버전
해석은 증감분석의 일이 아니라 **계정 전체의 공용 어휘**라서 여기로 올렸다.

여기 있는 규칙:
- 버전은 (연, 월, 종류[, 경영계획 차수])로 가리킨다. 종류는 forecast(속보) · actual(실적) ·
  plan(경영계획)이고, 월을 안 주고 "지금"으로 두면 DB에 들어온 가장 최근 속보 월이 된다.
- "전실행"은 이름만으로 뜻이 통하지 않는다 — 1월 속보에서는 전년 12월 실적이고, 실적 버전에서는
  같은 달 속보다. 그래서 라벨까지 서버가 만들어 내려준다(resolve_bases).
- 경영계획 차수는 ★ → 확정 → 최신 순으로 고른다(services/entries.pick_plan_no 한 곳).
- 금액은 plan이면 management_plan_entries, 나머지는 cost_entries에서 읽는다(load_basis).
"""

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dataclasses import dataclass

from app.shared.core.periods import yyyymm
from app.accounts.overseas.services.aggregate import extra_row_months
from app.accounts.overseas.services.extra_rows import read_extra_rows
from app.accounts.overseas.services.fxcalc import rate_arrays
from app.accounts.overseas.services.entries import (
    current_basis_month,
    entry_months_bulk,
    filled_cost_yyyymm,
    has_summable_rows,
    pick_plan_no,
    plan_entry_months_bulk,
)
from app.accounts.overseas.services.labs import ACCOUNT_KEY, LabView, is_summable, lab_views
from app.shared.models.common import CostEntry
from app.shared.models.plan import ManagementPlanEntry, ManagementPlanVersion
from app.shared.models.version import Version
from app.accounts.overseas.schemas import PlanRevisionOut, VarianceBasisOut, VarianceBasisResult

BASE_LABELS = {"prev": "전실행", "prior": "전년", "plan": "경영계획"}
KIND_LABELS = {"forecast": "속보", "actual": "실적", "plan": "경영계획"}


def ym_label(year: int, month: int, kind: str) -> str:
    """"26.09 속보" — 화면·엑셀·챗봇이 같은 문장을 쓰게 하려고 라벨을 한 군데서 만든다."""
    return f"{year % 100:02d}.{month:02d} {KIND_LABELS.get(kind, kind)}"


def check_kind(kind: str) -> str:
    if kind not in ("forecast", "actual", "plan"):
        raise HTTPException(400, "kind는 forecast(속보) | actual(실적) | plan(경영계획)만 쓸 수 있다.")
    return kind


# ---------- 비교 기준 해석 ----------
# "그 버전에 데이터가 있나"는 **계산이 실제로 읽는 집합**으로 판정한다(entries.has_summable_rows ·
# filled_cost_yyyymm). 행이 하나라도 있으면 있다고 보면, 값이 없는 줄 하나만 들어간 달이
# "속보가 있는 달"로 집힌다 — 그 달로 화면이 열리고 증감분석은 "데이터가 없다"로 404를 낸다.
def has_cost_data(db: Session, version: str, yyyymm: str) -> bool:
    return has_summable_rows(db, version, yyyymm)


def latest_cost_yyyymm(db: Session, version: str, before: str | None = None) -> str | None:
    """그 버전 종류에서 데이터가 실제로 들어있는 가장 최근 편성 연월. before를 주면 그보다 앞선 것만."""
    return filled_cost_yyyymm(db, version, before=before) or None


def plan_revisions(db: Session, year: int) -> list[PlanRevisionOut]:
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


def default_plan_no(revisions: list[PlanRevisionOut]) -> str:
    """기본 차수 = ★(그 연도의 공식 비교 기준) → **확정된 차수 중 최신** → 가장 최근 차수.

    규칙 자체는 `services/entries.pick_plan_no` 한 곳에 있다 — 집계·증감·`/overseas/state`가
    서로 다른 차수를 보면 같은 화면에서 숫자가 갈린다. 마지막 단계(작성 중 차수)까지 내려가는
    이유는 경영계획 대비 칸을 빈 화면으로 두지 않으려는 것이고, 어느 차수를 보고 있는지는
    라벨(`label`의 ★·`resolved_label`)로 밝힌다."""
    return pick_plan_no(revisions)


def prev_spec(db: Session, year: int, month: int, kind: str) -> tuple[int, int, str, str]:
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
    if has_cost_data(db, tkind, yyyymm(ty, tm)):
        return ty, tm, tkind, ""

    cur_yyyymm = yyyymm(year, month)
    for fb_kind in ("actual", "forecast"):
        latest = latest_cost_yyyymm(db, fb_kind, before=cur_yyyymm)
        if latest:
            fy, fm = int(latest[:4]), int(latest[4:6])
            return (
                fy,
                fm,
                fb_kind,
                f"{ym_label(ty, tm, tkind)} 데이터가 없어 {ym_label(fy, fm, fb_kind)} 기준으로 비교합니다.",
            )
    return ty, tm, tkind, f"{ym_label(ty, tm, tkind)} 데이터가 없어 전실행 대비 증감을 낼 수 없습니다."


def prior_spec(db: Session, year: int, month: int) -> tuple[int, int, str, str]:
    """전년 = 전년 12월 실적. 없으면 최근 실적 → 최근 속보로 물러나고 그 사실을 문장으로 밝힌다
    ("전년 12월 실적이 없어 26.11 속보 기준으로 비교합니다.")."""
    ty, tm, tkind = year - 1, 12, "actual"
    if has_cost_data(db, tkind, yyyymm(ty, tm)):
        return ty, tm, tkind, ""

    cur_yyyymm = yyyymm(year, month)
    for fb_kind in ("actual", "forecast"):
        latest = latest_cost_yyyymm(db, fb_kind, before=cur_yyyymm)
        if latest:
            fy, fm = int(latest[:4]), int(latest[4:6])
            return (
                fy,
                fm,
                fb_kind,
                f"전년 12월 실적이 없어 {ym_label(fy, fm, fb_kind)} 기준으로 비교합니다.",
            )
    return ty, tm, tkind, "전년 12월 실적이 없어 전년 대비 증감을 낼 수 없습니다."


def plan_creation_prev(db: Session, year: int, plan_no: str) -> tuple[int, int, str, str]:
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
    if not has_cost_data(db, row.kind, yyyymm(row.year, row.month)):
        note = f"{ym_label(row.year, row.month, row.kind)}에 저장된 데이터가 없어 전실행 대비 증감을 낼 수 없습니다."
    return row.year, row.month, row.kind, note


def resolve_bases(
    db: Session, basis_year: int, basis_month: int, kind: str, plan_no: str | None = None
) -> VarianceBasisResult:
    revisions = plan_revisions(db, basis_year)
    default_plan = default_plan_no(revisions)
    chosen_plan = plan_no or default_plan
    if plan_no and all(r.plan_no != plan_no for r in revisions):
        raise HTTPException(404, f"{basis_year}년에 그런 경영계획 차수가 없다: {plan_no!r}")

    if kind == "plan":
        py, pm, pkind, pnote = plan_creation_prev(db, basis_year, chosen_plan)
    else:
        py, pm, pkind, pnote = prev_spec(db, basis_year, basis_month, kind)
    ry, rm, rkind, rnote = prior_spec(db, basis_year, basis_month)

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
            resolved_label=ym_label(py, pm, pkind) if pkind else "—",
            year=py,
            month=pm,
            kind=pkind,
            has_data=not pnote.endswith("낼 수 없습니다."),
            fallback_note=pnote,
        ),
        VarianceBasisOut(
            key="prior",
            label=BASE_LABELS["prior"],
            resolved_label=ym_label(ry, rm, rkind),
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
        basis_label=f"{basis_year}년 {plan_label}" if kind == "plan" else ym_label(basis_year, basis_month, kind),
        bases=bases,
        plan_revisions=revisions,
        default_plan_no=default_plan,
    )


# ---------- 버전 데이터 읽기 ----------
def load_cost_version(db: Session, version: str, yyyymm: str, lab_pks: set[str]) -> dict[str, dict[str, list]]:
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


def load_plan_version(db: Session, year: int, plan_no: str, lab_pks: set[str]) -> dict[str, dict[str, list]]:
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


def load_basis(db: Session, spec, lab_pks: set[str]) -> dict[str, dict[str, list]]:
    """비교 기준(또는 현재 버전) 한 쪽의 데이터. plan은 management_plan_entries, 나머지는 cost_entries."""
    year, month, kind, plan_no = spec
    if kind == "plan":
        return load_plan_version(db, year, plan_no, lab_pks)
    if not kind:
        return {}
    return load_cost_version(db, kind, yyyymm(year, month), lab_pks)


def active_labs(db: Session, basis_year: int, basis_month: int) -> dict[str, LabView]:
    """계산에 넣을 연구소 pk → 기준정보. 기준정보는 "그 버전 시점" 값으로 읽는다 — 통화를 지금
    값으로 보면 지난달에 보고한 환차 분해가 소급해서 달라진다(공통 RULE).

    합계 행(part_code="TOTAL")은 다른 행을 더해 만든 값이라 뺀다 — 넣으면
    SARC가 CPU+GPU+TOTAL로 두 번 세어져 금액이 정확히 두 배가 된다."""
    return {
        lab.pk: lab
        for lab in lab_views(db, f"{basis_year:04d}-{basis_month:02d}")
        if is_summable(lab)
    }


def scope_pks(labs: dict[str, LabView], lab_scope: str) -> list[str]:
    scope = (lab_scope or "ALL").strip()
    if scope.upper() == "ALL":
        return sorted(labs)
    pks = sorted(pk for pk, lab in labs.items() if pk == scope or lab.lab_code == scope)
    if not pks:
        raise HTTPException(404, f"그런 연구소가 없다: {scope!r}")
    return pks



def fx_specs(cur_spec, base_spec) -> tuple[tuple[int, int, str], tuple[int, int, str]]:
    """환율 기준(연·월·forecast|actual) 두 쌍. 경영계획은 속보/실적 환율 기준이 없어서, 그쪽을
    비교할 때는 "지금 버전의 환율"을 양쪽에 똑같이 써서 환차를 0으로 둔다 — 경영계획 금액은 애초에
    그때의 환율 가정으로 만든 값이라 환차를 가를 근거가 없다. 화면도 같은 제약을 둔다."""
    cy, cm, ckind, _ = cur_spec
    by, bm, bkind, _ = base_spec
    cur_fx = (cy, cm, ckind if ckind in ("forecast", "actual") else "forecast")
    base_fx = (by, bm, bkind) if bkind in ("forecast", "actual") else cur_fx
    return cur_fx, base_fx



@dataclass(frozen=True)
class TableInputs:
    """집계표·인건비율·히어로 카드가 쓰는 "어느 버전의 무엇을 그릴 것인가" 묶음.

    표마다 따로 읽으면 같은 화면의 표들이 서로 다른 버전·환율을 보게 된다 — 한 번 풀어서 돌려 쓴다.
    """

    basis_year: int
    basis_month: int          # 보고 있는 버전의 기준월(호출부가 안 줬으면 최신 속보월)
    kind: str                 # 읽은 버전의 종류: forecast | actual | plan
    plan_no: str              # 경영계획이면 차수, 아니면 빈 문자열
    label: str                # "26.09 속보" 같은 사람이 읽는 이름
    api_version: str          # 추가 행·환율을 읽을 슬롯(forecast | actual)
    extras_yyyymm: str        # 그 슬롯의 연월("YYYYMM")
    labs: dict                # 계산에 넣을 연구소 pk → 기준정보(합계 시트 제외)
    pks: list                 # labs의 키를 정렬한 목록
    d: dict                   # 연구소 pk → 비용항목 → 12개월($)
    extras: dict              # 추가 행 — 연구소 pk → 얹힐 계정 키 → 12개월($)
    extra_rows: list          # 추가 행 원본(파생 행 포함) — 파생 열 계산이 행 단위로 다시 본다
    rate: list | None         # 원/$ 12개월. with_rate=False면 None(달러 표만 그릴 때)


def table_inputs(
    db: Session,
    basis_year: int,
    version: str,
    with_rate: bool,
    basis_month: int | None = None,
    kind: str = "forecast",
    plan_no: str | None = None,
) -> TableInputs:
    """어느 버전의 표인가를 (basis_year, basis_month, kind[, plan_no])로 **받아서** 읽는다.

    버전 해석(전실행이 무엇인가 · 경영계획 몇 차수인가)은 증감분석이 쓰는 층을 그대로 쓴다
    (resolve_bases · load_basis). 두 화면이 "전실행"을 다르게 풀면 같은 자리에서 다른 숫자가 나온다.

    basis_month를 안 주면 DB의 최신 속보월로 떨어진다.
    """
    if version not in ("cur", "prev", "prior", "plan"):
        raise HTTPException(400, "version은 cur | prev | prior | plan만 쓸 수 있다.")
    check_kind(kind)
    month = basis_month or current_basis_month(db) or 1
    if not 1 <= month <= 12:
        raise HTTPException(400, "basis_month는 1~12여야 한다.")

    resolved = resolve_bases(db, basis_year, month, kind, plan_no)
    if version == "cur":
        # 보고 있는 버전 그대로. kind=plan이면 요청한 차수를 쓴다 — 기본 차수로 풀면 P01을 달라고
        # 해도 최종 차수 표가 나간다.
        chosen_plan = next(b.plan_no for b in resolved.bases if b.key == "plan")
        spec = (
            (basis_year, month, "plan", chosen_plan)
            if kind == "plan"
            else (basis_year, month, kind, "")
        )
        label = resolved.basis_label
    else:
        basis = next(b for b in resolved.bases if b.key == version)
        if not basis.has_data:
            raise HTTPException(404, basis.fallback_note or f"{basis.label} 데이터가 없다.")
        spec = (basis.year, basis.month, basis.kind, basis.plan_no)
        label = basis.resolved_label

    # 기준정보(통화·파트 구성)는 **보고 있는 버전 시점** 값으로 읽는다 — 지금 값으로 보면 지난달
    # 보고서를 다시 띄웠을 때 숫자가 소급해서 달라진다.
    labs = active_labs(db, basis_year, month)
    pks = scope_pks(labs, "ALL")

    # 추가 행·환율은 그 버전이 저장된 슬롯에서 읽는다. 경영계획은 속보/실적 슬롯이 없어서 추가 행도
    # 환율도 보고 있는 버전(속보) 것을 쓴다(services/variance.fx_specs와 같은 제약).
    spec_year, spec_month, spec_kind, _ = spec
    api_version = spec_kind if spec_kind in ("forecast", "actual") else "forecast"
    fx_year, fx_month = (
        (spec_year, spec_month) if spec_kind in ("forecast", "actual") else (basis_year, month)
    )
    extras_yyyymm = yyyymm(fx_year, fx_month)
    # 원본 목록과 "계정에 얹은 묶음"을 같이 들고 간다 — 집계 합계는 묶음(extras)을 쓰고, 파생 열은
    # 행 하나하나(라벨·파생 여부·근거)를 봐야 해서 원본이 필요하다. 두 번 읽으면 그 사이 저장이
    # 끼었을 때 같은 표의 합계와 열이 서로 다른 재료를 보게 된다.
    extra_rows = read_extra_rows(db, yyyymm=extras_yyyymm, version=api_version)
    extras = extra_row_months(extra_rows)

    d = load_basis(db, spec, set(pks))

    rate = rate_arrays(db, fx_year, fx_month, api_version, set(), tuple(range(12)))["USD"] if with_rate else None

    return TableInputs(
        basis_year=basis_year, basis_month=month, kind=spec_kind, plan_no=spec[3], label=label,
        api_version=api_version, extras_yyyymm=extras_yyyymm,
        labs=labs, pks=pks, d=d, extras=extras, extra_rows=extra_rows, rate=rate,
    )
