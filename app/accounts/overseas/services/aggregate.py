"""해외연구소 집계 — 손익계산서 계층 전개와 원화 환산.

원래 overseas.py의 `_group_tree` · `_krw_months` · `_cp_quarter_amounts` · `_extra_row_months`와
`_TOT_TREE` · `_LABOR_TREE` 상수였고, 집계 엔드포인트가 그 밑줄 이름을 직접 import해서 썼다
(PLAN.md P1-1).

여기 식은 프런트(core.js의 ovAgg · ovCPc · ovCustExtra)와 맞춰야 한다 —
챗봇이 말하는 금액과 사용자가 보는 표가 어긋나면 안 된다. 어긋남은
tests/test_formula_crosscheck.py가 세 구현을 실제로 실행해서 잡는다(PLAN.md P1-4).
"""

from dataclasses import dataclass

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
from app.accounts.overseas.services.cp import cp_rows_at


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

def cp_quarter_amounts(db: Session, year: int, as_of: str) -> tuple[float, float]:
    """그 해 CP 계약의 분기 금액 (개발비 총액, 인건비) — **K$**로 돌려준다.

    저장값(overseas_cp_rows)은 계약서 그대로 $ 단위라 1000으로 나눠서 집계 단위(K$)에 맞춘다 —
    아래 식의 ÷4는 분기 분할이고 ÷1000이 그 환산이다(프런트 ovCPc도 같은 두 번의 나눗셈을 한다).
    POC 헤더값(poc_total · poc_cost_sum)은 직급 행마다 반복 저장돼 있어 POC 단위로 접어서 더한다
    (행마다 더하면 직급 수만큼 부풀려진다). 연 금액을 4로 나눠 분기 하나치를 만든다 — core.js ovCPc.

    as_of("YYYY-MM")는 보고 있는 버전의 기준연월이다. 이 값은 **생략할 수 없다** — 분기 정산액은
    저장된 값이 아니라 여기서 매번 다시 계산되는 값이라, 시점 없이 "지금 계약"을 읽으면 이미
    마감한 과거 버전의 분기 정산액까지 새 계약액으로 다시 계산된다(services/cp.py 참고)."""
    pocs = {
        (r.poc_type, r.poc_proj): (float(r.poc_total), float(r.poc_cost_sum))
        for r in cp_rows_at(db, year, as_of)
    }
    total = sum(v[0] for v in pocs.values()) / 4 / 1000
    labor = sum(v[1] for v in pocs.values()) / 4 / 1000
    return total, labor

# 참고 행(IT 관련 비용 분해 · 외주용역비)에 붙은 추가 행은 그 참고 행 합계에만 잡힌다.
#
# 규칙은 하나다: **추가 행은 자기가 붙은 자리의 합계에 더해진다.** 전산비 아래에 넣으면
# 전산비 → 일반운영비 → 개발비 → 개발비(매출)로 타고 올라가고, IT 관련 비용 아래에 넣으면
# IT 관련 비용 합계까지만 간다(그 줄 자체가 어느 총계에도 안 올라가기 때문 —
# cost_groups.AGGREGATE_ROWS의 "참고 행"). 빼면 그 합계에서 빠진다.
#
# 한때 참고 행에 붙은 추가 행만 개발비 합계에도 더해 보았는데, 그러면 "행이 붙은 자리"와 "그 금액이
# 세어지는 자리"가 달라져서 규칙을 설명할 수 없게 된다. 어느 합계에 넣고 싶은지는 자리를 고를 때
# 정하면 되는 일이다. 화면도 같은 규칙이다(core.js ovPartEl) — 어긋나면 test_formula_crosscheck가 잡는다.
REF_ONLY_ANCHORS = frozenset(ANCHORABLE - TOT_TREE - {"pct"})


def extra_keys(extra: dict[str, list[float]], tree: set[str]) -> list[str]:
    """이 트리로 세어야 할 추가 행의 anchor 키들 — 그 트리 안에 붙은 행만.

    합계와 "있냐 없냐"를 따로 쓰는 호출부가 있어 키 목록을 돌려준다 — 값이 0인 추가 행도 "있는"
    것이라, 합계가 0이라는 이유로 빈칸으로 접으면 방금 만든 행이 화면에서 사라진다."""
    return [key for key in extra if key in tree]


def extra_total(extra: dict[str, list[float]], tree: set[str], j: int) -> float:
    """그 달(j)에 이 트리로 세어야 할 추가 행 금액의 합."""
    return sum(extra[key][j] for key in extra_keys(extra, tree))


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
            + extra_total(extra, tree, j)
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
        keys = extra_keys(extra, tree)
        if not vals and not keys:
            out.append(None)  # 그 달에 올라온 게 하나도 없다 → 빈칸
            continue
        total = sum(float(v) for v in vals) + sum(extra[key][j] for key in keys)
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


