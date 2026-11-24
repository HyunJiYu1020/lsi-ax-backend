"""증감사유·외주용역 유니크 키에서 version을 뺀다 — 키는 (연구소, 기준월, …)이다

왜: yyyymm이 생기면서 version(cur|prev|prior|plan)은 **자료를 식별하는 값이 아니라 "올릴 때 어느
자리였나"를 적어 두는 값**이 됐다. 읽는 쪽은 이미 달로 자리를 정한다 — 9월 자료는 9월을 볼 때
cur이고 10월을 볼 때 prev다. 그런데 키에 version이 남아 있으면 같은 달 자료가 cur 한 벌, prev 한 벌로
**두 벌이 된다**: 10월 화면에서 저장하면 9월 자료가 prev로 한 벌 더 들어가고, 다음에 읽을 때 그 달
자료가 두 번 나와서 저장이 유니크 키 충돌로 깨진다(실제로 test_state_roundtrip이 그렇게 깨졌다).

Revision ID: f5a2c3d7e9b1
Revises: e4f1a2b6c8d3
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f5a2c3d7e9b1"
down_revision: Union[str, None] = "e4f1a2b6c8d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    ("overseas_reason_rows", "uq_overseas_reason_rows_key",
     ["lab_code", "version", "yyyymm", "category_key", "block"],
     ["lab_code", "yyyymm", "category_key", "block"]),
    ("overseas_vendor_rows", "uq_overseas_vendor_rows_key",
     ["lab_code", "version", "yyyymm", "row_no"],
     ["lab_code", "yyyymm", "row_no"]),
)


def upgrade() -> None:
    # 키를 좁히기 전에, 같은 (연구소, 기준월)에 version만 다른 중복이 있으면 하나만 남긴다.
    op.execute(
        """
        DELETE FROM overseas_vendor_row_details d
        USING overseas_vendor_rows a, overseas_vendor_rows b
        WHERE d.vendor_row_id = a.id
          AND a.lab_code = b.lab_code AND a.yyyymm = b.yyyymm AND a.row_no = b.row_no AND a.id > b.id
        """
    )
    op.execute(
        """
        DELETE FROM overseas_vendor_rows a USING overseas_vendor_rows b
        WHERE a.lab_code = b.lab_code AND a.yyyymm = b.yyyymm AND a.row_no = b.row_no AND a.id > b.id
        """
    )
    op.execute(
        """
        DELETE FROM overseas_reason_rows a USING overseas_reason_rows b
        WHERE a.lab_code = b.lab_code AND a.yyyymm = b.yyyymm AND a.category_key = b.category_key
          AND a.block = b.block AND a.id > b.id
        """
    )
    for table, uq, _old_cols, new_cols in TABLES:
        op.drop_constraint(uq, table, type_="unique")
        op.create_unique_constraint(uq, table, new_cols)


def downgrade() -> None:
    for table, uq, old_cols, _new_cols in TABLES:
        op.drop_constraint(uq, table, type_="unique")
        op.create_unique_constraint(uq, table, old_cols)
