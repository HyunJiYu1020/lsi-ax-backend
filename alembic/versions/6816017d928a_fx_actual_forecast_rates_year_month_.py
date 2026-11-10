"""fx actual/forecast rates year_month format check

Revision ID: 6816017d928a
Revises: 664badd0c2fe
Create Date: 2026-11-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6816017d928a'
down_revision: Union[str, Sequence[str], None] = '664badd0c2fe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_check_constraint(
        'ck_fx_actual_rates_year_month_format',
        'fx_actual_rates',
        r"year_month ~ '^\d{4}-(0[1-9]|1[0-2])$'",
    )
    op.create_check_constraint(
        'ck_fx_forecast_rates_year_month_format',
        'fx_forecast_rates',
        r"year_month ~ '^\d{4}-(0[1-9]|1[0-2])$'",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_fx_forecast_rates_year_month_format', 'fx_forecast_rates', type_='check')
    op.drop_constraint('ck_fx_actual_rates_year_month_format', 'fx_actual_rates', type_='check')
