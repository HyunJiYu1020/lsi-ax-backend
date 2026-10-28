"""renumber cost_entries and fx_rates ids from 1

Revision ID: 49f565230cf8
Revises: ea7b3f8a4e22
Create Date: 2026-10-28 20:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '49f565230cf8'
down_revision: Union[str, Sequence[str], None] = 'ea7b3f8a4e22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COST_COLS = (
    "account_key, scope_key, version, category_key, actual_thru_month, "
    "month1, month2, month3, month4, month5, month6, month7, month8, month9, month10, month11, month12, "
    "extra, created_at, created_by_id, updated_at, updated_by_id"
)
FX_COLS = (
    "version, currency, rate_type, "
    "month1, month2, month3, month4, month5, month6, month7, month8, month9, month10, month11, month12"
)


def upgrade() -> None:
    """Upgrade schema.

    id는 다른 테이블에서 FK로 참조하지 않는 내부 식별자라 renumbering이 안전하다.
    repeated upsert(ON CONFLICT DO UPDATE)로 커진 시퀀스를 1부터 다시 채운다.
    """
    op.execute(f"CREATE TEMP TABLE ce_backup ON COMMIT DROP AS SELECT {COST_COLS} FROM cost_entries ORDER BY id")
    op.execute("TRUNCATE TABLE cost_entries RESTART IDENTITY")
    op.execute(f"INSERT INTO cost_entries ({COST_COLS}) SELECT {COST_COLS} FROM ce_backup")

    op.execute(f"CREATE TEMP TABLE fx_backup ON COMMIT DROP AS SELECT {FX_COLS} FROM fx_rates ORDER BY id")
    op.execute("TRUNCATE TABLE fx_rates RESTART IDENTITY")
    op.execute(f"INSERT INTO fx_rates ({FX_COLS}) SELECT {FX_COLS} FROM fx_backup")


def downgrade() -> None:
    """Downgrade schema. id 재번호는 의미상 되돌릴 대상이 없어 no-op."""
    pass
