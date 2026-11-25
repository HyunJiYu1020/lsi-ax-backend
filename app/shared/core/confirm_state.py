"""계정 확정 상태를 "작성 중"으로 되돌리는 공용 처리.

왜 필요한가: 확정한 뒤에 값이 바뀌면 확정 표시가 거짓이 된다. "9월 속보 확정 완료"라고 적혀 있는데
그 뒤 숫자가 바뀌어 있으면, 보고된 값과 화면 값이 다른데 아무도 모른다. 화면은 이걸 이미 하고
있다(core.js의 ovUnconfirm — 값을 고치면 자동으로 작성 중으로 돌린다). 서버에서 값을 바꾸는 경로가
생기면(행 합치기·재계산) 같은 일을 서버도 해야 한다.

지우는 것까지 화면과 같은 규칙이다 — 확정 일시와 note를 같이 비운다(공통 RULE: 완료 후 수정 시
작성중 + 일자 삭제). 되돌렸는데 "미업로드 2개 포함 확정"이 남아 있으면 그게 지금 상태인 줄 읽힌다.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.core.periods import ym_month, ym_year
from app.shared.models.version import Version, VersionAccountStatus

# cost_entries의 version 값 → versions.kind. 같은 값이지만 두 테이블이 쓰는 이름이 달라서 한 곳에
# 적어 둔다(cost_entries는 version, versions는 kind).
VERSION_TO_KIND = {"forecast": "forecast", "actual": "actual"}


def unconfirm_account(db: Session, yyyymm: int, version: str, account_key: str) -> bool:
    """그 버전·그 계정의 확정을 작성 중으로 되돌린다. 되돌렸으면 True, 되돌릴 게 없었으면 False.

    버전 행이 아예 없거나 그 계정이 확정 상태가 아니면 아무것도 하지 않는다 — 없는 버전을 만들지
    않는다(확정하지 않은 것을 "확정 취소"로 남기면 이력이 거짓이 된다). commit은 호출부가 한다:
    값 변경과 상태 되돌리기가 한 트랜잭션에 묶여야, 값은 바뀌었는데 확정은 남아 있는 중간 상태가
    생기지 않는다.
    """
    kind = VERSION_TO_KIND.get(version)
    if not kind or not 200001 <= yyyymm <= 299912:
        return False
    year, month = ym_year(yyyymm), ym_month(yyyymm)
    v = db.scalar(
        select(Version).where(Version.year == year, Version.month == month, Version.kind == kind)
    )
    if v is None:
        return False
    row = db.scalar(
        select(VersionAccountStatus).where(
            VersionAccountStatus.version_id == v.id, VersionAccountStatus.account_code == account_key
        )
    )
    if row is None or row.status != "done":
        return False
    row.status = "drafting"
    row.confirmed_at = None
    row.note = ""
    return True
