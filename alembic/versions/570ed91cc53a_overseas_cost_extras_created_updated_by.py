"""overseas cost extras created_by_id/updated_by_id (user tracking, unused until auth exists)

Revision ID: 570ed91cc53a
Revises: ba55cd5bf5b4
Create Date: 2026-11-08 03:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '570ed91cc53a'
down_revision: Union[str, Sequence[str], None] = 'ba55cd5bf5b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'overseas_cost_extras',
        sa.Column('created_by_id', sa.Integer(), nullable=True, comment='최초 입력한 사용자 (users.id). 로그인 인증 붙기 전까지는 항상 NULL'),
    )
    op.add_column(
        'overseas_cost_extras',
        sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id). 로그인 인증 붙기 전까지는 항상 NULL'),
    )
    op.create_foreign_key(
        'fk_overseas_cost_extras_created_by_id_users', 'overseas_cost_extras', 'users', ['created_by_id'], ['id']
    )
    op.create_foreign_key(
        'fk_overseas_cost_extras_updated_by_id_users', 'overseas_cost_extras', 'users', ['updated_by_id'], ['id']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_overseas_cost_extras_updated_by_id_users', 'overseas_cost_extras', type_='foreignkey')
    op.drop_constraint('fk_overseas_cost_extras_created_by_id_users', 'overseas_cost_extras', type_='foreignkey')
    op.drop_column('overseas_cost_extras', 'updated_by_id')
    op.drop_column('overseas_cost_extras', 'created_by_id')
