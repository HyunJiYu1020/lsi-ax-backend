"""extra cols fmt — 추가 열의 표시 형식

양식 밖 열(비고 · 사용률 …)에는 글자·금액·비율이 섞여 온다. 비율은 연구소마다 적는 모양이
달라서("0.825" · "82.5" · "82.5%") 화면이 값만 보고 맞힐 수 없다 — 0.825를 82.5%로 고쳐
그리면 소수로 적은 연구소는 맞고 이미 퍼센트로 적은 연구소는 100배가 된다.

그래서 **열마다 사람이 한 번 고른 형식**을 열에 달아 둔다. 값은 원문 그대로 두고(칸 값은
cost_entry_details) 그리는 방법만 이 칸이 정한다 — 글자 값은 어떤 형식이든 원문 그대로다.

raw        그대로(기본 · 지금까지의 동작)
pct_ratio  소수를 백분율로: 0.825 → 82.5%
pct_plain  이미 퍼센트로 적어 보낸 숫자에 기호만: 82.5 → 82.5%

Revision ID: f2a9c1d4b7e6
Revises: 8569dfc97bdd
Create Date: 2026-11-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a9c1d4b7e6'
down_revision: Union[str, Sequence[str], None] = '8569dfc97bdd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "extra_cols",
        sa.Column(
            "fmt", sa.String(16), nullable=False, server_default=sa.text("'raw'"),
            comment="칸을 그리는 형식: raw(그대로) | pct_ratio(0.825→82.5%) | pct_plain(82.5→82.5%). 글자 값은 형식과 무관하게 원문 그대로",
        ),
    )
    op.create_check_constraint(
        "ck_extra_cols_fmt", "extra_cols", "fmt IN ('raw', 'pct_ratio', 'pct_plain')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_extra_cols_fmt", "extra_cols", type_="check")
    op.drop_column("extra_cols", "fmt")
