"""인건비 증감을 인원 효과 · 단가 효과로 나눈다 — 화면 core.js의 ovLaborSplit과 같은 식.

준비(`prepare`)와 기간별 계산(`split_for`)이 갈라져 있다 — `GET /overseas/labor-split`은 한
기간만 쓰고, 증감분석 표는 준비를 한 번만 하고 17기간을 돈다.

**두 경로가 같은 함수를 쓰는 게 핵심이다.** 식이 두 벌이 되면 화면과 챗봇이 다른 답을 한다.

식:
  환차外 = 당월인건비@비교환율 − 비교인건비@비교환율
         = (당월인원 − 비교인원) × 비교단가  +  당월인원 × (당월단가 − 비교단가)
           └──── 인원 효과 ────┘      └───── 단가 효과 ─────┘
비교 버전 환율로 환산해야 환율이 움직인 몫이 빠진다(= 환차外 기준). 인력을 안 낸 연구소는 단가를
낼 수 없어 그 몫을 `unsplit`으로 따로 뺀다 — 인원·단가에 섞으면 단가가 왜곡된다.
"""

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.accounts.overseas.cost_groups import resolve_leaves
from app.accounts.overseas.schemas import LaborSplitLab, LaborSplitResult
from app.accounts.overseas.services.entries import current_basis_month
from app.accounts.overseas.services.fxcalc import rate_arrays
from app.accounts.overseas.services.versions import (
    active_labs,
    check_kind,
    load_basis,
    resolve_bases,
    ym_label,
)
from app.shared.core.values import n

# 인건비 리프 — 공통코드(OVERSEAS_CATEGORY)에서 labor의 하위를 그때그때 펼친다.
# 손으로 적어 두면 공통코드에 인건비 하위 계정(예: 퇴직급여)이 하나 늘었을 때 집계의 인건비 행은
# 그걸 더하는데 이 분해(환차/환차外)는 빼놓아서, 두 숫자가 경고도 없이 어긋난다.
def labor_leaves() -> list[str]:
    return resolve_leaves("labor")

BASE_LABELS = {"prev": "전실행", "prior": "전년 실적", "plan": "경영계획"}


@dataclass(frozen=True)
class LaborSplitInputs:
    """기간과 무관한 준비물. 기간마다 다시 만들면 같은 DB 읽기를 17번 한다."""

    basis_year: int
    basis_month: int
    base_version: str
    scope: str
    labs: dict
    pks: list[str]
    cur_d: dict
    base_d: dict
    cur_spec: tuple[int, int, str]
    base_spec: tuple[int, int, str]
    local_curs: set[str]


