"""해외연구소 편성 "증감사유 · 외주용역" 시트의 예시(더미) 데이터를 DB에 채운다.

프로토타입 화면(frontend-html/shared/core.js의 ovSeedX)이 브라우저 메모리에만 만들던 예시를,
같은 규칙·같은 문장 템플릿으로 서버에서 만들어 적재한다 — 브라우저 저장소를 비우거나 다른 PC에서
열어도 같은 화면이 나오게 하려는 것.

금액은 비용 시트(cost_entries)에서 그대로 읽어 쓴다:
  - 당기(cur)  = 기준월 forecast(속보)  · 전기(prev) = 그 앞의 최신 actual(실적) 스냅샷
  - PLAN       = management_plan_entries에 해외연구소 행이 아직 없어, 증감사유 행 안에서만
                 당기값에 ±12% 범위의 결정적 변동을 걸어 합성한다(비용 데이터는 건드리지 않는다).
외주용역 상세의 월 금액은 비용 시트 "외주용역비(outs)"를 가중 배분하므로 화면의 대사(상세 합계 vs
비용 시트)가 저절로 맞는다 — 프로토타입이 일부러 틀리게 둔 SSCR 3월 1건만 예외로 유지한다.

실행: uv run python -m app.db.seed_overseas_sheets
"""

import math

from sqlalchemy import delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.cost_groups import GROUP_CALC
from app.db.session import SessionLocal
from app.models import (
    CostEntry,
    CostEntryDetail,
    OverseasLab,
    OverseasReasonRow,
    OverseasVendorRow,
    OverseasVendorRowDetail,
)

ACCOUNT_KEY = "overseas"

# 화면 OV_EL과 같은 순서의 비용 항목 키(pct 제외). 증감사유 시트는 이 44개 행을 연간(y)·당월(m)
# 두 블록으로 올린다 — 집계 행(tot·A·hr…)도 연구소가 보내는 시트에 그대로 들어 있어 같이 저장한다.
CATEGORY_KEYS = (
    "hc", "tot", "A", "hr", "labor", "sal", "bonus", "welf", "edu", "travel",
    "asset", "dep", "amort", "rent", "repair", "ins", "direct", "supp", "test", "ord",
    "gen", "comm", "it", "cons", "util", "car", "tel", "pub", "oth", "mgr",
    "conv", "event", "social", "misc", "tax", "nonop", "B", "itx", "eda", "emu",
    "swl", "svr", "itoth", "outs",
)
HC_KEY = "hc"  # 유일한 "명" 단위 행 — 연간 블록은 합이 아니라 12개월 평균이다

# 증감사유 문장 템플릿 — 화면(core.js OV_RTPL·OV_RPREV·ovRemark)의 원문 그대로.
OV_RTPL: dict[str, dict[str, tuple[str, str]]] = {
    "sal": {"up": ("higher basic salary from new hires", "merit increase applied in April"),
            "dn": ("less basic salary due to delayed hiring", "resignations in Q3")},
    "bonus": {"up": ("MBO accrual true-up", "retention bonus for key engineers"),
              "dn": ("lower MBO payout ratio", "bonus accrual reversal")},
    "welf": {"up": ("higher health insurance premium at annual renewal", "401K matching increase"),
             "dn": ("social security refund", "lower insurance premium after vendor change")},
    "edu": {"up": ("additional technical training and conference fees", "leadership program"),
            "dn": ("training postponed to next year", "online courses instead of external training")},
    "travel": {"up": ("additional trips to HQ for project reviews", "customer visits for new design-in"),
               "dn": ("fewer overseas trips", "trips replaced with video meetings")},
    "dep": {"up": ("depreciation of newly acquired test equipment", "server capex brought forward"),
            "dn": ("capex delayed to Q4", "assets fully depreciated in H1")},
    "rent": {"up": ("office lease renewal at higher rate", "additional lab space"),
             "dn": ("office space reduction", "rent-free period on lease renewal")},
    "ord": {"up": ("new R&D project kick-off", "prototype tape-out cost"),
            "dn": ("project scope reduction", "tape-out schedule moved to next year")},
    "supp": {"up": ("additional evaluation boards and samples", "lab consumables for new project"),
             "dn": ("fewer sample builds", "reuse of existing evaluation boards")},
    "comm": {"up": ("additional consulting/legal service contracts", "recruiting agency fees"),
             "dn": ("lower service fees", "consulting contract ended")},
    "it": {"up": ("SW license renewal (EDA/collaboration tools)", "additional cloud usage"),
           "dn": ("license consolidation", "cloud usage optimization")},
}
OV_RPREV = {
    "SSIR|sal": "annual salary increase (avg 6%) effective September",
    "SARC|ord": "new GPU project kick-off",
    "SCSC|travel": "overseas trips cut in H2",
    "SRCX|comm": "new external consulting contract",
}

