"""해외연구소 집계 — 손익계산서 계층 전개와 원화 환산.

**이 파일이 유일한 계산자**다. 화면은 집계·카드·마감 금액을 전부 서버에서 받고, 출력의 내부
정합성을 test_krw_aggregation_invariants가 검사한다. 프런트에 남은 복제는 ovPartEl ·
ovLaborSplit · ov17 · ovPct17 넷이고, 그 넷은 tests/overseas/test_formula_crosscheck.py가
계속 대조한다.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.overseas import cost_groups
from app.accounts.overseas.cost_groups import (
    AGGREGATE_ROWS,
    CATEGORY_LABELS,
    CATEGORY_LABELS_EN,
    GROUP_CALC,
    resolve_leaves,
)
from app.shared.core.values import n
from app.accounts.overseas.services.cp import cp_rows_at


STEP_COUNT = 3


def group_tree(key: str) -> set[str]:
    """그 그룹과 그 아래 모든 하위 키(그룹·리프 전부). 추가 행이 어느 집계에 얹히는지 판정에 쓴다."""
    out = {key}
    for sub in GROUP_CALC.get(key, ()):
        out |= group_tree(sub)
    return out


# 아래 네 개는 계정 정의(cost_groups)에서 뽑아 쓰는 파생값이다. 공통코드가 바뀌면 같이 바뀌어야
# 하므로 **제자리 갱신이 되는 그릇**(list·set)에 담고, 갱신은 cost_groups의 reload 훅이 부른다 —
# 다른 모듈이 `from aggregate import TOT_TREE`로 이름을 들고 있어서 새 객체를 대입하면 그쪽은
# 옛 값을 계속 본다(frozenset·tuple을 쓰면 애초에 갱신이 불가능하다).
TOT_LEAVES: list[str] = []
#: 업로드 여부 판정에 보는 입력 칸(화면 OV_IN = 파생 아닌 모든 행 + 인력).
INPUT_KEYS: set[str] = set()
TOT_TREE: set[str] = set()
LABOR_TREE: set[str] = set()
#: 추가 행의 anchor_key로 받아들이는 키(= 사용 중인 계정 전부). 여기 없거나 인력(hc)이면 화면과
#: 똑같이 기타경비(misc)로 떨어뜨린다 — 공통코드에서 끈 코드도 여기서 빠진다.
ANCHORABLE: set[str] = set()
#: "어느 소계에도 넣지 않는다"는 뜻의 특수 자리. anchor_key를 **안 정한 것**(None)과는 다르다 —
#: None은 "아직 안 골랐다"라서 기타경비로 떨어지고, 이건 "골라서 뺐다"라 어느 합계에도 안 들어간다.
#: 계정 코드와 섞이지 않게 밑줄 두 개로 감싼다(공통코드에 이런 코드는 만들 수 없다).
NO_ANCHOR = "__none__"


@cost_groups.on_reload
def _rebuild_trees() -> None:
    TOT_LEAVES[:] = resolve_leaves("tot")
    INPUT_KEYS.clear()
    INPUT_KEYS.update(TOT_LEAVES)
    INPUT_KEYS.add("hc")
    TOT_TREE.clear()
    TOT_TREE.update(group_tree("tot"))
    LABOR_TREE.clear()
    LABOR_TREE.update(group_tree("labor"))
    ANCHORABLE.clear()
    ANCHORABLE.update(set(CATEGORY_LABELS) - {"hc"})
    REF_ONLY_ANCHORS.clear()
    REF_ONLY_ANCHORS.update(ANCHORABLE - TOT_TREE - {"pct"})

def cp_quarter_amounts(db: Session, year: int, as_of: str) -> tuple[float, float]:
    """그 해 CP 계약의 분기 금액 (개발비 총액, 인건비) — **$**로 돌려준다.

    저장값(overseas_cp_rows)도 계약서 그대로 $ 단위라 환산이 없다 — 아래 식의 ÷4는 분기 분할뿐이다.
    POC 헤더값(poc_total · poc_cost_sum)은 직급 행마다 반복 저장돼 있어 POC 단위로 접어서 더한다
    (행마다 더하면 직급 수만큼 부풀려진다). 연 금액을 4로 나눠 분기 하나치를 만든다.

    as_of("YYYY-MM")는 보고 있는 버전의 기준연월이다. 이 값은 **생략할 수 없다** — 분기 정산액은
    저장된 값이 아니라 여기서 매번 다시 계산되는 값이라, 시점 없이 "지금 계약"을 읽으면 이미
    마감한 과거 버전의 분기 정산액까지 새 계약액으로 다시 계산된다(services/cp.py 참고)."""
    pocs = {
        (r.poc_type, r.poc_proj): (float(r.poc_total), float(r.poc_cost_sum))
        for r in cp_rows_at(db, year, as_of)
    }
    total = sum(v[0] for v in pocs.values()) / 4
    labor = sum(v[1] for v in pocs.values()) / 4
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
REF_ONLY_ANCHORS: set[str] = set()
# 위 네 개와 같은 이유로 제자리 갱신한다(_rebuild_trees가 채운다). 모듈을 다 읽은 지금 한 번
# 돌려서 하드코딩 초기값 기준으로 채워 두고, DB를 읽은 뒤에는 reload 훅이 다시 채운다.
_rebuild_trees()


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
    (endpoints → services 한 방향). 호출부가 읽어서 넘긴다.

    NO_ANCHOR("합산 제외")는 아예 빼고 돌려준다 — 그 행은 표에만 있고 어느 합계에도 안 들어간다.
    """
    out: dict[str, dict[str, list[float]]] = {}
    for row in rows:
        if row.anchor_key == NO_ANCHOR:
            continue
        key = row.anchor_key if row.anchor_key in ANCHORABLE else "misc"
        bucket = out.setdefault(row.lab_pk, {}).setdefault(key, [0.0] * 12)
        for j, v in enumerate(row.v[:12]):
            bucket[j] += n(v)
    return out

