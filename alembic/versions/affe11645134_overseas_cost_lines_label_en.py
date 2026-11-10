"""overseas cost lines label_en (Cost Element 영문명 for custom rows)

Revision ID: affe11645134
Revises: 356b1a6f6692
Create Date: 2026-11-08 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'affe11645134'
down_revision: Union[str, Sequence[str], None] = '356b1a6f6692'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'overseas_cost_lines',
        sa.Column(
            'label_en', sa.String(length=100), nullable=True,
            comment='사용자가 직접 추가한 행의 영문 이름(Cost Element). 고정 계정은 NULL',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('overseas_cost_lines', 'label_en')
