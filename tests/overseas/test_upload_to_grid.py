"""업로드 한 번을 사람이 하는 그대로 — 엑셀 파일을 만들어 올리고, 미리보기에서 걸리는지 보고,
[반영] 뒤 취합 그리드에 **엑셀에 적은 그대로** 들어왔는지 확인한다.

왜 따로 있나: 기존 업로드 테스트(test_upload_validation_crosscheck.py)는 **이미 표로 풀어 놓은
시트**를 core.js 함수에 바로 넣는다. 실제 업로드에는 그 앞뒤가 더 있다 —
  앞: 파일 선택 · change 이벤트 · .xlsx 압축 해제와 XML 해석(XL.read는 브라우저 기능을 쓴다)
  뒤: [반영]이 그리드 <td>에 쓰는 값(ovCostView) — 상태만 봐서는 사람이 보는 숫자와 같다고 못 한다
그래서 여기서는 헤드리스 브라우저로 취합 화면을 실제로 띄운다(upload_grid_harness.mjs).

엑셀 파일은 **파이썬이 직접** 만든다(_xlsx). core.js의 XL.write로 만들면 쓰기와 읽기가 같은
코드라, 둘이 같이 틀려도 통과한다 — 읽는 쪽을 확인하려면 파일은 다른 데서 와야 한다.

서버에 쓰지 않는다: 하네스가 GET과 이상 감지(anomaly-scan)만 실제 백엔드로 보내고 나머지 쓰기
요청은 가로채 기록만 한다. 서버가 저장을 제대로 받는지는 test_state_roundtrip.py가 본다.

돌아가는 조건(없으면 스킵한다):
  - node + playwright:  npm i -D playwright && npx playwright install chromium
  - frontend-html/shared/core.js
  - 로컬 Postgres (이 테스트가 백엔드를 직접 띄운다)
"""

import json
import shutil
import socket
import subprocess
import threading
import time
import zipfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
HARNESS = HERE / "upload_grid_harness.mjs"
CORE_JS = HERE.parents[2] / "frontend-html" / "shared" / "core.js"

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not (shutil.which("node") and HARNESS.exists() and CORE_JS.exists()),
        reason="node 또는 프런트 소스(frontend-html/shared/core.js)가 없어 화면을 띄울 수 없다",
    ),
]

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
HDR = ["계정코드", "구분", "Cost Element"] + MONTHS + ["1Q", "2Q", "3Q", "4Q", "'26"]

# 엑셀에 적어 넣는 값(K$). 달마다 다른 숫자를 쓴다 — 전부 같은 값이면 열이 한 칸 밀려 들어와도
# 티가 안 난다. 개발 DB에 이미 들어 있을 법한 숫자(0 · 10 · 100)도 피한다.
SAL = [11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]
# 1Q는 파일에 적혀 온 값(99)을 그대로 보여주고, 비워 온 2Q~4Q만 월 합으로 채운다.
SAL_Q = ["99", "45", "54", "63"]
SAL_Y = "198"
BONUS = [5, 5, "", 5, 5, 5, 5, 5, 5, 5, 5, 5]   # 3월은 빈칸(미제출) — 0으로 보고한 것과 다르다
FILE_Q1_SAL = 99                      # 파일에 적혀 온 1Q 칸: 월 합(36)과 어긋난다 → 검증 대상
# 소계 행(인건비) — 월 12칸은 하위 합과 맞춰 적고(합계 불일치를 일부러 만들지 않는다) 1Q 칸만
# 월 합(46)과 다른 값을 적어 보낸다. 소계 행도 **파일에 적힌 분기 칸을 그대로** 보여주는지가 여기
# 걸린다: 이 칸을 안 들고 가면 미리보기가 월 합으로 계산해서 46을 보여주고, 담당자는 연구소가
# 적어 보낸 777을 미리보기에서 영원히 못 본다(반영 뒤 표는 소계를 하위 합으로 그린다).
LABOR = [a + b for a, b in zip(SAL, [v or 0 for v in BONUS])]
FILE_Q1_LABOR = 777