# 외주용역 상세의 Category · Project name · Description 보기값 — 화면 OV_OCAT·OV_OPRJ·OV_ODSC 그대로.
OV_OCAT = ("Outsourcing", "Outsourcing", "Outsourcing", "Design Service", "Verification", "Test/Measurement", "Consulting")
OV_OPRJ = (
    "UCIe (D2D)", "PCIe Gen6 PHY", "HBM4 Interface", "NPU Compiler", "Camera ISP", "5G Modem", "Auto SoC",
    "Security IP", "Display DDI", "Power Management IC", "GPU Driver", "Image Sensor", "USB4 Controller", "DDR5 PHY",
)
OV_ODSC = (
    "Test board design", "Layout service", "RTL verification support", "Silicon validation", "Firmware porting",
    "Characterization measurement", "Tool flow consulting", "Reliability test", "Signal integrity analysis",
    "Software QA", "Package design review", "Driver development support",
)

MONTH_NAMES = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

NO_REASON_LABS = frozenset({"SRR"})   # 화면 ovSeedX가 증감사유 예시를 만들지 않는 연구소
NO_VENDOR_LABS = frozenset({"TDC"})   # 외주용역 예시를 만들지 않는 연구소


class JsRandom:
    """화면 core.js의 rnd(seedStr)와 비트 단위로 같은 난수열 (FNV-1a 해시 + xorshift32).
    같은 시드 문자열에서 같은 값이 나와야 "다시 적재해도 같은 예시"가 보장된다."""

    def __init__(self, seed: str) -> None:
        h = 2166136261
        for ch in seed:
            h = (h ^ ord(ch)) & 0xFFFFFFFF
            h = (h * 16777619) & 0xFFFFFFFF
        self._h = h

    def __call__(self) -> float:
        h = self._h
        h = (h ^ (h << 13)) & 0xFFFFFFFF
        h = h ^ (h >> 17)
        h = (h ^ (h << 5)) & 0xFFFFFFFF
        self._h = h
        return (h % 10000) / 10000


def _js_round(v: float) -> int:
    """JS Math.round (항상 반올림 올림쪽) — Python round()는 짝수로 가서 결과가 미세하게 달라진다."""
    return math.floor(v + 0.5)


def _r1(v: float) -> float:
    """소수 1자리 반올림 (화면의 Math.round(v*10)/10)."""
    return _js_round(v * 10) / 10


def _js_num(v: float) -> str:
    """JS가 숫자를 문자열로 붙일 때와 같은 표기 (3.0 -> "3", 3.5 -> "3.5")."""
    return str(int(v)) if float(v).is_integer() else str(v)


def _k_usd(v: float) -> str:
    """사유 문장에 들어가는 금액 표기 — 화면 ovRemark의 K()와 동일."""
    return "$" + (f"{_js_round(v):,}" if v >= 10 else _js_num(_r1(v))) + "K"


def _remark(key: str, diff: float, two_lines: bool, rand: JsRandom) -> str:
    """화면 ovRemark 포팅 — 증감 방향에 맞는 템플릿 문장. two_lines면 금액을 두 줄로 쪼갠다."""
    tpl = OV_RTPL.get(key)
    if not tpl:
        return ""
    phrases = tpl["up" if diff > 0 else "dn"]
    direction = "Increase" if diff > 0 else "Decrease"
    amount = abs(diff)
    if not two_lines:
        return f"- {direction} mainly due to {_k_usd(amount)} {phrases[0]}"
    first = amount * (0.55 + rand() * 0.25)
    return (
        f"- {direction} mainly due to\n"
        f"1] {_k_usd(first)} {phrases[0]}\n"
        f"2] {_k_usd(amount - first)} {phrases[1]}"
    )


def _hc_remark(diff: float) -> str:
    """인력(hc) 행은 금액이 아니라 인원 문장을 쓴다 — 화면 ovSeedX의 hc 전용 분기."""
    n = _js_round(abs(diff))
    if diff > 0:
        return f"Increase as {n}{' more engineer was' if n == 1 else ' more engineers were'} hired than planned"
    return f"Decrease as {n}{' planned hire was' if n == 1 else ' planned hires were'} delayed or employees resigned"


# ---------- 비용 시트(cost_entries) 읽기 ----------

