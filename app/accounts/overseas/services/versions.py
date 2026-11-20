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

from app.shared.core.periods import yyyymm
from app.accounts.overseas.services.entries import (
    entry_months_bulk,
    pick_plan_no,
    plan_entry_months_bulk,
)
from app.accounts.overseas.services.labs import ACCOUNT_KEY, LabView, lab_views
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
def has_cost_data(db: Session, version: str, yyyymm: str) -> bool:
    return bool(
        db.scalar(
            select(CostEntry.id)
            .where(CostEntry.account_code == ACCOUNT_KEY, CostEntry.version == version, CostEntry.yyyymm == yyyymm)
            .limit(1)
        )
    )


def latest_cost_yyyymm(db: Session, version: str, before: str | None = None) -> str | None:
    """그 버전 종류에서 데이터가 실제로 들어있는 가장 최근 편성 연월. before를 주면 그보다 앞선 것만.
    yyyymm이 "YYYYMM" 고정폭이라 문자열 최댓값이 곧 날짜순 최댓값이다."""
    stmt = select(func.max(CostEntry.yyyymm)).where(
        CostEntry.account_code == ACCOUNT_KEY, CostEntry.version == version
    )
    if before is not None:
        stmt = stmt.where(CostEntry.yyyymm < before)
    return db.scalar(stmt)


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

    합계 행(part_code="TOTAL")과 원화 기타(_ETC)는 다른 행을 더해 만든 값이라 뺀다 — 넣으면
    SARC가 CPU+GPU+TOTAL로 두 번 세어져 금액이 정확히 두 배가 된다."""
    return {
        lab.pk: lab
        for lab in lab_views(db, f"{basis_year:04d}-{basis_month:02d}")
        if lab.is_active and lab.pk != "_ETC" and (lab.part_code or "").upper() != "TOTAL"
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

