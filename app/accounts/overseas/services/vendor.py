"""외주용역 상세 저장 — 연구소×버전 단위 전체 교체.

왜 서비스로 뺐나 (PLAN.md Phase 5): 저장이 `PUT /overseas/state` 하나에 뭉쳐 있어서, 외주용역
한 시트를 올려도 화면이 가진 상태 전부가 전송된다. 도메인별 전용 저장 API로 떼어내는 중이고,
**두 경로가 같은 함수를 부르게** 둔다 — 쓰기 규칙이 두 벌이 되면 PLAN P1-3에서 겪은
"같은 데이터, 두 가지 쓰기 규칙"을 다시 만든다.

왜 upsert가 아니라 전체 교체인가: 업로드마다 행 수가 달라진다. upsert만 하면 지워진 행이 남아서
"연구소가 뺀 항목"이 화면에 계속 보인다. 보내지 않은 연구소·버전은 건드리지 않는다 — 한 연구소
시트를 올렸을 때 다른 연구소 자료가 사라지면 안 되기 때문이다.
"""

from fastapi import HTTPException
from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from app.accounts.overseas.models import OverseasVendorRow, OverseasVendorRowDetail
from app.accounts.overseas.schemas import VendorRow


def replace_vendor_rows(
    db: Session, basis_year: int, rows: list[VendorRow], known_lab_codes: set[str]
) -> int:
    """rows에 등장하는 (연구소, 버전) 조합을 통째로 바꿔 넣는다. 바뀐 조합 수를 돌려준다.

    commit은 하지 않는다 — 호출부가 다른 쓰기와 한 트랜잭션으로 묶을 수 있어야 한다
    (`PUT /overseas/state`는 commit 1회로 원자성을 보장한다, PLAN P1-2).
    """
    unknown = {r.lab_code for r in rows if r.lab_code not in known_lab_codes}
    if unknown:
        raise HTTPException(status_code=400, detail=f"알 수 없는 연구소 코드(외주용역): {sorted(unknown)}")

    units = {(r.lab_code, r.version) for r in rows}
    for lab_code, version in units:
        db.execute(
            delete(OverseasVendorRow).where(
                OverseasVendorRow.lab_code == lab_code, OverseasVendorRow.version == version
            )
        )
    if not rows:
        return 0

    new_ids = (
        db.execute(
            insert(OverseasVendorRow).returning(OverseasVendorRow.id, sort_by_parameter_order=True),
            [
                {
                    "lab_code": r.lab_code, "version": r.version, "row_no": r.row_no, "category": r.category,
                    "project": r.project, "company": r.company, "description": r.description,
                    "source_file": r.source_file,
                }
                for r in rows
            ],
        )
        .scalars()
        .all()
    )
    details = [
        {"vendor_row_id": new_id, "year": basis_year, "month": month, "amount": float(amount)}
        for new_id, r in zip(new_ids, rows)
        for month, amount in enumerate(r.m[:12], start=1)
    ]
    if details:
        db.execute(insert(OverseasVendorRowDetail), details)
    return len(units)
