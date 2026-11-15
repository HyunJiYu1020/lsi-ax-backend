"""증감사유 번역 API. 실제 LLM은 부르지 않고(테스트가 키·네트워크·요금에 묶이면 안 된다)
translate_batch만 가짜로 바꿔서, 캐시·중복 제거·실패 처리가 맞는지 본다 — 번역 품질이 아니라
"같은 원문을 두 번 번역하지 않는다 / 번역이 실패해도 화면이 멈추지 않는다"가 이 API의 계약이다.

엔드포인트가 async인데 이 저장소엔 pytest-asyncio가 없어서, 테스트는 평범한 동기 함수로 쓰고
_run이 asyncio.run으로 한 번 돌린다 — 번역 API 하나 때문에 테스트 플러그인을 늘리지 않는다."""

import asyncio

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import overseas as ov
from app.core.reason_translate import MAX_TEXTS, _parse, normalize
from app.schemas.overseas import ReasonTranslateIn

EN = "higher basic salary from new hires"


@pytest.fixture
def fake_llm(monkeypatch):
    """translate_batch를 "영문 → [번역] 영문"으로 바꾸고, 몇 건을 몇 번 물었는지 기록한다."""
    calls: list[list[str]] = []

    async def fake(texts, provider=None):
        calls.append(list(texts))
        return [f"[번역] {t}" for t in texts]

    monkeypatch.setattr(ov, "translate_batch", fake)
    return calls


def _run(db, texts, **kw):
    return asyncio.run(ov.translate_reasons(ReasonTranslateIn(texts=texts, **kw), db=db))


def test_translates_and_then_serves_from_cache(db, fake_llm):
    """같은 원문을 두 번 요청하면 두 번째는 LLM을 부르지 않고 저장된 번역을 그대로 준다."""
    first = _run(db, [EN])
    assert [i.ko for i in first.items] == [f"[번역] {EN}"]
    assert (first.translated, first.cached, first.failed) == (1, 0, 0)
    assert first.items[0].cached is False
    assert first.items[0].engine  # 어느 모델이 번역했는지 같이 돌려준다

    second = _run(db, [EN])
    assert [i.ko for i in second.items] == [f"[번역] {EN}"]
    assert (second.translated, second.cached) == (0, 1)
    assert second.items[0].cached is True
    assert len(fake_llm) == 1 and fake_llm[0] == [EN]  # 두 번째 요청은 LLM을 안 불렀다


def test_duplicates_and_whitespace_collapse_to_one_call(db, fake_llm):
    """공백·줄바꿈만 다른 같은 문장은 한 건으로 묶어서 한 번만 번역한다
    (엑셀에서 긁어온 원문은 같은 문장이 '- Increase  mainly' / 'Increase mainly'로 섞여 온다)."""
    texts = [f"- {EN}", f"-  {EN}", f"- {EN}\n"]
    r = _run(db, texts)

    assert len(r.items) == 1  # 중복 제거 — 원문 1건
    assert r.items[0].source == normalize(f"- {EN}")
    assert len(fake_llm) == 1 and len(fake_llm[0]) == 1
    assert r.translated == 1


def test_refresh_overwrites_stored_translation(db, monkeypatch):
    """refresh=True면 저장된 번역을 무시하고 다시 번역해서 덮어쓴다(모델 교체·오역 수정)."""
    async def v1(texts, provider=None):
        return ["구버전 번역"] * len(texts)

    async def v2(texts, provider=None):
        return ["신버전 번역"] * len(texts)

    monkeypatch.setattr(ov, "translate_batch", v1)
    assert _run(db, [EN]).items[0].ko == "구버전 번역"

    monkeypatch.setattr(ov, "translate_batch", v2)
    assert _run(db, [EN]).items[0].ko == "구버전 번역"  # 기본은 캐시 우선
    assert _run(db, [EN], refresh=True).items[0].ko == "신버전 번역"
    assert _run(db, [EN]).items[0].ko == "신버전 번역"  # 덮어써져서 다음부터도 신버전


def test_llm_failure_keeps_cached_rows_and_reports_reason(db, monkeypatch, fake_llm):
    """LLM이 죽어도 200으로 답하고, 이미 번역해 둔 건은 그대로 준다 — 화면은 ko=null인 건만
    원문으로 그린다. 실패 이유(키 없음 등)는 error에 담아 보낸다."""
    _run(db, [EN])  # 한 건은 미리 캐시

    async def boom(texts, provider=None):
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY가 설정되지 않았습니다.")

    monkeypatch.setattr(ov, "translate_batch", boom)
    r = _run(db, [EN, "brand new reason text"])

    by_src = {i.source: i for i in r.items}
    assert by_src[EN].ko == f"[번역] {EN}" and by_src[EN].cached is True
    assert by_src["brand new reason text"].ko is None
    assert r.failed == 1 and "GEMINI_API_KEY" in r.error


def test_blank_texts_dropped_and_batch_limit_enforced(db, fake_llm):
    """빈 칸(사유 미기재)은 번역 대상이 아니고, 한 번에 보낼 수 있는 건수에는 상한이 있다."""
    r = _run(db, ["", "   ", "\n"])
    assert r.items == [] and not fake_llm  # 부를 게 없으면 LLM도 안 부른다

    with pytest.raises(HTTPException) as e:
        _run(db, [f"reason {i}" for i in range(MAX_TEXTS + 1)])
    assert e.value.status_code == 400 and str(MAX_TEXTS) in e.value.detail


def test_stored_pairs_are_searchable(db, fake_llm):
    """원문 · 번역 쌍이 목록으로 조회된다(오역 찾기용). 원문 쪽 검색도 번역 쪽 검색도 걸린다."""
    _run(db, [EN])

    assert any(i.source == EN for i in ov.list_reason_translations(q="basic salary", db=db))
    assert any(i.source == EN for i in ov.list_reason_translations(q="[번역]", db=db))
    assert ov.list_reason_translations(q="없는말zzz", db=db) == []


def test_parse_reads_json_array_even_with_fence_and_partial_result():
    """모델이 코드펜스·설명을 붙여 보내도 배열만 떼어 읽고, 일부만 돌아오면 나머지는 None으로 둔다
    (빠진 건은 다음 호출에서 다시 물어본다 — 빈 문자열로 덮어쓰면 캐시에 빈 번역이 박힌다)."""
    raw = '설명 한 줄\n```json\n[{"i": 0, "ko": "첫째"}, {"i": 2, "ko": "셋째"}]\n```'
    assert _parse(raw, 3) == ["첫째", None, "셋째"]
    assert _parse("JSON이 아닌 답", 2) == [None, None]
    assert _parse('[{"i": 0, "ko": ""}, {"i": 1, "ko": null}]', 2) == [None, None]
    # i가 빠져 있으면 나온 순서를 번호로 본다(모델이 i를 생략할 때가 있다).
    assert _parse('[{"ko": "하나"}, {"ko": "둘"}]', 2) == ["하나", "둘"]


def test_normalize_collapses_whitespace_only():
    """정규화는 공백만 건드린다 — 금액·기호를 지우면 캐시 키가 다른 문장을 같은 문장으로 묶는다."""
    assert normalize("  $449K   salary\nothers ") == "$449K salary others"
    assert normalize(None) == ""
