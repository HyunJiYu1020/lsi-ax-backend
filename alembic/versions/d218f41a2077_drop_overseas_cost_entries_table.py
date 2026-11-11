"""drop overseas_cost_entries table

Revision ID: d218f41a2077
Revises: df4276c24e85
Create Date: 2026-11-10 21:36:31.415097

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd218f41a2077'
down_revision: Union[str, Sequence[str], None] = 'df4276c24e85'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_table('overseas_cost_entries')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        'overseas_cost_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scope_key', sa.String(length=40), nullable=False, comment='연구소 pk (overseas_labs.pk)'),
        sa.Column('version', sa.String(length=10), nullable=False, comment="cur(당월 속보) | prev(전실행) | prior(전년 실적) | plan(경영계획)"),
        sa.Column('category_key', sa.String(length=40), nullable=False, comment='비용항목 키. 고정 enum 아닌 자유 문자열'),
        sa.Column('label', sa.String(length=100), nullable=True),
        sa.Column('label_en', sa.String(length=100), nullable=True),
        sa.Column('year', sa.SmallInteger(), nullable=False),
        sa.Column('actual_thru_month', sa.SmallInteger(), nullable=False, server_default='0'),
        sa.Column('month1', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month2', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month3', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month4', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month5', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month6', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month7', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month8', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month9', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month10', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month11', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month12', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('q1', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('q2', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('q3', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('q4', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('annual', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('custom_fields', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.Column('anchor_key', sa.String(length=40), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='overseas_cost_entries_created_by_id_fkey'),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name='overseas_cost_entries_updated_by_id_fkey'),
        sa.UniqueConstraint(
            'scope_key', 'version', 'category_key', 'actual_thru_month', 'year',
            name='overseas_cost_entries_scope_key_version_category_key_actual_key',
        ),
    )
