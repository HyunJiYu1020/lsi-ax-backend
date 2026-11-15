"""업로드 파일의 행 이름을 "이미 있는 행"에 맞춰 주는 판정.

왜 필요한가: 연구소마다 같은 비용을 다른 이름으로 적어 보낸다. 고정 계정 출장비의 영문명은
`Travel Exp`인데 어떤 연구소는 `Travel Expense`로 적는다. 지금까지는 이름이 (공백·기호를 뺀)
글자까지 똑같아야 같은 행으로 봐서, 한 글자만 달라도 "계정 목록에 없는 행"이 되어 따로 들어갔다.
그러면 집계표에 출장비가 두 줄로 쪼개지고, 한 줄만 보고 "이 연구소는 출장비 0"으로 읽게 된다.

그래서 **자동으로 합치지 않고 사람에게 물어본다**: 비슷한 이름이 있으면 후보를 내주고, 반영 전에
"같은 행인가요?"를 묻는다. 자동 병합은 위험하다 — `Travel Exp`와 `Test Exp`처럼 끝이 같거나
`Cloud Usage`와 `Cloud Service`처럼 앞이 같은, 정말 다른 행이 섞인다. 사람이 한 번 답한 결과는
overseas_row_aliases에 남겨서 다음 달부터 같은 질문을 반복하지 않는다.

점수는 "후보로 보여줄지"를 가르는 데만 쓴다 — 최종 판단은 항상 사람이다.
"""

import re
from difflib import SequenceMatcher

# 이름에서 떼어내도 뜻이 거의 안 바뀌는 꼬리말. `Travel Exp` / `Travel Expense` / `Travel Cost`를
# 같은 후보로 묶는 핵심 장치다 — 반대로 이걸 떼면 `Travel`과 `Test`가 남아 서로 확실히 달라진다.
# "other"·"기타"는 일부러 빼뒀다 — 그건 뜻을 바꾸는 말이다(고정 계정 `IT Expense`(전산비)와
# `Other IT Expenses`(IT 기타)는 서로 다른 행인데, other를 떼면 둘이 같은 이름이 된다).
GENERIC_TOKENS = {
    "exp", "exps", "expense", "expenses", "cost", "costs", "fee", "fees",
    "amount", "amt", "total", "tot", "sum",
    "비", "비용", "금액", "합계", "계",
}
# 한글은 꼬리말을 띄어 쓰지 않는다(출장비 = 출장 + 비) — 낱말 끝에 붙은 이 꼬리말은 떼고 비교해서
# `출장비`와 `출장 비용`이 같은 후보로 잡히게 한다. 떼고 두 글자 이상 남을 때만 뗀다.
CJK_TAILS = ("비용", "금액", "비")
# 후보로 보여줄 최저 점수. 이 밑은 "그냥 새 행"으로 두고 묻지 않는다 — 아닐 가능성이 큰 후보를
# 매번 물으면 담당자가 질문을 읽지 않고 넘기게 된다.
MIN_SCORE = 0.80
# 한 이름에 보여줄 후보 수 상한. 넷 다섯 개를 늘어놓으면 고르는 게 더 어렵다.
MAX_SUGGESTIONS = 3
# 접두 일치로 볼 최소 길이. 한글은 두 글자만으로도 뜻이 갈려서(출장/출하) 3으로 둔다.
MIN_PREFIX = 3


def normalize(label: str) -> str:
    """프런트 ovNorm과 같은 규칙 — 공백·괄호·구두점을 떼고 소문자로. 별칭 키로 쓴다."""
    return re.sub(r"[\s()\[\]·.,&\-_/'’]", "", str(label or "")).lower()


def _tokens(label: str) -> list[str]:
    """이름을 낱말로 쪼갠다. 파일에는 `TravelExp`처럼 붙여 쓴 이름도 와서 대문자 경계도 끊는다."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(label or ""))
    return [t for t in re.split(r"[^0-9A-Za-z가-힣]+", spaced.lower()) if t]


def _trim_cjk(token: str) -> str:
    if re.fullmatch(r"[가-힣]+", token):
        for tail in CJK_TAILS:
            if token.endswith(tail) and len(token) - len(tail) >= 2:
                return token[: -len(tail)]
    return token


def _core(label: str) -> list[str]:
    """꼬리말을 뺀 알맹이 낱말. 전부 꼬리말이면(`Expense` 한 단어짜리 행) 원래 낱말을 그대로 쓴다."""
    toks = _tokens(label)
    core = [_trim_cjk(t) for t in toks if t not in GENERIC_TOKENS]
    return core or toks


def _prefix_ok(a: str, b: str) -> bool:
    """한쪽이 다른 쪽의 앞부분인가 — `exp`↔`expense`처럼 줄여 쓴 이름을 잡는다."""
    if len(a) < MIN_PREFIX or len(b) < MIN_PREFIX:
        return a == b
    return a.startswith(b) or b.startswith(a)


def score(a: str, b: str) -> float:
    """두 행 이름이 같은 행일 가능성 (0~1). 1.0은 정규화 후 완전히 같은 이름."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ca, cb = _core(a), _core(b)
    if ca == cb:
        return 0.95  # 꼬리말만 다르다: Travel Expense ↔ Travel Exp
    ja, jb = "".join(ca), "".join(cb)
    if _prefix_ok(ja, jb):
        return 0.90  # 한쪽이 줄여 쓴 이름: 출장비 ↔ 출장비용
    if len(ca) == len(cb) and all(_prefix_ok(x, y) for x, y in zip(ca, cb)):
        return 0.88  # 낱말마다 줄여 쓴 이름: Dev Exp ↔ Development Expense
    ratio = SequenceMatcher(None, ja, jb).ratio()
    # 오타 한두 글자(Depreciaton ↔ Depreciation)만 잡을 만큼 높게 둔다 — 여기를 낮추면
    # Cloud Usage ↔ Cloud Service 같은 다른 행이 후보로 올라온다.
    return ratio if ratio >= 0.86 else 0.0


def rank(label: str, candidates: list[dict]) -> list[dict]:
    """후보 목록을 점수순으로 추려서 돌려준다. candidates의 각 항목은 kind·key·label(·label_en·labs)을
    갖고, 이름은 label과 label_en 둘 다로 비교한다(파일이 영문으로 오고 화면은 한글이라 양쪽 다 필요).

    같은 행(kind·key)이 한글명·영문명으로 두 번 걸리면 점수 높은 쪽만 남긴다."""
    best: dict[tuple[str, str], dict] = {}
    for c in candidates:
        matched, s = "", 0.0
        for name in (c.get("label"), c.get("label_en")):
            if not name:
                continue
            v = score(label, name)
            if v > s:
                matched, s = name, v
        if s < MIN_SCORE:
            continue
        key = (c["kind"], c["key"])
        if key not in best or s > best[key]["score"]:
            best[key] = {
                "kind": c["kind"],
                "key": c["key"],
                "label": c["label"],
                "label_en": c.get("label_en") or "",
                "matched_on": matched,
                "score": round(s, 3),
                "labs": c.get("labs") or [],
            }
    return sorted(best.values(), key=lambda x: (-x["score"], x["label"]))[:MAX_SUGGESTIONS]
