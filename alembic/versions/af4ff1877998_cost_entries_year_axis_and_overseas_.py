"""cost_entries year axis and overseas cost lines wide format

Revision ID: af4ff1877998
Revises: 09ce2c08b2de
Create Date: 2026-11-04 11:29:58.781245

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'af4ff1877998'
down_revision: Union[str, Sequence[str], None] = '4cdb2228af6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MONTH_PIVOT = ", ".join(f"max(amount) filter (where month={m}) as month{m}" for m in range(1, 13))
MONTH_SET = ", ".join(f"month{m} = coalesce(p.month{m}, 0)" for m in range(1, 13))


def upgrade() -> None:
    """Upgrade schema."""
    # cost_entries: year를 행 축으로 추가 — 같은 계정×행×버전×비용항목이라도 연도가 다르면 별도 행.
    op.add_column('cost_entries', sa.Column('year', sa.SmallInteger(), nullable=False, comment='연도'))
    op.drop_constraint('uq_cost_entries_account_scope_version_category_thru', 'cost_entries', type_='unique')
    op.create_unique_constraint(
        'uq_cost_entries_account_scope_version_category_year_thru',
        'cost_entries',
        ['account_key', 'scope_key', 'version', 'category_key', 'year', 'actual_thru_month'],
    )

    # overseas_cost_lines: month1~month12 컬럼 추가, overseas_cost_months에서 값 옮기고 자식 테이블 제거.
    for m in range(1, 13):
        op.add_column(
            'overseas_cost_lines',
            sa.Column(f'month{m}', sa.Numeric(14, 2), nullable=False, server_default='0', comment=f'{m}월 금액'),
        )
    op.execute(f"""
        update overseas_cost_lines l
        set {MONTH_SET}
        from (select line_id, {MONTH_PIVOT} from overseas_cost_months group by line_id) p
        where l.id = p.line_id
    """)
    op.drop_table('overseas_cost_months')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        'overseas_cost_months',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('line_id', sa.Integer(), nullable=False, comment='overseas_cost_lines.id'),
        sa.Column('year', sa.SmallInteger(), nullable=False, comment='연도'),
        sa.Column('month', sa.SmallInteger(), nullable=False, comment='월(1~12)'),
        sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False, comment='금액'),
        sa.ForeignKeyConstraint(['line_id'], ['overseas_cost_lines.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('line_id', 'year', 'month'),
    )
    op.execute("""
        insert into overseas_cost_months (line_id, year, month, amount)
        select id, 2026, m.month, m.amount
        from overseas_cost_lines
        cross join lateral (values (1, month1), (2, month2), (3, month3), (4, month4), (5, month5),
            (6, month6), (7, month7), (8, month8), (9, month9), (10, month10), (11, month11), (12, month12)
        ) as m(month, amount)
    """)
    for m in range(1, 13):
        op.drop_column('overseas_cost_lines', f'month{m}')

    op.drop_constraint('uq_cost_entries_account_scope_version_category_year_thru', 'cost_entries', type_='unique')
    op.create_unique_constraint(
        'uq_cost_entries_account_scope_version_category_thru',
        'cost_entries',
        ['account_key', 'scope_key', 'version', 'category_key', 'actual_thru_month'],
    )
    op.drop_column('cost_entries', 'year')
