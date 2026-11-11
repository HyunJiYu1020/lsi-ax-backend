"""rename fx_applied_rates basis_kind sok/act to forecast/actual

Revision ID: bcbf14de0f26
Revises: d218f41a2077
Create Date: 2026-11-10 21:45:06.401733

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bcbf14de0f26'
down_revision: Union[str, Sequence[str], None] = 'd218f41a2077'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("UPDATE fx_applied_rates SET basis_kind = 'forecast' WHERE basis_kind = 'sok'")
    op.execute("UPDATE fx_applied_rates SET basis_kind = 'actual' WHERE basis_kind = 'act'")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE fx_applied_rates SET basis_kind = 'sok' WHERE basis_kind = 'forecast'")
    op.execute("UPDATE fx_applied_rates SET basis_kind = 'act' WHERE basis_kind = 'actual'")
