"""users and created_by updated_by tracking

Revision ID: a4bfa9e6ec0b
Revises: 1ad0efd661a9
Create Date: 2026-10-28 16:18:11.241239

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4bfa9e6ec0b'
down_revision: Union[str, Sequence[str], None] = '1ad0efd661a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=50), nullable=False, comment='로그인 ID'),
    sa.Column('employee_no', sa.String(length=20), nullable=True, comment='사내 사번 (SSO 연동 시 매칭 키, 자체 로그인만 쓰면 NULL)'),
    sa.Column('email', sa.String(length=100), nullable=True, comment='이메일'),
    sa.Column('name', sa.String(length=50), nullable=False, comment='이름'),
    sa.Column('department', sa.String(length=50), nullable=True, comment='소속 부서'),
    sa.Column('password_hash', sa.String(length=255), nullable=True, comment='자체 로그인용 비밀번호 해시 (SSO만 쓰면 NULL)'),
    sa.Column('is_active', sa.Boolean(), nullable=False, comment='로그인 가능 여부'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='계정 생성 시각'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('employee_no'),
    sa.UniqueConstraint('username'),
    comment='로그인 사용자. 인증 방식(자체 ID/PW | 사내 SSO) 미정이라 password_hash는 nullable, SSO용 employee_no도 같이 둔다.'
    )

    op.add_column('account_progress', sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='단계를 이동시킨 사용자 (users.id)'))
    op.create_foreign_key('fk_account_progress_updated_by_id_users', 'account_progress', 'users', ['updated_by_id'], ['id'])

    op.add_column('common_codes', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='최초 등록 시각'))
    op.add_column('common_codes', sa.Column('created_by_id', sa.Integer(), nullable=True, comment='등록한 사용자 (users.id)'))
    op.add_column('common_codes', sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'))
    op.create_foreign_key('fk_common_codes_created_by_id_users', 'common_codes', 'users', ['created_by_id'], ['id'])
    op.create_foreign_key('fk_common_codes_updated_by_id_users', 'common_codes', 'users', ['updated_by_id'], ['id'])

    op.add_column('cost_entries', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='최초 입력 시각'))
    op.add_column('cost_entries', sa.Column('created_by_id', sa.Integer(), nullable=True, comment='최초 입력한 사용자 (users.id)'))
    op.add_column('cost_entries', sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'))
    op.create_foreign_key('fk_cost_entries_created_by_id_users', 'cost_entries', 'users', ['created_by_id'], ['id'])
    op.create_foreign_key('fk_cost_entries_updated_by_id_users', 'cost_entries', 'users', ['updated_by_id'], ['id'])

    op.add_column('custom_rows', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='행 추가 시각'))
    op.add_column('custom_rows', sa.Column('created_by_id', sa.Integer(), nullable=True, comment='행을 추가한 사용자 (users.id)'))
    op.add_column('custom_rows', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'))
    op.add_column('custom_rows', sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'))
    op.create_foreign_key('fk_custom_rows_created_by_id_users', 'custom_rows', 'users', ['created_by_id'], ['id'])
    op.create_foreign_key('fk_custom_rows_updated_by_id_users', 'custom_rows', 'users', ['updated_by_id'], ['id'])

    op.add_column('memo_cols', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='열 추가 시각'))
    op.add_column('memo_cols', sa.Column('created_by_id', sa.Integer(), nullable=True, comment='열을 추가한 사용자 (users.id)'))
    op.add_column('memo_cols', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'))
    op.add_column('memo_cols', sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'))
    op.create_foreign_key('fk_memo_cols_created_by_id_users', 'memo_cols', 'users', ['created_by_id'], ['id'])
    op.create_foreign_key('fk_memo_cols_updated_by_id_users', 'memo_cols', 'users', ['updated_by_id'], ['id'])

    op.add_column('memos', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='최초 작성 시각'))
    op.add_column('memos', sa.Column('created_by_id', sa.Integer(), nullable=True, comment='최초 작성한 사용자 (users.id)'))
    op.add_column('memos', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'))
    op.add_column('memos', sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'))
    op.create_foreign_key('fk_memos_created_by_id_users', 'memos', 'users', ['created_by_id'], ['id'])
    op.create_foreign_key('fk_memos_updated_by_id_users', 'memos', 'users', ['updated_by_id'], ['id'])

    op.add_column('notes', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='최초 작성 시각'))
    op.add_column('notes', sa.Column('created_by_id', sa.Integer(), nullable=True, comment='최초 작성한 사용자 (users.id)'))
    op.add_column('notes', sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'))
    op.create_foreign_key('fk_notes_created_by_id_users', 'notes', 'users', ['created_by_id'], ['id'])
    op.create_foreign_key('fk_notes_updated_by_id_users', 'notes', 'users', ['updated_by_id'], ['id'])

    op.add_column('uploads', sa.Column('uploaded_by_id', sa.Integer(), nullable=True, comment='업로드한 사용자 (users.id)'))
    op.create_foreign_key('fk_uploads_uploaded_by_id_users', 'uploads', 'users', ['uploaded_by_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_uploads_uploaded_by_id_users', 'uploads', type_='foreignkey')
    op.drop_column('uploads', 'uploaded_by_id')

    op.drop_constraint('fk_notes_created_by_id_users', 'notes', type_='foreignkey')
    op.drop_constraint('fk_notes_updated_by_id_users', 'notes', type_='foreignkey')
    op.drop_column('notes', 'updated_by_id')
    op.drop_column('notes', 'created_by_id')
    op.drop_column('notes', 'created_at')

    op.drop_constraint('fk_memos_created_by_id_users', 'memos', type_='foreignkey')
    op.drop_constraint('fk_memos_updated_by_id_users', 'memos', type_='foreignkey')
    op.drop_column('memos', 'updated_by_id')
    op.drop_column('memos', 'updated_at')
    op.drop_column('memos', 'created_by_id')
    op.drop_column('memos', 'created_at')

    op.drop_constraint('fk_memo_cols_created_by_id_users', 'memo_cols', type_='foreignkey')
    op.drop_constraint('fk_memo_cols_updated_by_id_users', 'memo_cols', type_='foreignkey')
    op.drop_column('memo_cols', 'updated_by_id')
    op.drop_column('memo_cols', 'updated_at')
    op.drop_column('memo_cols', 'created_by_id')
    op.drop_column('memo_cols', 'created_at')

    op.drop_constraint('fk_custom_rows_created_by_id_users', 'custom_rows', type_='foreignkey')
    op.drop_constraint('fk_custom_rows_updated_by_id_users', 'custom_rows', type_='foreignkey')
    op.drop_column('custom_rows', 'updated_by_id')
    op.drop_column('custom_rows', 'updated_at')
    op.drop_column('custom_rows', 'created_by_id')
    op.drop_column('custom_rows', 'created_at')

    op.drop_constraint('fk_cost_entries_created_by_id_users', 'cost_entries', type_='foreignkey')
    op.drop_constraint('fk_cost_entries_updated_by_id_users', 'cost_entries', type_='foreignkey')
    op.drop_column('cost_entries', 'updated_by_id')
    op.drop_column('cost_entries', 'created_by_id')
    op.drop_column('cost_entries', 'created_at')

    op.drop_constraint('fk_common_codes_created_by_id_users', 'common_codes', type_='foreignkey')
    op.drop_constraint('fk_common_codes_updated_by_id_users', 'common_codes', type_='foreignkey')
    op.drop_column('common_codes', 'updated_by_id')
    op.drop_column('common_codes', 'created_by_id')
    op.drop_column('common_codes', 'created_at')

    op.drop_constraint('fk_account_progress_updated_by_id_users', 'account_progress', type_='foreignkey')
    op.drop_column('account_progress', 'updated_by_id')

    op.drop_table('users')
