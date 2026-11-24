"""해외연구소 증감분석 도메인 로직 — 비교 기준 해석 · 증감 표 · 증감사유 · 증감 동인 · 실적 1차/2차.

증감 계산은 증감분석 화면·집계 사전점검·결산이 모두 같은 숫자를 봐야 하는 공용 도메인이다 —
판정을 두 군데서 따로 하면 같은 상황에서 다른 건수를 보여준다. 가르는 기준은 **@router가 붙은
라우트 핸들러만 엔드포인트에 남기고 나머지 전부 여기로**다.

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
import re
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.accounts.overseas.cost_groups import (
    AGGREGATE_ROWS,
    CATEGORY_LABELS,
    CATEGORY_LABELS_EN,
    GROUP_CALC,
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
from app.accounts.overseas.services.aggregate import (
    ANCHORABLE,
    NO_ANCHOR,
    extra_row_months,
    extra_total,
    group_tree,
)
from app.accounts.overseas.services.extra_rows import read_extra_rows
from app.accounts.overseas.services.fxcalc import rate_arrays
from app.accounts.overseas.services.labor_split import (
    prepare as labor_split_prepare,
    split_for as labor_split_for,
)
from app.accounts.overseas.services import reason_draft
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
    VarianceExtraCell,
    VarianceExtraRow,
    VarianceReasonCheck,
    VarianceReasonDraftIn,
    VarianceReasonDraftResult,
    VarianceReasonGap,
    VarianceReasonIn,
    VarianceReasonOut,
    VarianceResult,
    LaborSplitLab,
    LaborSplitTotals,
    VarianceLabCell,
    VarianceLabRow,
    VarianceLabsResult,
    VarianceTable,
    VarianceRow,
)

# 쿼리 파라미터로 받고 기본값만 여기 둔다(화면도 이 값을 받아서 표의 경고 표시 기준으로 쓴다).
DEFAULT_REASON_THRESHOLD_PCT = 10.0
# 금액 기준의 기본값은 0(= 금액으로는 안 걸러낸다)이다. 비율만 쓰던 동작을 그대로 두기 위한
# 값이지, "0원 이상이면 다 본다"는 뜻이 아니다 — 담당자가 "1억 이상만"이라고 말할 때 켜진다.
DEFAULT_REASON_THRESHOLD_AMOUNT = 0.0
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
    cur_extras: dict | None = None,
    base_extras: dict | None = None,
    rates: tuple[dict[str, list[float]], dict[str, list[float]]] | None = None,
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
    if rates is not None:
        rate_cur, rate_base = rates
    else:
        rate_cur = rate_arrays(db, *cur_fx, local_curs, ix)
        rate_base = rate_arrays(db, *base_fx, local_curs, ix) if split_fx else rate_cur
    cur_extras = cur_extras or {}
    base_extras = base_extras or {}

    # 화면이 그리는 행 전부를 계산한다 — 참고 행(IT 관련 비용 · 외주용역비 7개)까지.
    # VARIANCE_ROWS(38행)만 돌면 그 7개가 빠지고, 증감분석 화면은 집계표와 같은 45행을 그리므로
    # 화면이 서버 값을 쓸 때 그 줄이 빈칸으로 남는다. 두 표가 같은 행 목록을 쓰는 게 맞다.
    leaves = {key: resolve_leaves(key) for key, _, _, _ in AGGREGATE_ROWS if key != "pct"}
    # 집계표(agg_months)가 추가 행을 그 행이 얹힌 집계에 더한다 — 여기서도 같이 더해야
    # 같은 화면의 위(연구소별 요약)와 아래(계정별 상세)가 같은 숫자를 보여준다.
    trees = {key: group_tree(key) for key in leaves}
    out = {key: {"cur": 0.0, "base": 0.0, "fx_local": 0.0, "fx_usd": 0.0, "fx_outside": 0.0} for key in leaves}

    for pk in pks:
        lab = labs[pk]
        lc = lab.currency if (split_fx and lab.currency and lab.currency != "USD") else None
        cats_cur, cats_base = cur_d.get(pk, {}), base_d.get(pk, {})
        for key, keys in leaves.items():
            cell = out[key]
            tree = trees[key]
            ex_cur, ex_base = cur_extras.get(pk, {}), base_extras.get(pk, {})
            for j in ix:
                uc = sum(n(month_value(cats_cur, leaf, j)) for leaf in keys)
                ub = sum(n(month_value(cats_base, leaf, j)) for leaf in keys)
                if key == "hc":
                    # 인력은 금액이 아니라 명(月 평균)이다 — 환율을 곱하지 않고 기간 평균을 낸다.
                    cell["cur"] += uc / len(ix)
                    cell["base"] += ub / len(ix)
                    continue
                # 인력(hc)은 위에서 빠진 뒤다 — 추가 행은 금액 행에만 더한다.
                uc += extra_total(ex_cur, tree, j)
                ub += extra_total(ex_base, tree, j)
                a = fx_amounts(uc, ub, lc, j, rate_cur, rate_base)
                for f in ("cur", "base", "fx_local", "fx_usd", "fx_outside"):
                    cell[f] += a[f]
    return out


def fx_amounts(
    uc: float, ub: float, lc: str | None, j: int, rate_cur: dict, rate_base: dict
) -> dict[str, float]:
    """$ 금액 두 쪽(당월 uc · 비교 기준 ub)을 원화 금액과 환차 분해로 바꾼다. 식은 variance_cells
    docstring의 ①②③ 그대로다.

    고정 계정(variance_cells)과 추가 행(extra_variance_rows)이 **같은 함수**를 써야 두 숫자가
    어긋나지 않는다 — 추가 행 금액은 고정 계정 합계에 이미 녹아 있으므로, 분해 규칙이 갈리면
    "합계의 환차"와 "그 합계를 이루는 행들의 환차 합"이 달라진다.
    """
    rc, rb = rate_cur["USD"][j], rate_base["USD"][j]
    lrc = rate_cur.get(lc, [0.0] * 12)[j] if lc else 0.0
    lrb = rate_base.get(lc, [0.0] * 12)[j] if lc else 0.0
    # 저장값이 $라 환율만 곱하면 원화다(업로드가 K$ 셀을 ×1000해서 $로 담는다).
    out = {"cur": uc * rc, "base": ub * rb, "fx_local": 0.0, "fx_usd": 0.0, "fx_outside": 0.0}
    if lc and lrc > 0 and lrb > 0:
        local_cur = uc * lrc
        local_base = ub * lrb
        out["fx_local"] = (local_cur / lrc - local_cur / lrb) * rc
        out["fx_usd"] = (local_cur / lrb) * (rc - rb)
        out["fx_outside"] = ((local_cur - local_base) / lrb) * rb
    else:
        out["fx_usd"] = uc * (rc - rb)
        out["fx_outside"] = (uc - ub) * rb
    return out


def to_dollar(local_amount: float, local_rate: float) -> float:
    """현지통화 금액 → $. 현지 환율(현지통화/$)이 없으면 0으로 둔다 — 환율을 모르는 채 1:1로
    넘기면 ILS 금액이 $ 금액으로 들어가 틀린 숫자가 화면에 뜬다.

    비용 데이터가 전부 $ 단위라 1차 환산 결과도 거기에 맞춰야 2차와 더하거나 비교할 수 있다.
    local_amount는 현지통화의 1단위(ILS 1, INR 1)로 저장한다."""
    return (local_amount / local_rate) if local_rate > 0 else 0.0


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


def extra_variance_rows(st: "_VarianceSetup", rates_by_period: dict) -> list[VarianceExtraRow]:
    """연구소가 직접 추가한 행들의 증감(17기간) — 고정 45행에는 이름이 없는 그 줄들이다.

    당월에만 생긴 행은 비교 기준 쪽이 없어 base가 0이고 증감이 전액 들어간다(반대도 같다 — 비교
    기준에만 있던 행은 cur가 0이라 증감이 음수다).

    **줄은 (얹힌 계정, 이름)으로 묶는다** — 집계표 ① 전체 계정 탭과 같은 규칙(core.js byLabel).
    연구소마다 row_key가 달라서(ux_cloudfee · cloud_fee_dsra) 키로 묶으면 같은 '클라우드 사용료'가
    연구소 수만큼 줄로 서고, 연구소를 바꿔 볼 때마다 표의 줄 목록이 달라진다. 이름으로 묶으면
    어느 범위로 보든 줄 목록이 같고 금액만 달라진다. 이름이 같아도 얹힌 계정이 다르면 다른 줄이다 —
    각 줄이 자기 계정 합계와 맞아야 한다.

    줄 목록은 **전체 연구소**에서 모으고 금액만 조회 범위(st.pks)로 더한다 — 한 연구소가 추가한
    행도 다른 연구소 표에 같은 자리에 (0원으로) 서야 "이 연구소는 이 줄이 없다"가 읽힌다.

    파생 행(is_derived)은 뺀다 — 저장된 금액이 아니라 다른 행으로 계산한 값이라, 금액 증감으로
    읽으면 같은 돈을 두 번 세게 된다.

    금액·환차 분해는 고정 계정과 **같은 함수**(fx_amounts)로 낸다. 환차는 연구소 통화마다 달라서
    연구소별로 낸 뒤 더한다. 분기·연간도 고정 계정과 같이 월을 더해서 만든다 — 연구소가 적어 보낸
    분기 칸(per)을 쓰면 이 줄의 합이 얹힌 계정 합계와 어긋난다(그 합계도 월 합으로 만들어진다).
    """
    def anchor_eff(key: str) -> str:
        """표가 그 행을 실제로 접어 넣는 자리(aggregate.extra_row_months와 같은 판정) — 쓸 수 없는
        키는 기타경비(misc)로 떨어진다."""
        if key == NO_ANCHOR:
            return NO_ANCHOR
        return key if key in ANCHORABLE else "misc"

    def group_key(r) -> tuple[str, str]:
        return (anchor_eff(r.anchor_key or ""), (r.label or "").strip() or "(이름 없음)")

    # gkey -> {"label_en", "row_keys", "cur": {lab: [행]}, "base": {lab: [행]}}
    groups: dict[tuple[str, str], dict] = {}
    for which, src in (("cur", st.cur_extra_rows), ("base", st.base_extra_rows)):
        for r in src:
            # st.labs = 합산 대상 연구소 전부(조회 범위가 아니다) — 줄 목록을 여기서 모은다.
            if r.is_derived or r.lab_pk not in st.labs:
                continue
            g = groups.setdefault(group_key(r), {"label_en": "", "row_keys": [], "cur": {}, "base": {}})
            if not g["label_en"] and r.label_en:
                g["label_en"] = r.label_en
            if r.row_key not in g["row_keys"]:
                g["row_keys"].append(r.row_key)
            g[which].setdefault(r.lab_pk, []).append(r)

    out: list[VarianceExtraRow] = []
    for (anchor, label), g in groups.items():
        in_total = anchor != NO_ANCHOR
        labs_here = [pk for pk in st.pks if pk in g["cur"] or pk in g["base"]]
        cells: dict[str, VarianceExtraCell] = {}
        for period, ix in PERIOD_IX.items():
            rate_cur, rate_base = rates_by_period[period]
            acc = {"cur": 0.0, "base": 0.0, "fx_local": 0.0, "fx_usd": 0.0, "fx_outside": 0.0}
            for pk in labs_here:
                lab = st.labs[pk]
                lc = lab.currency if (st.split_fx and lab.currency and lab.currency != "USD") else None
                for j in ix:
                    uc = sum(n(r.v[j]) for r in g["cur"].get(pk, []) if r.v and j < len(r.v))
                    ub = sum(n(r.v[j]) for r in g["base"].get(pk, []) if r.v and j < len(r.v))
                    a = fx_amounts(uc, ub, lc, j, rate_cur, rate_base)
                    for f in acc:
                        acc[f] += a[f]
            cells[period] = VarianceExtraCell(
                base=acc["base"], cur=acc["cur"], diff=acc["cur"] - acc["base"],
                fx=acc["fx_local"] + acc["fx_usd"], fx_local=acc["fx_local"],
                fx_usd=acc["fx_usd"], fx_outside=acc["fx_outside"],
            )
        out.append(VarianceExtraRow(
            label=label, label_en=g["label_en"],
            anchor_key=anchor, anchor_label=CATEGORY_LABELS.get(anchor, ""),
            in_total=in_total, labs=labs_here, row_keys=g["row_keys"], cells=cells,
        ))
    return out


@dataclass(frozen=True)
class _VarianceSetup:
    """기간과 무관한 준비물. 17기간 표(compute_variance_table)는 이걸 한 번만 만들고 기간만
    바꿔 가며 계산한다 — 기간마다 다시 만들면 같은 DB 읽기를 17번 한다."""

    labs: dict
    pks: list[str]
    cur_d: dict
    base_d: dict
    # 추가 행(사용자가 [+ 행 추가]로 만든 행)의 월 값 — {연구소 pk: {얹힐 계정 키: 12개월}}.
    # 집계표가 더하는 값이라 증감분석도 같이 더한다(variance_cells 주석 참고).
    cur_extras: dict
    base_extras: dict
    # 같은 추가 행을 **접지 않은 채** 들고 있는 목록. 위 두 개는 얹힌 계정별로 합쳐 버려서
    # "어느 행이 새로 생겼나"를 알 수 없다 — 그 줄을 이름째로 보여주려면 원본이 필요하다.
    cur_extra_rows: list
    base_extra_rows: list
    cur_fx: tuple
    base_fx: tuple
    split_fx: bool
    basis: object
    plan_no: str
    note: str


def _extra_rows_at(db: Session, spec, basis_year: int, basis_month: int) -> list:
    """그 버전 슬롯의 추가 행 목록(접지 않은 원본).

    집계표가 쓰는 경로(services/versions.table_inputs)와 같은 규칙으로 읽는다: 경영계획은 추가 행을
    담는 슬롯이 없어서 보고 있는 버전(속보)의 행을 쓴다.
    """
    spec_year, spec_month, spec_kind, _ = spec
    if not spec_kind:
        return []
    version = spec_kind if spec_kind in ("forecast", "actual") else "forecast"
    year, month = (
        (spec_year, spec_month) if spec_kind in ("forecast", "actual") else (basis_year, basis_month)
    )
    return read_extra_rows(db, yyyymm=yyyymm(year, month), version=version)


def _extras_at(db: Session, spec, basis_year: int, basis_month: int) -> dict:
    """그 버전 슬롯의 추가 행 월 값 — {연구소 pk: {얹힐 계정 키: 12개월}}."""
    return extra_row_months(_extra_rows_at(db, spec, basis_year, basis_month))


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
    # 추가 행은 그 버전이 저장된 슬롯에서 읽는다 — 집계표(versions.table_inputs)와 같은 규칙이다.
    # 접은 값(얹힌 계정별 합)과 원본 목록을 둘 다 들고 간다 — 한 번 읽어 두 군데에 쓴다.
    cur_extra_rows = _extra_rows_at(db, cur_spec, basis_year, basis_month)
    base_extra_rows = _extra_rows_at(db, base_spec, basis_year, basis_month)
    cur_extras = extra_row_months(cur_extra_rows)
    base_extras = extra_row_months(base_extra_rows)
    # 고정 45행이 비어도 추가 행이 올라온 달은 "데이터 있는 달"로 본다 — 그 금액도 연구소가 올린
    # 값이고 표가 추가 행 줄에 그대로 보여준다. 존재 판정을 기준월 판정(entries.has_summable_rows)과
    # 같은 뜻으로 맞추는 것이다: 한쪽은 "있다", 계산은 "없다"고 답하면 화면이 열리고 나서 404가 난다.
    if not cur_d and not cur_extras:
        raise HTTPException(404, f"{resolved.basis_label} 데이터가 없다.")
    # 비교 기준 쪽도 같이 본다. 여기가 비면 증감이 **전액 신규**로 찍힌 표가 조용히 그려진다 —
    # 404도 안 뜨고 숫자만 틀리는 쪽이 더 위험하다(basis.has_data는 그 달에 행이 있나만 보고,
    # 합산 대상 연구소 범위까지는 보지 않는다).
    if not base_d and not base_extras:
        raise HTTPException(
            404, f"{basis.label}({basis.resolved_label}) 데이터가 없어 증감을 낼 수 없다."
        )

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
        labs, pks, cur_d, base_d, cur_extras, base_extras, cur_extra_rows, base_extra_rows,
        cur_fx, base_fx, split_fx, basis, basis.plan_no, note
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
        db, st.labs, st.pks, st.cur_d, st.base_d, ix, st.cur_fx, st.base_fx, st.split_fx,
        st.cur_extras, st.base_extras,
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
    # variance_reasons·variance_drivers는 전 계정 공용 테이블이다 —
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


def cache_translation(db: Session, source: str, ko: str, engine: str, guide_hash: str = "") -> None:
    """번역 1건을 /overseas/reason-translate와 같은 자리에 캐시한다 — 두 경로가 따로 캐시하면 같은
    원문이 두 번역을 갖게 되어 "원문 1건 = 번역 1개"가 깨진다. ko_text가 String(600)이라 길면 자른다.

    guide_hash는 "어느 번역 지침으로 만든 번역인가"다. 같이 적어 두지 않으면 담당자가 지침을 바꿔도
    캐시가 그대로 맞아서 옛 문장이 계속 나온다(models/generic.TextTranslation.guide_hash 참고)."""
    text = ko[:600]
    stmt = pg_insert(TextTranslation).values(
        source_hash=source_hash(source), source_text=source, ko_text=text, engine=engine,
        guide_hash=guide_hash,
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[TextTranslation.source_hash],
            set_={"ko_text": text, "engine": engine, "guide_hash": guide_hash, "updated_at": func.now()},
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
    """그 연월 해외연구소의 결산 단계 행. "1차 완료"는 mapping_done 하나가 진실이다."""
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
    threshold_amount: float = DEFAULT_REASON_THRESHOLD_AMOUNT,
) -> VarianceReasonCheck:
    """확정 직전 경고 — 증감이 기준치를 넘었는데 사유가 비었거나 재확인이 필요한 칸.

    기준이 둘이다: 증감률(threshold_pct, %)과 증감액(threshold_amount, 원). **둘 다 넘을 때만**
    걸린다 — 이상 감지의 급변 기준과 같은 결합이다(anomaly.py: 비율만 보면 잡비 $1K → $2K가
    +100%로 걸려서 경고가 수십 개 뜨고, 그러면 아무도 안 읽는다).

    그래서 "1억 이상 변동만 사유 써줘"는 좁히는 요청으로 성립한다(증감률 10% **그리고** 1억 이상).
    금액만으로 보고 싶으면 threshold_pct=0으로 비율 조건을 끈다 — 그러면 5%짜리 50억 변동도 걸린다.

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
        # 증감액은 None일 수 있다(비교 기준에 값이 없는 행) — 그때는 금액으로 걸러내지 않는다.
        # 여기서 continue하면 "비교값이 없어서 증감을 모르는" 행이 조용히 점검에서 빠진다.
        if threshold_amount and row.diff is not None and abs(row.diff) < threshold_amount:
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

    # 어떤 기준으로 걸러낸 결과인지 문장에 그대로 밝힌다 — 담당자가 "1억 이상만"이라고 바꿔 놓고
    # 잊으면 "사유 다 썼다"는 문장을 전체 기준으로 읽는다(실제로는 1억 미만이 빠져 있다).
    crit = []
    if threshold_pct:
        crit.append(f"증감률 {threshold_pct:g}% 이상")
    if threshold_amount:
        crit.append(f"증감액 {threshold_amount / 1e8:,.1f}억원 이상")
    scope = " 그리고 ".join(crit) if crit else "모든"

    parts = []
    if missing:
        parts.append(f"{scope}인데 사유가 없는 항목 {len(missing)}건")
    if stale:
        parts.append(f"사유 저장 뒤 금액이 바뀌어 재확인이 필요한 항목 {len(stale)}건")
    note = (
        " · ".join(parts) + " — 확정을 막지는 않지만, 이대로 확정하면 사유 없이 보고된다."
        if parts
        else f"{scope}인 항목은 모두 사유가 적혀 있다."
    )
    return VarianceReasonCheck(
        threshold_pct=threshold_pct, threshold_amount=threshold_amount,
        missing=missing, stale=stale, note=note,
    )


