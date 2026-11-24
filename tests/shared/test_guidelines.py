"""업무 지침(AI-18) — 등록·충돌 교체·삭제·프롬프트 주입.

지침 기능의 요점은 "한 번 말하면 계속 적용된다"다. 그래서 테스트도 그 쪽을 지킨다:
- 같은 주제를 다시 등록하면 **덮어쓴다**(같은 주제 지침이 둘 이상 살아 있으면 모델이 매번 다른
  걸 골라서 지난주와 이번주 보고서 단위가 달라진다 — TC-091)
- 적용 범위가 두 축(계정 × 기능)으로 겹쳐 걸린다(번역 지침이 대화 답변까지 바꾸면 안 된다)
- 등록한 문장이 시스템 프롬프트에 **그대로** 들어간다(안 들어가면 지침 기능 자체가 무의미하다)
"""

import pytest
from fastapi import HTTPException

from app.shared.core.chat_prompts import build_system_prompt
from app.shared.core.guidelines import guidelines_text, read_guidelines
from app.shared.endpoints.guideline import delete_guideline, list_guidelines, upsert_guideline
from app.shared.schemas.guideline import GuidelineIn

ACCT = "overseas"


def _put(db, **kw):
    return upsert_guideline(GuidelineIn(**kw), db)


def test_register_and_inject(db):
    """등록한 지침 문장이 시스템 프롬프트에 그대로 들어간다 (TC-090)."""
    _put(db, topic="금액 단위", text="금액은 억원 단위로 쓴다")
    text = guidelines_text(db, ACCT, "chat")
    assert "금액은 억원 단위로 쓴다" in text
    # 주제가 같이 보여야 모델이 "이미 금액 단위 지침이 있다"를 알아챈다(충돌 감지의 전제).
    assert "[금액 단위]" in text

    prompt = build_system_prompt({"key": ACCT}, text)
    assert "금액은 억원 단위로 쓴다" in prompt
    # 지침은 불변 규칙 뒤에 와야 "표현을 다듬는 것"으로 읽힌다.
    assert prompt.index("숫자나 사실을 지어내지 말고") < prompt.index("금액은 억원 단위로 쓴다")


def test_same_topic_replaces(db):
    """같은 주제를 다시 등록하면 덮어쓰고, 바뀐 전 문장을 알려준다 (TC-091)."""
    _put(db, topic="금액 단위", text="금액은 억원 단위로 쓴다")
    out = _put(db, topic="금액 단위", text="금액은 백만원 단위로 쓴다")

    assert out.replaced_text == "금액은 억원 단위로 쓴다"
    rows = read_guidelines(db, ACCT, "chat")
    amount = [g for g in rows if g.topic == "금액 단위"]
    assert len(amount) == 1, "같은 주제 지침이 두 줄로 쌓이면 모델이 매번 다른 걸 고른다"
    assert amount[0].text == "금액은 백만원 단위로 쓴다"


def test_same_text_is_not_a_replacement(db):
    """문장이 그대로면 replaced_text는 비어 있다 — 안 바뀐 걸 "바꿨습니다"로 알리면 안 된다."""
    _put(db, topic="사유 길이", text="사유는 3줄 이내로 쓴다")
    assert _put(db, topic="사유 길이", text="사유는 3줄 이내로 쓴다").replaced_text == ""


def test_delete(db):
    """지운 지침은 프롬프트에서 빠진다 (TC-092)."""
    _put(db, topic="사유 길이", text="사유는 3줄 이내로 쓴다")
    assert "3줄" in guidelines_text(db, ACCT, "chat")

    delete_guideline(topic="사유 길이", db=db)
    assert "3줄" not in guidelines_text(db, ACCT, "chat")

    with pytest.raises(HTTPException) as e:
        delete_guideline(topic="사유 길이", db=db)
    assert e.value.status_code == 404


def test_scope_two_axes(db):
    """계정 × 기능 두 축으로 걸린다 — 번역 지침이 대화 답변까지 바꾸면 안 된다."""
    _put(db, topic="금액 단위", text="금액은 억원으로 쓴다")                                  # 전 계정 · 어디서나
    _put(db, topic="용어", text="Headcount는 인원으로 쓴다", account_key=ACCT, applies_to="translate")

    chat = guidelines_text(db, ACCT, "chat")
    assert "금액은 억원으로 쓴다" in chat
    assert "Headcount" not in chat, "번역 전용 지침이 대화 답변에 섞였다"

    translate = guidelines_text(db, ACCT, "translate")
    assert "Headcount는 인원으로 쓴다" in translate
    assert "금액은 억원으로 쓴다" in translate, "전 계정·전 기능 공통 지침은 어디서나 걸려야 한다"

    # 다른 계정 화면에서는 그 계정 전용 지침이 안 보인다.
    assert "Headcount" not in guidelines_text(db, "dep", "translate")


