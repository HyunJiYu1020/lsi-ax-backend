"""rename fx_rate_inputs to fx_rates

Revision ID: 55699fe26112
Revises: 33dc51d60996
Create Date: 2026-11-10 22:55:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '55699fe26112'
down_revision: Union[str, Sequence[str], None] = '33dc51d60996'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.rename_table('fx_rate_inputs', 'fx_rates')
    op.execute("ALTER TABLE fx_rates RENAME CONSTRAINT uq_fx_rate_inputs_ym_currency_kind TO uq_fx_rates_ym_currency_kind")
    op.execute("ALTER TABLE fx_rates RENAME CONSTRAINT ck_fx_rate_inputs_year_month_format TO ck_fx_rates_year_month_format")
    op.execute("ALTER TABLE fx_rates RENAME CONSTRAINT ck_fx_rate_inputs_kind TO ck_fx_rates_kind")
    op.execute("ALTER TABLE fx_rates RENAME CONSTRAINT fx_rate_inputs_pkey TO fx_rates_pkey")
    op.execute("ALTER SEQUENCE fx_rate_inputs_id_seq RENAME TO fx_rates_id_seq")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("ALTER SEQUENCE fx_rates_id_seq RENAME TO fx_rate_inputs_id_seq")
    op.execute("ALTER TABLE fx_rates RENAME CONSTRAINT fx_rates_pkey TO fx_rate_inputs_pkey")
    op.execute("ALTER TABLE fx_rates RENAME CONSTRAINT ck_fx_rates_kind TO ck_fx_rate_inputs_kind")
    op.execute("ALTER TABLE fx_rates RENAME CONSTRAINT ck_fx_rates_year_month_format TO ck_fx_rate_inputs_year_month_format")
    op.execute("ALTER TABLE fx_rates RENAME CONSTRAINT uq_fx_rates_ym_currency_kind TO uq_fx_rate_inputs_ym_currency_kind")
    op.rename_table('fx_rates', 'fx_rate_inputs')