def _non_labor_keys() -> tuple[str, ...]:
    """인건비外 줄에 적을 수 있는 계정 키 — 금액 행이고, 그룹 합계도 참고 행도 아니고, 인건비
    잎(급여 · 상여 · 복리후생)도 아닌 것. 화면 ovRsnDraft의 같은 필터와 맞춰 둔다.

    부를 때마다 만든다(모듈 상수로 굳히지 않는다) — 계정 목록은 공통코드로 갈아끼울 수 있어서
    (cost_groups.reload_from_rows) 굳혀 두면 코드를 추가 · 중지한 뒤에도 옛 목록을 쓴다.
    """
    return tuple(
        key for key, _level, unit, memo in AGGREGATE_ROWS
        if unit == "krw" and not memo and key not in GROUP_CALC
        and key not in reason_draft.NON_LABOR_EXCLUDE
    )


def _labor_local(
    labs: dict[str, LabView], pks: list[str], data: dict, extras: dict,
    rate: dict[str, list[float]], ix: tuple[int, ...],
) -> float:
    """인건비를 **현지통화**로 합친 금액(기간 합). 분석 초안의 "인당 인건비 $115K→$121K" 한
    조각에만 쓴다.

    왜 labor_split의 per_head를 안 쓰나: 그 값은 원화 기준이다(통화가 다른 연구소를 섞어 봐도
    말이 되게 — services/labor_split.py 주석). 초안 문장의 인당 인건비는 연구소 담당자가 자기
    통화로 읽는 값이라 현지통화로 적는다(화면 ovRsnDraft와 같은 기준).

    저장값이 $라 현지통화 환율(현지/$)을 곱하면 현지통화 금액이다 — 환율을 못 찾으면 $ 그대로
    둔다(없는 환율로 1:1 환산하면 INR 금액 자리에 $ 금액이 들어간다).
    """
    leaves, tree = resolve_leaves("labor"), group_tree("labor")
    total = 0.0
    for pk in pks:
        lab = labs[pk]
        lc = lab.currency if lab.currency and lab.currency != "USD" else None
        cats, extra = data.get(pk, {}), extras.get(pk, {})
        for j in ix:
            value = sum(n(month_value(cats, leaf, j)) for leaf in leaves) + extra_total(extra, tree, j)
            local_rate = rate.get(lc, [0.0] * 12)[j] if lc else 0.0
            total += value * (local_rate if local_rate > 0 else 1.0)
    return total


