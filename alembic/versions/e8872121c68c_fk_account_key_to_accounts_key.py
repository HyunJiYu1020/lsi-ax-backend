"""fk account_key to accounts key

Revision ID: e8872121c68c
Revises: a4bfa9e6ec0b
Create Date: 2026-10-28 16:49:25.601508

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8872121c68c'
down_revision: Union[str, Sequence[str], None] = 'a4bfa9e6ec0b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_foreign_key('fk_cost_entries_account_key_accounts', 'cost_entries', 'accounts', ['account_key'], ['key'])
    op.create_foreign_key('fk_custom_rows_account_key_accounts', 'custom_rows', 'accounts', ['account_key'], ['key'])
    op.create_foreign_key('fk_memo_cols_account_key_accounts', 'memo_cols', 'accounts', ['account_key'], ['key'])
    op.create_foreign_key('fk_memos_account_key_accounts', 'memos', 'accounts', ['account_key'], ['key'])
    op.create_foreign_key('fk_notes_account_key_accounts', 'notes', 'accounts', ['account_key'], ['key'])
    op.create_foreign_key('fk_uploads_account_key_accounts', 'uploads', 'accounts', ['account_key'], ['key'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_uploads_account_key_accounts', 'uploads', type_='foreignkey')
    op.drop_constraint('fk_notes_account_key_accounts', 'notes', type_='foreignkey')
    op.drop_constraint('fk_memos_account_key_accounts', 'memos', type_='foreignkey')
    op.drop_constraint('fk_memo_cols_account_key_accounts', 'memo_cols', type_='foreignkey')
    op.drop_constraint('fk_custom_rows_account_key_accounts', 'custom_rows', type_='foreignkey')
    op.drop_constraint('fk_cost_entries_account_key_accounts', 'cost_entries', type_='foreignkey')
