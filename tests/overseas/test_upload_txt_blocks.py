"""텍스트 파일 한 개 = 연구소 한 곳 — 구분자 줄로 블록(시트)을 나눠 읽는다.

    비용
    <내용>
    (빈 줄)
    증감분석
    <내용>
    (빈 줄)
    외주용역
    <내용>

왜 이 모양인가: .txt는 시트가 없어서, 시트마다 파일을 따로 받으면 연구소 12곳이면 파일이 38개가
된다(SARC는 파트까지 다섯 장). 구분자로 나누면 엑셀과 똑같이 **연구소당 한 파일**이고, 담당자는
12개를 한 번에 올린다.

쪼갠 블록의 이름은 **양식 시트명**(Format · Analysis · CPU …)으로 되돌려 넘긴다. 그래서 이 뒤
길(ovSheetKind · ovCostPk · 대기 카드 · 미리보기 · 반영)은 엑셀 워크북과 완전히 같은 코드다 —
여기서 고정하는 것은 "쪼개는 데까지"다.

프런트를 베껴 적지 않고 js_upload_harness.mjs로 core.js를 실제 실행한다(원문 문자열을 그대로
넣는다 — 표로 미리 만들어 넣으면 쪼개지는 자리가 바로 그 문자열이라 아무것도 확인하지 못한다).
"""

import pytest

from tests.overseas.test_upload_validation_crosscheck import (  # noqa: F401
    HDR,
    cost_card,
    lab_card,
    pytestmark,
    run_js,
)

MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
Q = ["1Q", "2Q", "3Q", "4Q", "'26"]

# 비용 블록 — 양식 그대로 제목 행 + 머리글 + 계정 행. 달마다 다른 값을 쓴다(열이 한 칸 밀려
# 들어와도 티가 나게). 계정 행은 **셋 이상**이어야 한다: ovParse가 머리글 줄을 "그 아래가 계정으로
# 읽히는가"로 고르고 그 문턱이 3이다(숫자만 적힌 자료 줄을 머리글로 뽑는 사고를 막는 장치다).
COST = [
    ["", "TEST Expense Report (Unit : K$)"],
    HDR,
    ["sal", "(급여)", "Salaries & Wages"] + list(range(11, 23)) + [""] * 5,
    ["bonus", "(상여)", "Bonus (MBO etc)"] + [5] * 12 + [""] * 5,
    ["welf", "(복리후생)", "Welfare, Health Insurance"] + [0] * 12 + [""] * 5,
]
RSN = [
    ["", "TEST Analysis (Unit : K$)"],
    ["계정코드", "구분", "Cost Element", "PLAN", "M2610A", "M2611A", "Difference(2026)", "", "", "",
     "PLAN", "M2610A", "M2611A", "Difference(NOV)", "", "", ""],
    ["", "", "", "2026", "2026", "2026", "vs Plan", "Remark", "vs M2610A", "Remark",
     "NOV.", "NOV.", "NOV.", "vs Plan", "Remark", "vs M2610A", "Remark"],
    ["sal", "(급여)", "Salaries & Wages", 100, 99, 110, 10, "", 11, "사유", 10, "", 12, 2, "", 2, "사유"],
    ["bonus", "(상여)", "Bonus (MBO etc)", 50, 50, 50, 0, "", 0, "", 5, 5, 5, 0, "", 0, ""],
    ["welf", "(복리후생)", "Welfare, Health Insurance", 10, 10, 10, 0, "", 0, "", 1, 1, 1, 0, "", 0, ""],
]
# 외주용역 블록. **'Total' 행이 들어 있는 게 중요하다** — 값이 든 칸이 하나뿐인 줄이라, 앞에 빈
# 줄이 있는지 보지 않으면 "총계 블록 시작"으로 읽혀서 그 아래 금액이 비용 자리로 간다.
OUTS = [
    ["TEST LIST of Outsourcing Expenses (Unit : K$)"],
    ["Category", "Project name", "Name of Company/Institution", "Description / Purpose"] + MON + Q,
    ["IP", "Next-gen AP", "Synopsys Inc.", "IP license"] + [10] * 12 + [""] * 5,
    ["Total", "", "", ""] + [""] * 17,
]


def block(marker, aoa):
    """구분자 줄 + 내용. 칸은 탭, 줄은 개행 — 엑셀의 "텍스트(탭으로 분리)"와 같은 모양이다."""
    body = "\n".join("\t".join("" if c is None else str(c) for c in row) for row in aoa)
    return (marker + "\n" if marker else "") + body


def txt(*blocks):
    """블록 사이를 **빈 줄 한 개**로 띈다 — 구분자를 알아보는 조건이다."""
    return "\n\n".join(blocks) + "\n"


