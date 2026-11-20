"""fix versions kind column comment to forecast actual

Revision ID: 295b0de39cf1
Revises: 2c83a3287aa6
Create Date: 2026-11-11 03:35:59.886473

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '295b0de39cf1'
down_revision: Union[str, Sequence[str], None] = '2c83a3287aa6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("COMMENT ON COLUMN versions.kind IS 'forecast(속보) | actual(실적)'")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("COMMENT ON COLUMN versions.kind IS 'sok(속보) | act(실적)'")
