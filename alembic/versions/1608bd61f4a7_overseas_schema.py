"""overseas schema

Revision ID: 1608bd61f4a7
Revises: 
Create Date: 2026-10-23 09:06:57.598277

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '1608bd61f4a7'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("data_type", sa.String(20), nullable=False),
        sa.Column("color", sa.String(16), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="작성중"),
        sa.Column("memo", sa.Text(), nullable=True),
    )

    op.create_table(
        "account_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.SmallInteger(), sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("step_order", sa.SmallInteger(), nullable=False),
        sa.Column("tag", sa.String(20), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.UniqueConstraint("account_id", "step_order"),
    )

    op.create_table(
        "account_progress",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.SmallInteger(), nullable=False, unique=True),
        sa.Column("current_step_order", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(
            ["account_id", "current_step_order"],
            ["account_steps.account_id", "account_steps.step_order"],
        ),
    )

    op.create_table(
        "overseas_labs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pk", sa.String(20), nullable=False, unique=True),
        sa.Column("lab_code", sa.String(10), nullable=False),
        sa.Column("part_code", sa.String(10), nullable=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("country", sa.String(20), nullable=False),
    )

    op.create_table(
        "overseas_cost_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lab_id", sa.Integer(), sa.ForeignKey("overseas_labs.id"), nullable=False),
        sa.Column("version", sa.String(10), nullable=False),
        sa.Column("category_key", sa.String(40), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("extra", postgresql.JSONB(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("lab_id", "version", "category_key", "month"),
    )

    op.create_table(
        "overseas_uploads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lab_id", sa.Integer(), sa.ForeignKey("overseas_labs.id"), nullable=False),
        sa.Column("version", sa.String(10), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.String(10), nullable=False),
        sa.Column("currency", sa.String(6), nullable=False),
        sa.Column("rate_type", sa.String(10), nullable=False),
        sa.Column("month", sa.SmallInteger(), nullable=False),
        sa.Column("value", sa.Numeric(12, 4), nullable=False),
        sa.UniqueConstraint("version", "currency", "rate_type", "month"),
    )

    op.create_table(
        "overseas_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("base_version", sa.String(10), nullable=False),
        sa.Column("period", sa.String(4), nullable=False),
        sa.Column("lab_scope", sa.String(20), nullable=False),
        sa.Column("category_key", sa.String(40), nullable=False),
        sa.Column("note_text", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("base_version", "period", "lab_scope", "category_key"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("overseas_notes")
    op.drop_table("fx_rates")
    op.drop_table("overseas_uploads")
    op.drop_table("overseas_cost_entries")
    op.drop_table("overseas_labs")
    op.drop_table("account_progress")
    op.drop_table("account_steps")
    op.drop_table("accounts")
