"""화면이 열리는지 — 헤드리스 브라우저로 화면 22개를 띄워 JS 에러와 빈 화면을 잡는다.

왜 필요한가 (2026-11-18): core.js에서 아무도 부르지 않는 함수 68개를 지웠다. "참조가 없으니
못 불린다"는 정적 판정은 맞지만, 그걸 **실제로 띄워서** 확인할 방법이 레포에 없었다 — 그때는
삭제 전 사본을 손에 들고 일회성 스크립트로 비교했고, 그 스크립트는 남지 않았다. 다음 사람이
같은 정리를 할 때 같은 확인을 돌릴 수 있어야 한다.

**글자를 저장해 두고 비교하지는 않는다.** 이 프로젝트의 화면은 자주 바뀌어서, "전과 같은 글자"로
묶으면 문구 하나 고칠 때마다 정답 파일을 같이 고쳐야 하고 결국 아무도 안 본다. 숫자가 맞는지는
백엔드 테스트가 지키고, 여기서는 "코드를 지웠더니 화면이 깨졌다" 한 가지만 본다.

돌아가는 조건(없으면 스킵한다 — CI에 브라우저가 없어도 다른 테스트를 막지 않는다):
  - node + playwright:  npm i -D playwright && npx playwright install chromium
  - frontend-html/shared/core.js
  - 로컬 Postgres (이 테스트가 백엔드를 직접 띄운다 — 떠 있는 서버가 필요하지 않다)
"""

import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
HARNESS = HERE / "screens_harness.mjs"
CORE_JS = HERE.parents[2] / "frontend-html" / "shared" / "core.js"
HAS_NODE = shutil.which("node") is not None

# 화면 하나가 "비어 있지 않다"고 볼 최소 글자 수. 사이드바·헤더만 그려도 이 정도는 나오므로,
# 이보다 적으면 본문이 안 그려진 것이다(빈 화면 · 렌더 중단).
MIN_TEXT = 300

# 브라우저를 켜서 화면 22개를 도는 데 2분쯤 걸린다 — 나머지 테스트(80초)와 섞으면 매번 돌릴
# 엄두가 안 나므로 slow로 표시한다. 평소: pytest -m "not slow" · 화면을 건드렸으면 전체.
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not (HAS_NODE and HARNESS.exists() and CORE_JS.exists()),
        reason="node 또는 프런트 소스(frontend-html/shared/core.js)가 없어 화면을 띄울 수 없다",
    ),
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def backend():
    """테스트가 직접 띄우는 백엔드. 화면이 숫자를 서버에서 받으므로 서버 없이는 확인이 안 된다.

    uvicorn을 스레드에서 돌린다(별 프로세스로 띄우면 DB 설정·import 경로를 다시 맞춰야 한다).
    DB에 못 붙으면 startup에서 터지지 않고 요청 때 500이 나므로, 여기서는 /docs만 확인한다 —
    화면 쪽 에러는 하네스가 잡는다.
    """
    import uvicorn

    from app.main import app

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
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


@pytest.fixture(scope="module")
def result(backend):
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
    return data


def test_every_screen_opens_without_js_errors(result):
    """화면마다 JS 에러가 0건이어야 한다 — 지운 함수를 부르면 여기서 터진다."""
    broken = {s["name"]: s["errors"] for s in result["screens"] if s["errors"]}
    assert not broken, "JS 에러가 난 화면: " + json.dumps(broken, ensure_ascii=False, indent=1)


def test_no_screen_is_blank(result):
    """본문이 그려져야 한다. 렌더가 중간에 멈추면 글자 수가 뚝 떨어진다."""
    thin = {s["name"]: s["len"] for s in result["screens"] if s["len"] < MIN_TEXT}
    assert not thin, f"본문이 {MIN_TEXT}자 미만인 화면: " + json.dumps(thin, ensure_ascii=False)


def test_checks_the_screens_we_care_about(result):
    """확인한 화면 목록이 줄어들지 않게 한다 — 화면을 추가했는데 하네스에 안 넣으면 보호가 비는 것이
    티가 안 난다. 화면을 늘렸으면 이 숫자도 같이 올릴 것."""
    names = [s["name"] for s in result["screens"]]
    assert len(names) >= 22, f"화면이 {len(names)}개로 줄었다: {names}"
    for must in ("해외연구소 2단계 · 집계", "마감 2단계 · 매핑(해외연구소)", "분석 · 증감분석"):
        assert must in names, f"{must} 화면이 목록에서 빠졌다"
