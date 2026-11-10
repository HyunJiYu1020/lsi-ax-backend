"""overseas cost extras created_at/updated_at

Revision ID: ba55cd5bf5b4
Revises: affe11645134
Create Date: 2026-11-08 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ba55cd5bf5b4'
down_revision: Union[str, Sequence[str], None] = 'affe11645134'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'overseas_cost_extras',
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='최초 입력 시각'),
    )
    op.add_column(
        'overseas_cost_extras',
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('overseas_cost_extras', 'updated_at')
    op.drop_column('overseas_cost_extras', 'created_at')