CP_PK = "CP"  # CP는 파트가 없어 pk가 코드와 같다 — 프런트 ovAgg의 `pk==="CP"` 분기와 짝이다.


def cp_quarter_months(amount_k: float) -> list[float | None]:
    """분기 계약금(K$) 한 분기치 → 12개월 배열. 3 · 6 · 9 · 12월에만 금액이 있다.

    나머지 달을 0이 아니라 **미등록(None)으로 둔다**: CP는 분기 정산 계약이라 그 달에 "0원을
    보고한" 게 아니라 아예 금액이 없는 것이다. 합계는 어느 쪽이든 같고(sum은 미등록을 건너뛴다),
    화면도 0을 "–"로 그려서 보이는 것도 같다 — 다른 건 챗봇·API가 "그 달 자료가 있나"를
    물었을 때의 답이다(공통 RULE: 빈칸 처리).
    """
    return [amount_k if (j + 1) % 3 == 0 else None for j in range(12)]


def agg_across(
    pks: list[str],
    d: dict[str, dict[str, list[float | None]]],
    extras: dict[str, dict[str, list[float]]],
    tree: set[str],
    rate: list[float] | None = None,
    cp_months: list[float | None] | None = None,
) -> list[float | None]:
    """연구소 여러 곳을 묶어 월별 집계. 프런트 ovAgg가 pks를 훑어 더하는 것과 같다.

    미등록 더하기 규칙: 아무 연구소도 그 달 값을 안 올렸으면 미등록(None), 하나라도 올렸으면
    올린 것만 더한다. "안 올린 연구소를 0으로 세는 것"과 결과가 같지만, 전부 안 올린 달을
    0원으로 보여주지 않는 게 다르다(공통 RULE: 빈칸 처리).

    cp_months를 주면 **CP만** 올라온 PL 대신 그 배열(계약 기준 분기 금액, K$)을 쓴다 — 집계
    화면 ②·④의 "CP 분기 보정"이고, 프런트 ovAgg의 usdq · krwq 모드와 같다. CP는 분기 정산
    계약이라 매월 분배된 PL을 그대로 더하면 분기말 정산과 어긋난다.
    """
    out: list[float | None] = [None] * 12
    for pk in pks:
        if cp_months is not None and pk == CP_PK:
            # 계약 금액이라 환율만 입히면 된다(리프를 더할 게 없다). 환산식은 일반 경로와 같다.
            months = [
                None if m is None else (m * rate[j] * 1000 if rate else m)
                for j, m in enumerate(cp_months)
            ]
        else:
            months = agg_months(d.get(pk, {}), extras.get(pk, {}), tree, rate)
        for j in range(12):
            if months[j] is None:
                continue
            out[j] = months[j] if out[j] is None else out[j] + months[j]
    return out


def per_capita_dev_17(
    dev: list[float | None], hc: list[float | None]
) -> list[float | None]:
    """인당 개발비 17칸 = 개발비(A) ÷ 인력. 프런트 perCapRow와 같은 규칙.

    분기·연간을 **월별 비율의 평균으로 내면 틀린다** — 인력이 적은 달의 비율이 왜곡되게 끼어든다.
    그래서 "그 구간 개발비 합 ÷ 그 구간 평균 인력"으로 낸다.

    분모가 0이거나 미등록이면 빈칸이다(0이 아니다) — pct_17과 같은 판단이다.

    주의: 구간 평균 인력의 분모가 "인원이 올라온 달 수"가 아니라 **구간 길이(3 · 12)**다.
    프런트와 같게 맞춘 것이다 — 일부 달만 인원이 올라오면 평균이 낮게 나오고, 그러면 인당
    개발비가 높게 나온다. to_17의 avg가 고쳤던 것과 같은 모양의 문제라 따로 확인이 필요하다
    (고치면 화면 숫자가 바뀌므로 함께 바꿀 일이다).
    """
    def q(ds: float | None, hs: float | None) -> float | None:
        return None if not hs or ds is None else ds / hs

    out: list[float | None] = [
        q(dev[j], hc[j]) for j in range(12)
    ]
    for ix in QUARTER_IX:
        ds = sum(dev[j] or 0.0 for j in ix)
        out.append(q(ds, sum(hc[j] or 0.0 for j in ix) / len(ix)))
    ds = sum(v or 0.0 for v in dev)
    out.append(q(ds, sum(v or 0.0 for v in hc) / 12))
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


