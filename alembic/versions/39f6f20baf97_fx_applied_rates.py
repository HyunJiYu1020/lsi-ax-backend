"""fx applied rates

Revision ID: 39f6f20baf97
Revises: b78d797b5133
Create Date: 2026-11-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '39f6f20baf97'
down_revision: Union[str, Sequence[str], None] = 'b78d797b5133'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'fx_applied_rates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('basis_year', sa.SmallInteger(), nullable=False, comment='기준 연도'),
        sa.Column('basis_month', sa.SmallInteger(), nullable=False, comment='기준 월(1~12)'),
        sa.Column('basis_kind', sa.String(length=10), nullable=False, comment='sok(속보) | act(실적) | plan(경영)'),
        sa.Column('rule', sa.String(length=10), nullable=False, server_default='avg', comment='과거 대상월의 실적 기준: avg(평균) | last(말일)'),
        sa.Column('target_month', sa.SmallInteger(), nullable=False, comment='적용 환율이 매겨지는 대상 월(1~12)'),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('rate', sa.Numeric(precision=14, scale=4), nullable=False, server_default='0', comment='원/통화 적용 환율'),
        sa.Column('source', sa.String(length=10), nullable=False, comment='이 값의 출처: actual | snapshot | plan'),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'basis_year', 'basis_month', 'basis_kind', 'rule', 'target_month', 'currency_code',
            name='uq_fx_applied_rates_basis_rule_month_currency',
        ),
        comment='적용 환율(기준시점×대상월×통화) — 실적·속보·경영 조합 계산 결과 스냅샷(파생 테이블)',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('fx_applied_rates')
