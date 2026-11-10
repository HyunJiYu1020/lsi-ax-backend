"""overseas cost lines label, drop dependency on shared custom_rows

Revision ID: df0623cb26bd
Revises: f64971392bc2
Create Date: 2026-11-08 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'df0623cb26bd'
down_revision: Union[str, Sequence[str], None] = 'f64971392bc2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'overseas_cost_lines',
        sa.Column(
            'label', sa.String(length=100), nullable=True,
            comment='사용자가 직접 추가한 행의 이름. 고정 계정(category_key가 OV_EL에 있는 키)은 NULL',
        ),
    )
    # 지금까지 overseas가 custom_rows(공용 테이블)에 쌓아둔 행이 있으면 label을 그대로 옮긴다.
    op.execute("""
        update overseas_cost_lines l
        set label = c.label
        from custom_rows c
        where c.account_key = 'overseas'
          and c.scope_key = l.scope_key and c.version = l.version and c.row_key = l.category_key
    """)
    op.execute("delete from custom_rows where account_key = 'overseas'")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("""
        insert into custom_rows (account_key, scope_key, version, row_key, label)
        select 'overseas', scope_key, version, category_key, label
        from overseas_cost_lines where label is not null
    """)
    op.drop_column('overseas_cost_lines', 'label')
