"""overseas_carry_defs — 다음 버전에도 유지하기로 한 집계 파생 행·열

파생 행·열은 버전×기준연월 단위라 새 버전을 열면 사라진다. 매달 보는 지표는 계속 따라가야 하고,
그 달만 보려고 만든 지표는 따라가면 안 되므로, 만들 때 한 번 묻고 승인한 것만 여기 남겨 복제한다.

Revision ID: a1c5d93f6b24
Revises: f4a6b8e2d710
Create Date: 2026-11-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c5d93f6b24"
down_revision: Union[str, None] = "f4a6b8e2d710"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "overseas_carry_defs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=3), nullable=False, comment="row(파생 행) | col(파생 열)"),
        sa.Column("def_key", sa.String(length=100), nullable=False, comment="파생 행이면 row_key, 파생 열이면 label"),
        sa.Column(
            "label",
            sa.String(length=100),
            nullable=False,
            server_default="",
            comment="사람이 읽을 이름(어떤 지표를 유지 중인지 보여줄 때)",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="승인한 시각",
        ),
        sa.CheckConstraint("kind IN ('row', 'col')", name="ck_overseas_carry_defs_kind"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "def_key", name="uq_overseas_carry_defs_key"),
        comment="다음 버전에도 유지하기로 승인한 집계 파생 행·열. 새 버전을 열 때 이 정의만 복제한다.",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("overseas_carry_defs")
