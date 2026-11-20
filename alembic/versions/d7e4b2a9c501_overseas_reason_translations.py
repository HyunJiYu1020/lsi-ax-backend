"""overseas_reason_translations — 증감사유 영문 원문 → 한국어 번역 캐시

증감분석 화면은 사유 수십 건을 한 번에 그려서 번역을 매번 LLM에 물을 수 없고(느리고 비싸고),
같은 원문이 매번 다르게 번역되면 보고 문구가 흔들린다. 한 번 번역한 원문은 이 표의 값을 쓴다.
원문을 같이 남기는 건 공통 RULE(원문 보관) 때문이다.

Revision ID: d7e4b2a9c501
Revises: c3a9f1e07b52
Create Date: 2026-11-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7e4b2a9c501"
down_revision: Union[str, None] = "c3a9f1e07b52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "overseas_reason_translations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "source_hash",
            sa.String(length=64),
            nullable=False,
            comment="정규화(연속 공백 1칸)한 원문의 SHA-256 16진 문자열 — 같은 문장을 두 번 번역하지 않게 하는 키",
        ),
        sa.Column(
            "source_text",
            sa.String(length=600),
            nullable=False,
            comment="정규화한 영문 원문(보관용). 번역이 이상할 때 되짚는 근거",
        ),
        sa.Column("ko_text", sa.String(length=600), nullable=False, comment="한국어 번역문(보고용 명사형 간결체)"),
        sa.Column(
            "engine",
            sa.String(length=40),
            nullable=False,
            server_default="",
            comment='번역한 모델("gemini:gemini-3.5-flash-lite" 등). 모델을 바꾼 뒤 옛 번역을 구분·재번역할 때 쓴다',
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="처음 번역한 시각",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="다시 번역해서 덮어쓴 시각",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_hash", name="uq_overseas_reason_translations_hash"),
        comment="해외연구소 증감사유 영문 원문 → 한국어 번역 캐시(정규화한 원문 해시로 식별, 원문 같이 보관).",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("overseas_reason_translations")
