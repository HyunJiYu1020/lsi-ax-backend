"""overseas cost extras (merge memo_cols + memos for overseas into one line-scoped table)

Revision ID: 356b1a6f6692
Revises: df0623cb26bd
Create Date: 2026-11-08 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '356b1a6f6692'
down_revision: Union[str, Sequence[str], None] = 'df0623cb26bd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'overseas_cost_extras',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('line_id', sa.Integer(), nullable=False, comment='overseas_cost_lines.id (헤더 삭제 시 같이 삭제)'),
        sa.Column('col_key', sa.String(length=40), nullable=False, comment='클라이언트가 생성한 열 id ("c...")'),
        sa.Column('label', sa.String(length=100), nullable=False, comment='열 이름'),
        sa.Column('value', sa.Numeric(precision=14, scale=2), nullable=False, comment='값'),
        sa.ForeignKeyConstraint(['line_id'], ['overseas_cost_lines.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('line_id', 'col_key'),
        comment='overseas_cost_lines 한 행에 추가된 열(존재·이름·값). 1~12월과 별개 축, 집계에는 안 들어감.',
    )
    # overseas가 memo_cols/memos(공용 테이블)에 쌓아둔 데이터는 없음(label만 있던 빈 열) — 그대로 정리만 한다.
    op.execute("delete from memos where account_key = 'overseas'")
    op.execute("delete from memo_cols where account_key = 'overseas'")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('overseas_cost_extras')
