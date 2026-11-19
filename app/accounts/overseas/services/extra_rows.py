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

from app.accounts.overseas.cost_groups import CATEGORY_LABELS, CATEGORY_LABELS_EN, GROUP_CALC, resolve_leaves, category_label
from app.shared.core import row_match
from app.shared.core.values import n
from app.accounts.overseas.models import OverseasLab
from app.shared.models.common import CostEntry
from app.accounts.overseas.schemas import ExtraRowIn, ExtraRowOut
from app.accounts.overseas.services.entries import entry_months_bulk
from app.accounts.overseas.services.labs import ACCOUNT_KEY, ALL_SCOPE

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


# ---------- 파생 행·열의 계산식 검증과 평가 ----------
# 행 수식은 계정을 "세로로" 묶는다 — operands가 계정 키 목록이고 월별로(같은 달끼리) 계산한다.
#   {"op": "div_elementwise", "operands": ["labor", "hc"]}  → 인건비 ÷ 인력 (= 인당인건비)
# 열 수식은 월을 "가로로" 묶는다 — months가 월 번호 목록이고 각 행마다 그 달들을 계산한다.
#   {"op": "sum_months", "months": [7, 8, 9]}               → 3분기 합계
ROW_FORMULA_OPS = ("sum_elementwise", "div_elementwise")
COL_FORMULA_OPS = ("sum_months", "avg_months")

# 원시 행/열은 취합에서만, 파생 행/열은 집계에서만 지울 수 있다 — 추가한 화면에서만 치우게 해서
# 다른 단계 담당자가 남의 행을 모르고 지우는 걸 막는다. 단계 이름은 화면 상단 진행 표시의 짧은
# 이름(취합·집계·증감분석)과 같다. 단계는 브라우저 로컬 상태라 서버가 스스로 알 수 없고 호출부가
# 보내줘야 하므로, 이건 권한(authorization)이 아니라 실수 방지 장치다 — chat.py의 파생 행 차단과
# 같은 성격이다.
STEP_FOR_RAW, STEP_FOR_DERIVED = "취합", "집계"


def check_delete_step(step: str | None, is_derived: bool, what: str, confirmed: bool = False) -> None:
    """다른 단계에서 들어온 행·열을 지우려 하면 **한 번 묻는다**(막지 않는다).

    전에는 400으로 거부했다. 그런데 지우려는 사람은 그 행이 어느 단계에서 들어왔는지 모르고,
    "지워지지 않는다"만 보였다 — 화면을 옮겨 다시 눌러야 한다는 걸 알아낼 방법이 없었다.
    그래서 409로 사유를 돌려주고, 화면이 그대로 물어본 뒤 confirmed=true로 다시 부른다.
    """
    required = STEP_FOR_DERIVED if is_derived else STEP_FOR_RAW
    if step == required or confirmed:
        return
    kind = "계산으로 만든 파생" if is_derived else "업로드 또는 직접 입력으로 들어온"
    raise HTTPException(
        status_code=409,
        detail=(
            f"{kind} {what}입니다('{required}' 단계에서 들어왔습니다). "
            f"지우면 되돌릴 수 없습니다. 정말 지우시겠습니까?"
        ),
    )


def check_row_formula(formula: dict | None) -> dict:
    """파생 행의 계산식을 검증한다. 1차 범위는 '같은 연구소·버전·연월 안의 계정/행 참조'로 한정한다 —
    다른 연구소나 다른 연월을 섞는 수식은 참조 표현이 훨씬 복잡해져 아직 받지 않는다."""
    if not isinstance(formula, dict):
        raise HTTPException(status_code=400, detail="파생 행에는 formula(계산식)가 필요합니다.")
    op = formula.get("op")
    if op not in ROW_FORMULA_OPS:
        raise HTTPException(status_code=400, detail=f"알 수 없는 행 계산식 op: {op!r} ({' | '.join(ROW_FORMULA_OPS)})")
    operands = formula.get("operands")
    if not isinstance(operands, list) or not all(isinstance(k, str) and k for k in operands):
        raise HTTPException(status_code=400, detail="formula.operands는 계정 키 문자열 목록이어야 합니다.")
    if op == "div_elementwise" and len(operands) != 2:
        raise HTTPException(status_code=400, detail="div_elementwise는 operands가 정확히 2개([분자, 분모])여야 합니다.")
    if op == "sum_elementwise" and len(operands) < 2:
        raise HTTPException(status_code=400, detail="sum_elementwise는 operands가 2개 이상이어야 합니다.")
    return {"op": op, "operands": operands}


