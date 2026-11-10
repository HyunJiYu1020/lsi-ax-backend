"""overseas cost lines normalize months into overseas_cost_months

Revision ID: f64971392bc2
Revises: 0e2e2f6f3e34
Create Date: 2026-11-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f64971392bc2'
down_revision: Union[str, Sequence[str], None] = '0e2e2f6f3e34'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 백필용 — overseas_cost_lines엔 지금 연도 컬럼이 없어 version만으로는 몇 년도 값인지 DB가 모른다.
# 이 시점(2026-11-08) 기준 실제 작업 연도로 하드코딩한다(일회성). 이후로는 overseas_cost_months가
# 진짜 year 컬럼을 가지므로 다시는 이 추측이 필요없다.
YEAR_BY_ROLE = {"cur": 2026, "prev": 2026, "plan": 2026, "prior": 2025}

MONTH_PIVOT = ", ".join(f"max(amount) filter (where month={m}) as month{m}" for m in range(1, 13))


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'overseas_cost_months',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('line_id', sa.Integer(), nullable=False, comment='overseas_cost_lines.id (헤더 삭제 시 월별 금액도 같이 삭제)'),
        sa.Column('year', sa.SmallInteger(), nullable=False, comment='연도'),
        sa.Column('month', sa.SmallInteger(), nullable=False, comment='월(1~12)'),
        sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False, comment='금액'),
        sa.ForeignKeyConstraint(['line_id'], ['overseas_cost_lines.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('line_id', 'year', 'month'),
        comment='overseas_cost_lines 한 행의 연·월별 금액. 연도 경계 없이 임의 개수로 쌓인다.',
    )

    conn = op.get_bind()
    lines = conn.execute(
        sa.text(
            "select id, version from overseas_cost_lines"
        )
    ).mappings().all()
    new_months = [
        {"line_id": row["id"], "year": YEAR_BY_ROLE.get(row["version"], 2026), "month": m}
        for row in lines
        for m in range(1, 13)
    ]
    if new_months:
        months_table = sa.table(
            "overseas_cost_months",
            sa.column("line_id"), sa.column("year"), sa.column("month"), sa.column("amount"),
        )
        # month{m} 값을 line_id별로 다시 읽어와 끼워 넣는다(라인 수가 많지 않아 1건씩 조회해도 충분).
        for row in lines:
            amounts = conn.execute(
                sa.text(f"select {', '.join(f'month{m}' for m in range(1, 13))} from overseas_cost_lines where id = :id"),
                {"id": row["id"]},
            ).fetchone()
            conn.execute(
                sa.insert(months_table),
                [
                    {
                        "line_id": row["id"],
                        "year": YEAR_BY_ROLE.get(row["version"], 2026),
                        "month": m,
                        "amount": amounts[m - 1],
                    }
                    for m in range(1, 13)
                ],
            )

    for m in range(1, 13):
        op.drop_column('overseas_cost_lines', f'month{m}')


def downgrade() -> None:
    """Downgrade schema.

    주의: upgrade 직후 상태를 되돌리는 용도로만 쓸 것 — year가 YEAR_BY_ROLE 추정과 다른 값으로
    새로 쌓인 뒤에 downgrade하면 month1~12 한 벌로만 되돌릴 수 있어 나머지 연도 데이터는 버려진다.
    """
    for m in range(1, 13):
        op.add_column(
            'overseas_cost_lines',
            sa.Column(f'month{m}', sa.Numeric(14, 2), nullable=False, server_default='0', comment=f'{m}월 금액'),
        )
    op.execute(f"""
        update overseas_cost_lines l
        set {", ".join(f"month{m} = coalesce(p.month{m}, 0)" for m in range(1, 13))}
        from (select line_id, {MONTH_PIVOT} from overseas_cost_months group by line_id) p
        where l.id = p.line_id
    """)
    op.drop_table('overseas_cost_months')
