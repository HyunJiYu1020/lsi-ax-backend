"""증감분석 증감사유 **분석 초안** — 숫자로 본 증감 원인을 한국어 문장으로 만든다.

연구소별 증감 요약 표(services/variance.py compute_variance_labs)의 맨 끝 열이 증감사유이고, 그
칸에 처음 들어가 있는 문장이 이 초안이다. 담당자가 고쳐 [증감사유 저장]을 누른 것이 최종 사유다.

왜 서버로 옮겼나: 이 문장은 목업 시절부터 브라우저(core.js ovRsnDraft)가 **자기가 계산한** 숫자로
만들고 있었다. 같은 화면의 표는 이미 서버 계산인데 문장만 브라우저 계산이면, 환율·전실행 해석이
조금만 갈려도 "표에는 1.2억, 사유에는 1.3억"이 된다. 이제 표 칸을 만든 값 그대로 문장을 쓴다.

LLM은 쓰지 않는다 — 숫자에서 규칙으로 뽑는 문장이라 같은 데이터면 몇 번 조회해도 글자까지 같다.
회신 원문을 읽어 "왜 그랬는지"를 쓰는 건 [AI 초안](shared/core/reason_translate.draft_reason)의
일이다. 둘은 같은 칸에 [분석 초안] → [연구소 회신] → [AI 초안] 순으로 쌓인다.
"""

from dataclasses import dataclass

#: 초안 블록의 머리말. 화면이 [연구소 회신]·[AI 초안] 블록을 이 뒤에 붙이고, 담당자가 고친 뒤에도
#: 이 표시로 "어디까지가 자동 생성분인지" 구분한다 — 문구를 바꾸면 저장된 사유와 안 맞는다.
DRAFT_TAG = "[분석 초안]"

#: 한 줄을 쓸지 말지 가르는 금액(원). 화면과 같은 값이어야 한다 — 임계치가 갈리면 같은 데이터에서
#: 화면이 쓰던 줄이 사라지거나 없던 줄이 생긴다.
MIN_AMOUNT = 5_000_000.0

#: 인원 변동을 "변동 없음"으로 볼 한도(명). 인원은 월평균이라 0.04명 같은 값이 나오는데, 그걸
#: "0.0명 증가"로 적으면 읽는 사람이 변동으로 본다.
MIN_HEADCOUNT = 0.05

#: 적용 환율이 "움직였다"고 볼 한도(원/$). 소수점 아래 반올림 차이까지 환율 변동으로 적으면
#: 환차가 0인 달까지 문장에 끌려 들어온다.
MIN_RATE_MOVE = 0.5

#: 인건비 줄에서 따로 말하는 계정 — 인건비外 줄에서는 빼야 같은 금액을 두 번 세지 않는다.
#: labor(인건비)와 hr(인원성경비)는 그룹 합계라 GROUP_CALC로 걸러지고, 그 잎(급여·상여·복리후생)만
#: 여기서 뺀다. B(MARK UP)는 비용이 아니라 마진이라 증감 원인으로 말할 대상이 아니다.
NON_LABOR_EXCLUDE = frozenset({"sal", "bonus", "welf", "B"})

#: 인건비外 줄에 적는 계정 수. 전부 적으면 한 문장이 20개 계정으로 늘어나 읽을 수 없다.
TOP_CATEGORIES = 3


def tri(up: bool, text: str) -> str:
    """증감 표기 — 화면 core.js의 triS와 같다(증가는 뒤에 ↑, 감소는 앞에 △)."""
    return f"{text}↑" if up else f"△{text}"


def eok(value: float) -> str:
    """억원 표기. 임계치 미만은 '0억'으로 눌러 쓴다 — 500만원을 '0.1억'으로 적으면 안 적은 것보다
    나쁘다(읽는 사람이 그 줄을 의미 있는 변동으로 본다)."""
    return "0억" if abs(value) < MIN_AMOUNT else tri(value > 0, f"{abs(value) / 1e8:.1f}억")


def per_head_text(amount: float, currency: str) -> str:
    """인당 인건비 표기. 연구소 담당자가 읽는 값이라 **현지통화 K**로 쓴다(연구소가 받는 양식의
    단위가 K$다).

    저장값은 $다(업로드가 K$ 셀을 ×1000해서 담는다 — cost_entries 주석) 그래서 1000으로 나눠야 K가
    된다. 화면 ovRsnDraft의 mo()에는 이 나눗셈이 빠져 있어서 $115K를 '$115,000K'로 적고 있었다.
    """
    head = "$" if currency == "$" else f"{currency} "
    return f"{head}{round(amount / 1000):,}K"


