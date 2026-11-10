"""redesign cost_entries header and add cost_entry_months

Revision ID: 788a6d41f69c
Revises: ab904cae58dd
Create Date: 2026-11-04 22:54:40.648847

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '788a6d41f69c'
down_revision: Union[str, Sequence[str], None] = 'ab904cae58dd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # cost_entries는 아직 0건이라(어떤 계정도 안 씀) 데이터 이전 없이 구조만 바꾼다.
    # month1~month12(값)을 없애고 actual_thru_month -> month(편성 기준월)로 바꿔, cost_entries를
    # "편성 회차 헤더"로, 실제 월별 금액은 새 cost_entry_months(FK)로 분리한다.
    op.drop_constraint('uq_cost_entries_account_scope_version_category_year_thru', 'cost_entries', type_='unique')
    for m in range(1, 13):
        op.drop_column('cost_entries', f'month{m}')
    op.drop_column('cost_entries', 'actual_thru_month')
    op.add_column('cost_entries', sa.Column(
        'month', sa.SmallInteger(), nullable=False,
        comment="편성 기준월(1~12). cur 버전에서 같은 카테고리가 '실적'(기준월-1)과 '속보'(기준월)로 "
                "동시에 존재할 때 구분용으로도 쓰인다. 그 외 버전은 관례상 1(연초 1회 편성)",
    ))
    op.create_unique_constraint(
        'uq_cost_entries_account_scope_version_category_year_month', 'cost_entries',
        ['account_key', 'scope_key', 'version', 'category_key', 'year', 'month'],
    )
    op.alter_column('cost_entries', 'year', comment='편성 기준 연도')
    op.create_table_comment(
        'cost_entries',
        '계정×행×비용항목×버전×편성월 헤더. 실제 월별 금액은 cost_entry_months 참조. 19개 계정이 공유.',
        existing_comment='계정×행×비용항목×버전 비용 데이터(월은 month1~month12 컬럼). 19개 계정이 공유.',
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
        sa.UniqueConstraint('cost_entry_id', 'target_year', 'target_month'),
        comment='cost_entries 헤더별 월 금액 행. 당해 12개월 + 차년도(9월 속보~) 월이 함께 달릴 수 있다.',
    )
    op.create_foreign_key(
        'fk_cost_entry_months_cost_entry_id', 'cost_entry_months', 'cost_entries', ['cost_entry_id'], ['id'],
        ondelete='CASCADE',
    )
    op.create_foreign_key(
        'fk_cost_entry_months_updated_by_id_users', 'cost_entry_months', 'users', ['updated_by_id'], ['id'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_cost_entry_months_updated_by_id_users', 'cost_entry_months', type_='foreignkey')
    op.drop_constraint('fk_cost_entry_months_cost_entry_id', 'cost_entry_months', type_='foreignkey')
    op.drop_table('cost_entry_months')

    op.create_table_comment(
        'cost_entries',
        '계정×행×비용항목×버전 비용 데이터(월은 month1~month12 컬럼). 19개 계정이 공유.',
        existing_comment='계정×행×비용항목×버전×편성월 헤더. 실제 월별 금액은 cost_entry_months 참조. 19개 계정이 공유.',
    )
    op.alter_column('cost_entries', 'year', comment=None)
    op.drop_constraint('uq_cost_entries_account_scope_version_category_year_month', 'cost_entries', type_='unique')
    op.drop_column('cost_entries', 'month')
    op.add_column('cost_entries', sa.Column(
        'actual_thru_month', sa.SmallInteger(), nullable=False, server_default='0',
        comment="실적이 반영된 마지막 달(1~12). cur 버전에서 같은 카테고리가 '실적'(기준월-1)과 "
                "'속보'(기준월)로 동시에 존재할 때 구분용. 그 외 버전은 0(해당 없음)",
    ))
    for m in range(12, 0, -1):
        op.add_column('cost_entries', sa.Column(f'month{m}', sa.Numeric(14, 2), nullable=False, server_default='0', comment=f'{m}월 금액'))
    op.create_unique_constraint(
        'uq_cost_entries_account_scope_version_category_year_thru', 'cost_entries',
        ['account_key', 'scope_key', 'version', 'category_key', 'year', 'actual_thru_month'],
    )