# ---------- 집계 ②③④ 탭: 연구소별 표 ----------
# ① 전체 계정(pl_rows)이 "계정 45행 × 연구소 합계"라면, ②③④는 "연구소 행 × 한 가지 금액"이다.
# 프런트 ovTables()가 만들던 표를 그대로 옮긴 것이고, 행 순서·라벨·굵기까지 같아야 화면이
# 같은 표를 그린다(tests/overseas/test_lab_tables_crosscheck.py가 전수 대조한다).
#
# ②·④가 ③과 다른 점은 **CP 분기 정산**이다. CP는 분기 계약이라 올라온 월별 PL을 쓰지 않고
# 기준정보 › CP 계약 금액 ÷ 4를 3 · 6 · 9 · 12월에 넣는다(cp_quarter_months).


@dataclass(frozen=True)
class LabGroup:
    """표의 한 줄이 되는 연구소. parts가 있으면 그 아래 하위 줄도 그린다(SARC의 CPU · GPU)."""

    code: str
    name: str
    country: str
    pks: tuple[str, ...]
    parts: tuple[tuple[str, str], ...] = ()  # (pk, 하위 줄 라벨)


def _row(label, values, fmt, level=1, total=False, bold=False, muted=False) -> dict:
    return {
        "label": label, "level": level, "format": fmt, "values": values,
        "total": total, "bold": bold, "muted": muted,
    }


def _lab_label(g: LabGroup, table: str, with_country: bool) -> str:
    """연구소 줄 라벨. 프런트가 표마다 따로 붙이는 꼬리말을 그대로 옮긴다."""
    if g.code == "CP":
        # CP는 분기 정산이라는 걸 줄 이름에 적어 둔다 — ② 표에서만(③·④는 캡션에 적혀 있다).
        return f"{g.name} (미청구) NRE · 분기 정산" if table.startswith("t2") else g.name
    if g.code == "SRCX" and table == "t2u":
        return f"{g.name}(APJT포함)"
    if with_country and g.country:
        return f"{g.name} ({g.country})"
    return g.name