# 개발비(매출) 행 — 양식에 있는 행이고 **연구소가 금액을 적어 보낸다**. 하위 합(= 인건비, 이 파일엔
# 다른 계정이 없다)과 일부러 다르게 적는다: 우리가 리프를 다시 더해 만든 값이 아니라 연구소가
# 보고한 값이 저장돼야 한다는 게 여기서 걸린다. 어긋난다는 사실은 "합계 불일치"가 따로 알린다.
FILE_TOT = [v + 7 for v in LABOR]

COST_ROWS = [
    ["sal", "(급여)", "Salaries & Wages"] + SAL + [FILE_Q1_SAL, "", "", "", ""],
    ["bonus", "(상여)", "Bonus (MBO etc)"] + BONUS + [""] * 5,
    ["welf", "(복리후생)", "Welfare, Health Insurance"] + [0] * 12 + [""] * 5,
    ["labor", "인건비", "Labor Cost"] + LABOR + [FILE_Q1_LABOR, "", "", "", ""],
    ["tot", "개발비(매출)", "Development Expense Total (A+B)"] + FILE_TOT + [""] * 5,
]
SHEET = {"name": "Format", "aoa": [["", "TEST Expense Report (Unit : K$)"], HDR] + COST_ROWS}


def _cell(ref, v):
    if v == "" or v is None:
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f'<c r="{ref}"><v>{v}</v></c>'
    esc = str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{esc}</t></is></c>'


def _col(i):
    s = ""
    i += 1
    while i:
        i, m = divmod(i - 1, 26)
        s = chr(65 + m) + s
    return s


