"""overseas account_steps revert merge, restore 3 steps

Revision ID: 5929930a9de9
Revises: d54c29d3b4e6
Create Date: 2026-11-09 14:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5929930a9de9'
down_revision: Union[str, Sequence[str], None] = 'd54c29d3b4e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    e423ec91abdc에서 overseas 0단계(취합)를 1단계(집계)에 합친 게 잘못된 판단이었음 — 원래대로
    취합(0)/집계(1)/증감분석(2) 3단계로 되돌린다. (취합/편성 동시진행 병합은 dep 계정에만 해당했음.)
    """
    op.drop_constraint('account_progress_account_id_current_step_order_fkey', 'account_progress', type_='foreignkey')

    op.execute("""
        UPDATE account_steps s
        SET step_order = 2
        FROM accounts a
        WHERE s.account_id = a.id AND a.key = 'overseas' AND s.step_order = 1
    """)
    op.execute("""
        UPDATE account_steps s
        SET step_order = 1
        FROM accounts a
        WHERE s.account_id = a.id AND a.key = 'overseas' AND s.step_order = 0
    """)
    op.execute("""
        INSERT INTO account_steps (account_id, step_order, tag, title)
        SELECT a.id, 0, '취합', '연구소 데이터 업로드'
        FROM accounts a WHERE a.key = 'overseas'
    """)
    op.execute("""
        UPDATE account_progress p
        SET current_step_order = p.current_step_order + 1
        FROM accounts a
        WHERE p.account_id = a.id AND a.key = 'overseas'
    """)

    op.create_foreign_key(
        'account_progress_account_id_current_step_order_fkey', 'account_progress', 'account_steps',
        ['account_id', 'current_step_order'], ['account_id', 'step_order'],
    )


def downgrade() -> None:
    """Downgrade schema. 다시 취합(0)을 없애고 집계(1)->0, 증감분석(2)->1로 합친다."""
    op.drop_constraint('account_progress_account_id_current_step_order_fkey', 'account_progress', type_='foreignkey')

    op.execute("""
        UPDATE account_progress p
        SET current_step_order = CASE WHEN p.current_step_order <= 1 THEN 0 ELSE p.current_step_order - 1 END
        FROM accounts a
        WHERE p.account_id = a.id AND a.key = 'overseas'
    """)
    op.execute("""
        DELETE FROM account_steps s
        USING accounts a
        WHERE s.account_id = a.id AND a.key = 'overseas' AND s.step_order = 0
    """)
    op.execute("""
        UPDATE account_steps s
        SET step_order = 0
        FROM accounts a
        WHERE s.account_id = a.id AND a.key = 'overseas' AND s.step_order = 1
    """)
    op.execute("""
        UPDATE account_steps s
        SET step_order = 1
        FROM accounts a
        WHERE s.account_id = a.id AND a.key = 'overseas' AND s.step_order = 2
    """)

    op.create_foreign_key(
        'account_progress_account_id_current_step_order_fkey', 'account_progress', 'account_steps',
        ['account_id', 'current_step_order'], ['account_id', 'step_order'],
    )