def names(r):
    return [b["name"] for b in (r["split"] or [])]


# ---------- 일반 연구소: 세 블록 ----------

def test_three_markers_split_one_file_into_the_three_form_sheets():
    r = run_js(None, file="해외연구소_SIRC_M2611A.txt", lab="SIRC",
               text=txt(block("비용", COST), block("증감분석", RSN), block("외주용역", OUTS)))
    assert names(r) == ["Format", "Analysis", "LIST of Outsourcing Expenses"], names(r)
    # 자리는 엑셀과 같이 갈린다: 비용은 연구소 자리에, 증감사유·외주용역은 연구소 단위 카드에.
    assert cost_card(r)["cost_rows"] == 3, r["pends"]
    assert lab_card(r)["has_rsn"] and lab_card(r)["has_outs"]
    assert not [x for p in r["pends"] for x in p["errs"]], [p["errs"] for p in r["pends"]]


def test_english_sheet_names_work_as_markers_too():
    """연구소가 구분자를 양식 시트명(영문)으로 적어 보내도 같다 — 외워야 하는 이름을 하나로 못 정한다."""
    r = run_js(None, file="해외연구소_SIRC_M2611A.txt", lab="SIRC",
               text=txt(block("Format", COST), block("Analysis", RSN),
                        block("LIST of Outsourcing Expenses", OUTS)))
    assert names(r) == ["Format", "Analysis", "LIST of Outsourcing Expenses"], names(r)


def test_title_rows_alone_also_split_the_file():
    """구분자를 안 적고 시트를 그냥 이어 붙인 파일도 읽힌다 — 양식 제목 행이 경계가 된다."""
    r = run_js(None, file="해외연구소_SIRC_M2611A.txt", lab="SIRC",
               text=txt(block("", COST), block("", RSN), block("", OUTS)))
    assert names(r) == ["Format", "Analysis", "LIST of Outsourcing Expenses"], names(r)


# ---------- 파트가 나뉜 연구소(SARC): 다섯 블록 ----------

def test_five_markers_land_in_their_own_places_for_a_part_split_lab():
    """CPU · GPU · 총계가 각자 자리로 간다. 총계를 파트 자리에 넣으면 금액이 두 번 세어진다.

    'SARC+ACL Total (CPU+GPU)'처럼 총계 제목에 파트 글자가 들어 있어서, 보는 순서를 틀리면
    총계 블록이 CPU 자리로 간다 — 그래서 총계를 **먼저** 본다(ovTxtMark).
    """
    r = run_js(None, file="해외연구소_SARC_M2611A.txt", lab="SARC",
               text=txt(block("CPU", COST), block("GPU", COST), block("총계", COST),
                        block("증감사유", RSN), block("외주용역", OUTS)))
    assert names(r) == ["CPU", "GPU", "Format", "Analysis", "LIST of Outsourcing Expenses"], names(r)
    assert [p["pk"] for p in r["pends"]] == ["SARC:CPU", "SARC:GPU", "SARC:TOTAL", "SARC"], \
        [p["pk"] for p in r["pends"]]
    assert not [x for p in r["pends"] for x in p["errs"]], [p["errs"] for p in r["pends"]]


# ---------- 오인하지 않아야 하는 것들 ----------

def test_a_total_row_inside_a_block_is_not_read_as_a_marker():
    """외주용역 시트의 'Total' 행은 구분자가 아니다 — 앞에 빈 줄이 없기 때문이다.

    이걸 구분자로 읽으면 블록이 하나 더 생기고, 그 아래 외주 금액이 비용 총계 자리로 들어간다.
    금액이 아니라 **자리**가 틀리는 형태라 숫자만 봐서는 못 알아챈다.
    """
    r = run_js(None, file="해외연구소_SIRC_M2611A.txt", lab="SIRC",
               text=txt(block("비용", COST), block("외주용역", OUTS)))
    assert names(r) == ["Format", "LIST of Outsourcing Expenses"], names(r)
    assert lab_card(r)["has_outs"], "외주용역 블록이 읽히지 않았다"


def test_the_outsourcing_block_may_come_first():
    """외주용역이 맨 위에 와도 파일 전체가 탭 구분으로 읽힌다.

    그 블록의 첫 줄(제목)은 값이 한 칸뿐이라 탭이 없다. 구분자를 줄마다 다시 재면 이 파일이
    콤마 구분으로 판정돼 본문이 통째로 한 칸으로 뭉친다 — 그래서 파일 전체로 한 번 정한다.
    """
    r = run_js(None, file="해외연구소_SIRC_M2611A.txt", lab="SIRC",
               text=txt(block("외주용역", OUTS), block("비용", COST)))
    assert names(r) == ["LIST of Outsourcing Expenses", "Format"], names(r)
    assert cost_card(r)["cost_rows"] == 3, r["pends"]
    assert lab_card(r)["has_outs"]


