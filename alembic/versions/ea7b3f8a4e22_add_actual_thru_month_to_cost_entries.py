"""add actual_thru_month to cost_entries

Revision ID: ea7b3f8a4e22
Revises: c4bf654f2994
Create Date: 2026-10-28 20:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ea7b3f8a4e22'
down_revision: Union[str, Sequence[str], None] = 'c4bf654f2994'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 'cur'(당월 속보) 버전의 기존 데이터는 지금까지 "현재 기준월까지 반영된 속보" 하나뿐이었으니,
# 그 의미를 그대로 보존하려면 기존 cur 행을 전부 현재 기준월의 "속보"로 백필해야 한다.
CUR_BASIS_MONTH = 10  # 2026-10-28 기준


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'cost_entries',
        sa.Column(
            'actual_thru_month', sa.SmallInteger(), nullable=False, server_default='0',
            comment="실적이 반영된 마지막 달(1~12). cur 버전에서 같은 카테고리가 '실적'(기준월-1)과 "
            "'속보'(기준월)로 동시에 존재할 때 구분용. 그 외 버전은 0(해당 없음)",
        ),
    )
    op.execute(f"UPDATE cost_entries SET actual_thru_month = {CUR_BASIS_MONTH} WHERE version = 'cur'")
    op.drop_constraint('uq_cost_entries_account_scope_version_category', 'cost_entries', type_='unique')
    op.create_unique_constraint(
        'uq_cost_entries_account_scope_version_category_thru', 'cost_entries',
        ['account_key', 'scope_key', 'version', 'category_key', 'actual_thru_month'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('uq_cost_entries_account_scope_version_category_thru', 'cost_entries', type_='unique')
    op.create_unique_constraint(
        'uq_cost_entries_account_scope_version_category', 'cost_entries',
        ['account_key', 'scope_key', 'version', 'category_key'],
    )
    op.drop_column('cost_entries', 'actual_thru_month')