def test_broad_before_narrow(db):
    """넓은 지침이 먼저, 좁은 지침이 뒤에 온다 — 좁은 게 뒤에 와야 더 구체적인 지시로 읽힌다."""
    _put(db, topic="금액 단위", text="금액은 억원으로 쓴다")
    _put(db, topic="사유 작성", text="환율 변동폭을 같이 적는다", account_key=ACCT, applies_to="variance_reason")
    text = guidelines_text(db, ACCT, "variance_reason")
    assert text.index("금액은 억원으로 쓴다") < text.index("환율 변동폭을 같이 적는다")


def test_list_includes_common(db):
    """계정 지침을 물어도 전 계정 공통 지침이 같이 내려간다 — 공통 지침이 없는 것처럼 보이면 안 된다."""
    _put(db, topic="금액 단위", text="금액은 억원으로 쓴다")
    topics = {g.topic for g in list_guidelines(account_key=ACCT, applies_to="all", db=db)}
    assert "금액 단위" in topics


def test_rejects_bad_input(db):
    """빈 주제·빈 문장·없는 계정·없는 적용범위는 저장 전에 막는다."""
    for kw, code in [
        (dict(topic="   ", text="x"), 400),
        (dict(topic="t", text="   "), 400),
        (dict(topic="t", text="x", account_key="없는계정"), 400),
        (dict(topic="t", text="x", applies_to="everywhere"), 400),
    ]:
        with pytest.raises(HTTPException) as e:
            _put(db, **kw)
        assert e.value.status_code == code, kw


# ---------- 사유 초안·번역에 실제로 실리는지 ----------
# 위 테스트는 "지침이 저장되고 조회된다"까지다. 지침 기능이 값을 하는 건 그게 **프롬프트 문자열에
# 실제로 들어갈 때**라서, 초안·번역 프롬프트를 조립해 문장이 들어갔는지 따로 본다.


def test_draft_prompt_carries_guideline_and_examples(db):
    """증감사유 초안 프롬프트에 작성 지침과 지난 수정 사례가 함께 실린다 (TC-076 · TC-089)."""
    from app.shared.core.reason_translate import _draft_prompt

    _put(db, topic="사유 작성", text="환율 변동폭을 같이 적는다", account_key=ACCT, applies_to="variance_reason")
    prompt = _draft_prompt(
        "인원 증가", "more headcount", 1.2e8, 15.0,
        guidelines=guidelines_text(db, ACCT, "variance_reason"),
        examples=[("인건비 1.2억원 증가", "인건비 1.2억원 증가(인원 +3명, 환율 +2.1%)")],
    )
    assert "환율 변동폭을 같이 적는다" in prompt
    assert "인원 +3명, 환율 +2.1%" in prompt
    # 불변 규칙이 지침보다 앞에 와야 담당자 취향이 "숫자를 바꾸지 마라"를 덮어쓰지 않는다.
    assert prompt.index("다시 계산하거나 바꾸지 마라") < prompt.index("환율 변동폭을 같이 적는다")
    # 예시의 숫자를 베껴 쓰지 말라는 경고가 같이 가야 한다 — 다른 달·다른 계정 사례다.
    assert "숫자는 가져오지 마라" in prompt


def test_translate_prompt_carries_guideline(db):
    """번역 프롬프트에 번역 지침이 실리고, 용어표보다 뒤에 온다 (TC-037)."""
    from app.shared.core.reason_translate import _prompt

    _put(db, topic="용어", text="Headcount는 인원으로 쓴다", account_key=ACCT, applies_to="translate")
    prompt = _prompt(["headcount increased"], guidelines_text(db, ACCT, "translate"))
    assert "Headcount는 인원으로 쓴다" in prompt
    # 용어표는 "매달 같은 단어"를 지키는 장치지만, 담당자가 새로 정한 약속이 최신이라 뒤에 와야 한다.
    assert prompt.index("용어는 아래 표를 따른다") < prompt.index("Headcount는 인원으로 쓴다")