@dataclass(frozen=True)
class DraftInputs:
    """초안 한 건(연구소 하나 · 연간)에 필요한 숫자 전부. 금액은 원, 인원은 명이다.

    전부 **연구소별 요약 표의 그 칸을 만든 값**이다(compute_variance_labs). 여기서 다시 계산하지
    않는 게 이 모듈의 요점이다 — 문장과 바로 옆 숫자가 어긋날 자리를 아예 없앤다.
    """

    #: 데이터가 있는지 가리는 값. 한쪽 버전이 비어 있으면 증감을 말할 수 없다(0억 증가가 아니다).
    cur_total: float
    base_total: float
    diff: float
    fx: float
    fx_local: float
    fx_outside: float
    labor_outside: float
    non_labor_outside: float
    hc_base: float
    hc_cur: float
    #: 인건비 환차外를 인원 효과·단가 효과로 가를 수 있는지. 경영계획 대비처럼 환율 기준이 없으면
    #: False이고, 그때는 표의 분해 줄도 비어 있다(services/labor_split.py).
    splittable: bool
    hc_effect: float
    rate_effect: float
    #: 인당 인건비 — 현지통화 1단위 기준($ 연구소는 $). per_head_text가 K로 바꿔 적는다.
    per_head_base: float
    per_head_cur: float
    #: 표시용 통화("$" 또는 "INR"처럼 통화코드). 현지통화 환율을 못 찾으면 금액이 $로 남으므로 "$"다.
    currency: str
    #: 현지통화 코드. USD 연구소이거나 환율을 못 찾으면 빈 문자열이다(현지→$ 환차 문구에만 쓴다).
    local_currency: str
    #: (계정 이름, 환차外 금액) — 인건비外 계정 전부. 임계치·상위 몇 개는 이 모듈이 고른다.
    categories: tuple[tuple[str, float], ...]
    #: 월별 적용 환율(원/$) 12개. 환차가 왜 생겼는지 한 조각으로 적는 데만 쓴다.
    rate_base: tuple[float, ...]
    rate_cur: tuple[float, ...]


def build(inp: DraftInputs) -> str:
    """증감사유 칸에 들어갈 [분석 초안] 블록. 쓸 줄이 없으면 빈 문자열이다.

    빈 문자열을 돌려주는 게 중요하다 — 화면은 "서버 초안이 비어 있으면 그 블록을 안 붙인다"로
    읽고, 저장된 사유와 비교할 때도 그 상태를 그대로 쓴다.
    """
    lines = draft_lines(inp)
    if not lines:
        return ""
    return DRAFT_TAG + "\n" + "\n".join("· " + line for line in lines)


def draft_lines(inp: DraftInputs) -> list[str]:
    """초안의 줄 목록. 블록 머리말은 붙이지 않는다(테스트가 줄 단위로 보기 좋게)."""
    # 데이터가 없는 것과 변동이 없는 것은 다르다 — 둘을 같이 "변동 없음"으로 적으면 담당자가
    # "업로드가 안 됐다"를 "그대로다"로 읽는다.
    if not inp.cur_total:
        return ["선택 버전에 이 연구소 데이터가 없습니다"]
    if not inp.base_total:
        return ["비교 버전에 이 연구소 데이터가 없습니다"]
    if abs(inp.diff) < MIN_AMOUNT and abs(inp.fx) < MIN_AMOUNT:
        return ["변동 없음"]

    lines = [f"전체 {eok(inp.diff)} = 환차 {eok(inp.fx)} + 환차外 {eok(inp.fx_outside)}"]
    for line in (_labor_line(inp), _non_labor_line(inp), _fx_line(inp)):
        if line:
            lines.append(line)
    return lines


