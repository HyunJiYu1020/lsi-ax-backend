"""redesign cost_entries master and cost_entry_month detail

Revision ID: df4276c24e85
Revises: d4e8b2f6a1c3
Create Date: 2026-11-10 18:53:41.102332

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'df4276c24e85'
down_revision: Union[str, Sequence[str], None] = 'd4e8b2f6a1c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # drop 전에 기존 데이터를 백업해 새 구조로 옮긴다(2deed0d55305처럼 "테스트 데이터뿐"인 경우가
    # 아니라 실제 편성 데이터가 쌓여 있으므로 그냥 버릴 수 없다).
    # version 매핑: cur(속보) -> forecast. prev(전실행)·prior(전년 실적)는 새 설계에서 actual
    # 스냅샷 자체에서 유도되는 값이라(460d75798f81 참고) 그 원본 데이터가 곧 actual이다 -> actual.
    # plan(경영계획)은 새 version CHECK(forecast|actual)로 옮길 수 없고, 경영계획은 이미
    # management_plan_entries가 따로 맡고 있어 여기서는 제외한다(옮기지 않아도 현재 코드가 이
    # 테이블에서 plan을 읽지 않는다).
    op.execute("""
        CREATE TEMP TABLE ce_backup ON COMMIT DROP AS
        SELECT id AS old_id,
               year::text || lpad(month::text, 2, '0') AS yyyymm,
               account_key AS account_code,
               scope_key AS scope_code,
               CASE version WHEN 'cur' THEN 'forecast' ELSE 'actual' END AS version,
               category_key AS category_code,
               created_at, created_by_id, updated_at, updated_by_id
        FROM cost_entries
        WHERE version IN ('cur', 'prev', 'prior')
    """)
    op.execute("""
        CREATE TEMP TABLE cem_backup ON COMMIT DROP AS
        SELECT ceb.old_id AS old_cost_entry_id, cem.target_year AS year, cem.target_month AS period, cem.amount
        FROM cost_entry_months cem
        JOIN ce_backup ceb ON ceb.old_id = cem.cost_entry_id
    """)

    op.drop_table('cost_entry_months')
    op.drop_table('cost_entries')

    op.create_table(
        'cost_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('yyyymm', sa.String(length=6), nullable=False, comment='편성 기준 연월 "YYYYMM" (예: "202609")'),
        sa.Column('account_code', sa.String(length=32), nullable=False, comment='accounts.key ("overseas", "dep", ...) — 가장 큰 분류'),
        sa.Column('scope_code', sa.String(length=40), nullable=False, comment='계정 안의 행 그룹 식별자 (해외연구소는 연구소 pk, 없는 계정은 상수 "ALL")'),
        sa.Column('version', sa.String(length=10), nullable=False, comment='forecast(속보) | actual(실적)'),
        sa.Column('category_code', sa.String(length=40), nullable=False, comment='scope 안의 세부 비용항목 키. 고정 enum 아닌 자유 문자열(커스텀 행은 임의 id)'),
        sa.Column('label', sa.String(length=100), nullable=True, comment='커스텀 category_code의 표시명(한글). 고정 항목은 NULL'),
        sa.Column('label_en', sa.String(length=100), nullable=True, comment='커스텀 category_code의 표시명(영문). 고정 항목은 NULL'),
        sa.Column('anchor_key', sa.String(length=40), nullable=True, comment='커스텀 행을 끼워 넣을 고정 category_code 바로 뒤. NULL이면 표 맨 끝'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'),
        sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='최초 입력 시각'),
        sa.Column('created_by_id', sa.Integer(), nullable=True, comment='최초 입력한 사용자 (users.id)'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['account_code'], ['accounts.key'], name='fk_cost_entries_account_code_accounts'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_cost_entries_created_by_id_users'),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name='fk_cost_entries_updated_by_id_users'),
        sa.UniqueConstraint('account_code', 'scope_code', 'category_code', 'yyyymm', 'version', name='uq_cost_entries_account_scope_category_yyyymm_version'),
        sa.CheckConstraint(r"yyyymm ~ '^\d{4}(0[1-9]|1[0-2])$'", name='ck_cost_entries_yyyymm_format'),
        sa.CheckConstraint("version IN ('forecast', 'actual')", name='ck_cost_entries_version'),
        comment='계정×행×비용항목×편성시점 헤더. 실제 월별 금액은 cost_entry_month 참조. 19개 계정이 공유.',
    )

    op.create_table(
        'cost_entry_month',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cost_entry_id', sa.Integer(), nullable=False, comment='cost_entries.id (헤더 삭제 시 디테일도 같이 삭제)'),
        sa.Column('year', sa.SmallInteger(), nullable=False, server_default='0', comment='이 금액이 속한 연도'),
        sa.Column('period_type', sa.String(length=10), nullable=False, comment='MONTH | QUARTER | YEAR | CUSTOM'),
        sa.Column('period', sa.SmallInteger(), nullable=False, server_default='0', comment='MONTH:1~12, QUARTER:1~4, YEAR:0, CUSTOM:0'),
        sa.Column('col_name', sa.String(length=100), nullable=False, server_default='', comment='CUSTOM일 때만 그 열의 라벨(옛 custom_fields.label), 나머지는 ""'),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False, server_default='0', comment='금액'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'),
        sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['cost_entry_id'], ['cost_entries.id'], name='fk_cost_entry_month_cost_entry_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name='fk_cost_entry_month_updated_by_id_users'),
        sa.UniqueConstraint('cost_entry_id', 'year', 'period_type', 'period', 'col_name', name='uq_cost_entry_month_cost_entry_year_period_col'),
        sa.CheckConstraint("period_type IN ('MONTH', 'QUARTER', 'YEAR', 'CUSTOM')", name='ck_cost_entry_month_period_type'),
        comment='cost_entries 헤더별 디테일 행(월·분기·연간·커스텀열). 분기·연간도 원본 숫자를 그대로 저장.',
    )

    op.execute("""
        INSERT INTO cost_entries (yyyymm, account_code, scope_code, version, category_code,
                                   created_at, created_by_id, updated_at, updated_by_id)
        SELECT yyyymm, account_code, scope_code, version, category_code,
               created_at, created_by_id, updated_at, updated_by_id
        FROM ce_backup
        ORDER BY old_id
        ON CONFLICT (account_code, scope_code, category_code, yyyymm, version) DO NOTHING
    """)
    # id가 새 시퀀스로 다시 매겨지므로 old_id가 아니라 자연키(scope·category·yyyymm·version)로 다시 조인한다.
    op.execute("""
        INSERT INTO cost_entry_month (cost_entry_id, year, period_type, period, col_name, amount)
        SELECT ce.id, cem.year, 'MONTH', cem.period, '', cem.amount
        FROM cem_backup cem
        JOIN ce_backup ceb ON ceb.old_id = cem.old_cost_entry_id
        JOIN cost_entries ce ON ce.account_code = ceb.account_code AND ce.scope_code = ceb.scope_code
            AND ce.category_code = ceb.category_code AND ce.yyyymm = ceb.yyyymm AND ce.version = ceb.version
        ON CONFLICT (cost_entry_id, year, period_type, period, col_name) DO NOTHING
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('cost_entry_month')
    op.drop_table('cost_entries')

    op.create_table(
        'cost_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_key', sa.String(length=32), nullable=False, comment='accounts.key ("overseas", "dep", ...)'),
        sa.Column('scope_key', sa.String(length=40), nullable=False, comment='계정 안에서의 행 단위 식별자 (해외연구소는 연구소 pk)'),
        sa.Column('version', sa.String(length=10), nullable=False, comment='cur(당월 속보) | prev(전실행) | prior(전년 실적) | plan(경영계획)'),
        sa.Column('category_key', sa.String(length=40), nullable=False, comment='비용항목 키. 고정 enum 아닌 자유 문자열'),
        sa.Column('year', sa.SmallInteger(), nullable=False, comment='편성 기준 연도'),
        sa.Column('month', sa.SmallInteger(), nullable=False, comment="편성 기준월(1~12). cur 버전에서 같은 카테고리가 '실적'(기준월-1)과 '속보'(기준월)로 동시에 존재할 때 구분용으로도 쓰인다. 그 외 버전은 관례상 1(연초 1회 편성)"),
        sa.Column('extra', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='엑셀 업로드에서 인식 못한 열 보관용'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='최초 입력 시각'),
        sa.Column('created_by_id', sa.Integer(), nullable=True, comment='최초 입력한 사용자 (users.id)'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'),
        sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['account_key'], ['accounts.key'], name='fk_cost_entries_account_key_accounts'),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name='fk_cost_entries_created_by_id_users'),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name='fk_cost_entries_updated_by_id_users'),
        sa.UniqueConstraint('account_key', 'scope_key', 'version', 'category_key', 'year', 'month', name='uq_cost_entries_account_scope_version_category_year_month'),
        comment='계정×행×비용항목×버전×편성월 헤더. 실제 월별 금액은 cost_entry_months 참조. 19개 계정이 공유.',
    )

    op.create_table(
        'cost_entry_months',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cost_entry_id', sa.Integer(), nullable=False, comment='cost_entries.id (헤더 삭제 시 월별 금액도 같이 삭제)'),
        sa.Column('target_year', sa.SmallInteger(), nullable=False, comment='이 금액이 속한 연도 (당해 또는 차년도)'),
        sa.Column('target_month', sa.SmallInteger(), nullable=False, comment='이 금액이 속한 달(1~12)'),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False, server_default='0', comment='금액'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'),
        sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['cost_entry_id'], ['cost_entries.id'], name='fk_cost_entry_months_cost_entry_id', ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name='fk_cost_entry_months_updated_by_id_users'),
        sa.UniqueConstraint('cost_entry_id', 'target_year', 'target_month'),
        comment='cost_entries 헤더별 월 금액 행. 당해 12개월 + 차년도(9월 속보~) 월이 함께 달릴 수 있다.',
    )
