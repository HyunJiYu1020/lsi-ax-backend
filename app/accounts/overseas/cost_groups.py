"""해외연구소 비용 계정의 한글명과 집계 그룹 정의 — API·MCP 서버·챗봇이 함께 쓰는 단일 원본.

**원본은 DB의 common_codes(group_code='OVERSEAS_CATEGORY')다.** 아래 하드코딩 값은 DB를 읽기
전까지(그리고 DB를 못 읽을 때) 쓰는 초기값일 뿐이고, `reload_from_db()`가 돌면 이름·계층·행
순서가 전부 DB 값으로 바뀐다 — 공통코드에서 계정을 추가·중지·개명하면 배포 없이 서버 집계·
증감분석·행 맞추기 후보가 따라온다. 끈 코드(is_active=false)는 목록에서 빠지므로, 업로드 때
그 이름으로 온 행은 "계정 목록에 없는 행"(신규)으로 떨어진다 — 화면 ovRowKey와 같은 규칙이다.

갱신은 **제자리(in-place)로만** 한다. 다른 모듈이 `from cost_groups import CATEGORY_LABELS`처럼
이름을 직접 들고 있어서, 새 객체를 대입하면 그쪽은 옛 객체를 계속 본다 — 그래서 dict·list·set을
쓰고 clear()+채우기로 갱신한다(튜플·frozenset을 쓰면 이 갱신이 불가능하다).

frontend-html/shared/core.js의 OV_EL도 같은 공통코드를 받아 같은 규칙으로 맞춘다.
"""

from typing import Any, Callable


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
    # 계정이 아니라 "MARK UP ÷ 개발비(매출)" 비율 행이라 이름이 기호다(프런트 OV_EL과 같게).
    "pct": "(%)",
}

