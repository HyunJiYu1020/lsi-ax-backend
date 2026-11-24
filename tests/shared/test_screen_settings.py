"""화면 표시 설정 도구 (D3 · TC-047·065·093).

저장된 데이터를 건드리지 않고 **보는 방식만** 바꾸는 도구들이다. 그래서 셋 다 승인 팝업 없이 바로
실행된다 — 숫자를 눈으로 비교하려는 흐름이 승인 때마다 끊기면 안 된다.

프로젝트 방침상 이 값들은 브라우저에 남는다(CONNECTIONS.md 2-5절: "화면 위치·보기는 브라우저 —
사람마다 달라야 하는 값"). 로그인이 없어서 서버에 "누구의 설정"으로 적을 수도 없다.
여기서는 도구가 돌려주는 계약만 고정한다 — 화면 적용은 core.js가 한다.
"""

import asyncio

import pytest

from app import mcp_server as m
from app.shared.endpoints.chat import _is_write_tool


def _run(coro):
    return asyncio.run(coro)


# ---------- TC-047 표시 단위 ----------
def test_krw_and_dollar_units_are_separate_axes():
    """달러 칸과 원화 칸은 다른 축이다 — 해외연구소 표에는 둘이 같이 있어서, 한쪽을 바꾼다고
    다른 쪽이 바뀌면 "백만원으로 보여줘"에 달러 칸까지 $로 변한다."""
    krw = _run(m.set_display_unit(krw_unit="백만원"))
    assert krw["krw_unit"] == 1_000_000 and krw["krw_unit_name"] == "백만원"
    assert "display_unit" not in krw, "원화만 바꿨는데 달러 칸 설정이 따라왔다"

    usd = _run(m.set_display_unit(unit="dollar"))
    # 저장값이 $라서 $ 모드는 배수 1이다(K$ 모드가 1000으로 나눈다 — 2026-11-18 단위 전환).
    assert usd["display_unit"] == "dollar" and usd["factor"] == 1
    assert "krw_unit" not in usd


@pytest.mark.parametrize("word,won", [("원", 1), ("백만원", 1_000_000), ("백만", 1_000_000),
                                      ("억원", 100_000_000), ("million", 1_000_000), ("eok", 100_000_000)])
def test_krw_unit_words(word, won):
    assert _run(m.set_display_unit(krw_unit=word))["krw_unit"] == won


def test_display_unit_rejects_nonsense():
    for kw in (dict(unit="원"), dict(krw_unit="dollar"), dict()):
        with pytest.raises(ValueError):
            _run(m.set_display_unit(**kw))


def test_download_keeps_original_amounts():
    """표시 단위는 화면에만 적용된다 — 엑셀로 받으면 원래 금액이 내려간다(확인 포인트)."""
    assert "원래 금액" in _run(m.set_display_unit(krw_unit="백만원"))["download_unit"]


# ---------- TC-065 주요 변동만 표시 ----------
def test_variance_filter_both_conditions():
    """두 기준을 주면 **둘 다 넘는 것만** 남는다(이상 감지 급변 기준과 같은 결합)."""
    out = _run(m.set_variance_filter(pct=10, amount=1e8))
    assert (out["pct"], out["amount"], out["enabled"]) == (10, 1e8, True)
    assert out["basis"] == "연간 증감", "월별로 보면 한 달만 튀어도 남아서 '주요 변동'이 아니다"


def test_variance_filter_off():
    out = _run(m.set_variance_filter())
    assert out["enabled"] is False and "전부 보인다" in out["note"]


def test_variance_filter_rejects_negative():
    with pytest.raises(ValueError):
        _run(m.set_variance_filter(pct=-1))


# ---------- TC-093 적용 환율 기본 표시 통화 ----------
def test_pin_fx_currency():
    out = _run(m.pin_fx_currency("twd"))
    assert out["currency"] == "TWD" and out["show"] is True
    assert out["persists"] is True, "다음 접속에도 남아야 한다"
    assert "보는 사람 화면에만" in out["scope"], "다른 담당자 화면은 안 바뀌어야 한다"

    assert _run(m.pin_fx_currency("TWD", show=False))["show"] is False


@pytest.mark.parametrize("bad", ["US", "1234", "", "KRW원"])
def test_pin_fx_currency_rejects_bad_code(bad):
    """3자리 영문만 받는다. 형식 검사까지다 — 실재하는 통화인지는 보지 않는다.

    'WON'처럼 형식만 맞는 오타는 통과하고 빈 줄이 생긴다. 통화 목록으로 막지 않는 이유는 그 목록이
    업로드된 환율에서 나와서, 아직 환율을 안 올린 통화를 미리 고정해 둘 수 없게 되기 때문이다.
    대신 도구 설명이 get_fx_currency_coverage로 먼저 확인하라고 지시한다(해외-14와 같은 방식)."""
    with pytest.raises(ValueError):
        _run(m.pin_fx_currency(bad))


# ---------- 셋 다 승인 없이 실행된다 ----------
@pytest.mark.parametrize("name", ["set_display_unit", "set_variance_filter", "pin_fx_currency"])
def test_view_tools_skip_approval(name):
    """보는 방식만 바꾸는 도구라 승인 팝업을 띄우지 않는다 — 저장된 값을 건드리지 않는다."""
    assert not _is_write_tool(name, {})
