"""merge fx_actual_forecast_plan_rates into fx_rate_inputs

Revision ID: c7b867372a61
Revises: bcbf14de0f26
Create Date: 2026-11-10 22:33:42.470472

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7b867372a61'
down_revision: Union[str, Sequence[str], None] = 'bcbf14de0f26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MONTH_COLS = [f"month{m}" for m in range(1, 13)]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'fx_rate_inputs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('year_month', sa.String(length=7), nullable=False, comment='연월 "YYYY-MM"'),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False, comment='actual_avg | actual_eom | forecast | plan'),
        sa.Column('rate', sa.Numeric(14, 6), nullable=False, server_default='0', comment='환율 값'),
        sa.Column('source_file', sa.String(length=255), nullable=True, comment='업로드한 원본 파일명'),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=True, comment='업로드 시각'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('year_month', 'currency_code', 'kind', name='uq_fx_rate_inputs_ym_currency_kind'),
        sa.CheckConstraint(r"year_month ~ '^\d{4}-(0[1-9]|1[0-2])$'", name='ck_fx_rate_inputs_year_month_format'),
        sa.CheckConstraint("kind IN ('actual_avg', 'actual_eom', 'forecast', 'plan')", name='ck_fx_rate_inputs_kind'),
        comment='실적(월평균·말일)·속보·경영 환율 원본 입력(연월×통화×종류, 값 1개+업로드 정보).',
    )

    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO fx_rate_inputs (year_month, currency_code, kind, rate, source_file, uploaded_at) "
        "SELECT year_month, currency_code, 'actual_avg', avg_rate, avg_source_file, avg_uploaded_at "
        "FROM fx_actual_rates WHERE avg_source_file IS NOT NULL"
    ))
    conn.execute(sa.text(
        "INSERT INTO fx_rate_inputs (year_month, currency_code, kind, rate, source_file, uploaded_at) "
        "SELECT year_month, currency_code, 'actual_eom', eom_rate, eom_source_file, eom_uploaded_at "
        "FROM fx_actual_rates WHERE eom_source_file IS NOT NULL"
    ))
    conn.execute(sa.text(
        "INSERT INTO fx_rate_inputs (year_month, currency_code, kind, rate, source_file, uploaded_at) "
        "SELECT year_month, currency_code, 'forecast', rate, source_file, uploaded_at FROM fx_forecast_rates"
    ))
    for i, col in enumerate(_MONTH_COLS, start=1):
        conn.execute(sa.text(
            f"INSERT INTO fx_rate_inputs (year_month, currency_code, kind, rate, source_file, uploaded_at) "
            f"SELECT year || '-{i:02d}', currency_code, 'plan', {col}, source_file, uploaded_at FROM fx_plan_rates"
        ))

    op.drop_table('fx_actual_rates')
    op.drop_table('fx_forecast_rates')
    op.drop_table('fx_plan_rates')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        'fx_actual_rates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('year_month', sa.String(length=7), nullable=False, comment='연월 "YYYY-MM"'),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('avg_rate', sa.Numeric(14, 6), nullable=False, server_default='0', comment='월 평균 실적 환율'),
        sa.Column('avg_source_file', sa.String(length=255), nullable=True, comment='월평균 실적 환율 업로드 파일명'),
        sa.Column('avg_uploaded_at', sa.DateTime(timezone=True), nullable=True, comment='월평균 실적 환율 업로드 시각'),
        sa.Column('eom_rate', sa.Numeric(14, 6), nullable=False, server_default='0', comment='말일(월말) 환율'),
        sa.Column('eom_source_file', sa.String(length=255), nullable=True, comment='말일 환율 업로드 파일명'),
        sa.Column('eom_uploaded_at', sa.DateTime(timezone=True), nullable=True, comment='말일 환율 업로드 시각'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('year_month', 'currency_code', name='uq_fx_actual_rates_ym_currency'),
        sa.CheckConstraint(r"year_month ~ '^\d{4}-(0[1-9]|1[0-2])$'", name='ck_fx_actual_rates_year_month_format'),
        comment='실적 환율(연월×통화) — 월평균 실적 환율 + 말일 환율. 둘 다 월 1회, 통화당 값 1개.',
    )
    op.create_table(
        'fx_forecast_rates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('year_month', sa.String(length=7), nullable=False, comment='편성월 "YYYY-MM"'),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('rate', sa.Numeric(14, 4), nullable=False, server_default='0', comment='속보(=실행) 환율'),
        sa.Column('source_file', sa.String(length=255), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('year_month', 'currency_code', name='uq_fx_forecast_rates_ym_currency'),
        sa.CheckConstraint(r"year_month ~ '^\d{4}-(0[1-9]|1[0-2])$'", name='ck_fx_forecast_rates_year_month_format'),
        comment='속보(=실행) 환율 업로드. 연월×통화당 값 1개',
    )
    op.create_table(
        'fx_plan_rates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('year', sa.String(length=4), nullable=False),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        *[sa.Column(col, sa.Numeric(14, 4), nullable=False, server_default='0') for col in _MONTH_COLS],
        sa.Column('source_file', sa.String(length=255), nullable=False, server_default=''),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('year', 'currency_code', name='uq_fx_plan_rates_year_currency'),
        comment='경영환율 (연간, 통화당 1~12월)',
    )

    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO fx_actual_rates (year_month, currency_code, avg_rate, avg_source_file, avg_uploaded_at, "
        "eom_rate, eom_source_file, eom_uploaded_at) "
        "SELECT COALESCE(a.year_month, e.year_month), COALESCE(a.currency_code, e.currency_code), "
        "COALESCE(a.rate, 0), a.source_file, a.uploaded_at, COALESCE(e.rate, 0), e.source_file, e.uploaded_at "
        "FROM (SELECT * FROM fx_rate_inputs WHERE kind = 'actual_avg') a "
        "FULL OUTER JOIN (SELECT * FROM fx_rate_inputs WHERE kind = 'actual_eom') e "
        "ON a.year_month = e.year_month AND a.currency_code = e.currency_code"
    ))
    conn.execute(sa.text(
        "INSERT INTO fx_forecast_rates (year_month, currency_code, rate, source_file, uploaded_at) "
        "SELECT year_month, currency_code, rate, COALESCE(source_file, ''), COALESCE(uploaded_at, now()) "
        "FROM fx_rate_inputs WHERE kind = 'forecast'"
    ))
    month_case = ", ".join(
        f"MAX(CASE WHEN SUBSTRING(year_month, 6, 2) = '{i:02d}' THEN rate END) AS {col}"
        for i, col in enumerate(_MONTH_COLS, start=1)
    )
    conn.execute(sa.text(
        f"INSERT INTO fx_plan_rates (year, currency_code, {', '.join(_MONTH_COLS)}, source_file, uploaded_at) "
        f"SELECT SUBSTRING(year_month, 1, 4) AS year, currency_code, {month_case}, "
        f"MAX(COALESCE(source_file, '')), MAX(COALESCE(uploaded_at, now())) "
        f"FROM fx_rate_inputs WHERE kind = 'plan' GROUP BY SUBSTRING(year_month, 1, 4), currency_code"
    ))

    op.drop_table('fx_rate_inputs')