# 업로드 파일은 영문 양식으로 온다 — 행 이름을 맞출 때(app/core/row_match.py) 한글명만으로는
# `Travel Exp` 같은 파일 표기를 알아볼 수 없어서 영문명도 같이 둔다. core.js OV_EL의 en 필드와
# 1:1로 맞출 것(계정이 늘어나면 양쪽을 같이 고친다).
CATEGORY_LABELS_EN: dict[str, str] = {
    "hc": "Headcount", "tot": "Development Expense Total (A+B)", "A": "Development Expense (A)",
    "hr": "HR related", "labor": "Labor Cost", "sal": "Salaries & Wages", "bonus": "Bonus (MBO etc)",
    "welf": "Welfare, Health Insurance", "edu": "Education & Trainning Exp", "travel": "Travel Exp",
    "asset": "Asset related Exp", "dep": "Depreciation Exp", "amort": "Amortization Exp",
    "rent": "Rent Exp", "repair": "Repair & Maintenance Exp", "ins": "Insurance Exp",
    "direct": "Direct R&D Exp", "supp": "Product Supplies Exp", "test": "Test Exp",
    "ord": "Ordinary R&D Exp", "gen": "General Operation Exp", "comm": "Commision & Service Exp",
    "it": "IT Expense", "cons": "Consumable Supplies Expense", "util": "Light&Fuel Exp",
    "car": "Car R&M Exp", "tel": "Telecom Exp", "pub": "Publishing Exp", "oth": "others",
    "mgr": "Managerial Exp", "conv": "Convention Exp", "event": "Event Exp", "social": "Social Exp",
    "misc": "Misc.", "tax": "Taxes and Duties", "nonop": "Non-operational", "B": "Margin (B)",
    "itx": "IT Expenses", "eda": "EDA Tool", "emu": "Emulator", "swl": "SW/License Purchase",
    "svr": "Rent for SVR/Storage", "itoth": "Other IT Expenses",
    "outs": "Outsourcing Expense(Only for R&D)", "pct": "(%)",
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


# 증감분석 표의 행 순서·들여쓰기·단위. 화면(frontend/src/data/overseasConstants.js의 OV_EL)과 1:1로
# 맞춰야 한다 — 서버가 내려주는 증감분석 표(/overseas/variance)와 화면 표의 행 순서가 다르면,
# 챗봇이 "위에서 세 번째 줄"처럼 위치로 말할 때 어긋나고 엑셀 다운로드와도 대조가 안 된다.
# unit: krw(원화 금액) | hc(명, 월평균) | pct(%, B÷A)
VARIANCE_ROWS: list[tuple[str, int, str]] = [
    ("hc", 0, "hc"),
    ("tot", 0, "krw"),
    ("A", 0, "krw"),
    ("hr", 1, "krw"),
    ("labor", 2, "krw"),
    ("sal", 3, "krw"),
    ("bonus", 3, "krw"),
    ("welf", 3, "krw"),
    ("edu", 2, "krw"),
    ("travel", 2, "krw"),
    ("asset", 1, "krw"),
    ("dep", 2, "krw"),
    ("amort", 2, "krw"),
    ("rent", 2, "krw"),
    ("repair", 2, "krw"),
    ("ins", 2, "krw"),
    ("direct", 1, "krw"),
    ("supp", 2, "krw"),
    ("test", 2, "krw"),
    ("ord", 2, "krw"),
    ("gen", 1, "krw"),
    ("comm", 2, "krw"),
    ("it", 2, "krw"),
    ("cons", 2, "krw"),
    ("util", 2, "krw"),
    ("car", 2, "krw"),
    ("tel", 2, "krw"),
    ("pub", 2, "krw"),
    ("oth", 2, "krw"),
    ("mgr", 1, "krw"),
    ("conv", 2, "krw"),
    ("event", 2, "krw"),
    ("social", 2, "krw"),
    ("misc", 1, "krw"),
    ("tax", 1, "krw"),
    ("nonop", 1, "krw"),
    ("B", 0, "krw"),
    ("pct", 0, "pct"),
]


# 집계 화면 ① 전체 계정 표의 행 구성 — (key, 들여쓰기, 단위, 참고행인지).
# VARIANCE_ROWS(증감분석 표)와 달리 "참고 행"(memo)을 포함한다 — IT 관련 비용 분해와 외주용역비는
# 개발비 합계에 들어가지 않고 따로 보여주는 값이다(그래서 resolve_leaves("tot")에도 없다).
# 라벨은 CATEGORY_LABELS / CATEGORY_LABELS_EN에서 가져온다 — 이름을 두 번 적지 않기 위함이다.
AGGREGATE_ROWS: list[tuple[str, int, str, bool]] = [
    (key, level, unit, False) for key, level, unit in VARIANCE_ROWS
] + [
    # 참고 행 — 합계와 무관하다(이미 다른 분류에 잡힌 금액을 다른 각도로 묶어 보여주는 줄이라,
    # 총계에 또 더하면 같은 돈을 두 번 세게 된다). 여기에 붙인 추가 행도 이 줄 합계까지만 잡힌다 —
    # 추가 행은 자기가 붙은 자리의 합계에 더해진다(services/aggregate.py의 extra_keys).
    # itx는 아래 5개의 합(GROUP_CALC에 정의돼 있다).
    ("itx", 0, "krw", True),
    ("eda", 1, "krw", True),
    ("emu", 1, "krw", True),
    ("swl", 1, "krw", True),
    ("svr", 1, "krw", True),
    ("itoth", 1, "krw", True),
    ("outs", 0, "krw", True),
]


# ---------- 공통코드(DB) → 위 정의 갱신 ----------
#
# common_codes에 **코드가 없는** 화면 전용 행. tot·B·pct는 저장되는 계정이 아니라 화면·서버가
# 계산해서 만드는 줄이라(A+B 합계, MARK UP, 그 비율) parent_code로 계층을 표현할 대상이 없어
# 공통코드에 둘 수 없다(seed.py의 같은 설명 참고). 자리만 여기서 정해 끼워 넣는다.
# (key, 단위, 어느 코드 뒤에 끼울지 — None이면 맨 끝)
_SCREEN_ONLY_ROWS: tuple[tuple[str, str, str | None], ...] = (
    ("tot", "krw", "hc"),   # 인력 다음 줄
    ("B", "krw", None),
    ("pct", "pct", None),
)
_SCREEN_ONLY_LABELS = {"tot": ("개발비(매출)", "Development Expense Total (A+B)"),
                       "B": ("MARK UP", "Margin (B)"), "pct": ("(%)", "(%)")}

#: reload 뒤 자기 파생값을 다시 만들어야 하는 모듈이 등록해 두는 콜백(aggregate.py의 트리 등).
#: 여기 모아 두는 이유: cost_groups가 그 모듈들을 import하면 순환 참조가 된다.
_RELOAD_HOOKS: list[Callable[[], None]] = []


def on_reload(fn: Callable[[], None]) -> Callable[[], None]:
    """reload_from_rows() 뒤에 부를 콜백을 등록한다(데코레이터로도 쓸 수 있다)."""
    _RELOAD_HOOKS.append(fn)
    return fn


def _level_of(code: str, parent: dict[str, str | None]) -> int:
    """parent_code 체인을 타고 올라가며 센 들여쓰기 깊이 — 화면 ovCatLevel과 같은 계산이다."""
    lv, p, guard = 0, parent.get(code), 0
    while p and guard < 10:
        lv, p, guard = lv + 1, parent.get(p), guard + 1
    return lv


def reload_from_rows(rows: list[Any]) -> None:
    """common_codes(group_code='OVERSEAS_CATEGORY') 행들로 계정 정의를 제자리 갱신한다.

    rows는 code·name·description·parent_code·sort_order·is_active를 가진 객체(ORM 행이든
    dict든 — 둘 다 받는다). **사용 중(is_active=true)인 코드만** 쓴다: 끈 코드는 이름 사전에서
    빠지므로 업로드에서 그 이름으로 온 행은 매칭되지 않고 신규로 떨어진다.

    행이 비어 오면(아직 seed 전인 DB 등) 아무것도 바꾸지 않는다 — 하드코딩 초기값을 지우고 빈
    계정 목록으로 돌면 집계가 전부 0이 되고, 그건 "DB를 못 읽었다"보다 나쁜 상태다.
    """
    get = lambda r, k: (r.get(k) if isinstance(r, dict) else getattr(r, k, None))
    live = [r for r in rows if get(r, "is_active")]
    if not live:
        return
    live.sort(key=lambda r: (get(r, "sort_order") or 0, str(get(r, "code"))))
    codes = [str(get(r, "code")) for r in live]
    parent = {str(get(r, "code")): (get(r, "parent_code") or None) for r in live}
    # 끊긴 parent_code(상위가 중지됐거나 지워진 경우)는 최상위로 본다 — 안 그러면 들여쓰기
    # 계산이 없는 코드를 따라가고, 그룹 합계에도 아무 데도 안 걸린 행이 생긴다.
    parent = {k: (v if v in parent else None) for k, v in parent.items()}

    CATEGORY_LABELS.clear()
    CATEGORY_LABELS_EN.clear()
    for r in live:
        code = str(get(r, "code"))
        CATEGORY_LABELS[code] = str(get(r, "name") or code)
        CATEGORY_LABELS_EN[code] = str(get(r, "description") or get(r, "name") or code)
    for code, (ko, en) in _SCREEN_ONLY_LABELS.items():
        CATEGORY_LABELS[code] = ko
        CATEGORY_LABELS_EN[code] = en

    # 그룹 합계식 = parent_code의 역방향(자식 목록, sort_order 순). tot만 화면 전용이라 끼워 넣는다.
    GROUP_CALC.clear()
    for code in codes:
        p = parent[code]
        if p:
            GROUP_CALC.setdefault(p, ())
            GROUP_CALC[p] = GROUP_CALC[p] + (code,)
    if "A" in CATEGORY_LABELS:
        GROUP_CALC["tot"] = ("A", "B")

    # 표 행 목록 — 참고 행(memo)은 "개발비(매출) 트리 밖"이라는 뜻이다. 손으로 적던 목록을
    # 트리에서 뽑는다: 분류가 바뀌면 참고 행 구분도 같이 따라가야 한다.
    tot_tree: set[str] = set()

    def _walk(key: str) -> None:
        tot_tree.add(key)
        for sub in GROUP_CALC.get(key, ()):
            _walk(sub)

    _walk("tot")
    unit_of = lambda code: "hc" if code == "hc" else "krw"
    main = [(c, _level_of(c, parent), unit_of(c)) for c in codes if c in tot_tree or c == "hc"]
    memo = [(c, _level_of(c, parent), unit_of(c), True) for c in codes if c not in tot_tree and c != "hc"]
    for code, unit, after in _SCREEN_ONLY_ROWS:
        row = (code, 0, unit)
        if after is None:
            main.append(row)
        else:
            at = next((i for i, x in enumerate(main) if x[0] == after), len(main) - 1)
            main.insert(at + 1, row)

    VARIANCE_ROWS[:] = main
    AGGREGATE_ROWS[:] = [(k, lv, u, False) for k, lv, u in main] + memo

    global COST_GROUP_DEFS
    COST_GROUP_DEFS = group_defs_text()
    for hook in _RELOAD_HOOKS:
        hook()


def reload_from_db(db: Any) -> None:
    """DB에서 공통코드를 읽어 reload_from_rows를 부른다. 실패는 삼킨다 — 계정 정의를 못 읽어도
    하드코딩 초기값으로 계속 서비스하는 게 맞다(기동이 막히면 화면 전체가 안 뜬다)."""
    try:
        from sqlalchemy import select

        from app.shared.core.enums import CodeGroup
        from app.shared.models.system import CommonCode

        rows = list(
            db.scalars(select(CommonCode).where(CommonCode.group_code == CodeGroup.OVERSEAS_CATEGORY.value))
        )
        reload_from_rows(rows)
    except Exception:  # noqa: BLE001 — 기동·갱신이 계정 정의 때문에 막히면 안 된다
        import logging

        logging.getLogger(__name__).warning("해외연구소 계정 정의를 공통코드에서 읽지 못했습니다", exc_info=True)


def group_defs_text() -> str:
    """GROUP_CALC를 LLM이 읽을 한 줄 설명으로 풀어쓴다 — 손으로 적어 두면 공통코드가 바뀔 때
    설명만 옛 분류로 남는다(챗봇이 그걸 근거로 답한다)."""
    parts = []
    for key, subs in GROUP_CALC.items():
        parts.append(f"{key}({CATEGORY_LABELS.get(key, key)}) = " + " + ".join(subs))
    leaves = [k for k in CATEGORY_LABELS if k not in GROUP_CALC and k not in ("hc", "pct")]
    return " · ".join(parts) + (f" · 나머지는 하위 없는 리프다: {', '.join(sorted(leaves))}" if leaves else "")
