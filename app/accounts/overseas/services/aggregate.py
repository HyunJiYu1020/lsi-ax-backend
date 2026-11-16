"""해외연구소 집계 — 손익계산서 계층 전개와 원화 환산.

원래 overseas.py의 `_group_tree` · `_krw_months` · `_cp_quarter_amounts` · `_extra_row_months`와
`_TOT_TREE` · `_LABOR_TREE` 상수였고, 집계 엔드포인트가 그 밑줄 이름을 직접 import해서 썼다
(PLAN.md P1-1).

여기 식은 프런트(core.js의 ovAgg · ovCPc · ovCustExtra)와 맞춰야 한다 —
챗봇이 말하는 금액과 사용자가 보는 표가 어긋나면 안 된다. 어긋남은
tests/test_formula_crosscheck.py가 세 구현을 실제로 실행해서 잡는다(PLAN.md P1-4).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.overseas.cost_groups import (
    AGGREGATE_ROWS,
    CATEGORY_LABELS,
    CATEGORY_LABELS_EN,
    GROUP_CALC,
    resolve_leaves,
)
from app.shared.core.values import n
from app.accounts.overseas.models import OverseasCpRow


TOT_LEAVES = tuple(resolve_leaves("tot"))
# 업로드 여부 판정에 보는 입력 칸(화면 OV_IN = 파생 아닌 모든 행 + 인력).
INPUT_KEYS = frozenset(TOT_LEAVES) | {"hc"}
STEP_COUNT = 3


def group_tree(key: str) -> set[str]:
    """그 그룹과 그 아래 모든 하위 키(그룹·리프 전부). 추가 행이 어느 집계에 얹히는지 판정에 쓴다."""
    out = {key}
    for sub in GROUP_CALC.get(key, ()):
        out |= group_tree(sub)
    return out


TOT_TREE = group_tree("tot")
LABOR_TREE = group_tree("labor")
# 화면 OV_ELK에 있는 키(= 추가 행의 anchor_key로 받아들이는 키). 여기 없거나 인력(hc)이면 화면과
# 똑같이 기타경비(misc)로 떨어뜨린다.
ANCHORABLE = frozenset(CATEGORY_LABELS) - {"hc"}

TOT_TREE = group_tree("tot")
LABOR_TREE = group_tree("labor")

# 화면 OV_ELK에 있는 키(= 추가 행의 anchor_key로 받아들이는 키). 여기 없거나 인력(hc)이면 화면과
# 똑같이 기타경비(misc)로 떨어뜨린다.
ANCHORABLE = frozenset(CATEGORY_LABELS) - {"hc"}

def cp_quarter_amounts(db: Session, year: int) -> tuple[float, float]:
    """그 해 CP 계약의 분기 금액 (개발비 총액, 인건비) — 단위는 저장값과 같은 K$.
    POC 헤더값(poc_total · poc_cost_sum)은 직급 행마다 반복 저장돼 있어 POC 단위로 접어서 더한다
    (행마다 더하면 직급 수만큼 부풀려진다). 연 금액을 4로 나눠 분기 하나치를 만든다 — core.js ovCPc."""
    pocs = {
        (r.poc_type, r.poc_proj): (float(r.poc_total), float(r.poc_cost_sum))
        for r in db.scalars(select(OverseasCpRow).where(OverseasCpRow.year == f"{year:04d}"))
    }
    total = sum(v[0] for v in pocs.values()) / 4 / 1000
    labor = sum(v[1] for v in pocs.values()) / 4 / 1000
    return total, labor

def extra_row_months(rows) -> dict[str, dict[str, list[float]]]:
    """연구소 pk -> 얹힐 계정 키 -> 월별 금액(K$). 화면 ovCustExtra와 같은 규칙으로 anchor_key에 접는다.

    행을 직접 조회하지 않고 받는다(주입식) — 추가 행을 읽는 코드는 라우트 핸들러
    (list_extra_rows)에 있고, services가 엔드포인트를 import하면 의존 방향이 거꾸로 된다
    (endpoints → services 한 방향, PLAN.md 5.1). 호출부가 읽어서 넘긴다.
    """
    out: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        key = row.anchor_key if row.anchor_key in ANCHORABLE else "misc"
        bucket = out.setdefault(row.lab_pk, {}).setdefault(key, [0.0] * 12)
        for j, v in enumerate(row.v[:12]):
            bucket[j] += n(v)
    return out

def krw_months(
    cats: dict[str, list[float | None]], extra: dict[str, list[float]], tree: set[str], rate: list[float]
) -> list[float]:
    """그 연구소의 월별 원화 금액. 저장값은 K$라 ×1000×(원/$)로 원화가 된다 — core.js ovAgg의 krw 모드."""
    leaves = [k for k in tree if k not in GROUP_CALC]
    return [
        (
            sum(n(cats.get(leaf, [None] * 12)[j]) for leaf in leaves)
            + sum(months[j] for key, months in extra.items() if key in tree)
        )
        * rate[j]
        * 1000
        for j in range(12)
    ]


# ---------- 17열(월 12 + 분기 4 + 연간 1) ----------
# 프런트 ov17(core.js)과 같은 규칙이다. 집계표·증감분석이 모두 이 모양으로 숫자를 본다.
# 어긋나면 화면 숫자 ≠ 챗봇 숫자가 되므로 tests/overseas/test_formula_crosscheck.py가 대조한다.

QUARTER_IX: tuple[tuple[int, ...], ...] = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (9, 10, 11))
YEAR_IX: tuple[int, ...] = tuple(range(12))


def to_17(months: list[float | None], how: str = "sum") -> list[float | None]:
    """12개월 → 17칸(월 12 + 분기 4 + 연간). how: sum | avg | avgnz.

    미등록(None)과 0을 가른다 — 공통 RULE(빈칸 처리). 그래서 집계 방식이 셋이다:
    - sum   : 있는 값만 더한다. 전부 미등록이면 결과도 미등록(0이 아니다)
    - avg   : **있는 달로만** 평균. 미등록 달을 0으로 세면 인원 평균이 실제보다 낮게 나온다
    - avgnz : 0도 제외하고 평균. 환율처럼 "0일 수가 없는" 값에 쓴다 — 0은 "아직 안 넣은 달"이다

    프런트 ov17과 같은 규칙이어야 한다(그쪽은 null, 여기는 None).
    """
    if how not in ("sum", "avg", "avgnz"):
        raise ValueError(f"how는 sum | avg | avgnz만 쓸 수 있다: {how!r}")

    def agg(ix: tuple[int, ...]) -> float | None:
        if how == "sum":
            vals = [months[j] for j in ix if j < len(months) and months[j] is not None]
            return float(sum(vals)) if vals else None
        if how == "avg":
            vals = [months[j] for j in ix if j < len(months) and months[j] is not None]
        else:  # avgnz — 0도 뺀다
            vals = [months[j] for j in ix if j < len(months) and months[j]]
        return float(sum(vals) / len(vals)) if vals else None

    out: list[float | None] = [months[j] if j < len(months) else None for j in range(12)]
    out.extend(agg(q) for q in QUARTER_IX)
    out.append(agg(YEAR_IX))
    return out


def agg_months(
    cats: dict[str, list[float | None]],
    extra: dict[str, list[float]],
    tree: set[str],
    rate: list[float] | None = None,
) -> list[float | None]:
    """그 연구소의 월별 집계값. rate를 주면 원화(값 × 환율 × 1000), 안 주면 저장 단위(K$) 그대로.

    krw_months와 같은 계산이지만 **미등록(None)을 보존한다** — 그 달에 아무 리프도 안 올라왔으면
    0이 아니라 미등록이다(공통 RULE: 빈칸 처리). krw_months는 히어로 카드처럼 "합계만 필요한"
    자리에서 쓰여 0으로 접어도 됐지만, 집계표는 빈칸과 0원을 구분해 그려야 한다.
    """
    leaves = [k for k in tree if k not in GROUP_CALC]
    out: list[float | None] = []
    for j in range(12):
        vals = [cats[leaf][j] for leaf in leaves if leaf in cats and j < len(cats[leaf]) and cats[leaf][j] is not None]
        extras = [months[j] for key, months in extra.items() if key in tree]
        if not vals and not extras:
            out.append(None)  # 그 달에 올라온 게 하나도 없다 → 빈칸
            continue
        total = sum(float(v) for v in vals) + sum(extras)
        out.append(total * rate[j] * 1000 if rate else total)
    return out


def pct_17(numerator: list[float | None], denominator: list[float | None]) -> list[float | None]:
    """비율 17칸 = 분자 ÷ 분모 × 100. 프런트 ovPct17과 같은 규칙.

    분자가 미등록이거나 분모가 0·미등록이면 비율을 낼 수 없다 — 0%가 아니라 빈칸이다.
    (0%로 보여주면 "비율이 0"과 "계산할 수 없음"이 구분되지 않는다.)
    """
    return [
        None if (numerator[i] is None or not denominator[i]) else numerator[i] / denominator[i] * 100
        for i in range(len(numerator))
    ]


def agg_across(
    pks: list[str],
    d: dict[str, dict[str, list[float | None]]],
    extras: dict[str, dict[str, list[float]]],
    tree: set[str],
    rate: list[float] | None = None,
) -> list[float | None]:
    """연구소 여러 곳을 묶어 월별 집계. 프런트 ovAgg가 pks를 훑어 더하는 것과 같다.

    미등록 더하기 규칙: 아무 연구소도 그 달 값을 안 올렸으면 미등록(None), 하나라도 올렸으면
    올린 것만 더한다. "안 올린 연구소를 0으로 세는 것"과 결과가 같지만, 전부 안 올린 달을
    0원으로 보여주지 않는 게 다르다(공통 RULE: 빈칸 처리).
    """
    out: list[float | None] = [None] * 12
    for pk in pks:
        months = agg_months(d.get(pk, {}), extras.get(pk, {}), tree, rate)
        for j in range(12):
            if months[j] is None:
                continue
            out[j] = months[j] if out[j] is None else out[j] + months[j]
    return out


def pl_rows(
    pks: list[str],
    d: dict[str, dict[str, list[float | None]]],
    extras: dict[str, dict[str, list[float]]],
    mode: str,
    rate: list[float] | None,
) -> list[dict]:
    """집계 화면 ① 전체 계정 표(손익계산서형) 45행 × 17칸. 프런트 ovPLRows와 같은 표다.

    mode: usd(저장 단위 K$ 그대로) | krw(원화 환산 — rate 필요)

    세 가지가 행마다 다르다:
    - 인력(hc)은 금액이 아니라 명수다 → 환율을 곱하지 않고, 분기·연간은 **합이 아니라 평균**이다
    - 비율(pct)은 MARK UP(B) ÷ **개발비(매출)(tot)** × 100 → 분모가 없으면 빈칸(0%가 아니다).
      분모가 tot인 근거는 화면이 사용자에게 적어 주는 문장이다 — "합계 화면은 MARK UP ÷
      개발비(매출) 계산값"(core.js의 집계표 안내). 지금 쓰지 않는 React(calc.js)는 분모를 A로
      쓰고 있다 — 되살릴 때 먼저 고쳐야 한다(PLAN.md 질문 2)
    - 그 외는 리프를 더해 17칸으로 접는다
    """
    if mode not in ("usd", "krw"):
        raise ValueError(f"mode는 usd | krw만 쓸 수 있다: {mode!r}")
    if mode == "krw" and rate is None:
        raise ValueError("krw 모드는 환율 배열이 필요하다")
    money_rate = rate if mode == "krw" else None
    money_fmt = "usd" if mode == "usd" else "krw"

    # 비율의 분모는 개발비(매출)=tot이다(A가 아니다) — 위 docstring의 근거 참조.
    tot17 = to_17(agg_across(pks, d, extras, group_tree("tot"), money_rate))
    b17 = to_17(agg_across(pks, d, extras, group_tree("B"), money_rate))

    rows: list[dict] = []
    for key, level, unit, memo in AGGREGATE_ROWS:
        if unit == "pct":
            values, fmt = pct_17(b17, tot17), "pct"
        elif unit == "hc":
            # 인력은 환율과 무관하고(명수), 기간 집계는 평균이다 — 월별 인원을 더하면 뜻이 없다.
            values, fmt = to_17(agg_across(pks, d, extras, group_tree(key), None), "avg"), "hc"
        else:
            values, fmt = to_17(agg_across(pks, d, extras, group_tree(key), money_rate)), money_fmt
        rows.append({
            "key": key,
            "label": CATEGORY_LABELS.get(key, key),
            "label_en": CATEGORY_LABELS_EN.get(key, ""),
            "level": level,
            "format": fmt,
            "memo": memo,
            "values": values,
        })
    return rows