def lab_tables(
    groups: list[LabGroup],
    d: dict[str, dict[str, list[float | None]]],
    extras: dict[str, dict[str, list[float]]],
    rate: list[float] | None,
    cp_quarter: tuple[float, float] | None,
    usd_unit: str = "K$",
) -> dict[str, dict]:
    """집계 ②③④ 탭 6개. {탭키: {caption, note, rows}}.

    rate(원/$ 12개월)가 없으면 원화 표는 만들 수 없다 — 그때는 달러 표만 돌려준다.
    cp_quarter는 (총 계약금, 인건비 계약금) 각각 한 분기치(K$). 없으면 CP 보정 표를 뺀다.
    """
    all_pks = [pk for g in groups for pk in g.pks]
    def without(*codes: str) -> list[str]:
        return [pk for g in groups if g.code not in codes for pk in g.pks]

    tot, labor = group_tree("tot"), group_tree("labor")
    a_tree, hc_tree = group_tree("A"), group_tree("hc")
    cp_tot = cp_quarter_months(cp_quarter[0]) if cp_quarter else None
    cp_lab = cp_quarter_months(cp_quarter[1]) if cp_quarter else None

    def agg(pks, tree, r=None, cp=None) -> list[float | None]:
        return agg_across(pks, d, extras, tree, r, cp)

    def col(pks, tree, r=None, cp=None, how="sum") -> list[float | None]:
        return to_17(agg(pks, tree, r, cp), how)

    def non_labor(pks, r, cp_t, cp_l) -> list[float | None]:
        """인건비外 = 개발비(매출) − 인건비. 둘 다 미등록인 달만 빈칸이다."""
        a, b = agg(pks, tot, r, cp_t), agg(pks, labor, r, cp_l)
        return to_17([
            None if (a[j] is None and b[j] is None) else (a[j] or 0.0) - (b[j] or 0.0)
            for j in range(12)
        ])

    out: dict[str, dict] = {}

    # ── ② 개발비 집계 ─────────────────────────────────────────
    # 맨 윗줄은 CP 분기 보정을 넣은 개발비(매출)이고, 그 아래 "개발비(A,CP제외)"는 보정과
    # 무관한 순수 개발비다 — CP를 빼야 분기말마다 튀는 금액이 섞이지 않는다.
    u = [
        _row(f"개발비(매출), {usd_unit}", col(all_pks, tot, None, cp_tot), "usd", 0, total=True),
        _row(
            f"인당 개발비 ({usd_unit}/명, CP제외)",
            per_capita_dev_17(agg(without("CP"), a_tree), agg(without("CP"), hc_tree)),
            "usd",
        ),
        _row(f"개발비(A,CP제외)", col(without("CP"), a_tree), "usd", 0, bold=True),
    ]
    for g in groups:
        u.append(_row(_lab_label(g, "t2u", False), col(g.pks, tot, None, cp_tot), "usd"))
        # 하위 줄은 보정을 넣지 않는다(CP가 아니라 파트 분해라 분기 정산과 무관하다).
        u += [_row(lb, col([pk], tot), "usd", 2, muted=True) for pk, lb in g.parts]
    out["t2u"] = {
        "caption": "② 개발비 집계 (달러 기준)",
        "note": (
            "개발비(매출) = 개발비(A) + MARK UP(B). 개발비(A,CP제외) = CP를 뺀 연구소의 개발비(A). "
            f"CP는 기준정보 › CP 계약의 총 계약금(Total, $) ÷ 4를 3 · 6 · 9 · 12월에 넣었다. 단위 {usd_unit}."
        ),
        "rows": u,
    }

    if rate is None:
        return out

    # 환율 행은 금액 계층 밖이라 들여쓰기가 없다(프런트 rr()도 lv를 안 준다 → 0).
    fx = _row("적용 환율 (원/$)", to_17(list(rate), "avgnz"), "rate", 0, muted=True)
    cp_note = (
        "CP는 분기 정산 계약이라 PL 대신 기준정보 › CP 계약 금액 ÷ 4를 3 · 6 · 9 · 12월에 넣고, "
        "그 달 적용 환율(실적 월은 실적환율(말일 환율), 이후는 속보 환율)을 곱했다."
    )

    k = [fx, _row("개발비(매출), 억원", col(all_pks, tot, rate, cp_tot), "krw", 0, total=True),
         _row("개발비(A,CP제외)", col(without("CP"), a_tree, rate), "krw", 0, bold=True)]
    k += [_row(_lab_label(g, "t2k", False), col(g.pks, tot, rate, cp_tot), "krw") for g in groups]
    out["t2k"] = {
        "caption": "② 개발비 집계 (원화 기준)",
        "note": "개발비(매출) = 개발비(A) + MARK UP(B). " + cp_note,
        "rows": k,
    }

    # ── ③ 인건비 집계 (CP 분기 정산 미반영) ───────────────────
    # 올라온 PL 그대로다 — CP도 매월 분배된 금액을 쓴다. 보정한 표는 ④다.
    def labor_rows(fmt, r, cp, *, country=False, ex_label=None, parts=False, head=None):
        rows = [_row(head, col(all_pks, labor, r, cp), fmt, 0, total=True)]
        if ex_label:
            rows.append(_row(ex_label, col(without("CP", "SRCX"), labor, r, cp), fmt, 0, bold=True))
        for g in groups:
            rows.append(_row(_lab_label(g, "t3", country), col(g.pks, labor, r, cp), fmt))
            if parts:
                rows += [_row(lb, col([pk], labor, r, cp), fmt, 2, muted=True) for pk, lb in g.parts]
        return rows

    # ③ 달러 표에만 "CP·SRCX 제외" 줄이 없다 — 프런트가 그 줄을 빼고 그린다(filter(i!==1)).
    out["t3"] = {
        "caption": "③ 인건비 집계 (달러 기준) · CP 분기 정산 미반영",
        "note": f"인건비 = (급여) + (상여) + (복리후생). 단위 {usd_unit}.",
        "rows": labor_rows("usd", None, None, country=True, head=f"인건비({usd_unit})"),
    }
    out["t4"] = {
        "caption": "③ 인건비 집계 (원화 기준) · CP 분기 정산 미반영",
        "note": "연구소 데이터 그대로(CP도 매월 분배된 금액)를 원화로 환산했다.",
        "rows": [fx] + labor_rows(
            "krw", rate, None, ex_label="인건비(CP,SRCX 제외)", head="인건비(억원)"
        ),
    }

    if cp_quarter is None:
        return out

    # ── ④ CP 보정 ─────────────────────────────────────────────
    out["t5"] = {
        "caption": "④ CP 보정 · 인건비 (원화 기준)",
        "note": cp_note,
        "rows": [fx] + labor_rows(
            "krw", rate, cp_lab, ex_label="인건비(CP,SRCX 제외)", parts=True,
            head="인건비(억원, CP분기보정)",
        ),
    }

    n = [fx, _row("인건비 외 (억원, CP분기보정)", non_labor(all_pks, rate, cp_tot, cp_lab), "krw", 0, total=True),
         _row("인건비 외(CP,SRCX 제외)", non_labor(without("CP", "SRCX"), rate, cp_tot, cp_lab), "krw", 0, bold=True)]
    for g in groups:
        n.append(_row(g.name, non_labor(list(g.pks), rate, cp_tot, cp_lab), "krw"))
        n += [_row(lb, non_labor([pk], rate, cp_tot, cp_lab), "krw", 2, muted=True) for pk, lb in g.parts]
    out["t6"] = {
        "caption": "④ CP 보정 · 인건비외 (원화 기준)",
        "note": "인건비외 = 개발비(매출) − 인건비. " + cp_note,
        "rows": n,
    }
    return out
