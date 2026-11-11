"""add audit columns to versions and management_plan_versions

Revision ID: 9b53d679b0c4
Revises: 55699fe26112
Create Date: 2026-11-10 23:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b53d679b0c4'
down_revision: Union[str, Sequence[str], None] = '55699fe26112'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('versions', sa.Column('created_by_id', sa.Integer(), nullable=True, comment='만든 사용자 (users.id)'))
    op.add_column('versions', sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'))
    op.create_foreign_key('fk_versions_created_by_id_users', 'versions', 'users', ['created_by_id'], ['id'])
    op.create_foreign_key('fk_versions_updated_by_id_users', 'versions', 'users', ['updated_by_id'], ['id'])

    op.add_column('management_plan_versions', sa.Column(
        'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='최초 생성 시각'
    ))
    op.add_column('management_plan_versions', sa.Column('created_by_id', sa.Integer(), nullable=True, comment='최초 생성한 사용자 (users.id)'))
    op.add_column('management_plan_versions', sa.Column(
        'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'
    ))
    op.add_column('management_plan_versions', sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'))
    op.create_foreign_key('fk_management_plan_versions_created_by_id_users', 'management_plan_versions', 'users', ['created_by_id'], ['id'])
    op.create_foreign_key('fk_management_plan_versions_updated_by_id_users', 'management_plan_versions', 'users', ['updated_by_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_management_plan_versions_updated_by_id_users', 'management_plan_versions', type_='foreignkey')
    op.drop_constraint('fk_management_plan_versions_created_by_id_users', 'management_plan_versions', type_='foreignkey')
    op.drop_column('management_plan_versions', 'updated_by_id')
    op.drop_column('management_plan_versions', 'updated_at')
    op.drop_column('management_plan_versions', 'created_by_id')
    op.drop_column('management_plan_versions', 'created_at')

    op.drop_constraint('fk_versions_updated_by_id_users', 'versions', type_='foreignkey')
    op.drop_constraint('fk_versions_created_by_id_users', 'versions', type_='foreignkey')
    op.drop_column('versions', 'updated_by_id')
    op.drop_column('versions', 'created_by_id')
