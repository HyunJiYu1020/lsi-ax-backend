"""accounts and fx_rates audit columns

Revision ID: ab904cae58dd
Revises: 39f6f20baf97
Create Date: 2026-11-04 22:43:59.629102

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ab904cae58dd'
down_revision: Union[str, Sequence[str], None] = '39f6f20baf97'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # accounts.status/memo는 사용자가 고치는 값인데 감사 컬럼이 없었다 — updated만 추가
    # (행 자체는 시스템이 seed하는 레지스트리라 created_at/created_by_id는 의미가 없다).
    op.add_column('accounts', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='status · memo 마지막 수정 시각'))
    op.add_column('accounts', sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='status · memo를 마지막으로 수정한 사용자 (users.id)'))
    op.create_foreign_key('fk_accounts_updated_by_id_users', 'accounts', 'users', ['updated_by_id'], ['id'])

    # fx_rates는 실제로 쓰이는 테이블인데 감사 컬럼이 통째로 빠져 있었다 — 같은 패턴의
    # 다른 테이블(overseas_cost_lines 등)과 맞춰 created/updated 전부 추가.
    op.add_column('fx_rates', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='최초 입력 시각'))
    op.add_column('fx_rates', sa.Column('created_by_id', sa.Integer(), nullable=True, comment='최초 입력한 사용자 (users.id)'))
    op.add_column('fx_rates', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'))
    op.add_column('fx_rates', sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'))
    op.create_foreign_key('fk_fx_rates_created_by_id_users', 'fx_rates', 'users', ['created_by_id'], ['id'])
    op.create_foreign_key('fk_fx_rates_updated_by_id_users', 'fx_rates', 'users', ['updated_by_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_fx_rates_created_by_id_users', 'fx_rates', type_='foreignkey')
    op.drop_constraint('fk_fx_rates_updated_by_id_users', 'fx_rates', type_='foreignkey')
    op.drop_column('fx_rates', 'updated_by_id')
    op.drop_column('fx_rates', 'updated_at')
    op.drop_column('fx_rates', 'created_by_id')
    op.drop_column('fx_rates', 'created_at')

    op.drop_constraint('fk_accounts_updated_by_id_users', 'accounts', type_='foreignkey')
    op.drop_column('accounts', 'updated_by_id')
    op.drop_column('accounts', 'updated_at')
