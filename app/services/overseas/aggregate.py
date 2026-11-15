"""해외연구소 집계 — 손익계산서 계층 전개와 원화 환산.

원래 overseas.py의 `_group_tree` · `_krw_months` · `_cp_quarter_amounts` · `_extra_row_months`와
`_TOT_TREE` · `_LABOR_TREE` 상수였고, 집계 엔드포인트가 그 밑줄 이름을 직접 import해서 썼다
(PLAN.md P1-1).

여기 식은 프런트(core.js의 ovAgg · ovCPc · ovCustExtra, calc.js의 같은 이름)와 맞춰야 한다 —
챗봇이 말하는 금액과 사용자가 보는 표가 어긋나면 안 된다. 어긋남은
tests/test_formula_crosscheck.py가 세 구현을 실제로 실행해서 잡는다(PLAN.md P1-4).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.cost_groups import CATEGORY_LABELS, GROUP_CALC, resolve_leaves
from app.core.values import n
from app.models import OverseasCpRow


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