def test_a_file_without_markers_is_still_one_sheet():
    """구분자가 없으면 종전처럼 파일 한 개 = 시트 한 장이다(자리는 파일명으로 가린다)."""
    r = run_js(None, file="해외연구소_SIRC_M2611A.txt", lab="SIRC",
               text=block("", [HDR] + COST[2:]))   # 제목 행도 없는 민짜 표
    assert r["split"] is None, r["split"]
    assert cost_card(r)["cost_rows"] == 3, r["pends"]


def test_content_before_the_first_marker_is_not_dropped():
    """구분자를 잘못 적어 못 알아본 첫 블록을 **조용히 버리지 않는다**.

    버리면 비용이 통째로 안 들어간 채 "읽었습니다"로 보인다. 이름 없는 한 장으로 넘겨서
    파일명으로 자리를 찾게 하고, 자리가 겹치면 화면이 그 사실을 적는다.
    """
    r = run_js(None, file="해외연구소_SIRC_M2611A.txt", lab="SIRC",
               text=txt(block("첨부1", COST), block("증감분석", RSN)))
    assert names(r) == ["", "Analysis"], names(r)
    assert cost_card(r)["cost_rows"] == 3, "구분자를 못 알아본 블록이 사라졌다"


# ---------- 양식 → 업로드 왕복 ----------
# 양식이 쓰는 구분자와 업로드가 받는 구분자는 **같은 표**(ovWantSheets)에서 나와야 한다. 둘이
# 갈리면 연구소가 우리가 준 양식을 그대로 채워 보냈는데도 안 읽히고, 그 사실을 사람이 양식을
# 받아서 올려 볼 때까지 아무도 모른다. 그래서 받는 파일을 실제로 만들어 그대로 다시 올린다.

@pytest.mark.parametrize(
    "lab,want",
    [
        ("SIRC", ["Format", "Analysis", "LIST of Outsourcing Expenses"]),
        ("SARC", ["CPU", "GPU", "Format", "Analysis", "LIST of Outsourcing Expenses"]),
    ],
)
def test_the_text_form_we_hand_out_reads_back_as_its_own_sheets(lab, want):
    r = run_js(None, file="해외연구소_업로드양식_%s_M2611A.txt" % lab, lab=lab, form="txt")
    assert r["form"], "텍스트 양식을 만들지 못했다"
    assert r["form"]["name"].endswith(".txt"), r["form"]["name"]
    assert names(r) == want, names(r)
    assert not [x for p in r["pends"] for x in p["errs"]], [p["errs"] for p in r["pends"]]


def test_the_text_form_puts_a_marker_line_and_a_blank_line_between_blocks():
    """사람이 열어서 알아볼 수 있는 모양이어야 한다 — 구분자 줄, 내용, 빈 줄."""
    r = run_js(None, file="해외연구소_업로드양식_SIRC_M2611A.txt", lab="SIRC", form="txt")
    lines = r["form"]["text"].lstrip("﻿").split("\n")
    assert lines[0] == "비용", lines[:3]
    assert "증감사유" in lines and "외주용역" in lines, [x for x in lines if x and "\t" not in x]
    # 구분자 앞에는 빈 줄이 있다(그게 구분자로 인정받는 조건이다).
    for i, line in enumerate(lines):
        if line in ("증감사유", "외주용역"):
            assert lines[i - 1] == "", f"‘{line}’ 앞에 빈 줄이 없다"


def test_the_part_split_form_names_its_cost_blocks_by_part():
    """파트가 나뉜 연구소는 비용 블록이 셋이라, 구분자가 파트를 밝혀야 자리를 가릴 수 있다."""
    r = run_js(None, file="해외연구소_업로드양식_SARC_M2611A.txt", lab="SARC", form="txt")
    # 구분자는 "앞이 빈 줄인 한 칸짜리 줄"이다 — 블록 안의 제목 행도 한 칸짜리라(외주용역 시트의
    # 'LIST of Outsourcing Expenses (Unit : K$)'), 한 칸짜리 줄을 전부 세면 그것까지 섞인다.
    # 읽는 쪽이 보는 조건과 같은 조건으로 센다.
    lines = r["form"]["text"].lstrip("﻿").split("\n")
    marks = [x for i, x in enumerate(lines)
             if x and "\t" not in x and (i == 0 or lines[i - 1] == "")]
    assert marks == ["CPU", "GPU (ACL)", "총계", "증감사유", "외주용역"], marks
    assert [p["pk"] for p in r["pends"]] == ["SARC:CPU", "SARC:GPU", "SARC:TOTAL", "SARC"], \
        [p["pk"] for p in r["pends"]]