def _xlsx(path: Path, sheets):
    """최소한의 진짜 .xlsx — 압축(deflate) · inlineStr · 숫자 셀. 빈 칸은 아예 쓰지 않는다.

    core.js의 XL.write를 쓰지 않는 이유는 모듈 docstring에 있다(읽기·쓰기가 같이 틀리면 못 잡는다).
    여기서 deflate로 압축하는 것도 일부러다 — 브라우저 쪽 압축 해제 경로를 지나가게 한다.
    """
    def sheet_xml(aoa):
        rows = []
        for ri, row in enumerate(aoa):
            cells = "".join(_cell(f"{_col(ci)}{ri + 1}", v) for ci, v in enumerate(row))
            rows.append(f'<row r="{ri + 1}">{cells}</row>')
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(rows)}</sheetData></worksheet>'
        )

    ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   + "".join(
                       f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
                       'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                       for i in range(len(sheets)))
                   + "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   f'<Relationship Id="rId1" Type="{ns}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   f'xmlns:r="{ns}"><sheets>'
                   + "".join(f'<sheet name="{s["name"]}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                             for i, s in enumerate(sheets))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="{ns}/worksheet" '
                             f'Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(sheets)))
                   + "</Relationships>")
        for i, s in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", sheet_xml(s["aoa"]))
    return path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def backend():
    """테스트가 직접 띄우는 백엔드 — 화면이 숫자·기준·이상 감지를 서버에서 받는다."""
    import uvicorn

    from app.main import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    if not server.started:
        pytest.skip("백엔드를 띄우지 못했다")
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def run_harness(api, book, lab="SIRC"):
    """브라우저로 파일 하나를 올려 보고 하네스가 모은 것을 돌려준다(다른 업로드 테스트도 쓴다)."""
    proc = subprocess.run(
        ["node", str(HARNESS), f"--api={api}", f"--file={book}", f"--lab={lab}"],
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
    assert not data["errors"], "업로드를 끝내지 못했다: " + json.dumps(data["errors"], ensure_ascii=False)
    return data


@pytest.fixture(scope="module")
def result(backend, tmp_path_factory):
    book = _xlsx(tmp_path_factory.mktemp("up") / "해외연구소_SIRC_M2611A.xlsx", [SHEET])
    return run_harness(backend, book)


def _row(table, en):
    assert table, "표를 읽지 못했다 — 화면 구조(.ovt)가 바뀌었으면 하네스를 같이 고칠 것"
    hit = [r for r in table["rows"] if r["en"] == en]
    assert hit, f"'{en}' 행이 없다. 있는 행: {[r['en'] for r in table['rows']][:40]}"
    return hit[0]


def test_file_lands_in_the_pending_card_before_anything_changes(result):
    """파일을 고르면 대기 카드로 먼저 뜬다 — 반영을 누르기 전에는 그리드 숫자가 바뀌지 않는다."""
    # 대기 카드는 한 줄 요약이다(파일 몇 개 · 어느 연구소). 파일명과 읽은 행 수는 미리보기 창이
    # 말한다 — 그래서 둘을 나눠서 본다.
    assert "1개 파일" in result["card"]["text"] and "SIRC" in result["card"]["text"], result["card"]["text"]
    assert "해외연구소_SIRC_M2611A.xlsx" in result["modalText"]
    assert "비용 3개 행" in result["modalText"], f"읽은 행 수가 다르다: {result['modalText'][:300]}"

    # 아직 아무 값도 안 올라온 자리는 표를 아예 그리지 않는다 — 17열이 전부
    # '–'인 표는 "0으로 채워진 실제 값"으로 읽히고, 바로 위 업로드 현황표의 '미'와 화면이 서로
    # 다른 말을 하게 된다. 그 연구소에 이미 올라온 값이 있는 DB에서 돌 수도 있으니 둘 다 받는다.
    if result["before"] is None:
        assert "아직 올라온 비용 시트가 없습니다" in result["beforeEmpty"], result["beforeEmpty"]
    else:
        before = [c["txt"] for c in _row(result["before"], "Salaries & Wages")["cells"][:12]]
        assert before != [str(v) for v in SAL], "반영 전에 이미 파일 값이 그리드에 들어가 있다"


def test_preview_modal_shows_the_file_as_written(result):
    """미리보기는 **파일에 적힌 그대로**를 보여준다 — 빈칸은 –, 0으로 보고한 달은 0, 분기 칸은 파일 값."""
    sal = _row(result["modal"], "Salaries & Wages")
    assert [c["txt"] for c in sal["cells"][:12]] == [str(v) for v in SAL]
    assert sal["cells"][12]["txt"] == str(FILE_Q1_SAL), "1Q 칸을 파일 값이 아니라 계산값으로 보여준다"

    bonus = _row(result["modal"], "Bonus (MBO etc)")
    assert bonus["cells"][2]["txt"] == "–", "빈칸(미제출)이 0으로 보인다"
    welf = _row(result["modal"], "Welfare, Health Insurance")
    assert [c["txt"] for c in welf["cells"][:12]] == ["0"] * 12, "0으로 보고한 달이 빈칸으로 보인다"


def test_preview_modal_computes_a_subtotal_rows_quarter(result):
    """소계 행의 분기·연간은 **하위 합**으로 보여준다 — 파일에 적혀 온 칸이 있어도 그렇다.

    파일 값을 그대로 쓰면 하위에 행을 추가하고 값을 넣어도 1Q·연간이 옛 값에 묶여서, 같은 줄
    안에서 월 합과 분기가 어긋나 보인다. 소계는 월·분기·연간이 한 규칙(하위 합)이어야 읽힌다 — 연구소가 적어 보낸
    값과 다르다는 사실은 "합계 불일치" 지적과 취합 표의 빨간 칸이 따로 알린다.
    """
    labor = _row(result["modal"], "Labor Cost")
    assert [c["txt"] for c in labor["cells"][:12]] == [str(v) for v in LABOR]
    assert labor["cells"][12]["txt"] == str(sum(LABOR[:3])), "소계 1Q가 파일 값에 묶여 있다"
    assert labor["cells"][13]["txt"] == str(sum(LABOR[3:6]))
    assert labor["cells"][16]["txt"] == str(sum(LABOR))


def test_validation_marks_the_cells_in_the_preview_modal(result):
    """검증에 걸린 칸은 미리보기에서 표시(ovf-*)와 설명(title)이 붙는다 — 반영 전에 보여야 의미가 있다.

    무엇이 걸리는지는 서버(anomaly.detect)가 정하고 그 판정 자체는
    test_upload_validation_crosscheck.py가 본다. 여기서 보는 건 **그 결과가 화면 칸에 붙는가**다.
    """
    sal = _row(result["modal"], "Salaries & Wages")
    q1 = sal["cells"][12]
    assert q1["flag"], f"월 합(36)과 어긋난 1Q 칸({q1['txt']})에 아무 표시가 없다"
    assert q1["title"], "표시만 있고 왜 걸렸는지 설명(title)이 없다"

    bonus = _row(result["modal"], "Bonus (MBO etc)")
    assert bonus["cells"][2]["flag"], "빈칸으로 온 3월에 표시가 없다 — 미제출을 못 찾는다"


def test_grid_shows_exactly_what_excel_had(result):
    """[반영] 뒤 그리드가 엑셀에 적힌 값을 그대로 보여준다 — 월 12칸도, 연구소가 적어 보낸 분기도.

    연구소가 분기 칸에 적어 보낸 숫자(1Q=99)는 월 합(36)과 다르더라도 고치지 않는다. 그 표는
    연구소가 보고한 그대로여야 하고, 월 합과 다르다는 사실은 업로드 미리보기의 "기간 합 불일치"가
    따로 알린다. 비워 온 분기(2Q~4Q)·연간만 월 합으로 채운다."""
    assert result["pendLeft"] == 0, "반영했는데 대기 카드가 남아 있다"

    sal = _row(result["grid"], "Salaries & Wages")
    assert [c["txt"] for c in sal["cells"][:12]] == [str(v) for v in SAL]
    assert [c["txt"] for c in sal["cells"][12:16]] == SAL_Q, "엑셀에 적힌 분기 값이 그대로 안 나온다"
    assert sal["cells"][16]["txt"] == SAL_Y

    bonus = _row(result["grid"], "Bonus (MBO etc)")
    assert bonus["cells"][2]["txt"] == "–", "빈칸으로 온 3월이 0으로 들어갔다 — 미제출과 0 보고가 섞인다"
    assert bonus["cells"][0]["txt"] == "5"
    assert bonus["cells"][12]["txt"] == "10", "빈칸이 있는 분기는 있는 달만 더한다"


def test_grid_computes_a_subtotal_rows_quarter_and_flags_the_gap(result):
    """[반영] 뒤 취합 표도 소계 분기를 하위 합으로 그리고, 파일 값과 다르면 그 칸을 빨갛게 칠한다.

    파일의 1Q(777)와 월 합(46)이 다른 칸이다. 숫자는 집계한 값으로 바꿔 넣되 올라온 값을 숨기지
    않는다 — 칸에 ovf-err를 칠하고, 마우스를 올리면 두 값을 같이 보여준다. 미리보기에서 지적을
    보고도 안 고치고 반영한 경우가 바로 이 자리다.
    """
    labor = _row(result["grid"], "Labor Cost")
    assert [c["txt"] for c in labor["cells"][:12]] == [str(v) for v in LABOR], "소계 월 칸이 하위 합이 아니다"
    q1 = labor["cells"][12]
    assert q1["txt"] == str(sum(LABOR[:3])), "소계 1Q가 파일 값에 묶여 있다"
    assert "ovf-err" in q1["flag"], f"올라온 값과 다른 칸인데 표시가 없다(flag={q1['flag']!r})"
    assert str(FILE_Q1_LABOR) in q1["title"] and str(sum(LABOR[:3])) in q1["title"], q1["title"]
    assert labor["cells"][16]["txt"] == str(sum(LABOR))
    # 파일 값과 같은 칸은 칠하지 않는다 — 전부 빨가면 어디가 문제인지 못 고른다.
    assert not labor["cells"][0]["flag"], "월 칸은 파일과 같은데 칠했다"


def test_grid_tells_a_reported_zero_apart_from_a_blank(result):
    """0으로 보고한 달은 "0", 아직 안 올라온 달만 "–" — 그리드도 미리보기와 같은 구분을 한다.

    업로드 파싱(null ≠ 0) · 서버 저장(missing_cell) · 미리보기(ovModVal)가 전부 지켜 온 구분인데
    취합 그리드만 ovF.usd를 그대로 써서 0을 "–"로 적고 있었다(2026-11-19 고침). 마지막 표시에서
    뭉개지면 담당자 입장에서는 "연구소가 0을 냈다"와 "아직 안 냈다"가 같은 화면이 된다.
    """
    welf = _row(result["grid"], "Welfare, Health Insurance")
    assert [c["txt"] for c in welf["cells"][:12]] == ["0"] * 12, "0으로 보고한 달이 –로 보인다"
    assert welf["cells"][16]["txt"] == "0", "0만 더한 연간 칸이 –로 보인다"

    bonus = _row(result["grid"], "Bonus (MBO etc)")
    assert bonus["cells"][2]["txt"] == "–", "안 올라온 달은 –여야 한다"


def test_upload_does_not_write_to_the_server_in_this_test(result):
    """이 테스트가 개발 DB를 건드리지 않는다는 것 자체를 고정한다(쓰기 요청은 하네스가 가로챈다)."""
    assert all("anomaly-scan" not in w for w in result["writes"])


def test_every_finding_the_server_sent_is_shown_in_the_modal(result):
    """서버가 보낸 지적이 **빠짐없이** 미리보기에 글자로 붙는다.

    판정이 맞는지는 test_anomaly.py가 보고, 여기서는 "걸렀는데 화면에 안 보이는" 쪽을 막는다 —
    그 경우 담당자는 경고가 없는 줄 알고 반영한다. 화면은 설명을 title에 `기준: 메시지` 형태로
    넣으므로(ovModFlags) 그 문장이 어느 탭에든 있는지 본다.
    """
    shown = [t for tab in result["tabs"] for t in tab["titles"]]
    sent = [f for c in result["scan"] for f in c["findings"]]
    assert sent, "이 파일은 경고가 나야 하는 파일인데 서버가 아무것도 돌려주지 않았다"
    missing = [f["tip"] for f in sent if not any(f["tip"] in t for t in shown)]
    assert not missing, "서버는 걸렀는데 화면에 안 보이는 지적: " + json.dumps(missing, ensure_ascii=False, indent=1)


def test_only_whole_new_rows_are_marked_row_wide(result):
    """행 전체 표시는 "행이 통째로 새것"일 때만 — 칸을 짚을 수 있는 지적까지 행으로 칠하면
    어디를 고쳐야 하는지가 가려진다(anomaly.py 모듈 docstring의 약속).

    서버 쪽 같은 약속은 test_anomaly.py::test_only_new_rows_are_marked_row_wide가 본다. 여기서는
    화면까지 온 findings로 다시 확인한다 — 화면이 칸을 못 찾으면 행 테두리로 떨어지기 때문이다.
    """
    row_wide = [f for c in result["scan"] for f in c["findings"]
                if f["month"] is None and not f["months"] and not f["block"] and not f["row"]]
    bad = [f["tip"] for f in row_wide if f["rule"] not in ("new_category", "jump")]
    assert not bad, "칸을 짚을 수 있는데 행 전체로 표시된 지적: " + json.dumps(bad, ensure_ascii=False)


def test_a_total_that_disagrees_with_its_rows_shows_the_computed_value(result):
    """A 100 · B 100인데 총계가 400으로 온 경우 — 표에는 **집계한 값**이 들어간다.

    이 파일의 개발비(매출)은 하위 합보다 7 크게 적혀 있다(FILE_TOT). 담당자가 미리보기 지적을
    보고도 안 고치고 반영하면, 표가 올라온 400을 그대로 들고 있어서는 안 된다 — 그 숫자는 아래
    줄들과 더해지지 않는 값이라, 표 안에서 합이 안 맞는 채로 남는다.
    """
    tot = _row(result["grid"], "Development Expense Total (A+B)")
    assert [c["txt"] for c in tot["cells"][:12]] == [str(v) for v in LABOR], "올라온 총계가 그대로 들어갔다"


def test_that_mismatching_total_is_painted_and_explains_itself_on_hover(result):
    """그 칸은 빨갛고, 마우스를 올리면 올라온 값과 집계한 값을 같이 알려 준다."""
    tot = _row(result["grid"], "Development Expense Total (A+B)")
    for j in range(12):
        c = tot["cells"][j]
        assert "ovf-err" in c["flag"], f"{j+1}월 칸에 이상값 표시가 없다(flag={c['flag']!r})"
        assert str(FILE_TOT[j]) in c["title"], f"{j+1}월 안내에 올라온 값이 없다: {c['title']!r}"
        assert str(LABOR[j]) in c["title"], f"{j+1}월 안내에 집계한 값이 없다: {c['title']!r}"
