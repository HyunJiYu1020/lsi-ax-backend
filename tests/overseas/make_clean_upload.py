"""검증을 **하나도 안 걸리는** 업로드 파일을 만든다 — 손으로 올려 보며 확인할 때 쓰는 기준 파일.

왜 만들어 두나: 경고가 뜨는 파일은 쉽게 만들 수 있지만(아무 숫자나 적으면 급변이 뜬다), "제대로
올렸을 때 아무 경고도 안 뜨는지"는 서버가 들고 있는 전실행 값과 맞춰야만 확인된다. 그 계산을
사람이 손으로 하면 매달 다시 해야 한다.

기준 9개를 다 통과하려면 이렇게 적어야 한다:
  급변 · 전실행에 없던 값 · 단위(배수) → 월 값을 **전월 속보와 똑같이**(변동 0)
  빈칸                        → 리프 계정 36개의 12개월을 전부 채운다(0도 숫자로 적는다)
  기간 합                      → 1Q~4Q · 연간 칸을 월 합과 정확히 같게
  합계 불일치                  → 소계 · 합계 행을 하위 합과 정확히 같게
  사유 누락                    → 급변이 없으면 따라서 안 뜬다(급변에 종속된 기준)
  증감분석 경영계획 · 전실행 · 당월 대조
                              → 세 열에 각자의 상대 값을 적는다. 상대가 열마다 다르다:
                                PLAN  = 본사 경영계획(적용 차수) · M####A = 전실행(직전 버전)
                                당월 열 = 같은 파일의 비용 시트. 연간 블록은 12개월 합,
                                당월 블록은 기준월 한 칸이다.
                                그 상대를 여기서 베껴 계산하지 않고 **검증이 쓰는 같은 층**에서
                                읽는다(services/versions.resolve_bases · load_basis) — 손으로 적으면
                                기준이 바뀌는 날 이 파일이 조용히 "경고 0건"을 못 내게 된다.
  증감분석 당월 빈칸            → 당월 블록(PLAN · 전실행 · 당월) 세 칸을 다 채운다

쓰는 법:
    .venv/bin/python -m tests.overseas.make_clean_upload                       # SIRC · 2026-09 속보
    .venv/bin/python -m tests.overseas.make_clean_upload --lab DSRA --basis 2026-10
    .venv/bin/python -m tests.overseas.make_clean_upload --out ~/Desktop

급변 · 단위 의심이 쓰는 비교 기준은 **전월 속보**다(속보 11월을 보고 있으면 속보 10월.
core.js ovYyyymmPrevForecast와 같은 규칙) — 그래서 비용 시트의 월 값은 전월 속보를 그대로 베낀다.
증감분석 시트의 세 열은 그것과 **다른 상대**를 본다(위 기준 목록 참고): 경영계획과 전실행(= 직전
버전 · 실적 M-1)이다. 그래서 **올릴 때 화면에 띄워 둔 버전**과 --basis가 같아야 한다 — 다르면
비교 상대가 전부 달라져서 급변과 증감분석 대조가 같이 뜬다.

파트가 나뉜 연구소(SARC)는 증감분석이 연구소 전체 기준이라(파트 합) 세 열의 상대도 파트를
합쳐서 읽는다 — 검증과 같은 함수(anomaly_rules.scan_pks)를 쓴다. 다만 비용 시트는 한 장만
만들므로, 파트가 나뉜 연구소로 만들려면 그 파트들의 데이터가 DB에 다 있어야 한다.

만든 파일을 자동으로 확인하려면: tests/overseas/test_upload_clean_file.py (헤드리스 브라우저로
올려 보고 findings가 0인지 본다).
"""

import argparse
from pathlib import Path

from app.accounts.overseas.cost_groups import CATEGORY_LABELS, CATEGORY_LABELS_EN, GROUP_CALC, resolve_leaves
from app.accounts.overseas.endpoints.collect import months_by_category
from app.accounts.overseas.services.anomaly_rules import scan_pks
from app.accounts.overseas.services.versions import load_basis, resolve_bases
from app.db.session import SessionLocal
from tests.overseas.test_upload_to_grid import HDR, MONTHS, _xlsx

# 파일에 적을 소계·합계 행. GROUP_CALC 전부가 아니라 양식에 실제로 있는 것만 넣는다 —
# 하위 합으로 만드는 값이라 금액은 리프에서 계산한다(그래서 합계 불일치가 날 수 없다).
SUBTOTALS = ("labor", "hr", "asset", "direct", "gen", "mgr", "A", "tot")

# 분기 · 연간 칸. 월 합을 그대로 적는다.
def _periods(m):
    q = [round(sum(m[i * 3:i * 3 + 3]), 6) for i in range(4)]
    return q + [round(sum(m), 6)]


