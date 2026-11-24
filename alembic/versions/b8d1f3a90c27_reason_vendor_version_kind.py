"""증감사유·외주용역의 version을 forecast|actual로 (cost_entries와 같은 값)

Revision ID: b8d1f3a90c27
Revises: a7c4e0f21d58
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b8d1f3a90c27"
down_revision: Union[str, None] = "a7c4e0f21d58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("overseas_reason_rows", "overseas_vendor_rows")


def upgrade() -> None:
    for t in TABLES:
        op.execute(
            f"""UPDATE {t} SET version = CASE WHEN version IN ('prev','prior') THEN 'actual'
                ELSE 'forecast' END"""
        )
        op.create_check_constraint(
            f"ck_{t}_version", t, "version IN ('forecast', 'actual')"
        )
        op.alter_column(t, "version", comment="forecast(속보) | actual(실적)")


def downgrade() -> None:
    for t in TABLES:
        op.drop_constraint(f"ck_{t}_version", t, type_="check")
        op.execute(f"UPDATE {t} SET version = 'cur' WHERE version = 'forecast'")
        op.alter_column(t, "version", comment="올릴 때의 자리(cur|prev|prior|plan) — 식별 키가 아니라 기록용")
