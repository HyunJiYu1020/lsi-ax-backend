"""업로드 현황 격자의 ✓ 판정 — 프런트 쪽 가드레일을 Node로 실행해 확인한다.

서버 쪽("그 달에 행이 없으면 cur를 비워서 내려준다")은 test_state_roundtrip.py가 본다. 여기는
그 짝인 프런트 쪽이다: /overseas/state에 **보고 있는 달을 같이 보내는지**, 그리고 새 버전을 만들
때 직전 버전을 복사해 넣지 않는지(복사본이 남아 있으면 서버가 빈 달을 줘도 ✓가 켜진다).

검증 내용은 js_upload_status_check.mjs 안에 있다 — 수식 대조(js_formula_harness.mjs)와 같은
이유로 파이썬에 베껴 적지 않는다. 베껴 적으면 그게 세 번째 복제본이 된다.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

CHECK = Path(__file__).parent / "js_upload_status_check.mjs"
REPO = Path(__file__).resolve().parents[3]  # tests/overseas/ → backend → S.LSI (프런트가 옆에 있다)

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not (REPO / "frontend-html/shared/core.js").exists(),
    reason="node 또는 프런트 소스(frontend-html/shared/core.js)가 없어 확인할 수 없다",
)


def test_upload_status_checkmarks_follow_real_data():
    p = subprocess.run(["node", str(CHECK)], capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, f"프런트 가드레일이 깨졌다:\n{p.stdout}\n{p.stderr}"