def _lab_currency(
    labs: dict[str, LabView], pks: list[str], rate: dict[str, list[float]]
) -> tuple[str, str]:
    """(표시용 통화, 현지통화 코드). 현지통화 환율을 못 찾으면 금액이 $로 남으므로 표시도 $다 —
    INR이 아닌 숫자에 "INR"을 붙이면 연구소가 자기 값으로 읽을 수 없다."""
    code = next((labs[pk].currency for pk in pks if labs[pk].currency), "") or "USD"
    if code == "USD":
        return "$", ""
    rates = rate.get(code) or []
    return (code, code) if any(r > 0 for r in rates) else ("$", "")


def _lab_draft(
    st: _VarianceSetup,
    gpks: list[str],
    cells: dict[str, dict[str, float]],
    sp: LaborSplitLab | None,
    rates: tuple[dict[str, list[float]], dict[str, list[float]]],
    ix: tuple[int, ...],
) -> str:
    """연구소 한 곳의 [분석 초안](연간). 연간 칸을 만든 값을 그대로 문장으로 넘긴다 — 여기서 다시
    계산하지 않는 것이 요점이다(services/reason_draft.py 머리말)."""
    rate_cur, rate_base = rates
    tot, labor, hc = cells["tot"], cells["labor"], cells["hc"]
    currency, local = _lab_currency(st.labs, gpks, rate_cur)
    loc_cur = _labor_local(st.labs, gpks, st.cur_d, st.cur_extras, rate_cur, ix)
    loc_base = _labor_local(st.labs, gpks, st.base_d, st.base_extras, rate_base, ix)
    return reason_draft.build(reason_draft.DraftInputs(
        cur_total=tot["cur"], base_total=tot["base"], diff=tot["cur"] - tot["base"],
        fx=tot["fx_local"] + tot["fx_usd"], fx_local=tot["fx_local"], fx_outside=tot["fx_outside"],
        labor_outside=labor["fx_outside"],
        non_labor_outside=tot["fx_outside"] - labor["fx_outside"],
        hc_base=hc["base"], hc_cur=hc["cur"],
        splittable=sp is not None,
        hc_effect=sp.hc_effect if sp else 0.0,
        rate_effect=sp.rate_effect if sp else 0.0,
        per_head_base=(loc_base / hc["base"]) if hc["base"] > 0 else 0.0,
        per_head_cur=(loc_cur / hc["cur"]) if hc["cur"] > 0 else 0.0,
        currency=currency, local_currency=local,
        categories=tuple(
            (CATEGORY_LABELS.get(key, key), cells[key]["fx_outside"])
            for key in _non_labor_keys() if key in cells
        ),
        rate_base=tuple(rate_base.get("USD") or ()),
        rate_cur=tuple(rate_cur.get("USD") or ()),
    ))


