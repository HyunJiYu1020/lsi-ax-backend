"""화면 표시 단위에 맞춰 들어온 금액이 저장 단위($)로 환산되는지 — DB 없이 순수 함수만 본다.

저장은 $, 화면 기본 표시는 K$다(2026-11-18 단위 전환). 그래서 기본(K$ 화면)에서 들어온 금액은
×1000이고, "$로 보여줘" 상태에서 들어온 금액은 이미 $라서 그대로 간다 — 전환 전과 방향이 반대다.
"""

import asyncio

import pytest

from app.shared.endpoints.chat import SAFE_NON_PREFIXED_TOOLS, _is_write_tool, _to_stored_dollar, _tool_result_text
from app.mcp_server import set_display_unit

DOLLAR = {"usd_unit": "dollar"}
KDOLLAR = {"usd_unit": "kdollar"}


def test_amount_untouched_when_screen_already_shows_dollars():
    args = {"lab_pk": "SARC:CPU", "amount": 1_200_000, "month": 9}
    assert _to_stored_dollar("upsert_cost_entry", args, DOLLAR)["amount"] == 1_200_000
    v = _to_stored_dollar("upsert_extra_row", {"v": [1_200_000, 0, None]}, DOLLAR)["v"]
    assert v == [1_200_000, 0, None]


def test_amount_is_multiplied_by_1000_when_screen_shows_kdollars():
    """화면 기본은 K$다 — 사용자가 "1,200"이라고 말하면 저장은 $1,200,000이어야 한다."""
    args = {"amount": 1200}
    assert _to_stored_dollar("upsert_cost_entry", args, KDOLLAR)["amount"] == 1_200_000
    assert _to_stored_dollar("upsert_cost_entry", args, {})["amount"] == 1_200_000  # 키가 없으면 기본(K$)
    # 월 12개 배열(upsert_extra_row의 v)도 같이 환산된다. None은 "그 달은 미등록"이라 건드리지 않는다.
    v = _to_stored_dollar("upsert_extra_row", {"v": [1200, 0, None]}, KDOLLAR)["v"]
    assert v == [1_200_000, 0, None]


def test_non_amount_tools_untouched():
    # 추가 열(upsert_cost_extra)은 금액이 아닐 수도 있어 환산 대상이 아니다 — 화면 입력칸도 그대로 쓴다.
    assert _to_stored_dollar("upsert_cost_extra", {"value": 1200}, KDOLLAR)["value"] == 1200
    assert _to_stored_dollar("list_cost_entries", {"month": 9}, KDOLLAR)["month"] == 9


def test_set_display_unit_runs_without_approval():
    assert "set_display_unit" in SAFE_NON_PREFIXED_TOOLS
    assert not _is_write_tool("set_display_unit")


def test_set_display_unit_normalizes_aliases():
    run = lambda unit: asyncio.run(set_display_unit(unit))  # noqa: E731
    assert run("$")["display_unit"] == "dollar"
    assert run("K$")["display_unit"] == "kdollar"
    # 저장값이 $라서 $ 모드는 그대로 보여주고(배수 1), K$ 모드가 1000으로 나눈다.
    assert run("dollar")["factor"] == 1
    assert run("kdollar")["factor"] == 0.001
    with pytest.raises(ValueError):
        run("원화")


def test_bad_unit_comes_back_as_readable_tool_error():
    """잘못된 unit은 MCP가 UnexpectedToolError로 감싸는데, chat.py가 원인(ValueError) 문구를 살려서
    모델에게 돌려줘야 스스로 고쳐 재시도할 수 있다."""
    text, is_error = asyncio.run(_tool_result_text("set_display_unit", {"unit": "원화"}, {}))
    assert is_error
    assert "dollar" in text and "kdollar" in text
