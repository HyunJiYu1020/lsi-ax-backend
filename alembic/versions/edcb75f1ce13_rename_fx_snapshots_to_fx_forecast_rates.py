"""rename fx_snapshots to fx_forecast_rates

Revision ID: edcb75f1ce13
Revises: 788a6d41f69c
Create Date: 2026-11-05 11:13:54.214741

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'edcb75f1ce13'
down_revision: Union[str, Sequence[str], None] = '788a6d41f69c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.rename_table('fx_snapshots', 'fx_forecast_rates')
    op.execute("ALTER SEQUENCE fx_snapshots_id_seq RENAME TO fx_forecast_rates_id_seq")
    op.execute("ALTER TABLE fx_forecast_rates RENAME CONSTRAINT fx_snapshots_pkey TO fx_forecast_rates_pkey")
    op.execute(
        "ALTER TABLE fx_forecast_rates RENAME CONSTRAINT uq_fx_snapshots_ym_currency TO uq_fx_forecast_rates_ym_currency"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "ALTER TABLE fx_forecast_rates RENAME CONSTRAINT uq_fx_forecast_rates_ym_currency TO uq_fx_snapshots_ym_currency"
    )
    op.execute("ALTER TABLE fx_forecast_rates RENAME CONSTRAINT fx_forecast_rates_pkey TO fx_snapshots_pkey")
    op.execute("ALTER SEQUENCE fx_forecast_rates_id_seq RENAME TO fx_snapshots_id_seq")
    op.rename_table('fx_forecast_rates', 'fx_snapshots')