# 인력(hc)은 명이고 MARK UP 비율(pct)은 %다 — 업로드 파서가 금액 칸만 ×1000하므로(core.js
# isMoney) 이 두 행은 파일에도 그대로 적는다. 나누면 "전 버전의 1/1000"이 되어 단위 의심에 걸린다.
NOT_MONEY = ("hc", "pct")


def _k(v, key="x"):
    """저장 단위($) → 파일 단위(K$). 업로드 파서가 ×1000 하므로 원래 값으로 되돌아간다."""
    v = float(v or 0)
    return round(v, 6) if key in NOT_MONEY else round(v / 1000, 6)


def build(lab: str, basis: str, out_dir: Path) -> Path:
    y, m = basis.split("-")
    # 화면이 업로드 검증의 비교 상대로 쓰는 버전·연월 — **전월 속보**다(core.js ovYyyymmPrevForecast).
    pm = int(m) - 1
    base_ym = (int(y) - 1) * 100 + 12 if pm < 1 else int(y) * 100 + pm
    db = SessionLocal()
    try:
        base = months_by_category(db, lab, "forecast", base_ym)
        # 증감분석 세 열의 상대. 검증과 **같은 층**에서 읽는다(여기서 베껴 계산하지 않는다).
        # 파트가 나뉜 연구소는 파트를 합쳐서 본다 — 증감분석이 연구소 단위 시트라서다.
        pks = set(scan_pks(db, lab, True))
        by_key = {b.key: b for b in resolve_bases(db, int(y), int(m), "forecast").bases}

        def basis_months(key: str, what: str) -> dict:
            b = by_key.get(key)
            if b is None or not b.has_data:
                raise SystemExit(
                    f"{lab}의 {what} 값이 DB에 없다 ({(b.fallback_note if b else '') or '비교 기준 없음'})"
                    " — 증감분석 세 열 중 하나라도 상대가 없으면 '경고 0건' 파일을 만들 수 없다."
                )
            loaded = load_basis(db, (b.year, b.month, b.kind, b.plan_no), pks)
            out: dict[str, list[float]] = {}
            for pk in pks:
                for cat, months in loaded.get(pk, {}).items():
                    acc = out.setdefault(cat, [])
                    for i, v in enumerate(months or []):
                        if i < len(acc):
                            acc[i] += float(v or 0)
                        else:
                            acc.append(float(v or 0))
            return out

        plan_base = basis_months("plan", "경영계획(적용 차수)")
        prev_base = basis_months("prev", "전실행(직전 버전)")
    finally:
        db.close()
    if not base:
        raise SystemExit(
            f"{lab}의 전월 속보({base_ym}) 값이 DB에 없다 — 비교 기준이 없으면 급변·전실행에 없던"
            " 값을 통과했다고 말할 수 없다. --basis를 데이터가 있는 달로 바꿀 것."
        )

    # 리프 계정만 쓴다(소계는 아래에서 계산해 넣는다). 전실행에 있는 계정을 그대로 따라간다 —
    # 비교 상대에 없는 계정에 금액을 적으면 그게 바로 "전실행에 없던 값"으로 걸린다.
    leaves = [k for k in base if k not in GROUP_CALC]
    vals = {k: [_k(v, k) for v in (base[k] + [0] * 12)[:12]] for k in leaves}

    def row(key, m12):
        return [key, f"({CATEGORY_LABELS.get(key, key)})", CATEGORY_LABELS_EN.get(key, key)] + m12 + _periods(m12)

    rows = [row(k, vals[k]) for k in leaves]
    for g in SUBTOTALS:
        ls = [x for x in resolve_leaves(g) if x in vals]
        if not ls:
            continue
        m12 = [round(sum(vals[x][i] for x in ls), 6) for i in range(12)]
        rows.append(row(g, m12))

    cost = {"name": "Format", "aoa": [["", f"{lab} Expense Report (Unit : K$)"], HDR] + rows}

    # ---------- 증감사유(증감분석) 시트 ----------
    # 연간 블록의 전실행 열은 본사가 들고 있는 **연간 합**과 맞아야 한다(analysis_prev).
    # 당월 블록은 세 칸(PLAN · 전실행 · 당월)이 다 차 있어야 한다(analysis_missing).
    mon_i = min(max(int(m), 1), 12) - 1
    # 머리글은 파서가 찾는 모양이어야 한다(core.js ovParseRsn): "PLAN"과 M####A 코드가 한 줄에
    # 있어야 그 줄을 머리글로 잡고, 둘째 줄의 "vs Plan" · "vs M####A"로 차이·사유 칸을 찾는다.
    # 블록은 PLAN 열 두 개로 끊는다 — 앞이 연간, 뒤가 당월이다.
    prev_code, cur_code = f"M{base_ym % 10000:04d}A", f"M{str(y)[2:]}{int(m):02d}A"
    mon_label = MONTHS[mon_i] + "."
    a_hdr1 = ["계정코드", "구분", "Cost Element", "PLAN", prev_code, cur_code, f"Difference({y})", "", "", "",
              "PLAN", prev_code, cur_code, f"Difference({MONTHS[mon_i]})", "", "", ""]
    a_hdr2 = ["", "", "", y, y, y, "vs Plan", "Remark", f"vs {prev_code}", "Remark",
              mon_label, mon_label, mon_label, "vs Plan", "Remark", f"vs {prev_code}", "Remark"]
    # 열마다 상대가 다르므로 칸도 따로 만든다 — "자기 월 합"은 당월 열(비용 시트)에만 맞는
    # 값이고, PLAN · 전실행 열은 본사 값과 맞대보므로 거기서 가져와 적는다.
    def _col(src: dict, k: str) -> tuple[float, float]:
        """그 열의 (연간, 기준월) 값 — 파일 단위(K$)로. 본사에 없는 계정은 0이다(0으로 편성·보고)."""
        ms = (src.get(k) or []) + [0] * 12
        return round(sum(_k(v, k) for v in ms[:12]), 6), _k(ms[mon_i], k)

    a_rows = []
    for k in leaves:
        p_yr, p_mo = _col(plan_base, k)            # PLAN 열   → 본사 경영계획
        v_yr, v_mo = _col(prev_base, k)            # M####A 열 → 전실행(직전 버전)
        c_yr, c_mo = round(sum(vals[k]), 6), vals[k][mon_i]   # 당월 열 → 이 파일의 비용 시트
        # 차이 칸은 비워 두지 않고 적는다 — 비우면 화면이 (당월 − 기준)으로 계산해 "계산값"으로
        # 표시한다(검증 대상은 아니지만, 기준 파일은 연구소가 제대로 적어 보낸 모양이어야 한다).
        a_rows.append([k, f"({CATEGORY_LABELS.get(k, k)})", CATEGORY_LABELS_EN.get(k, k),
                       p_yr, v_yr, c_yr, round(c_yr - p_yr, 6), "", round(c_yr - v_yr, 6), "",
                       p_mo, v_mo, c_mo, round(c_mo - p_mo, 6), "", round(c_mo - v_mo, 6), ""])
    rsn = {"name": "Analysis", "aoa": [["", f"{lab} Analysis (Unit : K$)"], a_hdr1, a_hdr2] + a_rows}

    # ---------- 외주용역 시트 ----------
    # 업체 행도 기간 합을 본다(period_mismatch) — 월 합과 정확히 같게 적는다.
    o_months = [round(_k(base.get("outs", [0] * 12)[i] if i < len(base.get("outs", [])) else 0) / 2, 6)
                for i in range(12)]   # 외주용역비의 절반을 한 업체 행으로 — 금액 자체는 검증 대상이 아니다
    outs = {"name": "LIST of Outsourcing Expenses", "aoa": [
        [f"{lab} LIST of Outsourcing Expenses (Unit : K$)"],
        ["Category", "Project name", "Name of Company/Institution", "Description / Purpose"]
        + [x.title() for x in MONTHS] + ["1Q", "2Q", "3Q", "4Q", f"'{str(y)[2:]}"],
        ["Outsourcing", "검증용", "TEST Co.", "기준 파일"] + o_months + _periods(o_months),
    ]}

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"해외연구소_{lab}_M{str(y)[2:]}{int(m):02d}A_검증통과.xlsx"
    _xlsx(path, [cost, rsn, outs])
    print(f"만들었다: {path}")
    print(f"  연구소 {lab} · 올릴 화면 버전 {basis} 속보 · 급변 비교 기준 {base_ym} 속보(전월 속보)")
    print(f"  증감분석 PLAN 열 = 경영계획 {by_key['plan'].resolved_label}"
          f" · M####A 열 = 전실행 {by_key['prev'].resolved_label} · 당월 열 = 이 파일의 비용 시트")
    print(f"  리프 계정 {len(leaves)}개 + 소계 {len(rows) - len(leaves)}개 · 증감분석 {len(a_rows)}행 · 외주용역 1행")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="검증을 하나도 안 걸리는 업로드 파일 만들기")
    ap.add_argument("--lab", default="SIRC", help="연구소 코드(파트가 있으면 SARC:CPU 처럼)")
    ap.add_argument("--basis", default="2026-09", help="올릴 때 화면에 띄워 둘 버전의 연-월")
    ap.add_argument("--out", default="var/upload_samples", help="저장할 폴더")
    a = ap.parse_args()
    build(a.lab, a.basis, Path(a.out).expanduser())
