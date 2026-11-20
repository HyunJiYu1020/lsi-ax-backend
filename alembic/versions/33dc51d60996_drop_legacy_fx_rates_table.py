"""drop legacy fx_rates table

Revision ID: 33dc51d60996
Revises: c7b867372a61
Create Date: 2026-11-10 22:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '33dc51d60996'
down_revision: Union[str, Sequence[str], None] = 'c7b867372a61'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_table('fx_rates')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        'fx_rates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('version', sa.String(length=10), nullable=False, comment='forecast(속보) | actual(실적)'),
        sa.Column('currency', sa.String(length=6), nullable=False, comment='USD | ILS(이스라엘) | INR(인도)'),
        sa.Column('rate_type', sa.String(length=10), nullable=False, comment='end(월말환율) | act(실적환율) | cp(CP환율)'),
        *[sa.Column(f'month{m}', sa.Numeric(12, 4), nullable=False, server_default='0', comment=f'{m}월 환율') for m in range(1, 13)],
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='최초 입력 시각'),
        sa.Column('created_by_id', sa.Integer(), nullable=True, comment='최초 입력한 사용자 (users.id)'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'),
        sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_fx_rates_created_by_id_users'),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name='fk_fx_rates_updated_by_id_users'),
        sa.UniqueConstraint('version', 'currency', 'rate_type', name='uq_fx_rates_version_currency_rate_type'),
        comment='버전×통화×환율종류 환율 기준정보(월은 month1~month12 컬럼). 해외연구소 원화 환산·환차 분석에 쓰인다.',
    )
