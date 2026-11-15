"""overseas_row_aliases — 업로드 행 이름 → 이미 있는 행 매핑(사람이 확인한 결과)

연구소마다 같은 비용을 다른 이름으로 적어 보낸다('Travel Exp' vs 'Travel Expense'). 업로드할 때
비슷한 이름을 찾아 "같은 행인가요?"를 묻고, 그 대답을 여기 남겨서 다음 달부터 다시 묻지 않는다.
"별개 행"이라는 대답(distinct)도 같이 남긴다 — 그래야 그 질문도 반복되지 않는다.

Revision ID: e8f1c4d72a93
Revises: d7e4b2a9c501
Create Date: 2026-11-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8f1c4d72a93"
down_revision: Union[str, None] = "d7e4b2a9c501"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "overseas_row_aliases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "alias_norm",
            sa.String(length=100),
            nullable=False,
            comment="파일에 적혀 온 이름을 정규화한 값(공백·기호 제거 + 소문자). 이게 매칭 키다",
        ),
        sa.Column(
            "alias_label",
            sa.String(length=100),
            nullable=False,
            comment="파일에 적혀 온 이름 원문(보관용 — 어떤 표기에 답한 건지 되짚을 때)",
        ),
        sa.Column(
            "target_kind",
            sa.String(length=12),
            nullable=False,
            comment="category(고정 계정과 같은 행) | extra_row(이미 있는 추가 행과 같은 행) | distinct(별개 행)",
        ),
        sa.Column(
            "target_key",
            sa.String(length=100),
            nullable=False,
            server_default="",
            comment="category면 비용항목 키(travel 등), extra_row면 정식 이름의 정규화값. distinct면 빈 값",
        ),
        sa.Column(
            "target_label",
            sa.String(length=100),
            nullable=False,
            server_default="",
            comment="합쳐 넣을 정식 이름(화면·집계에서 쓰는 이름). distinct면 빈 값",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="처음 답한 시각",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="답을 바꿔서 덮어쓴 시각",
        ),
        sa.CheckConstraint(
            "target_kind IN ('category', 'extra_row', 'distinct')", name="ck_overseas_row_aliases_target_kind"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alias_norm", name="uq_overseas_row_aliases_norm"),
        comment=(
            "업로드 파일의 행 이름 → 이미 있는 행 매핑(사람이 확인한 결과). "
            "target_kind=category(고정 계정) | extra_row(추가 행) | distinct(별개 행이라고 답함)."
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("overseas_row_aliases")
