"""업로드 행 이름 맞추기 (Travel Exp ↔ Travel Expense).

판정 규칙은 app/shared/core/row_match.py에 있다. 여기는 "무엇과 비교할지"(후보 목록)와 "사람이
답한 결과"(overseas_row_aliases)를 맡는다. 자동으로 합치지 않는다 — 후보만 돌려주고, 합칠지는
화면이 사람에게 묻는다. 한 번 답한 이름은 다음 업로드부터 resolved로 바로 내려가서 다시 묻지 않는다.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.overseas.cost_groups import CATEGORY_LABELS, CATEGORY_LABELS_EN, GROUP_CALC
from app.shared.core import row_match
from app.shared.models.common import CostEntry
from app.shared.models.generic import RowAlias
from app.accounts.overseas.services.extra_rows import check_version
from app.accounts.overseas.services.labs import ACCOUNT_KEY, ALL_SCOPE


def alias_rows(db: Session, norms: list[str]) -> dict[str, RowAlias]:
    if not norms:
        return {}
    return {
        a.alias_norm: a
        for a in db.scalars(
            select(RowAlias).where(RowAlias.account_key == ACCOUNT_KEY, RowAlias.alias_norm.in_(norms))
        )
    }


def match_candidates(db: Session, yyyymm: int, version: str | None) -> list[dict]:
    """비교 대상: ① 고정 비용항목(집계로 계산되는 그룹 행은 제외 — 거기에 값을 꽂을 수 없다),
    ② 그 달에 이미 쓰이는 추가 행(연구소 전체에서 모은 이름, 어느 연구소가 쓰는지까지)."""
    out: list[dict] = [
        {"kind": "category", "key": key, "label": label, "label_en": CATEGORY_LABELS_EN.get(key, "")}
        for key, label in CATEGORY_LABELS.items()
        if key not in GROUP_CALC and key != "pct"
    ]

    stmt = select(CostEntry).where(
        CostEntry.account_code == ACCOUNT_KEY,
        CostEntry.label.is_not(None),
        CostEntry.yyyymm == yyyymm,
        CostEntry.is_derived.is_(False),
    )
    if version:
        stmt = stmt.where(CostEntry.version == check_version(version))
    by_norm: dict[str, dict] = {}
    for entry in db.scalars(stmt):
        label = (entry.label or "").strip()
        if not label:
            continue
        norm = row_match.normalize(label)
        row = by_norm.setdefault(
            norm, {"kind": "extra_row", "key": norm, "label": label, "label_en": entry.label_en or "", "labs": []}
        )
        if not row["label_en"] and entry.label_en:
            row["label_en"] = entry.label_en
        if entry.scope_code != ALL_SCOPE and entry.scope_code not in row["labs"]:
            row["labs"].append(entry.scope_code)
    return out + list(by_norm.values())
