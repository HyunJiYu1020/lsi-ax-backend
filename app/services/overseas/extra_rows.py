"""연구소가 직접 추가한 행(extra-rows) 읽기 + 파생 행 계산식 평가.

왜 여기로 옮겼나 (PLAN.md P1-1): 집계 엔드포인트가 이 읽기 로직을 라우트 핸들러
(`list_extra_rows`)로 직접 호출했다. 추가 행은 집계 합계에 얹히는 값이라(화면 ovCustExtra)
집계·취합·요약이 모두 같은 규칙으로 읽어야 한다.

파생 행(is_derived)은 월 값을 저장하지 않고 계산식만 저장한다 — 원본이 업로드로 바뀌면 파생값도
따라 바뀌어야 하므로 읽을 때마다 다시 평가한다(저장된 숫자는 금방 낡는다).
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.cost_groups import GROUP_CALC, resolve_leaves, category_label
from app.core.values import n
from app.models import CostEntry, OverseasLab
from app.schemas.overseas import ExtraRowOut
from app.services.overseas.entries import entry_months_bulk
from app.services.overseas.labs import ACCOUNT_KEY, ALL_SCOPE

# 근거 문장에 쓰는 연산 기호 — 사용자에게 "인건비 ÷ 인력"처럼 보여주기 위함.
OP_SYMBOL = {"sum_elementwise": " + ", "div_elementwise": " ÷ "}


def check_lab_pk(db: Session, pk: str, allow_all: bool = False) -> str:
    """scope_code(=연구소 pk)가 overseas_labs에 실재하는지 검증한다. DB가 FK로 못 막아주는 부분이라 여기서 확인.
    allow_all=True면 전체 합산 범위("ALL")도 받아준다(집계 화면의 파생 행 전용)."""
    if allow_all and pk == ALL_SCOPE:
        return pk
    if not db.scalar(select(OverseasLab.id).where(OverseasLab.pk == pk)):
        raise HTTPException(status_code=400, detail=f"알 수 없는 연구소 코드: {pk}")
    return pk

def check_version(version: str) -> str:
    if version not in ("forecast", "actual"):
        raise HTTPException(status_code=400, detail='version은 "forecast" 또는 "actual"이어야 합니다.')
    return version

def operand_months(key: str, by_category: dict[str, list[float]], width: int) -> list[float]:
    """계정 키 하나를 월 배열로 바꾼다. 집계 그룹(인건비 등)이면 리프까지 전개해서 월별로 더한다."""
    out = [0.0] * width
    for leaf in resolve_leaves(key):
        for i, v in enumerate(by_category.get(leaf, [])[:width]):
            out[i] += v
    return [round(v, 4) for v in out]

def months_by_category(db: Session, lab_pk: str, version: str, yyyymm: str) -> dict[str, list[float]]:
    """그 연구소·버전·연월에 저장된 모든 행의 월별 값을 category_code -> 월 배열로 모아 온다.
    파생 행(is_derived)은 제외한다 — 파생값을 다시 파생의 재료로 쓰면 평가가 꼬이고 순환할 수 있다.
    lab_pk가 ALL_SCOPE면 전체 연구소를 계정별로 합산한다(집계 화면의 파생 행이 쓰는 범위) —
    비율은 연구소별로 따로 계산해서 더하면 뜻이 없어지므로, 재료를 먼저 다 더한 뒤 계산해야 한다."""
    stmt = select(CostEntry).where(
        CostEntry.account_code == ACCOUNT_KEY,
        CostEntry.version == version,
        CostEntry.yyyymm == yyyymm,
        CostEntry.is_derived.is_(False),
    )
    if lab_pk != ALL_SCOPE:
        stmt = stmt.where(CostEntry.scope_code == lab_pk)
    entries = list(db.scalars(stmt))
    months_by_entry = entry_months_bulk(db, entries)
    out: dict[str, list[float]] = {}
    for e in entries:
        acc = out.setdefault(e.category_code, [])
        for i, v in enumerate(months_by_entry[e.id]):
            # 파생 행의 재료 — 미등록은 0으로 보고 더한다(있는 값만 더하는 것과 결과가 같다).
            if i < len(acc):
                acc[i] += n(v)
            else:
                acc.append(n(v))
    return out

def eval_row_formula(by_category: dict[str, list[float]], formula: dict) -> tuple[list[float], dict]:
    """파생 행의 계산식을 지금 저장된 원본 값으로 평가해 (월별 값, 근거)를 돌려준다.
    근거는 "인건비 1,000 ÷ 인력 10 = 100"처럼 사용자·챗봇에게 그대로 보여줄 수 있는 형태로 만든다.
    by_category는 months_by_category 결과 — 파생 행이 여러 개일 때 매번 다시 조회하지 않도록
    호출부에서 (연구소·버전·연월)당 한 번만 구해서 넘긴다."""
    op, operand_keys = formula["op"], formula["operands"]
    # 재료들의 월 개수가 다를 수 있어(어떤 계정만 24개월치가 들어온 경우) 가장 긴 쪽에 맞춘다.
    width = max((len(v) for v in by_category.values()), default=12) or 12
    series = [operand_months(k, by_category, width) for k in operand_keys]

    values: list[float] = []
    for i in range(width):
        if op == "sum_elementwise":
            values.append(round(sum(s[i] for s in series), 4))
        else:  # div_elementwise — 분모가 0인 달은 계산할 수 없어 0으로 둔다(근거에 그대로 드러난다)
            numerator, denominator = series[0][i], series[1][i]
            values.append(round(numerator / denominator, 4) if denominator else 0.0)

    operands_basis = [
        {"key": k, "label": category_label(k), "months": s, "sum": round(sum(s), 4)}
        for k, s in zip(operand_keys, series)
    ]
    # 연간 근거: 합이면 각 재료의 연간합을 더하고, 나누기면 "분자 연간합 ÷ 분모 연간합"으로 낸다
    # (월별 비율 12개를 더하는 건 뜻이 없다 — 연간 인당인건비는 연간 인건비 ÷ 연간 인력이다).
    sums = [o["sum"] for o in operands_basis]
    annual = round(sum(sums), 4) if op == "sum_elementwise" else (round(sums[0] / sums[1], 4) if sums[1] else 0.0)
    basis = {
        "text": OP_SYMBOL[op].join(o["label"] for o in operands_basis),
        "op": op,
        "operands": operands_basis,
        "months": [
            {"month": i + 1, "values": [s[i] for s in series], "result": values[i]}
            for i in range(min(width, 12))
        ],
        "annual": {"values": sums, "result": annual},
    }
    return values, basis

def read_extra_rows(
    db: Session,
    yyyymm: str,
    version: str | None = None,
    lab_pk: str | None = None,
) -> list[ExtraRowOut]:
    stmt = select(CostEntry).where(
        CostEntry.account_code == ACCOUNT_KEY, CostEntry.label.is_not(None), CostEntry.yyyymm == yyyymm,
    )
    if version:
        stmt = stmt.where(CostEntry.version == check_version(version))
    if lab_pk:
        stmt = stmt.where(CostEntry.scope_code == check_lab_pk(db, lab_pk, allow_all=True))

    entries = list(db.scalars(stmt))
    months_by_entry = entry_months_bulk(db, entries)
    # 파생 행은 저장된 월값이 없고 계산식으로 매번 계산한다. 재료값(원시 행 전체)은 그 행이 속한
    # (연구소·버전) 조합당 한 번만 조회해서 돌려 쓴다 — 파생 행 수만큼 다시 조회하지 않도록.
    by_category_cache: dict[tuple[str, str], dict[str, list[float]]] = {}
    out: list[ExtraRowOut] = []
    for entry in entries:
        values, basis = months_by_entry[entry.id], None
        if entry.is_derived and entry.formula:
            cache_key = (entry.scope_code, entry.version)
            if cache_key not in by_category_cache:
                by_category_cache[cache_key] = months_by_category(db, entry.scope_code, entry.version, yyyymm)
            values, basis = eval_row_formula(by_category_cache[cache_key], entry.formula)
        out.append(
            ExtraRowOut(
                lab_pk=entry.scope_code, version=entry.version, row_key=entry.category_code, label=entry.label,
                label_en=entry.label_en or "", yyyymm=yyyymm, anchor_key=entry.anchor_key, v=values,
                is_derived=entry.is_derived, formula=entry.formula, basis=basis,
            )
        )
    return out
