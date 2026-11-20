"""overseas_lab_revisions — 연구소 기준정보의 시점별 이력

기준정보를 바꿨을 때 지난 버전 숫자가 소급해서 바뀌지 않도록, "언제부터 이 값이었는지"를 남긴다
(공통 RULE: 기준정보 변경은 변경한 버전부터 적용, 지난 버전 영향 없음).

Revision ID: c3a9f1e07b52
Revises: f2b7c9d14e08
Create Date: 2026-11-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3a9f1e07b52"
down_revision: Union[str, None] = "f2b7c9d14e08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "overseas_lab_revisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "lab_code",
            sa.String(length=10),
            nullable=False,
            comment="연구소 코드(overseas_labs.lab_code). 파트가 여러 개면 파트 전부에 같이 적용된다",
        ),
        sa.Column(
            "effective_from",
            sa.String(length=7),
            nullable=False,
            comment="이 값이 적용되기 시작하는 편성 기준연월 \"YYYY-MM\". '0000-01'은 \"처음부터\"(바뀌기 전 값)를 뜻한다",
        ),
        sa.Column("name", sa.String(length=50), nullable=False, comment="그 시점의 연구소명"),
        sa.Column("currency", sa.String(length=3), nullable=False, comment="그 시점의 현지통화"),
        sa.Column("is_active", sa.Boolean(), nullable=False, comment="그 시점의 사용 여부"),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False, comment="그 시점의 표시 순서"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="이 이력이 기록된 시각",
        ),
        sa.CheckConstraint(
            r"effective_from ~ '^\d{4}-(0[1-9]|1[0-2])$'",
            name="ck_overseas_lab_revisions_effective_from_format",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lab_code", "effective_from", name="uq_overseas_lab_revisions_key"),
        comment=(
            "연구소 기준정보(이름·통화·사용여부·순서)의 시점별 이력. 어떤 버전을 볼 때 effective_from이 "
            "그 버전 시점 이하인 가장 최근 행의 값을 쓴다 — 기준정보를 바꿔도 지난 버전 숫자가 소급해서 "
            "바뀌지 않게 하려는 테이블."
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("overseas_lab_revisions")
