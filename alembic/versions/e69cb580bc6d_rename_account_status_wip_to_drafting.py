"""rename account status wip to drafting

Revision ID: e69cb580bc6d
Revises: 865df1ba9dbb
Create Date: 2026-10-28 18:29:55.896165

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e69cb580bc6d'
down_revision: Union[str, Sequence[str], None] = '865df1ba9dbb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

accounts_table = sa.table(
    "accounts",
    sa.column("status", sa.String),
)

common_codes_table = sa.table(
    "common_codes",
    sa.column("group_code", sa.String),
    sa.column("code", sa.String),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(accounts_table.update().where(accounts_table.c.status == "wip").values(status="drafting"))
    op.execute(
        common_codes_table.update()
        .where(common_codes_table.c.group_code == "ACCOUNT_STATUS", common_codes_table.c.code == "wip")
        .values(code="drafting")
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        common_codes_table.update()
        .where(common_codes_table.c.group_code == "ACCOUNT_STATUS", common_codes_table.c.code == "drafting")
        .values(code="wip")
    )
    op.execute(accounts_table.update().where(accounts_table.c.status == "drafting").values(status="wip"))
