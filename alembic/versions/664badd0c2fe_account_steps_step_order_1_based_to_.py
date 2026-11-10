"""account_steps step_order 1-based to match frontend display

Revision ID: 664badd0c2fe
Revises: 3d5471b47348
Create Date: 2026-11-09 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '664badd0c2fe'
down_revision: Union[str, Sequence[str], None] = '3d5471b47348'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    account_steps.step_order / account_progress.current_step_order를 0-based에서
    1-based로 바꾼다 — 화면에 보이는 "N단계" 숫자와 DB 저장값이 그대로 같아지도록
    (지금까지는 화면이 step_order+1로 표시해서 숫자가 하나 어긋나 있었음).
    """
    op.drop_constraint('account_progress_account_id_current_step_order_fkey', 'account_progress', type_='foreignkey')
    op.drop_constraint('account_steps_account_id_step_order_key', 'account_steps', type_='unique')

    op.execute("UPDATE account_steps SET step_order = step_order + 1")
    op.execute("UPDATE account_progress SET current_step_order = current_step_order + 1")

    op.create_unique_constraint('account_steps_account_id_step_order_key', 'account_steps', ['account_id', 'step_order'])
    op.create_foreign_key(
        'account_progress_account_id_current_step_order_fkey', 'account_progress', 'account_steps',
        ['account_id', 'current_step_order'], ['account_id', 'step_order'],
    )

    op.alter_column(
        'account_steps', 'step_order',
        comment="단계 순서 (1-based, 화면에 보이는 'N단계'와 동일한 값)",
        existing_comment='단계 순서 (0-based)',
    )
    op.alter_column(
        'account_progress', 'current_step_order',
        server_default='1',
        comment='현재 단계 (account_steps.step_order 참조, 복합 FK로 유효 단계만 허용). 1부터 시작',
        existing_server_default='0',
        existing_comment='현재 단계 (account_steps.step_order 참조, 복합 FK로 유효 단계만 허용)',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'account_progress', 'current_step_order',
        server_default='0',
        comment='현재 단계 (account_steps.step_order 참조, 복합 FK로 유효 단계만 허용)',
        existing_server_default='1',
        existing_comment='현재 단계 (account_steps.step_order 참조, 복합 FK로 유효 단계만 허용). 1부터 시작',
    )
    op.alter_column(
        'account_steps', 'step_order',
        comment='단계 순서 (0-based)',
        existing_comment="단계 순서 (1-based, 화면에 보이는 'N단계'와 동일한 값)",
    )

    op.drop_constraint('account_progress_account_id_current_step_order_fkey', 'account_progress', type_='foreignkey')
    op.drop_constraint('account_steps_account_id_step_order_key', 'account_steps', type_='unique')

    op.execute("UPDATE account_steps SET step_order = step_order - 1")
    op.execute("UPDATE account_progress SET current_step_order = current_step_order - 1")

    op.create_unique_constraint('account_steps_account_id_step_order_key', 'account_steps', ['account_id', 'step_order'])
    op.create_foreign_key(
        'account_progress_account_id_current_step_order_fkey', 'account_progress', 'account_steps',
        ['account_id', 'current_step_order'], ['account_id', 'step_order'],
    )
