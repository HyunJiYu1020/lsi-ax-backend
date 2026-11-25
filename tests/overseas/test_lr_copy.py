"""인건비율 모달: 연구소별 체크박스 + 클립보드 복사.

왜 필요한가: 담당자는 이 표에서 **연구소 몇 곳만** 떼어 메일·엑셀에 붙인다. [엑셀로 받기]는 표
전체를 파일로 떨어뜨려서, 두 연구소만 보내려면 파일을 열고 필요 없는 줄을 지워야 했다.

여기서 고정하는 것:
  - 체크박스는 **맨 윗줄(전체 연구소)과 연구소 줄에만** 붙는다. 파트 · 펼친 금액 줄에는 없다 —
    고르는 단위가 연구소다.
  - 체크박스는 구분 칸 **밖**, 표 맨 왼쪽의 자기 열에 선다. 구분 칸 안에 두면 체크박스가 붙은
    줄만 이름이 오른쪽으로 밀려서, 들여쓰기로 읽던 위계(전체 > 연구소 > 파트)가 흐트러진다.
    대신 .lb가 첫 칸이 아니게 되므로, 가로로 밀 때 붙어 있으라고 sticky를 따로 걸어 둔다.
  - 전체 줄은 아래 연구소를 한 번에 켜고 끈다. 연구소를 하나라도 풀면 전체 체크도 풀린다 —
    전체의 체크 상태를 따로 저장하지 않고 "연구소가 전부 체크됐나"로 계산하기 때문이다.
  - 체크를 눌러도 그 줄이 펼쳐지지 않는다. 줄 전체가 펼치기 버튼이라(data-vlrx) 체크박스 클릭이
    그대로 올라가면 체크 한 번에 금액 줄이 같이 열린다.
  - 복사는 **보이는 표를 그대로** 담는다: 체크한 연구소만, 접어 둔 금액 줄은 빼고. 아무것도
    체크하지 않으면 표 전체다(버튼이 아무 일도 안 하는 쪽보다 예상에 맞다).

돌아가는 조건(없으면 스킵): node + playwright, frontend-html/shared/core.js, 로컬 Postgres.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.overseas.test_screens import backend  # noqa: F401

HERE = Path(__file__).resolve().parent
HARNESS = HERE / "lr_copy_harness.mjs"
CORE_JS = HERE.parents[2] / "frontend-html" / "shared" / "core.js"

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not (shutil.which("node") and HARNESS.exists() and CORE_JS.exists()),
        reason="node 또는 프런트 소스(frontend-html/shared/core.js)가 없어 화면을 띄울 수 없다",
    ),
]


@pytest.fixture(scope="module")
def result(backend):  # noqa: F811
    proc = subprocess.run(
        ["node", str(HARNESS), f"--api={backend}"],
        capture_output=True, text=True, timeout=600, cwd=HERE,
    )
    if proc.returncode != 0:
        pytest.fail(f"하네스가 실패했다 (exit {proc.returncode}):\n{proc.stderr[-3000:]}")
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        pytest.fail(f"하네스 출력을 읽지 못했다:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    if data.get("skip"):
        pytest.skip(data["skip"])
    assert not data.get("errs"), (
        "화면에서 JS 에러가 났다: " + json.dumps(data["errs"], ensure_ascii=False)
    )
    return data


def test_checkboxes_sit_on_the_total_and_lab_rows(result):
    """체크박스는 맨 윗줄(전체 연구소)과 연구소 줄에만 — 파트 줄에는 없다."""
    assert len(result["ckRows"]) >= 3, f"체크박스가 모자라다: {result['ckRows']}"
    assert result["ckRows"][0] == "ALL", f"맨 윗줄에 전체 체크박스가 없다: {result['ckRows'][:3]}"
    assert result["ckOnTotal"] == 1, "전체 연구소 줄에 체크박스가 없다"
    assert result["ckOnPart"] == 0, "파트 줄에도 체크박스가 붙었다 — 고르는 단위는 연구소다"


def test_checkboxes_live_in_their_own_column(result):
    """체크박스는 구분 칸 밖, 맨 왼쪽 체크 열에 선다 — 구분 칸 안이면 그 줄만 이름이 밀린다."""
    assert result["ckInLb"] == 0, "체크박스가 구분 칸 안에 들어 있다 — 이름이 밀려 보인다"
    assert result["ckInCkCol"] == len(result["ckRows"]), (
        f"체크 열에 있는 체크박스 수가 다르다: {result['ckInCkCol']} vs {len(result['ckRows'])}"
    )


def test_the_label_column_still_sticks_while_scrolling(result):
    """체크 열이 생겨도 구분 칸은 가로 스크롤에 붙어 있다(체크 열 폭만큼 떨어진 자리에)."""
    st = result["lbSticky"]
    assert st["ck"] == "sticky 0px", f"체크 열이 안 붙어 있다: {st['ck']}"
    assert st["lb"] == "sticky 38px", f"구분 칸이 안 붙어 있다: {st['lb']}"


def test_the_total_checkbox_turns_every_lab_on(result):
    """전체 줄을 체크하면 아래 연구소가 모두 체크된다."""
    on = result["allOn"]
    assert set(on["sel"]) == set(result["ckRows"][1:]), (
        f"전체를 체크했는데 연구소가 다 안 켜졌다: {on['sel']}"
    )
    assert on["checked"] == result["ckRows"], (
        f"화면 체크 표시가 전부 켜지지 않았다: {on['checked']}"
    )


def test_unchecking_one_lab_clears_the_total_checkbox(result):
    """연구소를 하나라도 풀면 전체 체크도 같이 풀린다."""
    off = result["oneOff"]
    assert not off["all"], "연구소를 하나 풀었는데 전체 체크가 그대로다"
    assert len(off["checked"]) == len(result["ckRows"]) - 2, (
        f"푼 연구소 말고 다른 체크까지 움직였다: {off['checked']}"
    )


def test_the_total_checkbox_toggles_both_ways(result):
    """일부만 체크된 상태에서 전체를 누르면 전부 켜지고, 한 번 더 누르면 전부 꺼진다."""
    assert result["reAll"] == len(result["ckRows"]), f"전부 켜지지 않았다: {result['reAll']}"
    assert result["clearAll"] == 0, f"전부 꺼지지 않았다: {result['clearAll']}"


def test_checking_a_lab_does_not_expand_the_row(result):
    """체크 클릭이 줄 펼치기로 새지 않는다 — 두 번 체크해도 펼쳐진 줄은 0이다."""
    assert result["opened"] == 0, (
        f"체크를 눌렀는데 줄이 펼쳐졌다(lrOpen={result['opened']}) — 클릭이 tr까지 올라갔다"
    )
    assert result["checkedAfter"] == result["sel"] == result["ckRows"][1:3], (
        f"체크 상태가 화면과 저장값이 어긋난다: 화면={result['checkedAfter']} 저장={result['sel']}"
    )


def test_copy_carries_only_the_checked_labs(result):
    """복사본에 체크한 두 연구소만(그 연구소의 파트까지) 들어간다 — 나머지는 한 줄도 없다."""
    tsv = result["copied"]
    assert tsv, "클립보드로 아무것도 안 갔다"
    head, *rows = tsv.split("\n")
    assert head.split("\t")[:2] == ["구분", "1월"], f"머리글이 빠졌다: {head[:80]}"
    assert head.split("\t")[-1] == "연간", f"연간 열이 빠졌다: {head[:120]}"
    assert rows, f"줄이 하나도 없다:\n{tsv}"
    for name in result["names"].values():
        assert any(r.strip().startswith(name) for r in rows), f"체크한 {name}이 빠졌다:\n{tsv}"
    for name in result["unchecked"]:
        assert not any(r.strip() == name or r.split("\t")[0].strip() == name for r in rows), (
            f"체크하지 않은 {name}이 섞였다:\n{tsv}"
        )
    assert "총계" not in tsv and "전체 연구소" not in tsv, f"총계가 섞였다:\n{tsv}"


def test_copied_numbers_are_plain_so_excel_reads_them(result):
    """숫자는 쉼표 · % 없이 — 엑셀이 글자가 아니라 숫자로 읽어야 한다."""
    cells = [c for r in result["copied"].split("\n")[1:] for c in r.split("\t")[1:] if c]
    assert cells, f"숫자 칸이 비었다:\n{result['copied']}"
    bad = [c for c in cells if "," in c or "%" in c or c.strip() != c]
    assert not bad, f"엑셀이 글자로 읽을 칸이 있다: {bad[:5]}"
    assert all(c.replace("-", "").replace(".", "").isdigit() for c in cells), (
        f"숫자가 아닌 칸이 있다: {[c for c in cells if not c.replace('-', '').replace('.', '').isdigit()][:5]}"
    )


def test_no_selection_copies_the_whole_table(result):
    """체크를 다 풀면 표 전체(총계 포함)가 복사된다."""
    tsv = result["copiedAll"]
    assert tsv, "체크가 없을 때 아무것도 복사되지 않았다"
    assert "전체 연구소" in tsv, f"총계 줄이 빠졌다:\n{tsv[:300]}"
    assert len(tsv.split("\n")) > len(result["copied"].split("\n")), (
        "표 전체가 체크 두 개보다 줄이 적다"
    )


def test_expanded_amount_rows_come_along(result):
    """펼쳐 둔 줄은 인건비 · 개발비 금액까지 같이 복사된다 — 보이는 표 그대로다."""
    tsv = result["copiedOpen"]
    assert "인건비 (" in tsv and "개발비(매출) (" in tsv, (
        f"펼친 금액 줄이 복사본에 없다:\n{tsv[:400]}"
    )


def test_the_button_is_an_icon_button(result):
    """[엑셀로 받기] 왼쪽의 아이콘 버튼이다 — 글자 없이 아이콘만(고른 수만 뱃지로 붙는다)."""
    assert result["btnIconOnly"], "복사 버튼에 글자가 붙었다 — 아이콘만 있는 버튼이다"
