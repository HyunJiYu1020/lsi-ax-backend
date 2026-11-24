"""증감사유·외주용역에 기준월(yyyymm)을 붙인다

왜: 두 테이블은 version(cur|prev|prior|plan)이라는 **역할값**만 들고 있어서 "어느 달 회신인지"를
담을 자리가 없었다. 그래서 읽는 쪽이 "DB에 있는 가장 최근 속보월 것"이라고 짐작해서 내려줬고
(state.monthless_month), 9월에 올린 증감사유가 10월 자료로 잡혀 9월 업로드 현황이 '미'로 남았다.
비용(cost_entries.yyyymm)과 같은 기준을 두면 짐작할 일이 없어진다.

유니크 키에도 넣는다 — 안 넣으면 9월 회신과 10월 회신이 같은 키라 뒤에 올린 쪽이 앞을 덮어쓴다.

Revision ID: e4f1a2b6c8d3
Revises: c3e18a7d5b94
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4f1a2b6c8d3"
down_revision: Union[str, None] = "c3e18a7d5b94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    ("overseas_reason_rows", "uq_overseas_reason_rows_key",
     ["lab_code", "version", "category_key", "block"],
     ["lab_code", "version", "yyyymm", "category_key", "block"]),
    ("overseas_vendor_rows", "uq_overseas_vendor_rows_key",
     ["lab_code", "version", "row_no"],
     ["lab_code", "version", "yyyymm", "row_no"]),
)


def upgrade() -> None:
    for table, uq, _old_cols, new_cols in TABLES:
        op.add_column(
            table,
            sa.Column(
                "yyyymm", sa.String(length=6), nullable=False, server_default="",
                comment='이 회신이 속한 편성 기준연월 "YYYYMM"(cost_entries.yyyymm과 같은 기준)',
            ),
        )
        op.drop_constraint(uq, table, type_="unique")
        op.create_unique_constraint(uq, table, new_cols)
        op.create_check_constraint(f"ck_{table}_yyyymm_format", table, r"yyyymm ~ '^\d{6}$'")
    # 기존 행이 있으면 "가장 최근 속보월"이 그 행들이 실제로 속한 달이다 — 지금까지 읽는 쪽이 그렇게
    # 해석해 왔으므로 그 해석을 그대로 값으로 굳힌다. 행이 없으면 아무것도 하지 않는다.
    op.execute(
        """
        UPDATE overseas_reason_rows SET yyyymm = COALESCE(
            (SELECT max(yyyymm) FROM cost_entries WHERE account_code='overseas' AND version='forecast'), '')
        WHERE yyyymm = ''
        """
    )
    op.execute(
        """
        UPDATE overseas_vendor_rows SET yyyymm = COALESCE(
            (SELECT max(yyyymm) FROM cost_entries WHERE account_code='overseas' AND version='forecast'), '')
        WHERE yyyymm = ''
        """
    )


def downgrade() -> None:
    for table, uq, old_cols, _new_cols in TABLES:
        op.drop_constraint(f"ck_{table}_yyyymm_format", table, type_="check")
        op.drop_constraint(uq, table, type_="unique")
        op.drop_column(table, "yyyymm")
        op.create_unique_constraint(uq, table, old_cols)
