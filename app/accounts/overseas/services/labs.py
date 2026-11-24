"""해외연구소 기준정보(연구소 목록·시점 이력) 조회.

연구소 목록과 "그 버전 시점의 통화"는 네 화면이 똑같이 읽어야 하는 공용 기준정보다.

ACCOUNT_KEY도 같이 둔다 — cost_entries 같은 공용 테이블의 계정 구분 값이고, 네 엔드포인트가
전부 쓰는 상수다.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.accounts.overseas.models import OverseasLab, OverseasLabRevision
from app.shared.core.enums import CodeGroup
from app.shared.core.periods import ym_month, ym_year
from app.shared.models.account import Account
from app.shared.models.common import CostEntry
from app.shared.models.system import CommonCode

# cost_entries 등 공용 테이블은 account_code로 계정을 구분한다. 이 계정은 해외연구소 고정.
ACCOUNT_KEY = "overseas"

# 집계 화면의 파생 행이 쓰는 "전체 연구소 합산" 범위. cost_entries.scope_code 주석에 이미 적혀 있는
# 관례("없는 계정은 상수 ALL")를 그대로 쓴다 — 연구소 하나가 아니라 전체를 가리키는 행이라는 뜻이다.
# 비율 파생 행(인당인건비 등)은 연구소별로 계산해서 더하면 뜻이 없어지므로, 집계에서는 재료를 전체
# 합산한 뒤 계산해야 해서 이 범위가 필요하다.
ALL_SCOPE = "ALL"


def overseas_account(db: Session) -> Account:
    acct = db.scalar(select(Account).where(Account.key == ACCOUNT_KEY))
    if not acct:
        raise HTTPException(status_code=500, detail="overseas 계정이 시드되지 않았습니다 (app.db.seed 먼저 실행하세요).")
    return acct


def all_labs(db: Session) -> list[OverseasLab]:
    """지금 값(overseas_labs) 그대로. 쓰기(수정·삭제)와 "pk가 실재하나" 검증에만 쓴다 —
    버전별 숫자를 계산할 때는 lab_views(as_of=...)로 그 시점 값을 읽어야 한다."""
    return list(db.scalars(select(OverseasLab)))


# ---------- 연구소 기준정보를 "그 버전 시점"으로 읽기 ----------
# 공통 RULE: 기준정보 변경은 변경한 버전부터 적용하고 지난 버전은 영향받지 않는다.
# overseas_labs에는 "지금 값" 한 줄뿐이라 그대로 읽으면 과거 버전까지 새 값으로 다시 계산된다
# (통화를 USD→ILS로 바꾸는 순간 이미 마감한 달의 환차 분해가 달라진다). 그래서 변경 이력
# (overseas_lab_revisions)을 남기고, 버전이 보는 시점의 값으로 되돌려 읽는다.
# fx가 기본 환산 기준을 바꿀 때 과거 달에 예외를 심어 고정하는 것과 같은 방식이다.

LAB_EPOCH = "0000-01"  # "처음부터" — 기준정보가 처음 바뀌기 전 구간을 가리키는 effective_from


@dataclass(frozen=True)
class LabView:
    """as_of 시점으로 읽은 연구소 기준정보. 필드 이름을 OverseasLab과 맞춰 놔서 읽기만 하는
    호출부는 둘 중 아무거나 받아도 된다(쓰기는 ORM 객체인 labs 쪽을 써야 한다)."""

    pk: str
    lab_code: str
    part_code: str | None
    name: str
    country: str
    sort_order: int
    currency: str
    is_active: bool


def lab_views(db: Session, as_of: str | None = None) -> list[LabView]:
    """as_of("YYYY-MM") 시점의 기준정보. as_of가 없으면 지금 값 그대로 본다.

    그 시점 값 = effective_from이 as_of 이하인 이력 중 가장 최근 행. 한 번도 안 바뀐 연구소는
    이력이 없어서 overseas_labs를 그대로 쓴다(바뀐 적이 없으니 시점과 무관하게 같은 값이다)."""
    labs = all_labs(db)
    part_count: dict[str, int] = {}
    for lab in labs:
        part_count[lab.lab_code] = part_count.get(lab.lab_code, 0) + 1

    at: dict[str, OverseasLabRevision] = {}
    if as_of is not None:
        for rev in db.scalars(
            select(OverseasLabRevision)
            .where(OverseasLabRevision.effective_from <= as_of)
            .order_by(OverseasLabRevision.lab_code, OverseasLabRevision.effective_from)
        ):
            at[rev.lab_code] = rev  # 정렬돼 있으므로 마지막으로 덮어쓴 것이 가장 최근 이력

    out: list[LabView] = []
    for lab in labs:
        rev = at.get(lab.lab_code)
        out.append(
            LabView(
                pk=lab.pk,
                lab_code=lab.lab_code,
                part_code=lab.part_code,
                # SARC처럼 파트가 여러 개면 이름이 파트마다 달라서 이력의 단일 이름으로 덮어쓰지
                # 않는다 — save_labs/upsert_lab이 저장할 때 쓰는 규칙과 같다.
                name=rev.name if rev and part_count[lab.lab_code] == 1 else lab.name,
                country=lab.country,
                sort_order=rev.sort_order if rev else lab.sort_order,
                currency=rev.currency if rev else lab.currency,
                is_active=rev.is_active if rev else lab.is_active,
            )
        )
    return out


def is_summable(lab: LabView) -> bool:
    """합산에 넣는 연구소인가. 합계 시트(part_code="TOTAL")와 원화 기타(_ETC)는 다른 행을 더해
    만든 값이라 뺀다 — 넣으면 SARC가 CPU+GPU+TOTAL로 두 번 세어져 금액이 정확히 두 배가 된다
    (tests/overseas/test_total_slot.py). 집계표(versions.active_labs)와 파생 행의 재료
    (extra_rows.months_by_category)가 같은 판정을 써야 두 숫자가 어긋나지 않는다."""
    return bool(lab.is_active) and lab.pk != "_ETC" and (lab.part_code or "").upper() != "TOTAL"


def summable_pks(db: Session, as_of: str | None = None) -> list[str]:
    """그 시점에 합산에 넣을 연구소 pk 목록(is_summable 기준)."""
    return sorted(lab.pk for lab in lab_views(db, as_of) if is_summable(lab))


# ---------- 기준정보 변경(연구소 편집 화면·단건 API)이 쓰는 헬퍼 ----------
def current_basis_ym(db: Session) -> str:
    """지금 편성 중인 기준연월 "YYYY-MM" — 기준정보를 바꿀 때 기본 적용 시점(= "이 버전부터")."""
    latest = db.scalar(
        select(func.max(CostEntry.yyyymm)).where(CostEntry.account_code == ACCOUNT_KEY, CostEntry.version == "forecast")
    )
    if latest:
        return f"{ym_year(latest):04d}-{ym_month(latest):02d}"
    return datetime.now(timezone.utc).strftime("%Y-%m")


def check_effective_from(value: str | None, db: Session) -> str:
    """기준정보 변경이 적용되기 시작하는 연월. 안 주면 지금 편성 중인 기준월(= "이 버전부터")."""
    if not value:
        return current_basis_ym(db)
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise HTTPException(400, f'effective_from은 "YYYY-MM" 형식이어야 한다: {value!r}')
    return value


def record_lab_revision(db: Session, lab_code: str, before: dict, after: dict, effective_from: str) -> None:
    """기준정보가 실제로 바뀔 때만 이력 두 줄을 남긴다.

    1) 바뀌기 전 값을 '0000-01'(처음부터)로 한 번 박아서 지난 버전 전부를 옛 값으로 고정하고,
    2) 새 값을 effective_from으로 넣는다(같은 달에 또 고치면 그 줄을 덮어쓴다).
    1)은 이미 있으면 건드리지 않는다 — 처음 고정해 둔 "원래 값"이 두 번째 변경 때 덮이면
    과거가 다시 흔들린다."""
    if before == after:
        return
    db.execute(
        pg_insert(OverseasLabRevision)
        .values(lab_code=lab_code, effective_from=LAB_EPOCH, **before)
        .on_conflict_do_nothing(index_elements=["lab_code", "effective_from"])
    )
    stmt = pg_insert(OverseasLabRevision).values(lab_code=lab_code, effective_from=effective_from, **after)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["lab_code", "effective_from"],
            set_={k: stmt.excluded[k] for k in after},
        )
    )


def lab_attrs(labs: list[OverseasLab]) -> dict:
    """이력에 남길 값 묶음. 파트가 여러 개면 이름은 파트마다 다르므로 대표(첫 파트) 이름을 쓴다 —
    되돌려 읽을 때도 파트가 여럿이면 이름은 안 덮어쓰므로 비교·복원에 영향이 없다."""
    head = labs[0]
    return {
        "name": head.name,
        "currency": head.currency,
        "is_active": head.is_active,
        "sort_order": head.sort_order,
    }


def check_category(db: Session, category_key: str) -> str:
    """category_code가 고정 항목(common_codes group=OVERSEAS_CATEGORY) 이거나, 이미 등록된 커스텀
    행(cost_entries.label이 채워진 행)이어야 한다 — 둘 다 아니면 오타·존재하지 않는 코드로 보고 막는다.
    고정 항목 중에서도 "그룹"(A·hr·labor·asset·direct·gen·mgr·itx처럼 common_codes에서 다른 코드의
    parent_code로 가리켜지는 집계 전용 행)은 직접 금액을 못 넣는다 — 하위 항목들의 합계로만 존재해야
    하는 행에 실제 비용을 꽂으면 집계가 깨진다."""
    is_fixed = db.scalar(
        select(CommonCode.id).where(CommonCode.group_code == CodeGroup.OVERSEAS_CATEGORY.value, CommonCode.code == category_key)
    )
    is_registered_custom = db.scalar(
        select(CostEntry.id).where(
            CostEntry.account_code == ACCOUNT_KEY, CostEntry.category_code == category_key, CostEntry.label.is_not(None)
        )
    )
    if not is_fixed and not is_registered_custom:
        raise HTTPException(status_code=400, detail=f"알 수 없는 비용항목 코드: {category_key}")
    is_group = db.scalar(
        select(CommonCode.id).where(CommonCode.group_code == CodeGroup.OVERSEAS_CATEGORY.value, CommonCode.parent_code == category_key)
    )
    if is_group:
        raise HTTPException(status_code=400, detail=f"'{category_key}'는 하위 항목의 합계로만 쓰이는 그룹이라 직접 금액을 넣을 수 없습니다.")
    return category_key
