"""cost entry details custom cell data_type value

추가 열(CUSTOM) 칸의 원문 보관. 연구소 파일의 양식 밖 열에는 금액·비율·글자가 섞여 오는데,
지금까지는 amount(Numeric(14,2)) 하나뿐이라 글자는 버려지고 숫자도 소수 2자리에서 깎였다
(0.825 → 0.83). 원문을 value에 글자 그대로 두고 data_type(number|text)으로 타입을 박아,
읽는 쪽이 파싱 없이 분기만 하게 한다 — 숫자면 계산에 쓰고 글자면 그대로 그린다.

기존 행은 전부 숫자(amount가 참값)라 server_default='number' + value NULL로 백필이 끝난다 —
value가 NULL이면 amount가 참값이라는 규칙이 과거 저장분 호환을 겸한다.

Revision ID: 8569dfc97bdd
Revises: c1d4e7f20a93
Create Date: 2026-11-23 09:58:15.537462

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8569dfc97bdd'
down_revision: Union[str, Sequence[str], None] = 'c1d4e7f20a93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "cost_entry_details",
        sa.Column(
            "data_type", sa.String(10), nullable=False, server_default=sa.text("'number'"),
            comment="number | text — 읽는 쪽 분기 기준. CUSTOM 칸만 text가 될 수 있다",
        ),
    )
    op.add_column(
        "cost_entry_details",
        sa.Column(
            "value", sa.String(500), nullable=True,
            comment='CUSTOM 칸의 원문("0.825" · "환율 반영"). NULL이면 amount가 참값(월·분기·연간 행과 과거 저장분)',
        ),
    )
    op.create_check_constraint(
        "ck_cost_entry_details_data_type", "cost_entry_details", "data_type IN ('number', 'text')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_cost_entry_details_data_type", "cost_entry_details", type_="check")
    op.drop_column("cost_entry_details", "value")
    op.drop_column("cost_entry_details", "data_type")