def _basis(db: Session) -> tuple[str, str]:
    """(기준월 속보 yyyymm, 그 앞의 최신 실적 yyyymm). 화면의 "당월 속보 / 전실행"에 해당한다."""
    forecast_yms = sorted(
        db.scalars(
            select(CostEntry.yyyymm)
            .where(CostEntry.account_code == ACCOUNT_KEY, CostEntry.version == "forecast")
            .distinct()
        )
    )
    if not forecast_yms:
        raise SystemExit("cost_entries에 해외연구소 속보(forecast) 데이터가 없습니다 — 비용 시트를 먼저 적재하세요.")
    cur_ym = forecast_yms[-1]
    actual_yms = sorted(
        ym
        for ym in db.scalars(
            select(CostEntry.yyyymm)
            .where(CostEntry.account_code == ACCOUNT_KEY, CostEntry.version == "actual")
            .distinct()
        )
        if ym < cur_ym
    )
    return cur_ym, (actual_yms[-1] if actual_yms else "")


def _lab_parts(db: Session) -> dict[str, list[str]]:
    """연구소코드 -> 그 연구소의 scope_code 목록. 증감사유·외주용역은 연구소 단위(SARC는 CPU+GPU 합계)로
    올라오므로 파트를 합쳐야 한다. "SARC:TOTAL"은 연구소가 따로 보내준 총계 행이라 더하면 두 번
    세는 셈이 되어 제외한다."""
    parts: dict[str, list[str]] = {}
    for lab in db.scalars(select(OverseasLab).order_by(OverseasLab.sort_order, OverseasLab.pk)):
        if lab.pk == "_ETC" or lab.part_code == "TOTAL":
            continue
        parts.setdefault(lab.lab_code, []).append(lab.pk)
    return parts


def _lab_months(db: Session, yyyymm: str, version: str, lab_parts: dict[str, list[str]]) -> dict[str, dict[str, list[float]]]:
    """연구소코드 -> 비용항목 -> 12개월 금액. 리프 항목을 먼저 모아 파트 합산한 뒤 집계 행을 전개한다."""
    if not yyyymm:
        return {lab: {} for lab in lab_parts}
    pk_to_lab = {pk: lab for lab, pks in lab_parts.items() for pk in pks}
    entries = [
        e
        for e in db.scalars(
            select(CostEntry).where(
                CostEntry.account_code == ACCOUNT_KEY, CostEntry.version == version, CostEntry.yyyymm == yyyymm
            )
        )
        if e.scope_code in pk_to_lab
    ]
    months_by_entry: dict[int, dict[int, float]] = {}
    if entries:
        # year 조건이 꼭 필요하다 — 기간을 다음 해까지 늘려 받은 헤더는 같은 period(1~12)가 연도별로
        # 두 번 들어 있어서, 연도를 안 가리면 뒤에 읽힌 행이 앞의 값을 덮어쓴다(증감사유 시트는 화면과
        # 같이 기준연도 12개월만 쓴다).
        for d in db.scalars(
            select(CostEntryDetail).where(
                CostEntryDetail.cost_entry_id.in_([e.id for e in entries]),
                CostEntryDetail.period_type == "MONTH",
                CostEntryDetail.year == int(yyyymm[:4]),
            )
        ):
            months_by_entry.setdefault(d.cost_entry_id, {})[d.period] = float(d.amount)

    out: dict[str, dict[str, list[float]]] = {lab: {} for lab in lab_parts}
    for e in entries:
        lab = pk_to_lab[e.scope_code]
        vals = months_by_entry.get(e.id, {})
        acc = out[lab].setdefault(e.category_code, [0.0] * 12)
        for m in range(1, 13):
            acc[m - 1] += vals.get(m, 0.0)
    for cats in out.values():
        _expand_groups(cats)
    return out


def _expand_groups(cats: dict[str, list[float]]) -> None:
    """집계 행(tot·A·hr…)을 리프 합으로 채운다 — 화면 ovPartEl의 calc와 같은 재귀."""

    def value_of(key: str) -> list[float]:
        if key in GROUP_CALC:
            total = [0.0] * 12
            for sub in GROUP_CALC[key]:
                for j, v in enumerate(value_of(sub)):
                    total[j] += v
            cats[key] = total
            return total
        return cats.setdefault(key, [0.0] * 12)

    for key in CATEGORY_KEYS:
        value_of(key)


