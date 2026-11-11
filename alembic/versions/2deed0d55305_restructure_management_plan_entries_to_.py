"""restructure management_plan_entries to master detail

Revision ID: 2deed0d55305
Revises: 9b53d679b0c4
Create Date: 2026-11-10 23:37:15.455230

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '2deed0d55305'
down_revision: Union[str, Sequence[str], None] = '9b53d679b0c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # cost_entries/cost_entry_month와 통일성을 맞춘다: management_plan_entries를
    # 월1~12 wide 컬럼 헤더에서 "헤더+디테일" 구조로 바꾸고, account_key/scope_key/category_key ->
    # account_code/scope_code/category_code로 리네임(19개 계정 공유 범용 컬럼 네이밍 통일),
    # label/label_en/anchor_key를 추가해 커스텀 행을 지원한다.
    # 기존 데이터는 테스트용 1건(_ETC/etc, 전부 0원)뿐이라 이전 없이 drop 후 재생성한다.
    op.drop_table('management_plan_entries')

    op.create_table(
        'management_plan_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('year', sa.SmallInteger(), nullable=False, comment='편성 기준 연도'),
        sa.Column('plan_no', sa.String(length=4), nullable=False, comment='경영계획 버전 (P00~P03 등)'),
        sa.Column('account_code', sa.String(length=32), nullable=False, comment='accounts.key ("overseas", "dep", ...) — 가장 큰 분류'),
        sa.Column('scope_code', sa.String(length=40), nullable=False, comment='계정 안의 행 그룹 식별자 (해외연구소는 연구소 pk, 없는 계정은 상수 "ALL")'),
        sa.Column('category_code', sa.String(length=40), nullable=False, comment='scope 안의 세부 비용항목 키. 고정 enum 아닌 자유 문자열(커스텀 행은 임의 id)'),
        sa.Column('label', sa.String(length=100), nullable=True, comment='커스텀 category_code의 표시명(한글). 고정 항목은 NULL'),
        sa.Column('label_en', sa.String(length=100), nullable=True, comment='커스텀 category_code의 표시명(영문). 고정 항목은 NULL'),
        sa.Column('anchor_key', sa.String(length=40), nullable=True, comment='커스텀 행을 끼워 넣을 고정 category_code 바로 뒤. NULL이면 표 맨 끝'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'),
        sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='최초 입력 시각'),
        sa.Column('created_by_id', sa.Integer(), nullable=True, comment='최초 입력한 사용자 (users.id)'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['account_code'], ['accounts.key'], name='fk_management_plan_entries_account_code_accounts'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_management_plan_entries_created_by_id_users'),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name='fk_management_plan_entries_updated_by_id_users'),
        sa.ForeignKeyConstraint(['year', 'plan_no'], ['management_plan_versions.year', 'management_plan_versions.plan_no'], name='fk_management_plan_entries_year_plan_no'),
        sa.UniqueConstraint('account_code', 'scope_code', 'category_code', 'year', 'plan_no', name='uq_management_plan_entries_account_scope_category_year_plan'),
        comment='계정×행×비용항목×경영계획버전 헤더. 실제 금액은 management_plan_entry_detail 참조. 19개 계정이 공유.',
    )

    op.create_table(
        'management_plan_entry_detail',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plan_entry_id', sa.Integer(), nullable=False, comment='management_plan_entries.id (헤더 삭제 시 디테일도 같이 삭제)'),
        sa.Column('year', sa.SmallInteger(), nullable=False, server_default='0', comment='이 금액이 속한 연도'),
        sa.Column('period_type', sa.String(length=10), nullable=False, comment='MONTH | QUARTER | YEAR | CUSTOM'),
        sa.Column('period', sa.SmallInteger(), nullable=False, server_default='0', comment='MONTH:1~12, QUARTER:1~4, YEAR:0, CUSTOM:0'),
        sa.Column('col_name', sa.String(length=100), nullable=False, server_default='', comment='CUSTOM일 때만 그 열의 라벨, 나머지는 ""'),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False, server_default='0', comment='금액'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'),
        sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['plan_entry_id'], ['management_plan_entries.id'], name='fk_management_plan_entry_detail_plan_entry_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name='fk_management_plan_entry_detail_updated_by_id_users'),
        sa.UniqueConstraint('plan_entry_id', 'year', 'period_type', 'period', 'col_name', name='uq_management_plan_entry_detail_entry_year_period_col'),
        sa.CheckConstraint("period_type IN ('MONTH', 'QUARTER', 'YEAR', 'CUSTOM')", name='ck_management_plan_entry_detail_period_type'),
        comment='management_plan_entries 헤더별 디테일 행(월·분기·연간·커스텀열).',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('management_plan_entry_detail')
    op.drop_table('management_plan_entries')

    op.create_table(
        'management_plan_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_key', sa.String(length=32), nullable=False, comment='accounts.key ("overseas", "dep", ...)'),
        sa.Column('scope_key', sa.String(length=40), nullable=False, comment='계정 안에서의 행 단위 식별자 (해외연구소는 연구소 pk)'),
        sa.Column('year', sa.SmallInteger(), nullable=False, comment='편성 기준 연도'),
        sa.Column('plan_no', sa.String(length=4), nullable=False, comment='경영계획 버전 (P00~P03 등)'),
        sa.Column('category_key', sa.String(length=40), nullable=False, comment='비용항목 키. 고정 enum 아닌 자유 문자열'),
        sa.Column('month1', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month2', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month3', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month4', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month5', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month6', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month7', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month8', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month9', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month10', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month11', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('month12', sa.Numeric(14, 2), nullable=False, server_default='0'),
        sa.Column('extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='엑셀 업로드에서 인식 못한 열 보관용'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_by_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['account_key'], ['accounts.key'], name='management_plan_entries_account_key_fkey'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='management_plan_entries_created_by_id_fkey'),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name='management_plan_entries_updated_by_id_fkey'),
        sa.ForeignKeyConstraint(['year', 'plan_no'], ['management_plan_versions.year', 'management_plan_versions.plan_no'], name='management_plan_entries_year_plan_no_fkey'),
        sa.UniqueConstraint('account_key', 'scope_key', 'year', 'plan_no', 'category_key', name='management_plan_entries_account_key_scope_key_year_plan_no__key'),
    )