def _labor_line(inp: DraftInputs) -> str:
    """인건비: 인원이 늘어서인지 인당 단가가 올라서인지. 인력을 안 낸 연구소(CP 등)는 나눌 수
    없어 이 줄을 쓰지 않는다 — 단가를 만들어 적으면 없는 근거를 대는 셈이다."""
    if abs(inp.labor_outside) < MIN_AMOUNT or inp.hc_base <= 0 or inp.hc_cur <= 0:
        return ""
    moved = inp.hc_cur - inp.hc_base
    hc_text = "변동 없음" if abs(moved) < MIN_HEADCOUNT else tri(moved > 0, f"{abs(moved):.1f}명")
    head = (
        f"인건비 {eok(inp.labor_outside)}: "
        f"인원 {inp.hc_base:.1f}→{inp.hc_cur:.1f}명({hc_text})"
    )
    if not inp.splittable:
        # 표의 인건비 분해 줄이 비어 있는 경우다(경영계획 대비 등). 문장도 분해를 말하지 않는다 —
        # 분해한 척하는 숫자를 적는 것보다 인원 변동까지만 적는 쪽이 맞다.
        return head + " · 환율 기준이 없어 인원 · 단가 분해 불가"
    if inp.per_head_base <= 0:
        return head
    pct = (inp.per_head_cur / inp.per_head_base - 1) * 100
    line = (
        f"{head} 영향 {eok(inp.hc_effect)}, "
        f"인당 인건비 {per_head_text(inp.per_head_base, inp.currency)}"
        f"→{per_head_text(inp.per_head_cur, inp.currency)}"
        f"({tri(pct >= 0, f'{abs(pct):.1f}%')}) 영향 {eok(inp.rate_effect)}"
    )
    # 두 효과가 다 작으면 "주로 ~ 영향"을 붙이지 않는다 — 1천만원 차이로 어느 쪽이 주된 원인인지
    # 단정하면 보고 문장이 과해진다.
    if max(abs(inp.hc_effect), abs(inp.rate_effect)) < MIN_AMOUNT:
        return line
    if abs(inp.hc_effect) > abs(inp.rate_effect):
        cause = "주로 인원 " + ("증가" if moved > 0 else "감소") + " 영향"
    else:
        cause = "주로 인당 단가 " + ("상승" if pct >= 0 else "하락") + " 영향"
    local = f" (현지통화 {inp.currency} 기준)" if inp.local_currency else ""
    return f"{line} → {cause}{local}"


def _non_labor_line(inp: DraftInputs) -> str:
    """인건비外: 변동이 큰 계정 상위 몇 개. 전부 적지 않는 이유는 TOP_CATEGORIES 주석에 있다."""
    if abs(inp.non_labor_outside) < MIN_AMOUNT:
        return ""
    big = sorted(
        (c for c in inp.categories if abs(c[1]) >= MIN_AMOUNT),
        key=lambda c: -abs(c[1]),
    )[:TOP_CATEGORIES]
    # 계정 이름의 괄호는 뗀다 — 표에서는 들여쓰기를 괄호로 표시하지만("(급여)"), 문장에 그대로
    # 넣으면 "(교육훈련비) 1.2억↑"처럼 읽힌다.
    named = ", ".join(f"{label.replace('(', '').replace(')', '')} {eok(value)}" for label, value in big)
    return f"인건비外 {eok(inp.non_labor_outside)}" + (f": {named}" if named else "")


def _fx_line(inp: DraftInputs) -> str:
    """환차: 적용 환율이 얼마에서 얼마로 움직였는지. 환차가 작으면 쓰지 않는다."""
    if abs(inp.fx) < MIN_AMOUNT:
        return ""
    months = [
        m for m in range(1, 13)
        if abs(_at(inp.rate_cur, m) - _at(inp.rate_base, m)) >= MIN_RATE_MOVE
    ]
    moved = ""
    if months:
        span = "1~12월" if len(months) == 12 else f"{months[0]}~{months[-1]}월"
        avg = lambda rates: sum(_at(rates, m) for m in months) / len(months)  # noqa: E731
        moved = f" ({span} 평균 적용 환율 {round(avg(inp.rate_base)):,}→{round(avg(inp.rate_cur)):,}원/$)"
    local = ""
    if abs(inp.fx_local) >= MIN_AMOUNT:
        local = f", 현지통화({inp.local_currency})→$ {eok(inp.fx_local)}"
    return f"환차 {eok(inp.fx)}{moved}{local}"


def _at(rates: tuple[float, ...], month: int) -> float:
    """월(1~12) 환율. 배열이 짧게 와도 터지지 않게 둔다 — 초안 한 줄 때문에 표 전체를 잃을 이유가
    없다(화면 ovLaborSplit이 빈 배열을 0으로 보는 것과 같은 판단)."""
    return rates[month - 1] if 0 <= month - 1 < len(rates) else 0.0
