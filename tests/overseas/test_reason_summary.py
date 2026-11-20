"""회신 사유 묶음 요약 API. 번역 테스트와 같은 이유로 실제 LLM은 부르지 않고(키·네트워크·요금에
테스트를 묶지 않는다) summarize_reasons만 가짜로 바꾼다 — 요약 '품질'이 아니라 이 API의 계약,
즉 "화면이 보낸 범위만 요약한다 / 몇 건을 봤는지 밝힌다 / 요약이 실패해도 표가 안 멈춘다"를 본다.

엔드포인트가 async인데 저장소에 pytest-asyncio가 없어서 _run이 asyncio.run으로 돌린다
(test_reason_translate.py와 같은 방식)."""

import asyncio

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints import collect as ov
from app.accounts.overseas.schemas import ReasonSummaryIn, ReasonSummaryItem
from app.shared.core.reason_translate import MAX_LEN, MAX_TEXTS, _parse_summary

EN = "Increase due to $449K higher basic salary from 3 new hires"


@pytest.fixture
def fake_llm(monkeypatch):
    """summarize_reasons를 "받은 항목 수만큼 한 줄"로 바꾸고, 무엇을 받았는지 기록한다."""
    calls: list[tuple[list[tuple[str, str]], str]] = []

    async def fake(items, scope_label="", provider=None):
        calls.append((list(items), scope_label))
        return [f"{label}: 요약" for label, _ in items]

    monkeypatch.setattr(ov, "summarize_reasons", fake)
    return calls


def _run(items, **kw):
    body = ReasonSummaryIn(items=[ReasonSummaryItem(label=l, text=t) for l, t in items], **kw)
    return asyncio.run(ov.summarize_reason_rows(body))


def test_summarizes_what_the_screen_sent(fake_llm):
    """화면이 보낸 묶음만 요약하고, 몇 건을 봤는지(count)와 어느 모델인지(engine)를 같이 돌려준다."""
    r = _run([("기본급 · 전실행 대비", EN), ("출장비 · 전실행 대비", "Decrease due to $120K")], scope_label="SARC · 연간")

    assert r.lines == ["기본급 · 전실행 대비: 요약", "출장비 · 전실행 대비: 요약"]
    assert r.count == 2  # "회신 2건을 요약했습니다"를 화면이 밝힐 수 있어야 한다
    assert r.engine and r.error == ""
    items, scope = fake_llm[0]
    assert scope == "SARC · 연간"  # 무엇을 본 요약인지 모델에게도 알려준다
    assert items[0][0] == "기본급 · 전실행 대비"


def test_blank_texts_are_dropped(fake_llm):
    """사유가 비어 있는 칸은 요약에 넣지 않는다 — 빈 칸까지 세면 '15건 요약'이 거짓말이 된다."""
    r = _run([("기본급", EN), ("임차료", "   "), ("출장비", "")])

    assert r.count == 1
    assert [label for label, _ in fake_llm[0][0]] == ["기본급"]


def test_nothing_to_summarize_is_rejected():
    """요약할 원문이 하나도 없으면 400 — 빈 요약을 성공처럼 돌려주면 화면이 빈 상자를 띄운다."""
    with pytest.raises(HTTPException) as e:
        _run([("임차료", "  ")])
    assert e.value.status_code == 400


def test_too_many_rows_are_rejected_not_truncated(fake_llm):
    """상한을 넘으면 앞에서 잘라 요약하지 않고 400으로 막는다 — 어디까지 읽은 요약인지 모르면
    담당자가 '전부 봤다'로 읽는다(공통 RULE: 말없이 자르지 않는다)."""
    with pytest.raises(HTTPException) as e:
        _run([(f"계정{i}", EN) for i in range(MAX_TEXTS + 1)])
    assert e.value.status_code == 400
    assert str(MAX_TEXTS) in e.value.detail
    assert not fake_llm  # LLM은 아예 부르지 않는다


def test_overlong_text_is_dropped_and_told(fake_llm):
    """원문 1건이 상한보다 길면 그 건만 빼고 요약하되, 뺐다는 사실을 error에 밝힌다."""
    r = _run([("기본급", EN), ("긴 사유", "x" * (MAX_LEN + 1))])

    assert r.count == 1
    assert str(MAX_LEN) in r.error  # 조용히 빼지 않는다


def test_llm_failure_keeps_the_table_alive(monkeypatch):
    """LLM이 실패해도 200으로 답한다 — 여기서 502를 던지면 사유 표 전체가 에러 화면으로 바뀐다."""

    async def boom(items, scope_label="", provider=None):
        raise HTTPException(status_code=502, detail="키가 없습니다")

    monkeypatch.setattr(ov, "summarize_reasons", boom)
    r = _run([("기본급", EN)])

    assert r.lines == [] and r.count == 1
    assert "키가 없습니다" in r.error


def test_parse_summary_survives_a_chatty_model():
    """모델이 설명과 코드펜스를 붙여 보내도 요약 줄만 떼어 읽는다(번역 파서와 같은 사정)."""
    raw = '물론입니다!\n```json\n{"lines": ["인건비: 신규 채용 3명", "  ", "출장비: 이연"]}\n```'
    assert _parse_summary(raw) == ["인건비: 신규 채용 3명", "출장비: 이연"]
    assert _parse_summary("요약할 수 없습니다") == []
