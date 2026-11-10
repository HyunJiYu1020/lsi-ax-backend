"""fx actual_rates merge and snapshot simplify

Revision ID: b78d797b5133
Revises: 3024a3aef37b
Create Date: 2026-11-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b78d797b5133'
down_revision: Union[str, Sequence[str], None] = '3024a3aef37b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # fx_monthly_avg(월 1행, 통화당 평균값)과 fx_daily_rates(일별 RAW로 가정했던 테이블 — 실제로는
    # 말일 환율이 월 1건만 올라와 그레인이 같음이 확인됨)를 fx_actual_rates 하나로 합친다.
    op.create_table(
        'fx_actual_rates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('year_month', sa.String(length=7), nullable=False, comment='연월 "YYYY-MM"'),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('avg_rate', sa.Numeric(precision=14, scale=6), nullable=False, server_default='0', comment='월 평균 실적 환율'),
        sa.Column('avg_source_file', sa.String(length=255), nullable=True, comment='월평균 실적 환율 업로드 파일명'),
        sa.Column('avg_uploaded_at', sa.DateTime(timezone=True), nullable=True, comment='월평균 실적 환율 업로드 시각'),
        sa.Column('eom_rate', sa.Numeric(precision=14, scale=6), nullable=False, server_default='0', comment='말일(월말) 환율'),
        sa.Column('eom_source_file', sa.String(length=255), nullable=True, comment='말일 환율 업로드 파일명'),
        sa.Column('eom_uploaded_at', sa.DateTime(timezone=True), nullable=True, comment='말일 환율 업로드 시각'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('year_month', 'currency_code', name='uq_fx_actual_rates_ym_currency'),
        comment='실적 환율(연월×통화) — 월평균 실적 환율 + 말일 환율. 둘 다 월 1회, 통화당 값 1개.',
    )
    op.drop_table('fx_daily_rates')
    op.drop_table('fx_monthly_avg')

    # fx_snapshots: 속보 파일 1개에 6칸(전월실적·실행T0A·속보·계획4Q·전실행·실행)이 들어있다는
    # 가정으로 만들었던 걸, 실제로는 "실행 환율 = 속보"이고 연월×통화당 값 1개뿐임이 확인돼 단순화한다.
    for col in ("prior_actual", "exec_t0a", "flash", "plan_4q", "prior_exec", "exec_next"):
        op.drop_column('fx_snapshots', col)
    op.add_column(
        'fx_snapshots',
        sa.Column('rate', sa.Numeric(precision=14, scale=4), nullable=False, server_default='0', comment='속보(=실행) 환율'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('fx_snapshots', 'rate')
    op.add_column('fx_snapshots', sa.Column('exec_next', sa.Numeric(precision=14, scale=4), nullable=False, server_default='0'))
    op.add_column('fx_snapshots', sa.Column('prior_exec', sa.Numeric(precision=14, scale=4), nullable=False, server_default='0'))
    op.add_column('fx_snapshots', sa.Column('plan_4q', sa.Numeric(precision=14, scale=4), nullable=False, server_default='0'))
    op.add_column('fx_snapshots', sa.Column('flash', sa.Numeric(precision=14, scale=4), nullable=False, server_default='0'))
    op.add_column('fx_snapshots', sa.Column('exec_t0a', sa.Numeric(precision=14, scale=4), nullable=False, server_default='0'))
    op.add_column('fx_snapshots', sa.Column('prior_actual', sa.Numeric(precision=14, scale=4), nullable=False, server_default='0'))

    op.create_table(
        'fx_monthly_avg',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('year_month', sa.String(length=7), nullable=False),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('avg_rate', sa.Numeric(precision=14, scale=6), nullable=False, server_default='0'),
        sa.Column('source_file', sa.String(length=255), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('year_month', 'currency_code', name='uq_fx_monthly_avg_ym_currency'),
    )
    op.create_table(
        'fx_daily_rates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('from_currency', sa.String(length=3), nullable=False),
        sa.Column('to_currency', sa.String(length=3), nullable=False),
        sa.Column('rate_date', sa.Date(), nullable=False),
        sa.Column('rate', sa.Numeric(precision=14, scale=6), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('from_currency', 'to_currency', 'rate_date', name='uq_fx_daily_rates_pair_date'),
    )
    op.drop_table('fx_actual_rates')
