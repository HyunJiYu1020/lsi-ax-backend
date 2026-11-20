"""version_account_status — 버전×계정의 계정 확정 상태·일시

19개 계정 담당자가 각자 자기 계정을 확정하고, 전부 완료되면 버전을 확정한다. 누가 언제 확정했는지는
여러 사람이 같이 보는 값이라 브라우저에 둘 수 없어서 서버에 둔다. note에는 "미업로드 2개 포함 확정"
처럼 무엇을 알고도 확정했는지를 남긴다.

Revision ID: f4a6b8e2d710
Revises: e8f1c4d72a93
Create Date: 2026-11-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4a6b8e2d710"
down_revision: Union[str, None] = "e8f1c4d72a93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "version_account_status",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "version_id",
            sa.Integer(),
            nullable=False,
            comment="versions.id (버전을 지우면 계정 상태도 같이 지운다)",
        ),
        sa.Column("account_code", sa.String(length=32), nullable=False, comment="accounts.key (overseas, dep, outsource ...)"),
        sa.Column(
            "status",
            sa.String(length=10),
            nullable=False,
            server_default="drafting",
            comment="drafting(작성 중) | done(완료)",
        ),
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="완료로 바꾼 시각. 작성중으로 되돌리면 NULL로 지운다",
        ),
        sa.Column(
            "note",
            sa.String(length=200),
            nullable=False,
            server_default="",
            comment='확정 당시 단서("미업로드 2개 포함 확정" 등). 작성중으로 되돌리면 비운다',
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="마지막 갱신 시각",
        ),
        sa.CheckConstraint("status IN ('drafting', 'done')", name="ck_version_account_status_status"),
        sa.ForeignKeyConstraint(["version_id"], ["versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "account_code", name="uq_version_account_status_key"),
        comment="버전×계정의 계정 확정 상태·확정 일시·확정 당시 단서(note). 행이 없으면 작성 중.",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("version_account_status")
