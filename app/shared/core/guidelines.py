"""업무 지침(guidelines) 읽기 + 프롬프트 블록 만들기 (AI-18).

왜 core에 두는가: 지침을 쓰는 곳이 한 군데가 아니다. 대화 답변(chat.py), 증감사유 초안
(reason_translate.draft_reason), 회신 번역이 모두 같은 테이블을 읽어야 한다. 각자 쿼리를 쓰면
"전 계정 공통 지침도 같이 읽는다"는 규칙이 세 곳에 복제되고, 한 곳만 고치면 조용히 어긋난다.

적용 범위는 **두 축이 겹쳐서** 걸린다 — 계정(전 계정 공통 + 그 계정)과 기능(all + 그 기능).
"금액은 억원으로 써"는 전 계정·전 기능이고 "환율 변동폭 포함해서 써"는 해외연구소·증감사유
초안에만 걸려야 한다. 넓은 것부터 좁은 것 순으로 내보내서, 좁은 지침이 뒤에 와 모델에게 더
구체적인 지시로 읽히게 한다.
"""

import hashlib
import re

from sqlalchemy import select, true
from sqlalchemy.orm import Session

from app.shared.models.generic import Guideline

# applies_to에 쓸 수 있는 값. 모델의 CheckConstraint와 같은 목록이어야 한다.
APPLIES_TO = ("all", "chat", "variance_reason", "translate")
# 사람에게 보여줄 적용 범위 이름. 승인 카드(chat.py)와 중복 확인 문구가 같은 말을 써야 한다 —
# 한쪽만 고치면 같은 지침을 "어디서나"와 "전체"로 다르게 부르게 된다.
SCOPE_LABELS = {
    "all": "어디서나", "chat": "대화 답변", "variance_reason": "증감사유 초안", "translate": "회신 번역"
}


def read_guidelines(db: Session, account_key: str = "", applies_to: str = "all") -> list[Guideline]:
    """그 계정·그 기능에 걸리는 지침을 넓은 것 → 좁은 것 순으로 돌려준다.

    account_key가 비어 있으면 전 계정 공통만, 값이 있으면 전 계정 공통 + 그 계정 것을 같이 준다.
    applies_to가 'all'이면 전 기능 공통만 준다 — 증감사유 전용 지침이 대화 답변에 섞이면
    "환율 변동폭을 포함해서 쓴다"가 집계표를 물었을 때도 따라붙는다.
    """
    account_keys = [""] if not account_key else ["", account_key]
    scopes = ["all"] if applies_to == "all" else ["all", applies_to]
    rows = db.scalars(
        select(Guideline)
        .where(Guideline.account_key.in_(account_keys), Guideline.applies_to.in_(scopes))
        .order_by(Guideline.account_key, Guideline.applies_to, Guideline.topic)
    ).all()
    # 전 계정("") → 계정별, all → 기능별 순. DB 정렬만으로는 account_key의 ""가 먼저 오는 건
    # 맞지만 applies_to는 'all'이 'chat'보다 뒤라 사전순으로는 뒤집힌다.
    return sorted(rows, key=lambda g: (g.account_key != "", g.applies_to != "all", g.topic))


def guidelines_text(db: Session, account_key: str = "", applies_to: str = "all") -> str:
    """지침을 프롬프트에 넣을 블록 문자열로 만든다. 걸리는 지침이 없으면 빈 문자열.

    주제(topic)를 대괄호로 같이 보여주는 이유: 사용자가 "금액은 백만원으로 써"라고 다시 말할 때
    모델이 자기 프롬프트에서 `[금액 단위]`를 보고 "기존에 억원 지침이 있다"를 알아차려야 한다
    (TC-091 지침 충돌). 주제가 안 보이면 같은 주제인지 모르고 새 지침으로 또 등록한다.
    """
    rows = read_guidelines(db, account_key, applies_to)
    if not rows:
        return ""
    return "\n".join(f"- [{g.topic}] {g.text}" for g in rows)


def guide_fingerprint(db: Session, account_key: str = "", applies_to: str = "all") -> str:
    """지금 걸려 있는 지침의 지문(16자). 지침이 없으면 빈 문자열.

    번역 캐시(text_translations.guide_hash)가 "이 번역이 어느 지침으로 만들어졌나"를 기억하는 데
    쓴다. 지침이 바뀌면 지문이 바뀌어서 캐시가 자동으로 미스가 되고, 그 원문만 다시 번역된다 —
    담당자가 refresh를 기억할 필요가 없어진다.

    지침이 없을 때 빈 문자열인 게 중요하다: 이 컬럼이 생기기 전에 캐시된 행도 빈 문자열이라,
    지침을 아예 안 쓰는 동안에는 기존 캐시가 그대로 유효하다(쓸데없는 재번역이 없다).
    """
    text = guidelines_text(db, account_key, applies_to)
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ---------- 같은 뜻을 다른 주제로 또 등록하는 것 막기 ----------
# topic이 키라서 **같은** 주제를 다시 등록하면 덮어쓴다. 못 막는 건 주제를 조금씩 달리 말하는
# 경우다 — "금액 단위" → "금액 표기" → "단위"로 쌓이면 뜻이 겹치는 지침이 셋 다 살아서 프롬프트에
# 같이 들어가고, 모델이 매번 다른 걸 골라 지난주와 이번주 보고서 단위가 달라진다(topic 덮어쓰기가
# 막으려던 바로 그 상태다). 지침은 화면이 없고 챗봇으로만 등록돼서, 담당자가 "지금 뭐가 걸려
# 있는지"를 눈으로 훑을 자리도 없다.