def test_past_draft_edits_skips_unedited(db):
    """담당자가 초안을 그대로 쓴 건은 예시에서 빠진다 — 고친 게 없으면 배울 것도 없다."""
    from datetime import datetime, timezone

    from app.accounts.overseas.endpoints.variance import past_draft_edits
    from app.shared.models.generic import VarianceReason

    common = dict(
        account_key=ACCT, basis_year=2099, basis_month=1, kind="forecast", stage="",
        base_version="prev", lab_scope="ALL", saved_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    db.add_all([
        VarianceReason(period="m1", category_key="labor", ai_draft="초안 A", reason_text="확정 A", **common),
        VarianceReason(period="m2", category_key="travel", ai_draft="초안 B", reason_text="초안 B", **common),
        VarianceReason(period="m3", category_key="welf", ai_draft="", reason_text="확정 C", **common),
    ])
    db.flush()

    pairs = past_draft_edits(db, ACCT)
    assert ("초안 A", "확정 A") in pairs
    assert ("초안 B", "초안 B") not in pairs, "고치지 않은 건은 예시 자리를 차지하면 안 된다"
    assert all(d for d, _ in pairs), "초안이 없는 건은 쌍이 될 수 없다"


# ---------- 주제가 달라도 뜻이 겹치면 되묻는다 ----------
# topic 덮어쓰기(TC-091)가 막는 건 **같은 주제**를 다시 등록하는 경우뿐이다. 주제를 조금씩 달리
# 말하면("금액 단위" → "금액 표기") 뜻이 겹치는 지침이 둘 다 살아서 같은 프롬프트에 실리고,
# 모델이 매번 다른 걸 골라 지난주와 이번주 보고서 단위가 달라진다. 지침은 화면이 없어서 담당자가
# 쌓인 걸 눈으로 훑을 자리도 없다 — 그래서 저장 전에 서버가 후보를 내고 모델이 사용자에게 묻는다.


def test_blocks_overlapping_topic(db):
    """주제가 달라도 낱말이 겹치면 저장하지 않고 후보를 돌려준다."""
    _put(db, topic="금액 단위", text="금액은 억원으로 쓴다")

    with pytest.raises(HTTPException) as e:
        _put(db, topic="금액 표기", text="금액은 백만원으로 적는다")

    assert e.value.status_code == 409, "입력이 틀린 게 아니라 저장된 것과 충돌한 것이다"
    detail = e.value.detail
    assert "[금액 단위]" in detail, "어느 지침과 겹쳤는지 보여줘야 사용자에게 물을 수 있다"
    assert "금액은 억원으로 쓴다" in detail
    # 모델이 그냥 재시도하면 같은 자리에서 또 막힌다 — 무엇을 해야 하는지 같이 알려줘야 한다.
    assert "재시도하지 말고" in detail
    assert "confirm_new_topic" in detail

    rows = read_guidelines(db, ACCT, "chat")
    assert [g.topic for g in rows] == ["금액 단위"], "막았으면 저장돼 있어서는 안 된다"


def test_confirm_new_topic_lets_it_through(db):
    """사용자가 "둘 다 필요하다"고 답한 뒤에는 그대로 저장된다."""
    _put(db, topic="금액 단위", text="금액은 억원으로 쓴다")
    _put(db, topic="금액 반영 시점", text="금액은 확정 전 값으로 쓴다", confirm_new_topic=True)

    topics = {g.topic for g in read_guidelines(db, ACCT, "chat")}
    assert topics == {"금액 단위", "금액 반영 시점"}


def test_unrelated_topic_passes_without_asking(db):
    """낱말이 안 겹치는 지침은 묻지 않고 바로 저장한다 — 매번 물으면 질문을 안 읽게 된다."""
    _put(db, topic="금액 단위", text="금액은 억원으로 쓴다")
    _put(db, topic="사유 길이", text="사유는 3줄 이내로 쓴다")  # confirm_new_topic 없이 통과해야 한다
    assert "3줄" in guidelines_text(db, ACCT, "chat")


def test_same_topic_is_replacement_not_duplicate(db):
    """같은 주제를 다시 등록하는 건 교체다 — 중복으로 막으면 지침을 못 고친다."""
    _put(db, topic="금액 단위", text="금액은 억원으로 쓴다")
    out = _put(db, topic="금액 단위", text="금액은 백만원으로 쓴다")  # 409가 나면 안 된다
    assert out.replaced_text == "금액은 억원으로 쓴다"


def test_overlap_crosses_scopes(db):
    """전 계정·어디서나 지침과 계정·기능 전용 지침도 서로 본다 — 같은 프롬프트에 함께 실린다."""
    _put(db, topic="금액 단위", text="금액은 억원으로 쓴다")  # 전 계정 · 어디서나

    with pytest.raises(HTTPException) as e:
        _put(db, topic="금액 표기", text="금액은 백만원으로 쓴다",
             account_key=ACCT, applies_to="variance_reason")
    assert e.value.status_code == 409

    # 거꾸로도 걸려야 한다: 좁은 게 먼저 있고 넓은 걸 등록할 때.
    _put(db, topic="용어 통일", text="Headcount는 인원으로 쓴다",
         account_key=ACCT, applies_to="translate", confirm_new_topic=True)
    with pytest.raises(HTTPException) as e2:
        _put(db, topic="인원 표기", text="Headcount는 명 단위로 쓴다")
    assert e2.value.status_code == 409


def test_stop_words_do_not_trigger(db):
    """'쓴다'·'지침'처럼 뜻이 없는 낱말만 겹친 건 후보가 아니다 — 아니면 전부 걸려 질문이 무의미해진다."""
    from app.shared.core.guidelines import similar_guidelines

    _put(db, topic="사유 길이", text="사유는 3줄 이내로 쓴다")
    assert similar_guidelines(db, "", "all", "보고 대상", "대상 연구소를 먼저 쓴다") == []


def test_dup_candidates_are_capped(db):
    """후보는 세 개까지만 — 넷 다섯 개를 늘어놓으면 사용자가 고르기 더 어렵다."""
    from app.shared.core.guidelines import MAX_DUP_CANDIDATES, similar_guidelines

    for i in range(5):
        _put(db, topic=f"금액 규칙 {i}", text="금액은 억원으로 쓴다", confirm_new_topic=True)
    dups = similar_guidelines(db, "", "all", "금액 표기", "금액은 백만원으로 쓴다")
    assert len(dups) == MAX_DUP_CANDIDATES
