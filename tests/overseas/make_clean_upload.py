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
  증감분석 전월 대조            → 전실행 열에 본사가 들고 있는 비교 기준의 **연간 합**을 적는다
  증감분석 당월 빈칸            → 당월 블록(PLAN · 전실행 · 당월) 세 칸을 다 채운다

쓰는 법:
    .venv/bin/python -m tests.overseas.make_clean_upload                       # SIRC · 2026-09 속보
    .venv/bin/python -m tests.overseas.make_clean_upload --lab DSRA --basis 2026-10
    .venv/bin/python -m tests.overseas.make_clean_upload --out ~/Desktop

비교 기준은 화면이 업로드 검증에 쓰는 것과 같은 걸 읽는다 — **전월 속보**다(속보 11월을 보고
있으면 속보 10월. core.js ovYyyymmPrevForecast와 같은 규칙). 증감분석 화면의 "전실행"(실적 M-1)
과는 다른 기준이다. 그래서 **올릴 때 화면에 띄워 둔 버전**과 --basis가 같아야 한다 — 다르면
비교 상대가 달라져서 급변이 뜬다.

만든 파일을 자동으로 확인하려면: tests/overseas/test_upload_clean_file.py (헤드리스 브라우저로
올려 보고 findings가 0인지 본다).
"""

import argparse
from pathlib import Path

from app.accounts.overseas.cost_groups import CATEGORY_LABELS, CATEGORY_LABELS_EN, GROUP_CALC, resolve_leaves
from app.accounts.overseas.endpoints.collect import months_by_category
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
    base_ym = f"{int(y) - 1}12" if pm < 1 else f"{y}{pm:02d}"
    db = SessionLocal()
    try:
        base = months_by_category(db, lab, "forecast", base_ym)
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
    prev_code, cur_code = f"M{base_ym[2:]}A", f"M{str(y)[2:]}{int(m):02d}A"
    mon_label = MONTHS[mon_i] + "."
    a_hdr1 = ["계정코드", "구분", "Cost Element", "PLAN", prev_code, cur_code, f"Difference({y})", "", "", "",
              "PLAN", prev_code, cur_code, f"Difference({MONTHS[mon_i]})", "", "", ""]
    a_hdr2 = ["", "", "", y, y, y, "vs Plan", "Remark", f"vs {prev_code}", "Remark",
              mon_label, mon_label, mon_label, "vs Plan", "Remark", f"vs {prev_code}", "Remark"]
    a_rows = []
    for k in leaves:
        yr = round(sum(vals[k]), 6)          # 연간 = 월 합 = 본사가 들고 있는 비교 기준의 연간 합(analysis_prev 통과)
        mo = vals[k][mon_i]
        a_rows.append([k, f"({CATEGORY_LABELS.get(k, k)})", CATEGORY_LABELS_EN.get(k, k),
                       yr, yr, yr, 0, "", 0, "", mo, mo, mo, 0, "", 0, ""])
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
    print(f"  연구소 {lab} · 올릴 화면 버전 {basis} 속보 · 비교 기준 {base_ym} 속보(전월 속보)")
    print(f"  리프 계정 {len(leaves)}개 + 소계 {len(rows) - len(leaves)}개 · 증감분석 {len(a_rows)}행 · 외주용역 1행")
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="검증을 하나도 안 걸리는 업로드 파일 만들기")
    ap.add_argument("--lab", default="SIRC", help="연구소 코드(파트가 있으면 SARC:CPU 처럼)")
    ap.add_argument("--basis", default="2026-09", help="올릴 때 화면에 띄워 둘 버전의 연-월")
    ap.add_argument("--out", default="var/upload_samples", help="저장할 폴더")
    a = ap.parse_args()
    build(a.lab, a.basis, Path(a.out).expanduser())
