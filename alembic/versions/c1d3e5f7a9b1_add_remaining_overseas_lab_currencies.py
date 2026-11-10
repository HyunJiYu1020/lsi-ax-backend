"""add remaining overseas lab currencies to CURRENCY common codes

Revision ID: c1d3e5f7a9b1
Revises: 77aa9bcb809b
Create Date: 2026-11-03 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c1d3e5f7a9b1'
down_revision: Union[str, Sequence[str], None] = 'b8366699227c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# b3f4c91a7d2e가 overseas_labs.currency를 국가별로 백필하면서 쓴 6개 통화가
# CURRENCY 공통코드에는 없었다 (그때는 USD·ILS·INR 3개뿐). 여기서 나머지를 채운다.
CODES = [
    ("GBP", "영국 파운드", 3),
    ("CNY", "중국 위안", 4),
    ("TWD", "대만 달러", 5),
    ("PLN", "폴란드 즐로티", 6),
    ("RUB", "러시아 루블", 7),
    ("JPY", "일본 옌", 8),
]


def upgrade() -> None:
    """Upgrade schema."""
    common_codes = sa.table(
        "common_codes",
        sa.column("group_code", sa.String),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("sort_order", sa.SmallInteger),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        common_codes,
        [
            {"group_code": "CURRENCY", "code": code, "name": name, "sort_order": sort_order, "is_active": True}
            for code, name, sort_order in CODES
        ],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        sa.text("DELETE FROM common_codes WHERE group_code = 'CURRENCY' AND code IN :codes")
        .bindparams(sa.bindparam("codes", expanding=True))
        .bindparams(codes=[code for code, _, _ in CODES])
    )
