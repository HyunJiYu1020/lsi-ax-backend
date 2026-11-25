"""업로드 검증 대조 — 프런트 파싱(core.js)이 만든 값이 서버 기준(anomaly.detect)에 그대로 걸리는지.

이 기능의 핵심 구분은 **빈칸(null) ≠ 0**이다. 0은 "그 달에 안 썼다"는 보고이고 빈칸은 "아직 안
냈다"다. 구분이 프런트 파싱에서 시작해 서버 판정까지 이어져야 의미가 있는데, 한쪽만 고치면
둘 중 하나가 된다:
  - 프런트만 고치면 → 미제출이 조용히 0으로 들어가고 경고가 안 뜬다
  - 서버만 고치면   → 0으로 보고한 달이 전부 미제출로 걸려서 경고가 무용해진다(연구소 12개 × 36계정)
그래서 파싱을 파이썬으로 베껴 적지 않고 js_upload_harness.mjs로 **실제 실행**해 그 payload를
서버 판정에 먹인다(test_formula_crosscheck와 같은 방식).

같이 고정하는 것: 한 파일에서 비용 시트가 여러 장 오면(SARC: CPU · GPU · Format) 자리마다
대기 카드가 하나씩 생긴다는 것, 그리고 미리보기 모달이 예외 없이 그려진다는 것.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.accounts.overseas.cost_groups import GROUP_CALC, category_label, resolve_leaves
from app.shared.core import anomaly

HARNESS = Path(__file__).parent / "js_upload_harness.mjs"
REPO = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(
    not shutil.which("node") or not (REPO / "frontend-html/shared/core.js").exists(),
    reason="node 또는 프런트 소스(frontend-html/shared/core.js)가 없어 대조할 수 없다",
)

HDR = ["계정코드", "구분", "Cost Element"] + [
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"
] + ["1Q", "2Q", "3Q", "4Q", "'26"]


def cost_sheet(name, rows):
    return {"name": name, "aoa": [["", "TEST Expense Report (Unit : K$)"], HDR] + rows}


def run_js(sheets, file="해외연구소_SIRC_M2611A.xlsx", lab="SIRC", basis="2026-11",
           pastes=None, text=None, form=None):
    """text를 주면 **텍스트 파일 원문**을 넣는 길이다(구분자로 블록을 쪼개는 ovTxtSheets를 지난다)
    — test_upload_txt_blocks.py가 쓴다. sheets(표)로 미리 만들어 넣으면 쪼개지는 자리를 못 본다."""
    job = json.dumps({"file": file, "sheets": sheets, "lab": lab, "basis": basis,
                      "pastes": pastes or [], "text": text,
                      "form": form}, ensure_ascii=False)
    p = subprocess.run(["node", str(HARNESS)], input=job, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        pytest.fail(
            "프런트 업로드 파싱을 실행할 수 없다. core.js 구조가 바뀌었으면 "
            f"js_upload_harness.mjs를 같이 고칠 것:\n{p.stderr[-3000:]}"
        )
    return json.loads(p.stdout)


def cost_card(r, i=0):
    """비용 카드(자리별). SARC는 CPU · GPU · Format 순으로 셋이다."""
    return [p for p in r["pends"] if p["cost_rows"]][i]


def lab_card(r):
    """연구소 단위 카드 — 증감분석 · 외주용역이 여기 붙는다(비용과 반영 단위가 다르다)."""
    return next(p for p in r["pends"] if p["has_rsn"] or p["has_outs"])


def detect(payload, base=None, abases=None, **over):
    """프런트가 보낸 payload를 서버 판정에 그대로 먹인다(엔드포인트가 하는 것과 같은 조립).

    abases는 증감분석 세 열의 비교 상대다 — 엔드포인트에서는 서버가 푼다(전실행=직전 버전 ·
    경영계획=적용 차수). "cur"(비용 시트)만 payload가 들고 오므로 안 주면 그걸 쓴다.
    """
    rules = anomaly.merged_rules([], over or None)
    ab = dict(abases or {})
    if "cur" not in ab and payload.get("cost_vals"):
        ab["cur"] = {k: list(v or []) for k, v in payload["cost_vals"].items()}
    return anomaly.detect(
        vals={k: list(v or []) for k, v in (payload["vals"] or {}).items()},
        base_vals=base, sums=payload.get("sums"), reasons=payload.get("reasons"),
        rules=rules, group_calc=GROUP_CALC, label_of=category_label, leaves_of=resolve_leaves,
        periods=payload.get("periods"), outs_rows=payload.get("outs_rows"),
        analysis=payload.get("analysis"), analysis_bases=ab or None,
        basis_month=payload.get("basis_month"),
    )


BLANK_AND_ZERO = [
    ["sal", "(급여)", "Salaries & Wages"] + [10] * 12 + [99, 30, 30, 30, 120],   # 1Q 칸이 월 합과 다르다
    ["bonus", "(상여)", "Bonus (MBO etc)"] + [5, 5, "", 5, 5, 5, 5, 5, 5, 5, 5, 5] + [""] * 5,  # 3월 빈칸
    ["welf", "(복리후생)", "Welfare, Health Insurance"] + [0] * 12 + [""] * 5,     # 0으로 보고한 달
]


def test_blank_is_null_and_zero_stays_zero():
    """빈칸은 null로 올라오고 0은 0으로 올라온다 — 이 구분이 깨지면 나머지 검증이 전부 무의미해진다."""
    r = run_js([cost_sheet("Format", BLANK_AND_ZERO)])
    vals = cost_card(r)["payload"]["vals"]

    assert vals["bonus"][2] is None, "빈칸이 0으로 바뀌었다 — 미제출을 찾을 수 없게 된다"
    assert vals["welf"] == [0] * 12, "0으로 보고한 달이 null이 되었다 — 미제출로 오인된다"

    found = detect(cost_card(r)["payload"])
    blanks = [f for f in found if f["rule"] == "missing_cell"]
    # 분기·연간 칸(13~16=1Q~4Q, 17=연간)도 빈칸으로 센다 — 저장이 그 칸을 월 합으로 만들어
    # 채우지 않으므로, 안 적어 보내면 실제로 비는 칸이 된다.
    # 이 파일은 bonus·welf가 분기·연간을 전부 비워 보냈고 sal만 적어 보냈다.
    assert [(f["category_key"], f["months"]) for f in blanks] == [
        ("bonus", [3, 13, 14, 15, 16, 17]),
        ("welf", [13, 14, 15, 16, 17]),
    ]


def test_quarter_cells_reach_the_server_and_are_compared():
    """파일에 적혀 온 1Q~4Q·연간 칸이 payload에 실려 월 합과 대조된다(13~16=분기, 17=연간)."""
    r = run_js([cost_sheet("Format", BLANK_AND_ZERO)])
    payload = cost_card(r)["payload"]
    # 파일은 K$인데 파서가 ×1000해서 저장 단위($)로 올린다 — 분기·연간 칸도 같이 올라야 월 합과
    # 대조가 된다(한쪽만 올리면 전 행이 기간 합 불일치로 걸린다).
    assert payload["periods"]["sal"] == [99_000, 30_000, 30_000, 30_000, 120_000]
    assert "bonus" not in payload["periods"], "분기 칸을 안 적은 행은 보내지 않는다(빈칸 기준이 잡는다)"

    hit = [f for f in detect(payload) if f["rule"] == "period_mismatch"]
    assert len(hit) == 1 and hit[0]["month"] == 13 and hit[0]["category_key"] == "sal"


def test_analysis_sheet_blanks_and_prev_column_are_checked():
    """증감분석은 당월 빈칸과 전실행 열 대조를 받는다. 당월 블록에서 **빈 칸만** 표시 대상이다."""
    rsn = {"name": "Analysis", "aoa": [
        ["", "TEST Analysis (Unit : K$)"],
        ["계정코드", "구분", "Cost Element", "PLAN", "M2610A", "M2611A", "Difference(2026)", "", "", "",
         "PLAN", "M2610A", "M2611A", "Difference(NOV)", "", "", ""],
        ["", "", "", "2026", "2026", "2026", "vs Plan", "Remark", "vs M2610A", "Remark",
         "NOV.", "NOV.", "NOV.", "vs Plan", "Remark", "vs M2610A", "Remark"],
        ["sal", "(급여)", "Salaries & Wages", 100, 99, 110, 10, "", 11, "사유", 10, "", 12, 2, "", 2, "사유"],
        ["bonus", "(상여)", "Bonus (MBO etc)", 50, 50, 50, 0, "", 0, "", 5, 5, 5, 0, "", 0, ""],
    ]}
    r = run_js([cost_sheet("Format", BLANK_AND_ZERO), rsn])
    payload = lab_card(r)["payload"]
    assert payload["base_lab_wide"] is True, "증감분석은 전실행을 연구소 단위(파트 합)로 봐야 한다"
    assert payload["analysis"]["sal"]["m"]["prev"] is None, "당월 빈칸이 0으로 바뀌었다"
    assert payload["analysis"]["sal"]["y"]["prev"] == 99_000   # K$ 99 → $ 99,000

    # 전실행 열의 비교 상대는 **전실행(직전 버전)**이다 — 급변이 쓰는 전월 속보가 아니라 따로 온다.
    found = detect(payload, abases={"prev": {"sal": [10_000] * 12}})  # 본사 $120,000 ≠ 파일 $99,000
    prev = [f for f in found if f["rule"] == "analysis_prev"]
    assert len(prev) == 1 and prev[0]["category_key"] == "sal"
    assert prev[0]["block"] == "y" and prev[0]["fields"] == ["prev"]

    miss = [f for f in found if f["rule"] == "analysis_missing"]
    assert [(f["category_key"], f["fields"]) for f in miss] == [("sal", ["prev"])]


def test_analysis_current_column_is_compared_with_the_cost_sheet_in_the_same_file():
    """증감분석 당월 열은 **같은 파일의 비용 시트**와 맞댄다 — 연간 블록은 12개월 합, 당월 블록은
    기준월(11월) 한 칸이다.

    왜 파일 안에서 보나: 반영 전이라 비용 값은 아직 DB에 없다. 그리고 여기서 보려는 것이 바로
    "한 파일 안에서 두 시트가 같은 금액을 말하는가"다 — 어긋나면 어느 쪽을 저장해도 틀린 보고가
    되고, 여기서 안 짚으면 반영 뒤 집계에서야 숫자가 안 맞는 것으로 드러난다.
    """
    # 비용 시트: 급여 월 10K$ × 12 → 연간 120K$, 11월 10K$ (비용 파서는 계정 행이 **셋 이상**
    # 있어야 머리글 줄을 고른다 — core.js ovParse의 seen.size<3)
    cost = [
        ["sal", "(급여)", "Salaries & Wages"] + [10] * 12 + [30, 30, 30, 30, 120],
        ["bonus", "(상여)", "Bonus (MBO etc)"] + [5] * 12 + [15, 15, 15, 15, 60],
        ["welf", "(복리후생)", "Welfare, Health Insurance"] + [0] * 12 + [0, 0, 0, 0, 0],
    ]
    rsn = {"name": "Analysis", "aoa": [
        ["", "T Analysis"],
        ["계정코드", "구분", "Cost Element", "PLAN", "M2610A", "M2611A", "Difference(2026)", "", "", "",
         "PLAN", "M2610A", "M2611A", "Difference(NOV)", "", "", ""],
        ["", "", "", "2026", "2026", "2026", "vs Plan", "Remark", "vs M2610A", "Remark",
         "NOV.", "NOV.", "NOV.", "vs Plan", "Remark", "vs M2610A", "Remark"],
        # 연간 당월 열 120 = 비용 연간과 같다(통과) · 당월 블록 당월 열 77 ≠ 비용 11월 10(걸린다)
        ["sal", "(급여)", "Salaries & Wages", 100, 99, 120, 20, "", 21, "사유", 10, 10, 77, 67, "", 67, "사유"],
        # 증감분석 파서도 계정 행이 둘 이상이어야 한다(core.js ovParseRsn). 이 행은 비용 시트와
        # 똑같이 적어 — 걸리는 것은 위 급여 당월 칸 하나뿐이어야 한다.
        ["bonus", "(상여)", "Bonus (MBO etc)", 60, 60, 60, 0, "", 0, "", 5, 5, 5, 0, "", 0, ""],
    ]}
    r = run_js([cost_sheet("Format", cost), rsn])
    payload = lab_card(r)["payload"]
    # 비용 값은 증감분석 카드에 **같이 실려** 온다(연구소 카드엔 vals가 없다).
    assert payload["vals"] == {}, "연구소 카드에 비용이 vals로 섞였다"
    assert payload["cost_vals"]["sal"] == [10_000] * 12, "비용 시트 값이 K$→$로 안 올라왔다"

    hit = [f for f in detect(payload) if f["rule"] == "analysis_cur"]
    assert [(f["block"], f["fields"]) for f in hit] == [("m", ["cur"])], \
        "연간은 맞고 당월만 어긋난 파일인데 짚은 칸이 다르다"
    assert "11월" in hit[0]["message"] and "77" in hit[0]["message"]


def test_part_sheets_are_summed_for_the_analysis_cost_comparison():
    """파트가 나뉜 연구소(SARC)는 증감분석이 연구소 전체 기준이라 비용 시트도 CPU+GPU를 더해서
    보낸다. 총계 시트(Format → SARC:TOTAL)는 그 둘을 더해 만든 값이라 빼고 더한다 — 넣으면
    금액이 정확히 두 배가 되어 맞게 적어 보낸 파일도 전부 불일치로 걸린다."""
    rsn = {"name": "Analysis", "aoa": [
        ["", "T Analysis"],
        ["계정코드", "구분", "Cost Element", "PLAN", "M2610A", "M2611A", "Difference(2026)", "", "", "",
         "PLAN", "M2610A", "M2611A", "Difference(NOV)", "", "", ""],
        ["", "", "", "2026", "2026", "2026", "vs Plan", "Remark", "vs M2610A", "Remark",
         "NOV.", "NOV.", "NOV.", "vs Plan", "Remark", "vs M2610A", "Remark"],
        ["sal", "(급여)", "Salaries & Wages", 100, 99, 240, 140, "", 141, "사유", 10, 10, 20, 10, "", 10, "사유"],
        ["bonus", "(상여)", "Bonus (MBO etc)", 120, 120, 120, 0, "", 0, "", 10, 10, 10, 0, "", 0, ""],
    ]}
    one = [
        ["sal", "(급여)", "Salaries & Wages"] + [10] * 12 + [30, 30, 30, 30, 120],
        ["bonus", "(상여)", "Bonus (MBO etc)"] + [5] * 12 + [15, 15, 15, 15, 60],
        ["welf", "(복리후생)", "Welfare, Health Insurance"] + [0] * 12 + [0, 0, 0, 0, 0],
    ]
    both = [
        ["sal", "(급여)", "Salaries & Wages"] + [20] * 12 + [60, 60, 60, 60, 240],
        ["bonus", "(상여)", "Bonus (MBO etc)"] + [10] * 12 + [30, 30, 30, 30, 120],
        ["welf", "(복리후생)", "Welfare, Health Insurance"] + [0] * 12 + [0, 0, 0, 0, 0],
    ]
    r = run_js(
        [cost_sheet("CPU", one), cost_sheet("GPU", one), cost_sheet("Format", both), rsn],
        file="해외연구소_업로드양식_SARC_M2611A.xlsx", lab="SARC",
    )
    payload = lab_card(r)["payload"]
    # CPU 10 + GPU 10 = 20K$. Format(총계 20K$)까지 더했다면 40K$가 된다.
    assert payload["cost_vals"]["sal"] == [20_000] * 12
    assert [f for f in detect(payload) if f["rule"] == "analysis_cur"] == []


def test_outsourcing_rows_are_compared_by_row():
    """외주용역은 업체 행 단위로 기간 합을 대조한다 — 계정이 아니라 행 이름으로 가리킨다."""
    outs = {"name": "LIST of Outsourcing Expenses", "aoa": [
        ["TEST LIST of Outsourcing Expenses (Unit : K$)"],
        ["Category", "Project name", "Name of Company/Institution", "Description / Purpose",
         "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
         "1Q", "2Q", "3Q", "4Q", "'26"],
        ["Outsourcing", "UCIe", "ACME", "Test board", 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 3, 3, 3, 3, 13],
    ]}
    r = run_js([cost_sheet("Format", BLANK_AND_ZERO), outs])
    payload = lab_card(r)["payload"]
    assert payload["outs_rows"] and payload["outs_rows"][0]["periods"][4] == 13_000  # K$ 13 → $ 13,000

    hit = [f for f in detect(payload) if f["rule"] == "period_mismatch" and f["row_label"]]
    assert len(hit) == 1 and hit[0]["month"] == 17 and "ACME" in hit[0]["row_label"]


def test_one_file_with_part_sheets_makes_one_card_per_slot():
    """SARC 워크북(CPU · GPU · Format)은 자리마다 대기 카드가 하나씩 생긴다 — Format(총계)은
    SARC:TOTAL로 간다. 종류별 첫 시트만 받던 때는 GPU·Format이 조용히 버려졌다."""
    r = run_js(
        [cost_sheet("CPU", BLANK_AND_ZERO), cost_sheet("GPU", BLANK_AND_ZERO),
         cost_sheet("Format", BLANK_AND_ZERO)],
        file="해외연구소_업로드양식_SARC_M2611A.xlsx", lab="SARC",
    )
    assert [p["pk"] for p in r["pends"]] == ["SARC:CPU", "SARC:GPU", "SARC:TOTAL"]
    assert [p["sheet"] for p in r["pends"]] == ["CPU", "GPU", "Format"]


def test_analysis_and_outsourcing_get_their_own_card():
    """증감분석 · 외주용역은 연구소 단위로 저장되므로 카드도 따로다 — 비용 카드에 얹으면 그 카드를
    [취소]할 때 같이 사라지는데 카드에는 그렇게 안 적혀 있어서 담당자가 비용만 버린 줄 안다."""
    rsn = {"name": "Analysis", "aoa": [
        ["", "T Analysis"],
        ["계정코드", "구분", "Cost Element", "PLAN", "M2610A", "M2611A", "Difference(2026)", "", "", "",
         "PLAN", "M2610A", "M2611A", "Difference(NOV)", "", "", ""],
        ["", "", "", "2026", "2026", "2026", "vs Plan", "Remark", "vs M2610A", "Remark",
         "NOV.", "NOV.", "NOV.", "vs Plan", "Remark", "vs M2610A", "Remark"],
        ["sal", "(급여)", "Salaries & Wages", 100, 99, 110, 10, "", 11, "", 10, 10, 12, 2, "", 2, ""],
        ["bonus", "(상여)", "Bonus (MBO etc)", 50, 50, 50, 0, "", 0, "", 5, 5, 5, 0, "", 0, ""],
    ]}
    r = run_js(
        [cost_sheet("CPU", BLANK_AND_ZERO), cost_sheet("GPU", BLANK_AND_ZERO),
         cost_sheet("Format", BLANK_AND_ZERO), rsn],
        file="해외연구소_업로드양식_SARC_M2611A.xlsx", lab="SARC",
    )
    assert [p["pk"] for p in r["pends"]] == ["SARC:CPU", "SARC:GPU", "SARC:TOTAL", "SARC"]
    assert [p["has_rsn"] for p in r["pends"]] == [False, False, False, True]
    assert all(p["cost_rows"] == 0 for p in r["pends"] if p["has_rsn"]), "연구소 카드에 비용이 섞였다"


def test_preview_modal_renders_for_every_tab_shape():
    """미리보기 모달이 예외 없이 그려진다 — 파싱 결과가 바뀌면 여기서 먼저 빨개져야 한다
    (빈 창이 나가면 담당자는 "이상 없음"으로 읽는다)."""
    r = run_js([cost_sheet("Format", BLANK_AND_ZERO)])
    assert r["modal"]["ok"] and not r["modal"]["error"]
    assert r["modal"]["tabs"] >= 1


def test_extra_rows_keep_the_cost_element_from_the_file():
    """계정 목록에 없는 행도 파일의 Cost Element(영문명)를 들고 온다.

    고정 계정 45행은 영문명을 OV_EL에서 꺼내 쓰지만 추가 행은 파일에 적혀 온 글자뿐이다 —
    파싱이 그걸 흘리면 취합 표의 Cost Element 칸이 **빈 입력창**으로 남고(추가 행만 입력창이다),
    열 제목 placeholder가 셀 안에 보여서 그 줄만 깨진 것처럼 읽힌다.
    """
    rows = [
        ["sal", "(급여)", "Salaries & Wages"] + [10] * 12 + ["", "", "", "", ""],
        ["bonus", "(상여)", "Bonus (MBO etc)"] + [10] * 12 + ["", "", "", "", ""],
        ["welf", "(복리후생)", "Welfare, Health Insurance"] + [10] * 12 + ["", "", "", "", ""],
        ["", "특별상여", "Special Bonus"] + [10] * 12 + ["", "", "", "", ""],
        ["", "인당인원성경비", "HR Cost per Head"] + [10] * 12 + ["", "", "", "", ""],
    ]
    got = {u["label"]: u["label_en"] for p in run_js([cost_sheet("Format", rows)])["pends"]
           for u in p["unmatched"]}
    assert got == {"특별상여": "Special Bonus", "인당인원성경비": "HR Cost per Head"}


def test_english_only_row_does_not_duplicate_its_name():
    """영문만 적어 보낸 행은 영문명을 비워 둔다 — 구분·Cost Element에 같은 글자가 두 번 보이면
    어느 쪽이 연구소가 쓴 원문인지 알 수 없다."""
    rows = [
        ["sal", "(급여)", "Salaries & Wages"] + [10] * 12 + ["", "", "", "", ""],
        ["bonus", "(상여)", "Bonus (MBO etc)"] + [10] * 12 + ["", "", "", "", ""],
        ["welf", "(복리후생)", "Welfare, Health Insurance"] + [10] * 12 + ["", "", "", "", ""],
        ["", "", "Cloud Fee"] + [10] * 12 + ["", "", "", "", ""],
    ]
    got = [u for p in run_js([cost_sheet("Format", rows)])["pends"] for u in p["unmatched"]]
    assert got == [{"label": "Cloud Fee", "label_en": ""}]


# ---------- 안 온 시트 묻기 (양식 한 벌 중 빠진 자리) ----------
RSN_SHEET = {"name": "Analysis", "aoa": [
    ["", "TEST Analysis (Unit : K$)"],
    ["계정코드", "구분", "Cost Element", "PLAN", "M2610A", "M2611A", "Difference(2026)", "", "", "",
     "PLAN", "M2610A", "M2611A", "Difference(NOV)", "", "", ""],
    ["", "", "", "2026", "2026", "2026", "vs Plan", "Remark", "vs M2610A", "Remark",
     "NOV.", "NOV.", "NOV.", "vs Plan", "Remark", "vs M2610A", "Remark"],
    ["sal", "(급여)", "Salaries & Wages", 100, 99, 110, 10, "", 11, "사유", 10, "", 12, 2, "", 2, "사유"],
    ["bonus", "(상여)", "Bonus (MBO etc)", 50, 50, 50, 0, "", 0, "", 5, 5, 5, 0, "", 0, ""],
]}


def test_excel_workbook_still_reports_missing_part_sheets():
    """양식 한 벌 중 안 온 시트를 자리별로 적는다 — 이게 없으면 CPU·GPU가 통째로 비어도 조용하다.

    파트가 나뉜 연구소(SARC)가 Format 한 장만 보냈는데 "비용 시트는 찾았으니" 아무 말이 없으면,
    담당자가 알 길이 업로드 현황의 '미' 표시뿐이다 — 그건 연구소에 재요청해야 하는 일이다.
    """
    r = run_js(
        [cost_sheet("CPU", BLANK_AND_ZERO), RSN_SHEET],
        file="해외연구소_업로드양식_SARC_M2611A.xlsx", lab="SARC",
    )
    lost = [x for p in r["pends"] for x in p["lost"]]
    assert any("GPU" in x for x in lost) and any("Format" in x for x in lost), lost


# ---------- 탭 구분 원문 (엑셀 Ctrl+C 클립보드 · 엑셀이 저장한 .txt가 같은 모양이다) ----------
# 표(aoa)를 미리 만들어 넣으면 이 구간을 통째로 못 본다 — 깨지는 자리가 **문자열을 표로 자르는**
# parseCSV이기 때문이다. 그래서 여기만 원문 텍스트를 그대로 넣는다.
# 엑셀을 못 여는 담당자가 쓰는 길이 이것이다([.txt 받기] → 채워서 .txt로 업로드).

PASTE_HDR = ["구분", "Cost Element", "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
             "JUL", "AUG", "SEP", "OCT", "NOV", "DEC", "1Q", "2Q", "3Q", "4Q", "'26"]


def clipboard(rows):
    """엑셀 클립보드 그대로: 탭 구분 · CRLF 줄바꿈 · 보이는 글자(천단위 콤마 · 괄호 음수)."""
    return "\r\n".join("\t".join(str(c) for c in r) for r in rows)


def test_excel_clipboard_keeps_thousand_separated_amounts_in_one_cell():
    """"1,234"가 한 칸으로 남는다 — 쪼개지면 그 줄 금액이 전부 다음 달로 밀린다.

    엑셀 클립보드는 탭 구분인데 숫자는 **화면에 보이는 글자**로 넘어오고, 엑셀은 콤마가 들어간 칸을
    따옴표로 감싸지 않는다. 탭과 콤마를 같이 구분자로 보면 "1,234"가 "1" · "234" 두 칸이 되어
    [1234000]*12가 [1000, 234000, …]로 어긋난다 — 밀린 숫자도 그럴듯해서 아무도 못 알아챈다.
    """
    text = clipboard([
        ["SARC CPU Expense Report (Unit : K$)"] + [""] * 18,
        PASTE_HDR,
        ["(급여)", "Salaries & Wages"] + ["1,234"] * 12 + ["3,702"] * 4 + ["14,808"],
        ["(상여)", "Bonus (MBO etc)", "(50)"] + ["50"] * 11 + [""] * 5,
        ["(복리후생)", "Welfare, Health Insurance", "10", "", "0"] + ["10"] * 9 + [""] * 5,
    ])
    r = run_js([], file="해외연구소_SARC_M2611A.txt", lab="SARC",
               pastes=[{"name": "CPU", "text": text}])
    vals = cost_card(r)["payload"]["vals"]
    # 저장 단위는 $다(양식 K$ × 1000) — chat.py _to_stored_dollar와 같은 기준.
    assert vals["sal"] == [1234000] * 12, vals["sal"]
    # 괄호는 음수, 빈칸은 미제출(None), 0은 "0으로 보고한 달"로 서로 구분된다.
    assert vals["bonus"][0] == -50000, vals["bonus"]
    assert vals["welf"][1] is None and vals["welf"][2] == 0, vals["welf"]


def test_real_csv_is_still_comma_separated():
    """콤마 구분 csv는 그대로 읽는다 — 위 완화가 콤마까지 끄면 진짜 csv가 한 칸으로 뭉친다."""
    text = "\n".join([
        "구분,Cost Element,JAN,FEB,MAR,APR,MAY,JUN,JUL,AUG,SEP,OCT,NOV,DEC",
        "(급여),Salaries & Wages,1,2,3,4,5,6,7,8,9,10,11,12",
        "(상여),Bonus (MBO etc),1,1,1,1,1,1,1,1,1,1,1,1",
        "(복리후생),\"Welfare, Health Insurance\",2,2,2,2,2,2,2,2,2,2,2,2",
    ])
    r = run_js([], file="해외연구소_SIRC_M2611A.csv", lab="SIRC",
               pastes=[{"name": "해외연구소_SIRC_M2611A.csv", "text": text}])
    vals = cost_card(r)["payload"]["vals"]
    assert vals["sal"] == [n * 1000 for n in range(1, 13)], vals["sal"]
    # 따옴표로 감싼 칸 안의 콤마는 구분자가 아니다 — 그 행이 밀리지 않았는지로 본다.
    assert vals["welf"] == [2000] * 12, vals["welf"]