def compute_variance_labs(
    db: Session,
    basis_year: int,
    basis_month: int,
    kind: str,
    base_version: str,
    plan_no: str | None,
    stage: str = "",
) -> VarianceLabsResult:
    """증감분석 **연구소별** 요약 표 — 17기간 × 연구소. 계정별 표(compute_variance_table)의 짝이다.

    위(연구소별 요약)와 아래(계정별 상세)가 같은 화면에 같이 뜨므로 한 군데서 계산해야 한다 —
    재료(전실행 해석 · 환율)가 갈리면 같은 자리에서 다른 숫자가 나온다.

    준비물(_variance_setup)은 한 번만 만들고 기간·연구소만 바꿔 돈다. 환율 배열은 기간마다 한 번씩
    읽고 연구소 전부가 돌려 쓴다 — 연구소마다 다시 읽으면 19×17번 DB를 왕복한다.

    인건비 분해(인원 효과·단가 효과)는 services/labor_split.py가 이미 연구소별로 주므로 그대로
    가져와 칸에 붙인다. 경영계획 대비처럼 환율 기준이 없어 분해할 수 없는 경우는 splittable=false다.
    """
    st = _variance_setup(db, basis_year, basis_month, kind, base_version, "ALL", plan_no)

    # 표의 행: 맨 위 "전체 연구소" + 연구소(파트가 있으면 파트를 합친 연구소 단위)
    by_code: dict[str, list[str]] = {}
    names: dict[str, str] = {}
    for pk in st.pks:
        lab = st.labs[pk]
        by_code.setdefault(lab.lab_code, []).append(pk)
        # 파트별 이름("SARC+ACL · CPU")이 아니라 연구소 이름으로 묶는다 — 표의 한 줄이 연구소다.
        names.setdefault(lab.lab_code, re.sub(r"\s*·.*$", "", lab.name))
    groups: list[tuple[str, str, list[str], bool]] = [("ALL", "전체 연구소", list(st.pks), True)]
    groups += [
        (code, names[code], gpks, False)
        for code, gpks in sorted(by_code.items(), key=lambda kv: (st.labs[kv[1][0]].sort_order, kv[0]))
    ]

    # 인건비 분해: 연구소 전체를 한 번에 준비하고 기간마다 한 번씩 돌린다(연구소별 줄이 같이 온다).
    split_by_period: dict[str, dict[str, LaborSplitLab]] = {}
    try:
        inp = labor_split_prepare(db, basis_year, base_version, "ALL", basis_month, kind, plan_no)
        for period, ix in PERIOD_IX.items():
            res = labor_split_for(db, inp, period, ix)
            # labor_split은 pk 단위다 — 표는 연구소 단위라 파트를 합친다.
            merged: dict[str, LaborSplitLab] = {}
            for row in res.labs:
                code = st.labs[row.lab_pk].lab_code if row.lab_pk in st.labs else row.lab_pk
                cur = merged.get(code)
                if cur is None:
                    merged[code] = row.model_copy()
                else:
                    for f in ("hc_base", "hc_cur", "hc_effect", "rate_effect", "unsplit", "fx_outside"):
                        setattr(cur, f, getattr(cur, f) + getattr(row, f))
            # 단가는 합칠 수 없다(금액 ÷ 인원이라) — 합친 줄에서는 비우고, 전체 줄만 서버 값을 쓴다.
            for row in merged.values():
                row.per_head_base = 0.0
                row.per_head_cur = 0.0
            merged["ALL"] = LaborSplitLab(
                lab_pk="ALL", lab_name="전체 연구소", currency="",
                hc_base=res.hc_base, hc_cur=res.hc_cur,
                per_head_base=res.per_head_base, per_head_cur=res.per_head_cur,
                hc_effect=res.hc_effect, rate_effect=res.rate_effect,
                unsplit=res.unsplit, fx_outside=res.fx_outside,
            )
            split_by_period[period] = merged
    except HTTPException:
        # 분해할 수 없는 비교 기준(경영계획 등)이면 칸만 비운다 — 표 자체는 그려야 한다.
        split_by_period = {}

    # 기간별 환율을 미리 한 번씩 읽는다(연구소 수만큼 다시 읽지 않도록).
    all_local = {
        st.labs[pk].currency for pk in st.pks
        if st.labs[pk].currency and st.labs[pk].currency != "USD"
    } if st.split_fx else set()
    rates_by_period: dict[str, tuple[dict, dict]] = {}
    for period, ix in PERIOD_IX.items():
        rc = rate_arrays(db, *st.cur_fx, all_local, ix)
        rb = rate_arrays(db, *st.base_fx, all_local, ix) if st.split_fx else rc
        rates_by_period[period] = (rc, rb)

    rows: list[VarianceLabRow] = []
    for code, name, gpks, is_total in groups:
        cells: dict[str, VarianceLabCell] = {}
        # 증감사유 초안은 연간 기준 한 개다 — 화면의 사유 칸이 기간별로 나뉘어 있지 않다(연간
        # 증감을 설명하는 문장 하나를 쓰고, 기간별 숫자는 같은 줄의 칸들이 보여준다).
        draft = ""
        for period, ix in PERIOD_IX.items():
            c = variance_cells(
                db, st.labs, gpks, st.cur_d, st.base_d, ix, st.cur_fx, st.base_fx, st.split_fx,
                st.cur_extras, st.base_extras, rates_by_period[period],
            )
            tot, lab_cell = c["tot"], c["labor"]
            sp = (split_by_period.get(period) or {}).get(code)
            if period == "y" and not is_total:
                draft = _lab_draft(st, gpks, c, sp, rates_by_period[period], ix)
            cells[period] = VarianceLabCell(
                base=tot["base"], cur=tot["cur"], diff=tot["cur"] - tot["base"],
                fx=tot["fx_local"] + tot["fx_usd"], fx_local=tot["fx_local"], fx_usd=tot["fx_usd"],
                fx_outside=tot["fx_outside"],
                labor_outside=lab_cell["fx_outside"],
                non_labor_outside=tot["fx_outside"] - lab_cell["fx_outside"],
                splittable=sp is not None,
                hc_base=sp.hc_base if sp else 0.0, hc_cur=sp.hc_cur if sp else 0.0,
                per_head_base=sp.per_head_base if sp else 0.0,
                per_head_cur=sp.per_head_cur if sp else 0.0,
                hc_effect=sp.hc_effect if sp else 0.0, rate_effect=sp.rate_effect if sp else 0.0,
                unsplit=sp.unsplit if sp else 0.0,
            )
        rows.append(VarianceLabRow(
            lab_code=code, lab_name=name, is_total=is_total, cells=cells, reason_draft=draft,
        ))

    return VarianceLabsResult(
        basis_year=basis_year, basis_month=basis_month, kind=kind, stage=stage,
        base_version=base_version, base_label=st.basis.resolved_label, plan_no=st.plan_no,
        labs=rows, fallback_note=st.basis.fallback_note, note=st.note,
    )


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
    # 기간별 환율을 미리 한 번씩 읽는다(기간마다 다시 읽지 않도록 — 연구소별 표가 하는 것과 같다).
    # 추가 행 증감도 **같은 환율**을 써야 그 줄의 합이 얹힌 계정 합계와 맞는다.
    local_curs = {
        st.labs[pk].currency for pk in st.pks
        if st.labs[pk].currency and st.labs[pk].currency != "USD"
    } if st.split_fx else set()
    rates_by_period: dict[str, tuple[dict, dict]] = {}
    for period, ix in PERIOD_IX.items():
        rc = rate_arrays(db, *st.cur_fx, local_curs, ix)
        rb = rate_arrays(db, *st.base_fx, local_curs, ix) if st.split_fx else rc
        rates_by_period[period] = (rc, rb)
    periods = {
        period: variance_rows(
            variance_cells(
                db, st.labs, st.pks, st.cur_d, st.base_d, ix, st.cur_fx, st.base_fx, st.split_fx,
                st.cur_extras, st.base_extras, rates_by_period[period],
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
        extras=extra_variance_rows(st, rates_by_period),
        fallback_note=st.basis.fallback_note,
        note=st.note,
    )