# 겹쳐도 "같은 주제"의 근거가 못 되는 낱말. 이걸 안 빼면 '쓴다'·'지침'만 겹친 전혀 다른 지침이
# 매번 후보로 올라오고, 그러면 담당자가 중복 질문을 읽지 않고 넘기게 된다
# (row_match.py의 MIN_SCORE 주석과 같은 이유).
STOP_WORDS = {
    "쓴다", "써라", "적는다", "적어라", "한다", "해라", "하라", "하고", "말고", "마라",
    "않는다", "없다", "있다", "된다", "같이", "함께", "반드시", "항상", "모두", "먼저",
    "그리고", "또는", "경우", "지침", "규칙",
}
# 한글 조사 — "금액은"과 "금액"이 같은 낱말로 보여야 "금액 단위"와 "금액은 백만원으로"가 묶인다.
# 떼고 두 글자 이상 남을 때만 뗀다(`로`를 떼면 `으`만 남는 식을 피한다).
PARTICLES = ("으로", "에서", "까지", "라고", "은", "는", "이", "가", "을", "를", "의", "에", "도", "만", "로")
# 한 번에 보여줄 후보 수 상한. 넷 다섯 개를 늘어놓으면 사용자가 고르기 더 어렵다.
MAX_DUP_CANDIDATES = 3


def _strip_particle(word: str) -> str:
    if not re.fullmatch(r"[가-힣]+", word):
        return word
    for p in PARTICLES:
        if word.endswith(p) and len(word) - len(p) >= 2:
            return word[: -len(p)]
    return word


def content_words(*texts: str) -> set[str]:
    """뜻을 담는 낱말만 남긴다. 한 글자는 버린다 — 한글 한 글자는 조사·의존명사가 대부분이다."""
    words = set()
    for text in texts:
        for raw in re.split(r"[^0-9A-Za-z가-힣]+", str(text or "").lower()):
            word = _strip_particle(raw)
            if len(word) >= 2 and word not in STOP_WORDS:
                words.add(word)
    return words


def similar_guidelines(
    db: Session, account_key: str, applies_to: str, topic: str, text: str
) -> list[Guideline]:
    """이미 있는 지침 중 "같은 주제를 다르게 말한 것"일 수 있는 후보. 없으면 빈 리스트.

    **자동으로 합치거나 거절하지 않는다**: 뜻이 같은지는 낱말 겹침으로 단정할 수 없어서
    (`금액 단위`와 `금액 반영 시점`은 '금액'이 겹쳐도 다른 주제다) 후보만 돌려주고, 같은 주제인지는
    모델이 사용자에게 물어 정한다 — row_match가 행 이름을 자동 병합하지 않는 것과 같은 판단이다.
    규칙은 "물어볼 가치가 있는 것"까지만 좁히고, 뜻을 읽는 일은 모델에게 맡긴다.

    후보 범위는 **그 지침과 같은 프롬프트에 실릴 수 있는 것** 전부다. 등록하려는 쪽이 넓으면
    (전 계정·어디서나) 좁은 지침까지, 좁으면 자기를 덮는 넓은 지침까지 본다 — 한쪽만 보면
    "전 계정 [금액 단위]"가 있는데 "해외연구소 [금액 표기]"를 또 등록하는 걸 놓친다.

    같은 topic 행은 후보에서 뺀다. 그건 중복이 아니라 **교체**이고, 덮어쓰기로 이미 처리된다.
    """
    account_cond = (
        true() if not account_key else Guideline.account_key.in_(["", account_key])
    )
    scope_cond = true() if applies_to == "all" else Guideline.applies_to.in_(["all", applies_to])
    rows = db.scalars(
        select(Guideline).where(
            account_cond, scope_cond, Guideline.topic != topic.strip()
        )
    ).all()

    new_words = content_words(topic, text)
    if not new_words:
        return []
    scored = []
    for g in rows:
        shared = new_words & content_words(g.topic, g.text)
        if shared:
            scored.append((len(shared), g))
    # 겹치는 낱말이 많은 것부터. 같으면 주제 사전순이라 같은 입력에 항상 같은 순서로 묻는다.
    scored.sort(key=lambda pair: (-pair[0], pair[1].topic))
    return [g for _, g in scored[:MAX_DUP_CANDIDATES]]


def duplicate_hint(candidates: list[Guideline], topic: str) -> str:
    """중복 후보를 모델에게 돌려줄 문구. 무엇을 해야 하는지까지 적는다 — 사유만 주면 모델이 그냥
    재시도해서 같은 자리에서 다시 막힌다(collect.py의 추가 행 중복 차단과 같은 형식)."""
    lines = "\n".join(
        f"- [{g.topic}] \"{g.text}\""
        f" ({'전 계정' if not g.account_key else g.account_key + ' 계정'}"
        f" · {SCOPE_LABELS.get(g.applies_to, g.applies_to)})"
        for g in candidates
    )
    return (
        f"지침을 저장하지 않았다. 등록하려는 주제 '{topic}'과 뜻이 겹칠 수 있는 지침이 이미 있다:\n"
        f"{lines}\n"
        "같은 뜻의 지침이 두 줄로 살아 있으면 모델이 매번 다른 걸 골라서 지난주와 이번주 보고서가 "
        "달라진다. 재시도하지 말고 위 지침을 사용자에게 보여주며 물어라 — "
        "(1) 기존 지침을 바꾸는 것이면 topic을 위에 있는 주제 그대로 넣어 다시 호출해라(덮어쓴다). "
        "(2) 뜻이 다른 별개 지침이라 둘 다 필요하면 confirm_new_topic=true로 다시 호출해라. "
        "사용자가 답하기 전에 confirm_new_topic을 쓰면 안 된다 — 그러면 묻는 의미가 없다."
    )
