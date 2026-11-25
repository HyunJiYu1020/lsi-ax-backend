"""엑셀 대신 **탭 구분 텍스트(.txt)** 파일을 올려도 엑셀과 같은 길을 지난다.

왜 필요한가: 보안 프로그램이 엑셀 파일을 못 읽는 연구소가 있어서, 그 연구소는 엑셀에서
"텍스트(탭으로 분리)"로 저장한 파일을 보낸다. 화면이 그 확장자를 안 받으면
(ovHandleOneFile의 확장자 검사) 담당자는 붙여넣기 칸에 한 연구소씩 손으로 옮겨 붙여야 하고,
12개 연구소를 한 번에 올리는 길이 없다.

여기서 고정하는 것은 **확장자만 늘었고 그 뒤는 엑셀과 같은 코드**라는 사실이다: 미리보기가
저절로 뜨고, 파일에 적힌 값을 그대로(빈칸은 빈칸, 분기 칸은 파일 값) 보여준다. 숫자가 그리드까지
가는 경로 자체는 test_upload_to_grid.py가 엑셀로 본다 — 중간부터 같은 함수(ovHandleSheets)다.

.txt 한 개는 시트 한 장이라 "안 온 시트" 경고(ovWantSheets)는 뜨지 않아야 한다 — 그 경고의
쓸모는 "받은 파일이 불완전하니 연구소에 재요청하라"인데, 시트마다 파일을 따로 올리는 형식에서는
한 장만 올린 게 정상이다.
"""

import pytest

from tests.overseas.test_upload_to_grid import (  # noqa: F401  (백엔드 · 하네스를 그대로 쓴다)
    FILE_Q1_SAL,
    HARNESS,
    SAL,
    SHEET,
    backend,
    pytestmark,
    run_harness,
)


def _tsv(path, aoa):
    """엑셀의 "텍스트(탭으로 분리)"가 내놓는 모양 — 칸은 탭, 줄은 개행, 빈칸은 빈 문자열.

    파이썬이 직접 쓴다(core.js의 쓰기 코드를 쓰지 않는다): 읽는 쪽을 확인하려면 파일이 다른 데서
    와야 한다 — 같은 코드로 쓰고 읽으면 둘이 같이 틀려도 통과한다.
    """
    text = "\n".join("\t".join("" if c is None else str(c) for c in row) for row in aoa)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def result(backend, tmp_path_factory):
    book = _tsv(tmp_path_factory.mktemp("txt") / "해외연구소_SIRC_M2611A.txt", SHEET["aoa"])
    return run_harness(backend, book)


def _row(table, en):
    assert table, "미리보기 표를 읽지 못했다 — .txt가 아예 안 읽혔을 수 있다"
    hit = [r for r in table["rows"] if r["en"] == en]
    assert hit, f"'{en}' 행이 없다. 있는 행: {[r['en'] for r in table['rows']][:40]}"
    return hit[0]


def test_a_txt_file_is_read_and_previewed_like_excel(result):
    """.txt를 고르면 돌려보내지 않고 대기 카드 + 미리보기까지 간다."""
    assert "올릴 수 없습니다" not in result["modalText"], result["modalText"][:300]
    assert "1개 파일" in result["card"]["text"] and "SIRC" in result["card"]["text"], result["card"]["text"]
    assert "해외연구소_SIRC_M2611A.txt" in result["modalText"]
    assert "비용 3개 행" in result["modalText"], f"읽은 행 수가 다르다: {result['modalText'][:300]}"
    assert result["modalAuto"], "엑셀과 달리 미리보기가 저절로 열리지 않았다"


def test_tab_separated_numbers_land_in_the_right_months(result):
    """탭 구분이 월 칸으로 제대로 갈린다 — 파일 값 그대로(분기 칸도 파일 값).

    구분자를 잘못 집으면("1,234"를 콤마로 쪼개는 사고 — parseCSV 주석) 금액이 다음 달로 밀리는데
    밀린 숫자도 그럴듯해서 사람 눈에는 안 보인다. 달마다 다른 값을 쓴 파일로 그 밀림을 잡는다.
    """
    sal = _row(result["modal"], "Salaries & Wages")
    assert [c["txt"] for c in sal["cells"][:12]] == [str(v) for v in SAL]
    assert sal["cells"][12]["txt"] == str(FILE_Q1_SAL), "1Q 칸을 파일 값이 아니라 계산값으로 보여준다"
    bonus = _row(result["modal"], "Bonus (MBO etc)")
    assert bonus["cells"][2]["txt"] == "–", "빈칸(미제출)이 0으로 보인다"


def test_one_txt_file_does_not_warn_about_the_other_sheets(result):
    """시트 한 장짜리 파일에 "‘Analysis’(증감사유)가 안 왔다"를 붙이지 않는다."""
    assert "Analysis" not in result["modalText"], (
        "시트마다 파일을 따로 올리는 형식인데 안 온 시트를 경고했다: " + result["modalText"][:400]
    )