def _plan_months(cur: dict[str, list[float]], lab: str) -> dict[str, list[float]]:
    """PLAN(경영계획) 합성값 — 항목마다 당기값에 ±12% 안의 결정적 변동을 걸어 만든다.
    비용 쪽 경영계획 테이블(management_plan_entries)엔 해외연구소 행이 없어서, 증감사유 시트의
    "vs PLAN" 열이 당기 전액이 되어버리는 걸 막기 위한 예시값이다(DB의 비용 데이터는 건드리지 않음)."""
    rand = JsRandom("ovplan|" + lab)
    plan: dict[str, list[float]] = {}
    for key in CATEGORY_KEYS:
        factor = 1 + (rand() - 0.5) * 0.24
        months = cur.get(key, [0.0] * 12)
        plan[key] = [float(_js_round(v * factor)) if key == HC_KEY else _r1(v * factor) for v in months]
    return plan


# ---------- 증감사유 ----------

def _reason_rows(lab: str, cur_ym: str, prev_ym: str, el: dict[str, dict[str, list[float]]]) -> list[dict]:
    """한 연구소의 증감사유 시트 행(44개 항목 × 연간·당월 2블록)을 만든다 — 화면 ovSeedX와 같은 규칙."""
    month = int(cur_ym[4:6])
    rand = JsRandom("ovrsn|" + lab)

    def agg(version: str, key: str, annual: bool) -> float:
        months = el[version].get(key, [0.0] * 12)
        if not annual:
            return months[month - 1]
        return sum(months) / 12 if key == HC_KEY else sum(months)

    blocks: dict[str, dict[str, dict]] = {}
    for key in CATEGORY_KEYS:
        blocks[key] = {}
        for block, annual in (("y", True), ("m", False)):
            plan, prev, cur = agg("plan", key, annual), agg("prev", key, annual), agg("cur", key, annual)
            blocks[key][block] = {
                "plan": plan, "prev": prev, "cur": cur,
                "d_plan": cur - plan, "d_prev": cur - prev, "r_plan": "", "r_prev": "",
            }

    # vs PLAN: 차이가 큰 쪽만 사유를 적는다 (연간 상위 3개 · 당월 상위 2개, 첫 번째는 두 줄).
    for block, top_n in (("y", 3), ("m", 2)):
        ranked = sorted(
            (k for k in OV_RTPL if abs(blocks[k][block]["d_plan"]) >= 0.5),
            key=lambda k: -abs(blocks[k][block]["d_plan"]),
        )[:top_n]
        for i, key in enumerate(ranked):
            cell = blocks[key][block]
            cell["r_plan"] = _remark(key, cell["d_plan"], i == 0, rand)
        hc = blocks[HC_KEY][block]
        if abs(hc["d_plan"]) >= 0.5:
            hc["r_plan"] = _hc_remark(hc["d_plan"])

    # vs 전기: 실제로 바뀐 항목 전부에 사유를 적는다 (연구소별 특이 사유는 OV_RPREV로 덮어씀).
    for block in ("y", "m"):
        for key in OV_RTPL:
            cell = blocks[key][block]
            if abs(cell["d_prev"]) < 0.5:
                continue
            special = OV_RPREV.get(f"{lab}|{key}")
            if special:
                direction = "Increase" if cell["d_prev"] > 0 else "Decrease"
                cell["r_prev"] = f"- {direction} due to ${_js_num(_r1(abs(cell['d_prev'])))}K {special}"
            else:
                cell["r_prev"] = _remark(key, cell["d_prev"], False, rand)

    prev_label = f"M{prev_ym[2:]}A" if prev_ym else ""
    cur_label = f"M{cur_ym[2:]}A"
    source_file = f"OV_{lab}_{cur_ym[2:]}.xlsx"
    return [
        {
            "lab_code": lab, "version": "cur", "category_key": key, "block": block,
            "mon_label": MONTH_NAMES[month - 1] if block == "m" else "",
            "plan_value": round(cell["plan"], 2), "prev_value": round(cell["prev"], 2), "cur_value": round(cell["cur"], 2),
            "diff_plan": round(cell["d_plan"], 2), "diff_prev": round(cell["d_prev"], 2),
            "remark_plan": cell["r_plan"], "remark_prev": cell["r_prev"],
            "prev_label": prev_label, "cur_label": cur_label, "source_file": source_file,
        }
        for key in CATEGORY_KEYS
        for block, cell in blocks[key].items()
    ]


# ---------- 외주용역 ----------

