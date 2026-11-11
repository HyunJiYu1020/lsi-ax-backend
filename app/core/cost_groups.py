"""해외연구소 비용 계정의 한글명과 집계 그룹 정의 — API·MCP 서버·챗봇이 함께 쓰는 단일 원본.

frontend-html/shared/core.js의 OV_EL(ko 필드와 calc 필드)과 1:1로 맞춰야 한다(계정이 늘어나면
여기도 같이 고칠 것). 원래는 mcp_server.py 안에만 있었는데, 파생 행/열의 계산식을 평가하는 쪽
(app/api/v1/endpoints/overseas.py)에서도 그룹 전개가 필요해져 공용 모듈로 옮겼다 — API가
MCP 어댑터를 import하는 역방향 의존을 만들지 않고, 같은 정의를 세 번째로 베껴 적지 않기 위함이다.
"""


# 계정 코드를 사람이 읽는 한글명으로 바꿀 때 쓴다 — 챗봇이 쓰기 승인 전에 보여주는 설명 문장이나
# 파생 행의 근거("인건비 ÷ 인력")에 'labor' 같은 내부 코드를 그대로 노출하지 않기 위함.
CATEGORY_LABELS: dict[str, str] = {
    "hc": "인력", "sal": "급여", "bonus": "상여", "welf": "복리후생", "edu": "교육훈련비",
    "travel": "출장비", "dep": "감가상각비", "amort": "무형자산상각", "rent": "임차료",
    "repair": "수선비", "ins": "보험료", "supp": "개발소모품비", "test": "인증테스트비",
    "ord": "경상연구비", "comm": "지급수수료", "it": "전산비", "cons": "일반소모품",
    "util": "수도광열비", "car": "차량유지비", "tel": "통신비", "pub": "도서인쇄비", "oth": "잡비",
    "conv": "회의비", "event": "행사비", "social": "교제비", "misc": "기타경비", "tax": "조세공과",
    "nonop": "영업외수지", "hr": "인원성경비", "asset": "자산성경비", "direct": "직접개발비",
    "gen": "일반운영비", "mgr": "통제성경비", "itx": "IT 관련 비용", "eda": "EDA Tool",
    "emu": "Emulator", "swl": "SW/License구매", "svr": "SVR/Storage임대", "itoth": "IT 기타",
    "outs": "외주용역비", "A": "개발비", "tot": "개발비(매출)", "labor": "인건비", "B": "MARK UP",
}

# 그리드의 "구분"에 보이는 집계 행(개발비(A) 등)은 그 자체로 저장된 값이 아니라 리프 여러 개를
# 더한 값이다. 이 정의로 리프까지 재귀 전개해서 계산한다 — LLM이 직접 더하다 리프를 빠뜨리는
# 사고(실제 사례: 27개 리프를 손으로 더해 1,934.5 대신 1,607.7을 답함)를 원천 차단하기 위함.
# "tot"의 "B"(MARK UP)는 하위 리프가 없는 자체 입력값이라 그대로 둔다.
GROUP_CALC: dict[str, tuple[str, ...]] = {
    "tot": ("A", "B"),
    "A": ("hr", "asset", "direct", "gen", "mgr", "misc", "tax", "nonop"),
    "hr": ("labor", "edu", "travel"),
    "labor": ("sal", "bonus", "welf"),
    "asset": ("dep", "amort", "rent", "repair", "ins"),
    "direct": ("supp", "test", "ord"),
    "gen": ("comm", "it", "cons", "util", "car", "tel", "pub", "oth"),
    "mgr": ("conv", "event", "social"),
    "itx": ("eda", "emu", "swl", "svr", "itoth"),
}

# GROUP_CALC를 사람(LLM)이 읽을 설명으로 풀어쓴 것 — 실제 계산은 위 구조화된 dict로 한다.
COST_GROUP_DEFS = (
    'tot(개발비(매출)) = A + B · A(개발비, Development Expense) = hr + asset + direct + gen + mgr + '
    "misc + tax + nonop (hc·B 제외, 리프 거의 전체의 합) · hr(인원성경비) = sal + bonus + welf + edu + "
    "travel · labor(인건비, hr의 하위) = sal + bonus + welf · asset(자산성경비) = dep + amort + rent + "
    "repair + ins · direct(직접개발비) = supp + test + ord · gen(일반운영비) = comm + it + cons + util + "
    "car + tel + pub + oth · mgr(통제성경비) = conv + event + social · misc(기타경비)/tax(조세공과)/"
    "nonop(영업외수지)은 하위 없이 그 자체가 리프다"
)


def resolve_leaves(group_key: str) -> list[str]:
    """집계 그룹 키를 실제로 저장된 리프 계정 키 목록으로 재귀 전개한다.
    그룹이 아니면(리프거나 커스텀 행 키면) 그 키 하나만 담아 돌려준다."""
    if group_key not in GROUP_CALC:
        return [group_key]
    leaves: list[str] = []
    for sub in GROUP_CALC[group_key]:
        leaves.extend(resolve_leaves(sub))
    return leaves


def category_label(key: str) -> str:
    """계정 코드의 한글명. 등록되지 않은 키(사용자가 추가한 행 등)는 코드를 그대로 돌려준다."""
    return CATEGORY_LABELS.get(key, key)
