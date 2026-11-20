"""rename cost_entry_month to cost_entry_detail

Revision ID: f80ba7f4e039
Revises: 2deed0d55305
Create Date: 2026-11-10 23:47:30.777960

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f80ba7f4e039'
down_revision: Union[str, Sequence[str], None] = '2deed0d55305'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # management_plan_entry_detail과 이름을 맞춘다 — "month"는 period_type에 QUARTER/YEAR/CUSTOM도
    # 담기는 지금 모양을 더 이상 정확히 설명하지 못하는 옛 이름이었다.
    op.rename_table('cost_entry_month', 'cost_entry_detail')
    op.execute("ALTER TABLE cost_entry_detail RENAME CONSTRAINT uq_cost_entry_month_cost_entry_year_period_col TO uq_cost_entry_detail_cost_entry_year_period_col")
    op.execute("ALTER TABLE cost_entry_detail RENAME CONSTRAINT ck_cost_entry_month_period_type TO ck_cost_entry_detail_period_type")
    op.execute("ALTER TABLE cost_entry_detail RENAME CONSTRAINT fk_cost_entry_month_cost_entry_id TO fk_cost_entry_detail_cost_entry_id")
    op.execute("ALTER TABLE cost_entry_detail RENAME CONSTRAINT fk_cost_entry_month_updated_by_id_users TO fk_cost_entry_detail_updated_by_id_users")
    op.execute("ALTER TABLE cost_entry_detail RENAME CONSTRAINT cost_entry_month_pkey TO cost_entry_detail_pkey")
    op.execute("ALTER SEQUENCE cost_entry_month_id_seq RENAME TO cost_entry_detail_id_seq")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER SEQUENCE cost_entry_detail_id_seq RENAME TO cost_entry_month_id_seq")
    op.execute("ALTER TABLE cost_entry_detail RENAME CONSTRAINT cost_entry_detail_pkey TO cost_entry_month_pkey")
    op.execute("ALTER TABLE cost_entry_detail RENAME CONSTRAINT fk_cost_entry_detail_updated_by_id_users TO fk_cost_entry_month_updated_by_id_users")
    op.execute("ALTER TABLE cost_entry_detail RENAME CONSTRAINT fk_cost_entry_detail_cost_entry_id TO fk_cost_entry_month_cost_entry_id")
    op.execute("ALTER TABLE cost_entry_detail RENAME CONSTRAINT ck_cost_entry_detail_period_type TO ck_cost_entry_month_period_type")
    op.execute("ALTER TABLE cost_entry_detail RENAME CONSTRAINT uq_cost_entry_detail_cost_entry_year_period_col TO uq_cost_entry_month_cost_entry_year_period_col")
    op.rename_table('cost_entry_detail', 'cost_entry_month')