# ---------- 양식에서 연구소 이름을 뺀다 ----------
# [업로드 양식 받기]는 연구소와 무관한 공용 한 벌을 주고, 파트가 나뉜 연구소(비용표가 여러 장인
# 곳)만 따로 한 벌을 더 준다. 파일명이나 제목 행(B1 "… Expense Report (Unit : K$)")에 연구소
# 이름이 박히면 그 양식은 그 연구소 전용이 되어, 담당자가 열두 곳에 돌려 쓸 수 없다 — 연구소마다
# 화면에서 고르고 다시 받아야 한다.


def test_the_form_does_not_carry_a_lab_name_in_its_title_rows():
    """양식 안에 연구소 이름이 없어야 한다 — 있으면 그 파일은 그 연구소 전용이 된다."""
    r = run_js(None, file="해외연구소_업로드양식_M2611A.txt", lab="SIRC", form="txt")
    text = r["form"]["text"]
    assert "SARC" not in text and "SIRC" not in text and "ACL" not in text, \
        [x for x in text.split("\n") if "SARC" in x or "SIRC" in x or "ACL" in x]
    # 제목 행은 남는다(사람이 열어서 무슨 표인지 알아야 한다) — 연구소 이름만 빠진다.
    lines = [x.split("\t")[1] for x in text.split("\n") if x.startswith("\t")]
    assert "Expense Report (Unit : K$)" in lines, lines
    assert "Analysis (Unit : K$)" in lines, lines


def test_the_common_form_has_no_lab_code_in_its_file_name():
    """공용 양식의 파일명에는 연구소 코드가 없다 — 파트 양식만 코드를 달고 나온다.

    코드를 안 붙인 파일이 공용이라는 약속은 업로드 쪽 자동 선택(ovDetect)과도 맞는다: 코드가
    없으면 화면에서 보고 있는 연구소로 들어가고, 그건 공용 양식을 쓰는 연구소에서 맞는 동작이다.
    """
    r = run_js(None, file="해외연구소_업로드양식_M2611A.txt", lab="SIRC", form="txt")
    files = r["form"]["files"]
    # 공용이 맨 앞이고, 그 파일명에는 어떤 연구소 코드도 없다.
    assert files[0] == "해외연구소_업로드양식_M2611A.txt", files
    # 파트가 나뉜 연구소 몫이 뒤에 붙는다(지금 기준정보로는 SARC 하나).
    assert files[1:] == ["해외연구소_업로드양식_SARC_M2611A.txt"], files


def test_the_common_form_is_the_three_sheet_one_and_the_part_form_adds_the_parts():
    """한 번 누르면 두 모양이 같이 나온다 — 공용(비용 · 증감사유 · 외주용역)과 파트별."""
    common = run_js(None, file="해외연구소_업로드양식_M2611A.txt", lab="SIRC", form="txt")
    assert names(common) == ["Format", "Analysis", "LIST of Outsourcing Expenses"], names(common)
    part = run_js(None, file="해외연구소_업로드양식_SARC_M2611A.txt", lab="SARC", form="txt")
    assert names(part) == ["CPU", "GPU", "Format", "Analysis", "LIST of Outsourcing Expenses"], \
        names(part)


def test_a_part_form_pasted_together_without_markers_still_splits_by_its_title_rows():
    """구분자를 지우고 시트를 그냥 이어 붙여 보낸 파일도 제목 행으로 자리를 가린다.

    그래서 cap에서 연구소 이름만 빼고 파트(CPU · GPU · Total)는 남겼다 — 비용표 세 장의 제목이
    모두 "Expense Report"로 같아지면 CPU 금액이 GPU 자리로 가거나 블록이 통째로 합쳐진다.
    """
    src = run_js(None, file="해외연구소_업로드양식_SARC_M2611A.txt", lab="SARC", form="txt")
    lines = src["form"]["text"].lstrip("﻿").split("\n")
    marks = {"CPU", "GPU (ACL)", "총계", "증감사유", "외주용역"}
    stripped = "\n".join(x for x in lines if x not in marks)
    r = run_js(None, file="해외연구소_업로드양식_SARC_M2611A.txt", lab="SARC", text=stripped)
    assert [p["pk"] for p in r["pends"]] == ["SARC:CPU", "SARC:GPU", "SARC:TOTAL", "SARC"], \
        [p["pk"] for p in r["pends"]]
