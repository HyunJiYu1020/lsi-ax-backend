"""추가 행은 **모든 연구소에 같은 줄**로 보인다 — A에서 넣은 행이 B 표에도 선다.

왜 필요한가: 추가 행은 연구소별로 저장된다(cost_entries.scope_code). 그대로 두면 A 연구소에서
넣은 '클라우드 사용료'가 그 연구소 표에만 생겨서 **연구소마다 표의 줄 수가 달라진다**. 담당자는
연구소를 넘겨 가며 같은 자리를 보는데 줄이 들쭉날쭉하면 어느 줄이 어느 줄인지 짚을 수 없고,
다른 연구소에 같은 비용이 있는지조차 표를 하나씩 열어 봐야 안다.

여기서 고정하는 것 셋:
  - A에서 만든 행이 B 표에도 뜬다. 값은 **빈칸**이다 — 0을 넣으면 "0으로 보고했다"가 되어
    미제출과 구분이 사라진다(공통 RULE: 빈칸 ≠ 0).
  - 표에는 ✕도 자리 이름도 없다. 자리는 끌어다 놓은 위치가 정하고(ovOrderMove), 삭제·숨김은
    AI 챗봇이 맡는다 — 화면에 입력 위젯이 늘어날수록 구분 칸이 좁아져 서로를 가린다.
  - 묶는 기준은 **행 이름**이다. row_key는 연구소마다 제각각이라(cloud_fee_sarc_cpu ·
    ux_cloudfee) 공통 식별자가 못 된다 — 총계 탭(ovCustRowsAgg)과 같은 기준이다.

돌아가는 조건(없으면 스킵): node + playwright, frontend-html/shared/core.js, 로컬 Postgres.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.overseas.test_screens import backend  # noqa: F401

HERE = Path(__file__).resolve().parent
HARNESS = HERE / "shared_custom_row_harness.mjs"
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
    assert not data.get("errors") and not data.get("errs"), (
        "화면에서 JS 에러가 났다: " + json.dumps(data.get("errs") or data.get("errors"), ensure_ascii=False)
    )
    return data


def test_the_row_is_saved_where_it_was_made(result):
    """만든 연구소(SARC CPU)에는 적은 금액이 그대로 들어간다."""
    a = result["A"]
    assert len(a) == 1, f"만든 연구소에 행이 없다: {a}"
    assert a[0]["vals"][0] == "7", f"적은 금액이 안 들어갔다: {a[0]['vals']}"


def test_other_labs_get_the_same_row_but_empty(result):
    """다른 연구소(DSRA) 표에도 같은 줄이 선다 — 값은 빈칸이다(0이 아니다)."""
    b = result["B"]
    assert len(b) == 1, "다른 연구소 표에 그 줄이 없다 — 연구소마다 표 생김새가 달라진다"
    assert b[0]["vals"] == ["", "", ""], (
        f"값이 없는 연구소에 0이 채워졌다(미제출과 구분이 사라진다): {b[0]['vals']}"
    )


def test_the_grid_has_no_delete_or_anchor_widgets(result):
    """표에는 ✕도 '참고'도 자리 이름도 없다 — 자리는 끌어다 놓은 위치가 정하고,
    삭제·숨김은 AI 챗봇(delete_extra_row · 행 숨김 도구)이 맡는다."""
    assert not result["B"][0]["hasX"], "표에 ✕가 남아 있다 — 삭제는 챗봇 몫으로 옮겼다"
    assert result["B"][0]["anchor"] is None, (
        f"자리 이름이 표에 남아 있다: {result['B'][0]['anchor']}"
    )


def test_the_test_row_is_cleaned_up(result):
    """테스트가 만든 줄은 반드시 지우고 끝낸다 — 개발 DB에 남으면 다음 사람 화면에 보인다."""
    assert result["cleanup"] == 200, f"정리에 실패했다: {result['cleanup']}"
