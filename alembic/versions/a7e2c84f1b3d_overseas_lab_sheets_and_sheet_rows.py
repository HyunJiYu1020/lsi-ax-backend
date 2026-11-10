"""overseas lab sheets and sheet rows

Revision ID: a7e2c84f1b3d
Revises: 49f565230cf8
Create Date: 2026-10-28 21:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a7e2c84f1b3d'
down_revision: Union[str, Sequence[str], None] = '49f565230cf8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'overseas_labs',
        sa.Column(
            'sort_order', sa.SmallInteger(), server_default='0', nullable=False,
            comment='탭·집계표 표시 순서(lab_code 단위, 같은 lab_code의 파트들은 같은 값)',
        ),
    )
    op.create_table(
        'overseas_lab_sheets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('lab_code', sa.String(length=10), nullable=False, comment='overseas_labs.lab_code (파트 구분 없이 연구소 단위)'),
        sa.Column(
            'sheet_key', sa.String(length=20), nullable=False,
            comment="시트 종류. common_codes(group_code='OVERSEAS_SHEET_TYPE')의 code 값 (cost|variance|vendor)",
        ),
        sa.Column('sheet_order', sa.SmallInteger(), server_default='0', nullable=False, comment='그 연구소 안에서 시트 탭 표시 순서'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('lab_code', 'sheet_key'),
        comment='연구소(lab_code)별 워크북 시트 구성(비용|증감분석|외주용역업체 등). 시트가 늘어도 행만 추가하면 된다.',
    )
    op.create_table(
        'sheet_rows',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_key', sa.String(length=32), nullable=False, comment='accounts.key'),
        sa.Column('scope_key', sa.String(length=40), nullable=False, comment='계정 안에서의 행 단위 식별자 (해외연구소는 연구소 pk)'),
        sa.Column('version', sa.String(length=10), nullable=False, comment='cur | prev | prior | plan'),
        sa.Column('sheet_key', sa.String(length=20), nullable=False, comment='시트 종류 (해외연구소는 variance|vendor 등)'),
        sa.Column('row_key', sa.String(length=40), nullable=False, comment='시트 안에서의 행 식별자 (업로드 시 행 순서 등)'),
        sa.Column('sort_order', sa.SmallInteger(), server_default='0', nullable=False, comment='시트 내 표시 순서'),
        sa.Column(
            'data', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
            comment='열이름 -> 값. 열 구조가 확정되지 않아 구조화 테이블 대신 JSON으로 보관',
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='최초 입력 시각'),
        sa.Column('created_by_id', sa.Integer(), nullable=True, comment='최초 입력한 사용자 (users.id)'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'),
        sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('account_key', 'scope_key', 'version', 'sheet_key', 'row_key'),
        comment='정형화 안 된 시트의 원본 행(JSON) 저장소. 19개 계정이 공유.',
    )
    op.create_foreign_key('fk_sheet_rows_account_key_accounts', 'sheet_rows', 'accounts', ['account_key'], ['key'])
    op.create_foreign_key('fk_sheet_rows_created_by_id_users', 'sheet_rows', 'users', ['created_by_id'], ['id'])
    op.create_foreign_key('fk_sheet_rows_updated_by_id_users', 'sheet_rows', 'users', ['updated_by_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_sheet_rows_updated_by_id_users', 'sheet_rows', type_='foreignkey')
    op.drop_constraint('fk_sheet_rows_created_by_id_users', 'sheet_rows', type_='foreignkey')
    op.drop_constraint('fk_sheet_rows_account_key_accounts', 'sheet_rows', type_='foreignkey')
    op.drop_table('sheet_rows')
    op.drop_table('overseas_lab_sheets')
    op.drop_column('overseas_labs', 'sort_order')
