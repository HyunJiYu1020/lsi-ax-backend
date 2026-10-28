"""pivot cost_entries and fx_rates month rows to month1 month12 columns

Revision ID: c4bf654f2994
Revises: 6848d4480249
Create Date: 2026-10-28 19:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4bf654f2994'
down_revision: Union[str, Sequence[str], None] = '6848d4480249'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MONTH_COLS = ", ".join(f"month{m} numeric(14,2) not null default 0" for m in range(1, 13))
FX_MONTH_COLS = ", ".join(f"month{m} numeric(12,4) not null default 0" for m in range(1, 13))
COST_PIVOT_SELECT = ", ".join(
    f"coalesce(max(amount) filter (where month={m}), 0) as month{m}" for m in range(1, 13)
)
FX_PIVOT_SELECT = ", ".join(
    f"coalesce(max(value) filter (where month={m}), 0) as month{m}" for m in range(1, 13)
)
COST_UNPIVOT = " union all ".join(
    f"select account_key, scope_key, version, category_key, {m} as month, month{m} as amount, "
    f"extra, created_at, created_by_id, updated_at, updated_by_id from cost_entries"
    for m in range(1, 13)
)
FX_UNPIVOT = " union all ".join(
    f"select version, currency, rate_type, {m} as month, month{m} as value from fx_rates" for m in range(1, 13)
)


def upgrade() -> None:
    """Upgrade schema."""
    # ---- cost_entries: 12행(월) -> 1행(카테고리, month1~month12 컬럼) ----
    op.execute(f"""
        create temp table cost_entries_pivot on commit drop as
        select account_key, scope_key, version, category_key,
            {COST_PIVOT_SELECT},
            (array_agg(extra) filter (where extra is not null))[1] as extra,
            min(created_at) as created_at,
            (array_agg(created_by_id order by created_at asc))[1] as created_by_id,
            max(updated_at) as updated_at,
            (array_agg(updated_by_id order by updated_at desc))[1] as updated_by_id
        from cost_entries
        group by account_key, scope_key, version, category_key
    """)
    op.execute("delete from cost_entries")
    op.drop_constraint('cost_entries_account_key_scope_key_version_category_key_mon_key', 'cost_entries', type_='unique')
    op.drop_column('cost_entries', 'month')
    op.drop_column('cost_entries', 'amount')
    for m in range(1, 13):
        op.add_column('cost_entries', sa.Column(f'month{m}', sa.Numeric(14, 2), nullable=False, server_default='0', comment=f'{m}월 금액'))
    op.execute("""
        insert into cost_entries
            (account_key, scope_key, version, category_key,
             month1, month2, month3, month4, month5, month6, month7, month8, month9, month10, month11, month12,
             extra, created_at, created_by_id, updated_at, updated_by_id)
        select account_key, scope_key, version, category_key,
            month1, month2, month3, month4, month5, month6, month7, month8, month9, month10, month11, month12,
            extra, created_at, created_by_id, updated_at, updated_by_id
        from cost_entries_pivot
    """)
    op.create_unique_constraint(
        'uq_cost_entries_account_scope_version_category', 'cost_entries',
        ['account_key', 'scope_key', 'version', 'category_key'],
    )

    # ---- fx_rates: 12행(월) -> 1행(통화×종류, month1~month12 컬럼) ----
    op.execute(f"""
        create temp table fx_rates_pivot on commit drop as
        select version, currency, rate_type, {FX_PIVOT_SELECT}
        from fx_rates
        group by version, currency, rate_type
    """)
    op.execute("delete from fx_rates")
    op.drop_constraint('fx_rates_version_currency_rate_type_month_key', 'fx_rates', type_='unique')
    op.drop_column('fx_rates', 'month')
    op.drop_column('fx_rates', 'value')
    for m in range(1, 13):
        op.add_column('fx_rates', sa.Column(f'month{m}', sa.Numeric(12, 4), nullable=False, server_default='0', comment=f'{m}월 환율'))
    op.execute("""
        insert into fx_rates
            (version, currency, rate_type,
             month1, month2, month3, month4, month5, month6, month7, month8, month9, month10, month11, month12)
        select version, currency, rate_type,
            month1, month2, month3, month4, month5, month6, month7, month8, month9, month10, month11, month12
        from fx_rates_pivot
    """)
    op.create_unique_constraint('uq_fx_rates_version_currency_rate_type', 'fx_rates', ['version', 'currency', 'rate_type'])

    op.execute("COMMENT ON TABLE cost_entries IS '계정×행×비용항목×버전 비용 데이터(월은 month1~month12 컬럼). 19개 계정이 공유.'")
    op.execute("COMMENT ON TABLE fx_rates IS '버전×통화×환율종류 환율 기준정보(월은 month1~month12 컬럼). 해외연구소 원화 환산·환차 분석에 쓰인다.'")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("COMMENT ON TABLE fx_rates IS '버전×통화×환율종류×월 환율 기준정보. 해외연구소 원화 환산·환차 분석에 쓰인다.'")
    op.execute("COMMENT ON TABLE cost_entries IS '계정×행×비용항목×월×버전 비용 데이터. 19개 계정이 공유.'")
    # ---- fx_rates: month1~month12 컬럼 -> 12행(월) ----
    op.execute(f"""
        create temp table fx_rates_unpivot on commit drop as
        {FX_UNPIVOT}
    """)
    op.execute("delete from fx_rates")
    op.drop_constraint('uq_fx_rates_version_currency_rate_type', 'fx_rates', type_='unique')
    for m in range(1, 13):
        op.drop_column('fx_rates', f'month{m}')
    op.add_column('fx_rates', sa.Column('month', sa.SmallInteger(), nullable=False, comment='1~12'))
    op.add_column('fx_rates', sa.Column('value', sa.Numeric(12, 4), nullable=False, comment='환율값'))
    op.execute("insert into fx_rates (version, currency, rate_type, month, value) select * from fx_rates_unpivot")
    op.create_unique_constraint(
        'fx_rates_version_currency_rate_type_month_key', 'fx_rates', ['version', 'currency', 'rate_type', 'month']
    )

    # ---- cost_entries: month1~month12 컬럼 -> 12행(월) ----
    op.execute(f"""
        create temp table cost_entries_unpivot on commit drop as
        {COST_UNPIVOT}
    """)
    op.execute("delete from cost_entries")
    op.drop_constraint('uq_cost_entries_account_scope_version_category', 'cost_entries', type_='unique')
    for m in range(1, 13):
        op.drop_column('cost_entries', f'month{m}')
    op.add_column('cost_entries', sa.Column('month', sa.SmallInteger(), nullable=False, comment='1~12'))
    op.add_column('cost_entries', sa.Column('amount', sa.Numeric(14, 2), nullable=False, server_default='0', comment='금액'))
    op.execute("""
        insert into cost_entries
            (account_key, scope_key, version, category_key, month, amount,
             extra, created_at, created_by_id, updated_at, updated_by_id)
        select account_key, scope_key, version, category_key, month, amount,
            extra, created_at, created_by_id, updated_at, updated_by_id
        from cost_entries_unpivot
    """)
    op.create_unique_constraint(
        'cost_entries_account_key_scope_key_version_category_key_month_key', 'cost_entries',
        ['account_key', 'scope_key', 'version', 'category_key', 'month'],
    )
