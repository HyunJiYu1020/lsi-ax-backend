"""overseas cost entries year not null

Revision ID: 576b6b9dc751
Revises: 38bec768cca3
Create Date: 2026-11-09 13:01:15.462925

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '576b6b9dc751'
down_revision: Union[str, Sequence[str], None] = '38bec768cca3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # custom_fields만 있고 금액은 없던 "앵커" 행들(이전 스키마에서 year 개념이 없던 행)에 연도를 채운다.
    # 이 시점(2026-11-09)에 실제로 쌓여있던 건 cur 버전 테스트 데이터뿐이라 기준연도 2026으로 백필한다.
    op.execute("UPDATE overseas_cost_entries SET year = 2026 WHERE year IS NULL")
    op.alter_column(
        'overseas_cost_entries', 'year',
        existing_type=sa.SmallInteger(),
        nullable=False,
        comment='이 금액이 속한 연도. custom_fields만 있는 행도 예외 없이 항상 있어야 함',
        existing_comment='이 금액이 속한 연도',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'overseas_cost_entries', 'year',
        existing_type=sa.SmallInteger(),
        nullable=True,
        comment='이 금액이 속한 연도',
        existing_comment='이 금액이 속한 연도. custom_fields만 있는 행도 예외 없이 항상 있어야 함',
    )