def krw_months(
    cats: dict[str, list[float | None]], extra: dict[str, list[float]], tree: set[str], rate: list[float]
) -> list[float]:
    """그 연구소의 월별 원화 금액. 저장값이 $라 (원/$)만 곱하면 원화가 된다.

    2026-11-18 단위 전환: 저장값이 K$였을 때는 여기에 ×1000이 붙어 있었다. 지금은 업로드가
    K$ 셀을 ×1000해서 $로 저장하므로(core.js ovParse) 환산 단계가 하나 줄었다."""
    leaves = [k for k in tree if k not in GROUP_CALC]
    return [
        (
            sum(n(cats.get(leaf, [None] * 12)[j]) for leaf in leaves)
            + extra_total(extra, tree, j)
        )
        * rate[j]
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
    """그 연구소의 월별 집계값. rate를 주면 원화(값 × 환율), 안 주면 저장 단위($) 그대로.

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
        out.append(total * rate[j] if rate else total)
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


CP_PK = "CP"  # CP는 파트가 없어 pk가 코드와 같다(연구소 pk == 연구소 코드).


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
    """연구소 여러 곳을 묶어 월별 집계.

    미등록 더하기 규칙: 아무 연구소도 그 달 값을 안 올렸으면 미등록(None), 하나라도 올렸으면
    올린 것만 더한다. "안 올린 연구소를 0으로 세는 것"과 결과가 같지만, 전부 안 올린 달을
    0원으로 보여주지 않는 게 다르다(공통 RULE: 빈칸 처리).

    cp_months를 주면 **CP만** 올라온 PL 대신 그 배열(계약 기준 분기 금액, K$)을 쓴다 — 집계
    화면 ②·④의 "CP 분기 보정"이다. CP는 분기 정산
    계약이라 매월 분배된 PL을 그대로 더하면 분기말 정산과 어긋난다.
    """
    out: list[float | None] = [None] * 12
    for pk in pks:
        if cp_months is not None and pk == CP_PK:
            # 계약 금액이라 환율만 입히면 된다(리프를 더할 게 없다). 환산식은 일반 경로와 같다.
            months = [
                None if m is None else (m * rate[j] if rate else m)
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
      쓰고 있다 — 되살릴 때 먼저 고쳐야 한다
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

# ---------- 집계 화면 연구소별 표(탭 ①②③) ----------
# 전체 계정(pl_rows)이 "계정 45행 × 연구소 합계"라면, 이 표들은 "연구소 행 × 한 가지 금액"이다.
#
# 화면 탭 구성:
#   ① 개발비        — 총계(t2u · t2k) · 인건비(t5u · t5) · 인건비외(t6u · t6)   … CP 분기 정산 반영
#   ② 개발비(CP_PL) — t7u · t7k                                               … CP도 올라온 PL 그대로
#   ③ 인건비(CP_PL) — t3 · t4                                                 … CP도 올라온 PL 그대로
#   ④ 전체 계정     — 이 함수가 아니라 pl_rows(/overseas/aggregate-table)
# 표 키(t2u · …)는 그대로 뒀다 — 화면 · 챗봇 · 테스트가 이미 그 키로 표를 집는다. 탭 번호가 아니라
# "어느 표인가"를 가리키는 이름이라 순서가 바뀌어도 키는 안 바뀐다.
#
# ①이 ②③과 다른 점은 **CP 분기 정산**이다. CP는 분기 계약이라 올라온 월별 PL을 쓰지 않고
# 기준정보 › CP 계약 금액 ÷ 4를 3 · 6 · 9 · 12월에 넣는다(cp_quarter_months). 같은 금액을 보정
# 없이 보는 자리가 ②(개발비) · ③(인건비)다 — 개발비·인건비를 PL본과 보정본 양쪽으로 다 볼 수
# 있어야 짝이 맞는다.


@dataclass(frozen=True)
class LabGroup:
    """표의 한 줄이 되는 연구소. parts가 있으면 그 아래 하위 줄도 그린다(SARC의 CPU · GPU)."""

    code: str
    name: str
    country: str
    pks: tuple[str, ...]
    parts: tuple[tuple[str, str], ...] = ()  # (pk, 하위 줄 라벨)


def _row(label, values, fmt, level=1, total=False, bold=False, muted=False, key="") -> dict:
    """key는 연구소 줄에만 붙는다(연구소 코드, 하위 줄은 pk). 합계·소계·환율·인당 줄은 빈 문자열이다.

    왜 필요한가: 화면의 "○월 실적 vs 전실행" 표가 이 표를 당월·전실행 두 번 받아 **같은 연구소끼리
    짝지어야** 한다. 라벨로 짝지으면 틀린다 — 같은 연구소인데 표마다 라벨이 다르다(개발비 달러 표의
    SRCX는 "SRCX(APJT포함)", 원화 표는 "SRCX"). 라벨은 사람이 읽는 것이고 짝짓기는 키로 한다."""
    return {
        "label": label, "key": key, "level": level, "format": fmt, "values": values,
        "total": total, "bold": bold, "muted": muted,
    }


def _lab_label(
    g: LabGroup, *, cp_quarterly: bool = False, with_country: bool = False, apjt: bool = False
) -> str:
    """연구소 줄 라벨. 프런트가 표마다 따로 붙이던 꼬리말을 그대로 옮긴다.

    cp_quarterly는 "이 표의 CP 줄이 계약 기준 분기 금액인가"다 — 그 표에서만 줄 이름에 적어 둔다
    (CP_PL 표의 CP 줄은 올라온 PL이라 분기 정산이라고 적으면 거짓말이 된다)."""
    if g.code == "CP" and cp_quarterly:
        return f"{g.name} (미청구) NRE · 분기 정산"
    if g.code == "SRCX" and apjt:
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
    """집계 화면 탭 ①②③의 표 10장. {탭키: {caption, note, rows}}.

    rate(원/$ 12개월)가 없으면 원화 표는 만들 수 없다 — 그때는 달러 표만 돌려준다.
    cp_quarter는 (총 계약금, 인건비 계약금) 각각 한 분기치(K$). 없으면 ① 개발비 탭(보정본)을
    통째로 뺀다 — 보정할 재료가 없는데 표를 내면 "보정했다"로 읽힌다. 그 해에도 ②③(CP_PL)은
    그대로 나온다(올라온 PL만 쓰므로 계약이 필요 없다).
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

    # ── 표 한 장의 행을 만드는 세 가지 ─────────────────────────
    # 같은 모양을 보정본(①)과 PL본(②③) 두 번 쓴다 — 다른 건 CP 줄에 넣는 금액(cp 인자)뿐이다.
    # 달러 표에만 "인당 개발비"와 SARC 하위 파트(CPU · GPU) 줄이 있는 건 화면이 그렇게 그려 온
    # 구성이다(원화 표는 연구소 합계까지만 본다).

    def dev_rows(fmt, r, cp):
        """개발비(매출) 표 — 맨 윗줄이 그 표의 개발비(매출)이고, 그 아래 "개발비(A,CP제외)"는
        CP를 뺀 순수 개발비다(분기말마다 튀는 금액이 섞이지 않는다)."""
        usd = fmt == "usd"
        rows = [_row(
            f"개발비(매출), {usd_unit}" if usd else "개발비(매출), 억원",
            col(all_pks, tot, r, cp), fmt, 0, total=True,
        )]
        if usd:
            rows.append(_row(
                f"인당 개발비 ({usd_unit}/명, CP제외)",
                per_capita_dev_17(agg(without("CP"), a_tree), agg(without("CP"), hc_tree)),
                fmt,
            ))
        rows.append(_row("개발비(A,CP제외)", col(without("CP"), a_tree, r), fmt, 0, bold=True))
        for g in groups:
            rows.append(_row(
                _lab_label(g, cp_quarterly=cp is not None, apjt=usd),
                col(g.pks, tot, r, cp), fmt, key=g.code,
            ))
            if usd:
                # 하위 줄은 보정을 넣지 않는다(CP가 아니라 파트 분해라 분기 정산과 무관하다).
                rows += [_row(lb, col([pk], tot, r), fmt, 2, muted=True, key=pk) for pk, lb in g.parts]
        return rows

    def labor_rows(fmt, r, cp, *, country=False, ex_label=None, parts=False, head=None):
        rows = [_row(head, col(all_pks, labor, r, cp), fmt, 0, total=True)]
        if ex_label:
            rows.append(_row(ex_label, col(without("CP", "SRCX"), labor, r, cp), fmt, 0, bold=True))
        for g in groups:
            rows.append(_row(_lab_label(g, with_country=country), col(g.pks, labor, r, cp), fmt, key=g.code))
            if parts:
                rows += [_row(lb, col([pk], labor, r, cp), fmt, 2, muted=True, key=pk) for pk, lb in g.parts]
        return rows

    def non_labor_rows(fmt, r, cp_t, cp_l, *, head):
        rows = [
            _row(head, non_labor(all_pks, r, cp_t, cp_l), fmt, 0, total=True),
            _row("인건비 외(CP,SRCX 제외)", non_labor(without("CP", "SRCX"), r, cp_t, cp_l), fmt, 0, bold=True),
        ]
        for g in groups:
            rows.append(_row(g.name, non_labor(list(g.pks), r, cp_t, cp_l), fmt, key=g.code))
            rows += [_row(lb, non_labor([pk], r, cp_t, cp_l), fmt, 2, muted=True, key=pk) for pk, lb in g.parts]
        return rows

    cp_q_note = (
        "CP는 분기 정산 계약이라 PL 대신 기준정보 › CP 계약 금액 ÷ 4를 3 · 6 · 9 · 12월에 넣었다"
    )
    cp_fx_note = (
        "그 달 적용 환율(실적 월은 실적환율(말일 환율), 이후는 속보 환율)을 곱했다."
    )
    cp_pl_note = "CP도 연구소가 보낸 PL(매월 금액) 그대로다 — 분기 정산을 넣지 않는다."
    dev_note = "개발비(매출) = 개발비(A) + MARK UP(B). 개발비(A,CP제외) = CP를 뺀 연구소의 개발비(A)."
    labor_note = "인건비 = (급여) + (상여) + (복리후생)."

    out: dict[str, dict] = {}

    # ── ① 개발비 (CP 분기 정산 반영) · 달러 ───────────────────
    if cp_quarter is not None:
        out["t2u"] = {
            "caption": "① 개발비 · 총계 (달러 기준)",
            "note": f"{dev_note} {cp_q_note}(총 계약금 Total, $). 단위 {usd_unit}.",
            "rows": dev_rows("usd", None, cp_tot),
        }
        out["t5u"] = {
            "caption": "① 개발비 · 인건비 (달러 기준)",
            "note": f"{labor_note} {cp_q_note}(인건비 계약금 Cost Sum POC, $). 단위 {usd_unit}.",
            # 달러 표도 원화 표와 같은 행 구성이다 — 같은 항목에서 단위만 바꿨을 때 줄이
            # 하나 늘었다 줄었다 하면 두 표를 나란히 읽을 수 없다.
            "rows": labor_rows(
                "usd", None, cp_lab, ex_label=f"인건비({usd_unit}, CP·SRCX 제외)", parts=True,
                head=f"인건비({usd_unit}, CP분기보정)",
            ),
        }
        out["t6u"] = {
            "caption": "① 개발비 · 인건비외 (달러 기준)",
            "note": (
                f"인건비외 = 개발비(매출) − 인건비. {cp_q_note}"
                f"(총 계약금 − 인건비 계약금, $). 단위 {usd_unit}."
            ),
            "rows": non_labor_rows("usd", None, cp_tot, cp_lab, head=f"인건비 외({usd_unit}, CP분기보정)"),
        }

    # ── ② 개발비(CP_PL) · 달러 ────────────────────────────────
    out["t7u"] = {
        "caption": "② 개발비(CP_PL) (달러 기준)",
        "note": f"{dev_note} {cp_pl_note} 단위 {usd_unit}.",
        "rows": dev_rows("usd", None, None),
    }

    # ── ③ 인건비(CP_PL) · 달러 ────────────────────────────────
    # 달러 표에만 "CP·SRCX 제외" 줄이 없다 — 화면이 그 줄을 빼고 그려 온 구성이다.
    out["t3"] = {
        "caption": "③ 인건비(CP_PL) (달러 기준)",
        "note": f"{labor_note} {cp_pl_note} 단위 {usd_unit}.",
        "rows": labor_rows("usd", None, None, country=True, head=f"인건비({usd_unit})"),
    }

    if rate is None:
        return out

    # 환율 행은 금액 계층 밖이라 들여쓰기가 없다(프런트 rr()도 lv를 안 준다 → 0).
    fx = _row("적용 환율 (원/$)", to_17(list(rate), "avgnz"), "rate", 0, muted=True)

    # ── ① 개발비 (CP 분기 정산 반영) · 원화 ───────────────────
    if cp_quarter is not None:
        out["t2k"] = {
            "caption": "① 개발비 · 총계 (원화 기준)",
            "note": f"{dev_note} {cp_q_note}(총 계약금 Total)에, {cp_fx_note}",
            "rows": [fx] + dev_rows("krw", rate, cp_tot),
        }
        out["t5"] = {
            "caption": "① 개발비 · 인건비 (원화 기준)",
            "note": f"{labor_note} {cp_q_note}(인건비 계약금 Cost Sum POC)에, {cp_fx_note}",
            "rows": [fx] + labor_rows(
                "krw", rate, cp_lab, ex_label="인건비(CP,SRCX 제외)", parts=True,
                head="인건비(억원, CP분기보정)",
            ),
        }
        out["t6"] = {
            "caption": "① 개발비 · 인건비외 (원화 기준)",
            "note": (
                f"인건비외 = 개발비(매출) − 인건비. {cp_q_note}"
                f"(총 계약금 − 인건비 계약금)에, {cp_fx_note}"
            ),
            "rows": [fx] + non_labor_rows(
                "krw", rate, cp_tot, cp_lab, head="인건비 외 (억원, CP분기보정)"
            ),
        }

    # ── ② 개발비(CP_PL) · 원화 ────────────────────────────────
    out["t7k"] = {
        "caption": "② 개발비(CP_PL) (원화 기준)",
        "note": f"{dev_note} {cp_pl_note} 금액에 {cp_fx_note}",
        "rows": [fx] + dev_rows("krw", rate, None),
    }

    # ── ③ 인건비(CP_PL) · 원화 ────────────────────────────────
    out["t4"] = {
        "caption": "③ 인건비(CP_PL) (원화 기준)",
        "note": f"{labor_note} {cp_pl_note} 금액에 {cp_fx_note}",
        "rows": [fx] + labor_rows(
            "krw", rate, None, ex_label="인건비(CP,SRCX 제외)", head="인건비(억원)"
        ),
    }
    return out

# ---------- 인건비율 팝업(집계 화면) ----------
# 화면 ovLRRows가 브라우저에서 만들던 블록을 그대로 옮긴 것이다(그쪽 수식은 2026-11-18에 지웠다).
# /overseas/labor-ratio(원화 · CP 분기 정산 · 한 기간)와 **다른 표**다:
#   - 단위가 달러(K$)다 — 팝업은 "환율 영향 없이 비율만 본다"는 화면이다
#   - CP도 올라온 PL 그대로다(분기 보정을 넣지 않는다 · 팝업 안내문에 그렇게 적혀 있다)
#   - 17칸(월 12 + 분기 4 + 연간)이고, 연구소 아래 **파트까지** 내려간다
# 비율을 분기·연간에서 "월별 비율의 평균"으로 내면 틀린다 — 기간 합계끼리 나눈다(pct_17).


def labor_ratio_groups(
    groups: list[LabGroup],
    d: dict[str, dict[str, list[float | None]]],
    extras: dict[str, dict[str, list[float]]],
) -> list[dict]:
    """[{key, label, level, labor[17], dev[17], ratio[17]}] — 전체 → 연구소 → 파트 순."""
    def block(key: str, label: str, level: int, pks: list[str]) -> dict:
        labor = to_17(agg_across(pks, d, extras, LABOR_TREE))
        dev = to_17(agg_across(pks, d, extras, TOT_TREE))
        return {
            "key": key, "label": label, "level": level,
            "labor": labor, "dev": dev, "ratio": pct_17(labor, dev),
        }

    out = [block("ALL", "전체 연구소", 0, [pk for g in groups for pk in g.pks])]
    for g in groups:
        out.append(block(g.code, g.name, 1, list(g.pks)))
        out += [block(pk, lb, 2, [pk]) for pk, lb in g.parts]
    return out


# ---------- 마감 화면: 연구소별(파트까지) 월별 원화 ----------
# 마감 2단계의 전표 매핑 타깃과 "전실행" 금액이다. 매핑 단위가 **파트**라서(SARC는 CPU · GPU
# 두 워크북이고 전표는 과제코드로 갈린다) 연구소 묶음이 아니라 pk 단위로 준다.
# CP 분기 보정을 넣는다 — 마감 화면이 브라우저에서 하던 환산과 같은 식이다.


def lab_monthly_krw(
    labs: dict,
    pks: list[str],
    d: dict[str, dict[str, list[float | None]]],
    extras: dict[str, dict[str, list[float]]],
    rate: list[float],
    cp_quarter: tuple[float, float] | None,
) -> tuple[list[dict], list[float | None]]:
    """([{pk, lab_code, label, months[12]}], 합계[12]). 금액은 원이고 CP는 계약 기준 분기 금액이다."""
    cp_months = cp_quarter_months(cp_quarter[0]) if cp_quarter else None
    rows = []
    for pk in pks:
        lab = labs[pk]
        rows.append({
            "pk": pk,
            "lab_code": lab.lab_code,
            "label": lab.name,
            "months": agg_across([pk], d, extras, TOT_TREE, rate, cp_months),
        })
    return rows, agg_across(pks, d, extras, TOT_TREE, rate, cp_months)


# ---------- 집계 ① 표의 파생 열(계산 열) ----------
# 화면에서 [+ 열 추가]로 만든 "3분기 합계" 같은 열이다. 열 자체(이름·계산식)는 이미 DB에 있고
# (overseas extra_cols, formula={"op":"sum_months","months":[7,8,9]}), 칸 값은 저장하지 않고
# 볼 때마다 계산한다 — 그 계산을 프런트 ovXVal이 하던 것을 여기로 옮긴 것이다(2026-11-18).
#
# 규칙은 ovXVal과 같다:
#   - 비율(pct) · 환율(rate) 행은 기간으로 묶는 뜻이 없어 빈칸이다(합치거나 평균 내면 거짓이 된다)
#   - 인력(hc) 행은 평균, 나머지는 합. op=avg_months면 전부 평균
#   - 비율 파생 행은 "그 구간 분자 합 ÷ 그 구간 분모 합" — 월별 비율을 더하거나 평균 내면 틀린다


def derived_col_value(
    months: list[float | None],
    fmt: str,
    op: str,
    ix: list[int],
    ratio: tuple[list[float | None], list[float | None]] | None = None,
) -> float | None:
    """파생 열 한 칸. ix는 0-based 월 인덱스 목록이다."""
    if fmt in ("pct", "rate"):
        return None
    if ratio is not None:
        num_m, den_m = ratio
        top = sum(n(num_m[j]) for j in ix if j < len(num_m))
        bottom = sum(n(den_m[j]) for j in ix if j < len(den_m))
        return (top / bottom) if bottom else 0.0
    vals = [n(months[j]) if j < len(months) else 0.0 for j in ix]
    total = sum(vals)
    if op == "avg_months" or fmt == "hc":
        return (total / len(vals)) if vals else 0.0
    return total


def derived_columns(
    cols: list[tuple[str, str, list[int]]],
    rows: list[tuple[str, list[float | None], str, tuple | None]],
) -> list[dict]:
    """[{label, op, months, values:{행키: 값|None}}].

    cols: (라벨, op, 월 1~12) · rows: (행키, 12개월, format, 비율근거|None)
    """
    out = []
    for label, op, months in cols:
        ix = [m - 1 for m in months if 1 <= m <= 12]
        values = {
            key: derived_col_value(mon, fmt, op, ix, ratio) for key, mon, fmt, ratio in rows
        }
        out.append({"label": label, "op": op, "months": list(months), "values": values})
    return out
