"""해외연구소 기준정보(연구소 목록·시점 이력) 조회.

왜 여기로 옮겼나: 원래 overseas.py의 `_labs` · `_lab_views` · `LabView` · `_overseas_account`였고
증감분석·집계 엔드포인트가 그 밑줄 이름을 직접 import해서 썼다(PLAN.md P1-1). 연구소 목록과
"그 버전 시점의 통화"는 네 화면이 똑같이 읽어야 하는 공용 기준정보라 services로 올렸다.

ACCOUNT_KEY도 같이 둔다 — cost_entries 같은 공용 테이블의 계정 구분 값이고, 네 엔드포인트가
전부 쓰는 상수다.
"""

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.overseas.models import OverseasLab, OverseasLabRevision
from app.shared.models.account import Account

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
