"""overseas cost entries anchor_key (추가 행을 특정 계정 아래에 끼워 넣기)

Revision ID: 82b996f20cec
Revises: 6816017d928a
Create Date: 2026-11-09 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '82b996f20cec'
down_revision: Union[str, Sequence[str], None] = '6816017d928a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 추가 행(label이 있는 행)이 표의 어느 고정 계정(OV_EL의 k) 바로 뒤에 붙을지 지정한다.
    # NULL이면 예전처럼 표 맨 끝에 붙는다 — 기존 행은 전부 NULL로 그대로 유지된다.
    op.add_column(
        'overseas_cost_entries',
        sa.Column('anchor_key', sa.String(length=40), nullable=True,
                   comment='추가 행을 이 계정(core.js OV_EL의 k) 바로 뒤에 끼워 넣는다. NULL이면 맨 끝.'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('overseas_cost_entries', 'anchor_key')
