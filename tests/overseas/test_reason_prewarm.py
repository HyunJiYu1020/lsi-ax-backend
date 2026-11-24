"""증감사유 업로드 시점 번역 — 조회할 때마다 같은 값이 나오게.

왜 조회 때가 아니라 업로드 때 번역하나: 조회 때 번역하면 첫 번째로 화면을 연 사람은 LLM을
기다리는 동안 폴백 번역(core.js ovTrRule — 목업 시절 사전·정규식)을 보고, 번역이 도착하면 눈앞에서
값이 바뀐다. LLM이 실패하면 그 폴백이 그대로 남아서 사람마다·시점마다 다른 문장이 보인다.

그리고 번역 지침(guidelines applies_to='translate')이 바뀌면 캐시가 자동으로 미스가 돼야 한다 —
담당자가 "Headcount는 인원으로 써"를 등록했는데 옛 번역이 계속 나오면 기능이 안 되는 것으로 읽는다.
"""

import asyncio

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select

from app.accounts.overseas.schemas import ReasonRow, ReasonRowsIn
from app.accounts.overseas.endpoints import collect as ov
from app.accounts.overseas.services import reason_rows as rr
from app.shared.core.guidelines import guide_fingerprint
from app.shared.endpoints.guideline import upsert_guideline
from app.shared.schemas.guideline import GuidelineIn
from app.shared.models.generic import TextTranslation

LAB, VER = "SARC", "forecast"
# 증감사유는 "어느 달 회신인지"(yyyymm)를 들고 다닌다 — 없으면 저장이 400으로 막힌다.
YM = "202609"
EN1 = "Increase mainly due to additional headcount"
EN2 = "Decrease due to delayed tape-out"


def _rows(**kw):
    base = dict(lab_code=LAB, version=VER, yyyymm=YM, category_key="labor", block="y",
                remark_plan=EN1, remark_prev=EN2)
    base.update(kw)
    return [ReasonRow(**base)]


@pytest.fixture
def fake_llm(monkeypatch):
    """translate_batch를 "[번역] 원문"으로 바꾸고 몇 건을 몇 번 물었는지 기록한다."""
    calls = []

    async def fake(texts, provider=None, guidelines=""):
        calls.append({"texts": list(texts), "guidelines": guidelines})
        return [f"[번역] {t}" for t in texts]

    monkeypatch.setattr(rr, "translate_batch", fake)
    return calls


def _cached(db, text):
    from app.shared.core.values import source_hash
    return db.scalar(select(TextTranslation).where(TextTranslation.source_hash == source_hash(text)))


def _save(db, rows):
    """저장 + 번역을 한 번에 — 엔드포인트는 번역을 응답 뒤로 미루므로(BackgroundTasks) 여기서는
    저장을 부른 뒤 번역 함수를 직접 돌린다. 실제로도 같은 순서로 일어난다.

    prewarm이 async인데 저장소에 pytest-asyncio가 없어서 asyncio.run으로 돌린다
    (test_reason_translate·test_reason_summary와 같은 관례 — 테스트 플러그인을 늘리지 않는다)."""
    saved = ov.save_reason_rows(ReasonRowsIn(rows=rows), BackgroundTasks(), db)
    return {**saved, "translated": asyncio.run(rr.prewarm_reason_translations(db, rows))}


def test_upload_stores_translation(db, fake_llm):
    """업로드하면 그 자리에서 번역해 DB에 넣는다 — 조회가 LLM을 타지 않는다."""
    out = _save(db, _rows())

    assert out["status"] == "ok" and out["saved"] == 1
    assert out["translated"]["translated"] == 2      # 원문 2건(vs PLAN · vs 전기)
    assert len(fake_llm) == 1, "원문을 모아 한 번에 보내야 한다"
    assert sorted(fake_llm[0]["texts"]) == sorted([EN1, EN2])

    for en in (EN1, EN2):
        row = _cached(db, en)
        assert row is not None and row.ko_text == f"[번역] {en}"


def test_reupload_does_not_retranslate(db, fake_llm):
    """재업로드는 다시 번역하지 않는다 — 연구소가 수정본을 보내는 일이 흔해서 매번 돌리면 비용만 든다."""
    _save(db, _rows())
    out = _save(db, _rows())

    assert len(fake_llm) == 1, "두 번째 업로드가 또 번역했다"
    assert out["translated"] == {"total": 2, "cached": 2, "translated": 0, "error": ""}


def test_same_text_is_translated_once(db, fake_llm):
    """줄바꿈·연속 공백만 다른 같은 문장은 한 번만 번역한다(캐시 키와 같은 정규화)."""
    rows = _rows(remark_plan=EN1, remark_prev="Increase   mainly due to\n additional headcount")
    _save(db, rows)
    assert fake_llm[0]["texts"] == [EN1]


def test_guideline_change_invalidates_cache(db, fake_llm):
    """번역 지침을 바꾸면 캐시가 미스가 되고 다시 번역한다 — refresh를 기억할 필요가 없다."""
    _save(db, _rows())
    assert _cached(db, EN1).guide_hash == "", "지침이 없으면 지문은 빈 문자열이다"

    upsert_guideline(
        GuidelineIn(topic="용어", text="Headcount는 인원으로 쓴다",
                    account_key="overseas", applies_to="translate"), db
    )
    gh = guide_fingerprint(db, "overseas", "translate")
    assert gh, "지침을 등록했으면 지문이 생긴다"

    out = _save(db, _rows())
    assert out["translated"]["translated"] == 2, "지침이 바뀌었는데 다시 번역하지 않았다"
    assert len(fake_llm) == 2
    assert "Headcount는 인원으로 쓴다" in fake_llm[1]["guidelines"], "지침이 프롬프트에 안 실렸다"
    assert _cached(db, EN1).guide_hash == gh

    # 같은 지침이면 또 번역하지 않는다
    _save(db, _rows())
    assert len(fake_llm) == 2


def test_llm_failure_does_not_block_upload(db, monkeypatch):
    """번역이 실패해도 업로드는 끝난다 — 연구소가 보낸 자료를 받는 게 먼저다."""
    from fastapi import HTTPException

    async def boom(texts, provider=None, guidelines=""):
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY가 설정되지 않았습니다.")

    monkeypatch.setattr(rr, "translate_batch", boom)
    out = _save(db, _rows())

    assert out["status"] == "ok" and out["saved"] == 1
    assert out["translated"]["translated"] == 0
    assert "GEMINI_API_KEY" in out["translated"]["error"]
    assert _cached(db, EN1) is None


def test_reason_texts_skips_blanks(db):
    """사유가 비어 있는 행은 번역 대상이 아니다."""
    assert rr.reason_texts(_rows(remark_plan="", remark_prev="   ")) == []
    assert rr.reason_texts(_rows(remark_plan=EN1, remark_prev="")) == [EN1]