def _vendor_rows(lab: str, cur_ym: str, outs: list[float]) -> list[dict]:
    """한 연구소의 외주용역 상세(업체·과제 30~40건)를 만든다 — 화면 ovSeedX와 같은 규칙."""
    rand = JsRandom("ovouts|" + lab)
    count = 30 + math.floor(rand() * 11)
    items: list[dict] = []
    for i in range(count):
        start = math.floor(rand() * 12)
        length = 12 if rand() < 0.35 else 1 + math.floor(rand() * 8)
        category = OV_OCAT[math.floor(rand() * len(OV_OCAT))]
        project = OV_OPRJ[math.floor(rand() * len(OV_OPRJ))]
        description = OV_ODSC[math.floor(rand() * len(OV_ODSC))]
        weight = 0.2 + rand() * rand() * 3
        items.append({
            "cat": category, "proj": project, "comp": f"Vendor {lab}-{i + 1:02d}", "desc": description,
            "s": 0 if i == 0 else start, "e": 11 if i == 0 else min(11, start + length - 1),
            "w": weight, "m": [0.0] * 12,
        })

    for j in range(12):
        active = [x for x in items if x["s"] <= j <= x["e"]]
        weight_sum = sum(x["w"] for x in active)
        left = _r1(outs[j])
        for i, x in enumerate(active):
            v = left if i == len(active) - 1 else _r1(outs[j] * x["w"] / weight_sum)
            x["m"][j] = _r1(v)
            left = _r1(left - v)
    if lab == "SSCR" and items:  # 대사 불일치 예시 1건(화면에 빨간 칸으로 보이는 3월)
        items[0]["m"][2] = _r1(items[0]["m"][2] + 5)

    source_file = f"OV_{lab}_{cur_ym[2:]}.xlsx"
    return [
        {
            "lab_code": lab, "version": "cur", "row_no": i + 1,
            "category": x["cat"], "project": x["proj"], "company": x["comp"], "description": x["desc"],
            "source_file": source_file, "m": x["m"],
        }
        for i, x in enumerate(items)
    ]


# ---------- 적재 ----------

def seed_overseas_sheets(db: Session) -> tuple[int, int]:
    cur_ym, prev_ym = _basis(db)
    lab_parts = _lab_parts(db)
    cur = _lab_months(db, cur_ym, "forecast", lab_parts)
    prev = _lab_months(db, prev_ym, "actual", lab_parts)

    reason_rows: list[dict] = []
    vendor_rows: list[dict] = []
    for lab in lab_parts:
        # 비용 시트가 아직 없는 연구소(화면에서 시험용으로 추가한 행 등)는 건너뛴다 —
        # 증감사유·외주용역은 비용 금액에서 만들어지므로 0만 가득한 행이 생길 뿐이다.
        if not any(any(months) for months in cur[lab].values()):
            continue
        el = {"cur": cur[lab], "prev": prev[lab], "plan": _plan_months(cur[lab], lab)}
        if lab not in NO_REASON_LABS:
            reason_rows.extend(_reason_rows(lab, cur_ym, prev_ym, el))
        if lab not in NO_VENDOR_LABS and any(cur[lab].get("outs", [])):
            vendor_rows.extend(_vendor_rows(lab, cur_ym, cur[lab]["outs"]))

    if reason_rows:
        set_cols = (
            "mon_label", "plan_value", "prev_value", "cur_value", "diff_plan", "diff_prev",
            "remark_plan", "remark_prev", "prev_label", "cur_label", "source_file",
        )
        stmt = pg_insert(OverseasReasonRow).values(reason_rows)
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=["lab_code", "version", "category_key", "block"],
                set_={k: getattr(stmt.excluded, k) for k in set_cols},
            )
        )

    # 지우고 다시 넣는다 — 마스터를 지우면 월 금액(디테일)도 FK로 같이 지워진다.
    if vendor_rows:
        db.execute(
            delete(OverseasVendorRow).where(
                OverseasVendorRow.version == "cur",
                OverseasVendorRow.lab_code.in_({r["lab_code"] for r in vendor_rows}),
            )
        )
        new_ids = (
            db.execute(
                insert(OverseasVendorRow).returning(OverseasVendorRow.id, sort_by_parameter_order=True),
                [{k: v for k, v in r.items() if k != "m"} for r in vendor_rows],
            )
            .scalars()
            .all()
        )
        db.execute(
            insert(OverseasVendorRowDetail),
            [
                {"vendor_row_id": new_id, "year": int(cur_ym[:4]), "month": month, "amount": amount}
                for new_id, r in zip(new_ids, vendor_rows)
                for month, amount in enumerate(r["m"], start=1)
            ],
        )

    db.commit()
    return len(reason_rows), len(vendor_rows)


def main() -> None:
    db = SessionLocal()
    try:
        n_reason, n_vendor = seed_overseas_sheets(db)
    finally:
        db.close()
    print(f"증감사유 {n_reason}행 · 외주용역 {n_vendor}건 적재 완료")


if __name__ == "__main__":
    main()