def check_col_formula(formula: dict | None) -> dict:
    if not isinstance(formula, dict):
        raise HTTPException(status_code=400, detail="파생 열에는 formula(계산식)가 필요합니다.")
    op = formula.get("op")
    if op not in COL_FORMULA_OPS:
        raise HTTPException(status_code=400, detail=f"알 수 없는 열 계산식 op: {op!r} ({' | '.join(COL_FORMULA_OPS)})")
    months = formula.get("months")
    if not isinstance(months, list) or not months or not all(isinstance(m, int) and 1 <= m <= 12 for m in months):
        raise HTTPException(status_code=400, detail="formula.months는 1~12 사이 월 번호 목록이어야 합니다.")
    return {"op": op, "months": months}


def eval_col_formula(formula: dict, row_months: list[float]) -> tuple[float, dict]:
    """파생 열의 계산식을 한 행의 월별 값에 적용해 (칸 값, 근거)를 돌려준다."""
    op, months = formula["op"], formula["months"]
    picked = [(m, round(row_months[m - 1], 4) if m - 1 < len(row_months) else 0.0) for m in months]
    total = round(sum(v for _, v in picked), 4)
    value = total if op == "sum_months" else round(total / len(picked), 4)
    return value, {
        "text": f"{'+'.join(f'{m}월' for m in months)}{'' if op == 'sum_months' else ' 평균'}",
        "op": op,
        "months": [{"month": m, "value": v} for m, v in picked],
        "result": value,
    }


# 챗봇으로 추가 행을 만들 때, 그 이름이 이미 있는 행과 겹치면 막는다 (TC-016).
#
# 왜 필요한가: "인건비 행 추가해줘"는 보통 "인건비 **아래에** 하위 항목을 넣어 달라"는 뜻이지
# "인건비라는 행을 또 만들어 달라"가 아니다. 그런데 row_key만 다르면 서버는 그대로 만들어 주고,
# 집계표에는 '인건비'가 두 줄로 나온다. 한 줄만 보고 "이 연구소 인건비는 이만큼"으로 읽으면 틀린다
# (row_match.py가 애초에 해결하려던 문제와 같은 모양이다 — 같은 이름이 두 줄로 쪼개지는 것).
#
# 화면 경로(source="screen")에는 걸지 않는다 — ExtraRowIn.source 주석에 이유를 적어 뒀다.
def block_duplicate_label(db: Session, body: ExtraRowIn, existing: CostEntry | None) -> None:
    norm = row_match.normalize(body.label)
    if not norm:
        return
    # ① 고정 계정과 같은 이름인가(한글명·영문명 양쪽으로 본다 — 파일은 영문으로 온다).
    for key, label in CATEGORY_LABELS.items():
        if norm in (row_match.normalize(label), row_match.normalize(CATEGORY_LABELS_EN.get(key, ""))):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"도구 실행 실패: '{body.label}'은 이미 계정 목록에 있는 행이다"
                    f"({label}). 같은 이름의 행을 또 만들면 집계표에 두 줄로 나와서, 한 줄만 보고 "
                    "읽으면 금액이 틀린다. 재시도하지 말고 사용자에게 무엇을 원하는지 먼저 물어라 — "
                    f"'{label}' 아래에 하위 항목을 넣으려는 것이면 그 하위 항목의 이름을 받아서 "
                    f"anchor_key='{key}'로 추가하고, 그 계정 금액을 고치려는 것이면 "
                    "upsert_cost_entry를 쓴다."
                ),
            )
    # ② 그 연구소·버전·연월에 이미 쓰이는 추가 행과 같은 이름인가(자기 자신은 뺀다).
    dup = next(
        (
            e
            for e in db.scalars(
                select(CostEntry).where(
                    CostEntry.account_code == ACCOUNT_KEY,
                    CostEntry.scope_code == body.lab_pk,
                    CostEntry.version == body.version,
                    CostEntry.yyyymm == body.yyyymm,
                    CostEntry.label.is_not(None),
                    CostEntry.category_code != body.row_key,
                )
            )
            if row_match.normalize(e.label or "") == norm
        ),
        None,
    )
    if dup is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"도구 실행 실패: '{body.label}' 행은 이미 있다(row_key '{dup.category_code}'). "
                "같은 이름으로 또 만들면 집계표에 두 줄로 나온다. 재시도하지 말고, 그 행의 금액을 "
                "고치려는 것인지 아니면 다른 이름의 새 행을 원하는지 사용자에게 물어라 — 이미 있는 "
                f"행을 옮기려는 것이면 같은 row_key('{dup.category_code}')로 v 없이 호출하면 된다."
            ),
        )