def prepare(
    db: Session,
    basis_year: int,
    base_version: str,
    lab_scope: str,
    basis_month: int | None = None,
    kind: str = "forecast",
    plan_no: str | None = None,
) -> LaborSplitInputs:
    """기간과 무관한 준비물. basis_month · kind가 "어느 버전을 보고 있는가"다.

    버전 해석은 집계·증감과 같은 층(services/versions.py)을 쓴다 — 안 주면 최신 속보로 떨어진다.
    여기서 따로 고르면 과거 버전을 띄웠을 때 분해만 최신 속보 기준이 된다.
    """
    if base_version not in BASE_LABELS:
        raise HTTPException(400, "base_version은 prev(전실행) | prior(전년 실적) | plan(경영계획)만 쓸 수 있다.")
    check_kind(kind)
    month = basis_month or current_basis_month(db) or 1
    if not 1 <= month <= 12:
        raise HTTPException(400, "basis_month는 1~12여야 한다.")

    resolved = resolve_bases(db, basis_year, month, kind, plan_no)
    basis = next(b for b in resolved.bases if b.key == base_version)

    # 합계 행(part_code="TOTAL")과 원화 기타(_ETC)는 다른 행을 더해 만든 값이라 빼야 한다 — 넣으면
    # SARC가 CPU+GPU+TOTAL로 두 번 세어져 인원과 금액이 정확히 두 배가 된다(실제로 그렇게 나왔다).
    # 기준정보는 "보고 있는 버전 시점" 값으로 읽는다 — 통화를 지금 값으로 보면 지난달에 보고한
    # 환차 분해가 소급해서 달라진다(공통 RULE: 기준정보 변경은 변경한 버전부터).
    labs = active_labs(db, basis_year, month)
    basis_month = month
    cur_spec_full = (
        (basis_year, month, "plan", resolved.default_plan_no)
        if kind == "plan"
        else (basis_year, month, kind, "")
    )
    cur_d = load_basis(db, cur_spec_full, set(labs))
    base_d = load_basis(db, (basis.year, basis.month, basis.kind, basis.plan_no), set(labs))
    if not cur_d:
        raise HTTPException(404, f"{resolved.basis_label} 데이터가 없다.")
    if not base_d:
        raise HTTPException(
            404,
            basis.fallback_note
            or f"{BASE_LABELS[base_version]} 데이터가 저장돼 있지 않아 인건비 증감을 나눌 수 없다.",
        )
    scope = (lab_scope or "ALL").strip()
    if scope.upper() == "ALL":
        pks = [pk for pk in labs if pk in cur_d or pk in base_d]
    else:
        # "SARC"처럼 파트 없는 코드를 주면 그 연구소의 파트를 모두 더한다.
        pks = [pk for pk, lab in labs.items() if pk == scope or lab.lab_code == scope]
        if not pks:
            raise HTTPException(404, f"그런 연구소가 없다: {scope!r}")

    # 환율 기준(연·월·속보실적). 경영계획은 속보/실적 환율 기준이 없어 환차·환차外를 나눌 수
    # 없다 — 한쪽에라도 끼면 막는다(화면도 같은 제약이고, 증감 표는 그 경우 분해를 싣지 않는다).
    if kind == "plan" or basis.kind == "plan":
        raise HTTPException(
            400, "경영계획 대비는 환율 기준(속보/실적)이 없어 인원·단가로 나눌 수 없다. 화면 표를 보라."
        )
    cur_spec = (basis_year, month, kind)
    base_spec = (basis.year, basis.month, basis.kind)

    # 현지통화 → $ 환차를 따로 나누는 통화. core.js의 ovLcOf와 똑같이 "통화가 USD가 아니면 현지통화"로
    # 본다 — CP도 통화가 ILS라 여기 포함된다.
    local_curs = {
        lab.currency for pk, lab in labs.items()
        if pk in pks and lab.currency and lab.currency != "USD"
    }
    return LaborSplitInputs(
        basis_year, basis_month, base_version, scope, labs, pks, cur_d, base_d,
        cur_spec, base_spec, local_curs,
    )


def split_for(db: Session, inp: LaborSplitInputs, period: str, ix: tuple[int, ...]) -> LaborSplitResult:
    """한 기간의 분해. 환율은 기간에 드는 달만 보므로 기간마다 다시 읽는다."""
    rate_cur = rate_arrays(db, *inp.cur_spec, inp.local_curs, ix)
    rate_base = rate_arrays(db, *inp.base_spec, inp.local_curs, ix)
    leaves = labor_leaves()

    rows: list[LaborSplitLab] = []
    tot = {"hc_cur": 0.0, "hc_base": 0.0, "amt_cur": 0.0, "amt_base": 0.0, "unsplit": 0.0}
    for pk in sorted(inp.pks):
        lab = inp.labs[pk]
        cats_cur, cats_base = inp.cur_d.get(pk, {}), inp.base_d.get(pk, {})
        lc = lab.currency if lab.currency and lab.currency != "USD" else None
        amt_cur = amt_base = hc_cur = hc_base = 0.0
        for j in ix:
            k_cur = sum(n(cats_cur.get(leaf, [None] * 12)[j]) for leaf in leaves)
            k_base = sum(n(cats_base.get(leaf, [None] * 12)[j]) for leaf in leaves)
            rb = rate_base["USD"][j]
            lrc = rate_cur.get(lc, [0.0] * 12)[j] if lc else 0.0
            lrb = rate_base.get(lc, [0.0] * 12)[j] if lc else 0.0
            # 비교 버전 환율로 환산해야 환율이 움직인 몫이 빠진다(= 환차外 기준).
            if lc and lrc > 0 and lrb > 0:
                amt_cur += k_cur * lrc / lrb * rb
            else:
                amt_cur += k_cur * rb
            amt_base += k_base * rb
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
        basis_year=inp.basis_year, basis_month=inp.basis_month, kind=inp.cur_spec[2],
        base_label=ym_label(*inp.base_spec), period=period, base_version=inp.base_version,
        lab_scope=inp.scope,
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
