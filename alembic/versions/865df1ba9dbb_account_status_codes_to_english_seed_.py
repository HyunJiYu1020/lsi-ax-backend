"""account status codes to english, seed account_status common codes

Revision ID: 865df1ba9dbb
Revises: e8872121c68c
Create Date: 2026-10-28 17:56:18.626111

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '865df1ba9dbb'
down_revision: Union[str, Sequence[str], None] = 'e8872121c68c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACCOUNT_STATUS_CODES = [
    ("ACCOUNT_STATUS", "wip", "작성중", 1),
    ("ACCOUNT_STATUS", "drafted", "작성완료", 2),
    ("ACCOUNT_STATUS", "not_submitted", "미제출", 3),
    ("ACCOUNT_STATUS", "submitted", "제출완료", 4),
    ("ACCOUNT_STATUS", "reviewed", "검토완료", 5),
    ("ACCOUNT_STATUS", "rejected", "반려", 6),
]

common_codes_table = sa.table(
    "common_codes",
    sa.column("group_code", sa.String),
    sa.column("code", sa.String),
    sa.column("name", sa.String),
    sa.column("sort_order", sa.SmallInteger),
    sa.column("is_active", sa.Boolean),
)

accounts_table = sa.table(
    "accounts",
    sa.column("status", sa.String),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.bulk_insert(
        common_codes_table,
        [
            {"group_code": g, "code": c, "name": n, "sort_order": o, "is_active": True}
            for g, c, n, o in ACCOUNT_STATUS_CODES
        ],
    )
    op.execute(accounts_table.update().where(accounts_table.c.status == "작성중").values(status="wip"))


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(accounts_table.update().where(accounts_table.c.status == "wip").values(status="작성중"))
    op.execute(common_codes_table.delete().where(common_codes_table.c.group_code == "ACCOUNT_STATUS"))
