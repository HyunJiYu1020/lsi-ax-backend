"""말 한마디 → 모델이 어떤 쓰기 도구를 고르나 (scenarios/chat_tools.yaml).

왜 여기까지만 보나: 쓰기 도구는 실행 전에 승인 카드(`pending`)로 올라온다. 그래서 **저장은
일어나지 않고**, MCP 서버가 안 떠 있어도 "무엇을 하려 했나"는 판정할 수 있다. 우리가 고치는
것(프롬프트·도구 설명·차단 규칙)이 바꾸는 것도 딱 그 지점이다.

조회 도구를 고른 경우엔 MCP가 REST를 부르러 간다 — 서버가 안 떠 있으면 도구 오류가 모델에게
전달되고 모델이 글로 답한다(죽지는 않는다). `no_write_tool`·`asks_user` 판정에는 영향이 없다.
"""

import os
from pathlib import Path

import pytest
import yaml

from app.shared.core.config import settings
from app.shared.endpoints.chat import _chat_guidelines, _run_turn
from app.shared.endpoints.guideline import upsert_guideline
from app.shared.schemas.guideline import GuidelineIn

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        os.getenv("RUN_EVAL") != "1",
        reason="RUN_EVAL=1로만 돈다 — 실제 LLM을 부른다(tests/eval/README.md)",
    ),
    pytest.mark.skipif(not settings.gemini_api_key, reason="GEMINI_API_KEY가 없다"),
]

# 프런트(agChatCtx)가 매 요청 보내는 모양을 그대로 흉내 낸다. 모델의 답은 context에 크게 좌우된다 —
# step(화면 탭)·usd_unit(표시 단위)이 빠지면 여기서 통과한 시나리오가 실제 화면에서 다르게 돈다.
BASE_CONTEXT = {
    "key": "overseas", "name": "해외연구소", "step": "취합",
    "lab_pk": "SARC:CPU", "version": "cur", "version_api": "forecast",
    "yyyymm_api": 209801, "year": 2098, "usd_unit": "kdollar",
}

SCENARIOS = yaml.safe_load((Path(__file__).parent / "scenarios" / "chat_tools.yaml").read_text())


def _given(sc: dict, db) -> None:
    """시나리오가 "이미 걸려 있다"고 한 지침을 먼저 넣는다. db 픽스처가 롤백하므로 남지 않는다."""
    for g in (sc.get("given") or {}).get("guidelines") or []:
        upsert_guideline(GuidelineIn(confirm_new_topic=True, **g), db)


def _check(sc: dict, resp, expect: dict) -> None:
    pending, reply = resp.pending, resp.reply or ""
    where = f"[{sc['id']}] {sc.get('why', '')}\n말: {sc['say']}\n답: {reply[:300]}"

    if expect.get("asks_user") or expect.get("no_write_tool"):
        assert pending is None, f"{where}\n→ 묻거나 멈춰야 하는데 {pending.name}을 부르려 했다"
    if expect.get("asks_user"):
        assert reply.strip(), f"{where}\n→ 되물어야 하는데 답이 비었다"

    if "tool" in expect:
        assert pending is not None, f"{where}\n→ {expect['tool']}을 부를 줄 알았는데 도구를 안 불렀다"
        assert pending.name == expect["tool"], f"{where}\n→ {pending.name}을 불렀다"
    # 부분 일치로 본다 — 전체 비교로 두면 도구 인자가 하나 늘 때마다 시나리오를 다 고쳐야 하고,
    # 그러면 아무도 안 고치고 세트가 썩는다.
    for key, want in (expect.get("args") or {}).items():
        got = pending.input.get(key)
        assert got == want, f"{where}\n→ 인자 {key}: {got!r} (기대 {want!r})"
    for key in expect.get("args_absent") or []:
        got = pending.input.get(key)
        assert got in (None, ""), f"{where}\n→ 말하지 않은 인자 {key}를 {got!r}로 채웠다"

    for s in expect.get("reply_has") or []:
        assert s in reply, f"{where}\n→ 답에 '{s}'가 없다"
    for s in expect.get("reply_lacks") or []:
        assert s not in reply, f"{where}\n→ 답에 '{s}'가 들어갔다"


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_chat_tool_choice(db, sc, run_llm):
    _given(sc, db)
    context = {**BASE_CONTEXT, **(sc.get("context") or {})}
    resp = run_llm(
        lambda: _run_turn(
            [{"role": "user", "content": sc["say"]}],
            context,
            "gemini",
            guidelines=_chat_guidelines(context, db),
            db=db,
        )
    )
    _check(sc, resp, sc["expect"])
