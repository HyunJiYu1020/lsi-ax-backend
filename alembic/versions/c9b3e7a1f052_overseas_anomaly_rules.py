"""overseas_anomaly_rules / _logs — 이상 감지 기준과 그 변경 이력

기준을 코드에 박으면 못 바꾸고, 브라우저에 두면 창을 닫으면 날아가고 사람마다 다르게 보인다.
챗봇에서 "이제 50% 넘는것만 알려줘"로 바꾸면 이 표가 바뀌고 해외연구소 전체에 적용된다.
기준이 바뀌면 경고 개수가 달라지므로 before/after를 이력으로 남긴다.

Revision ID: c9b3e7a1f052
Revises: b5d8c2f10e47
Create Date: 2026-11-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9b3e7a1f052"
down_revision: Union[str, None] = "b5d8c2f10e47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "overseas_anomaly_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "rule_key",
            sa.String(length=20),
            nullable=False,
            comment="jump(급변) | sum_mismatch(합계 불일치) | missing_reason(사유 누락) | unit_suspect(단위 의심)",
        ),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.true(), comment="사용 여부. false면 그 기준은 검사하지 않는다"
        ),
        sa.Column("pct", sa.Numeric(precision=6, scale=2), nullable=True, comment="비율 기준(%). 급변만 쓴다"),
        sa.Column(
            "amount",
            sa.Numeric(precision=14, scale=2),
            nullable=True,
            comment="금액 기준(K$). 급변=변동액 하한, 합계 불일치=허용 오차, 단위 의심=절대값 상한",
        ),
        sa.Column(
            "ratio",
            sa.Numeric(precision=10, scale=2),
            nullable=True,
            comment="배수 기준. 단위 의심만 쓴다(전 버전 대비 N배·1/N)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="마지막으로 바꾼 시각",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_key", name="uq_overseas_anomaly_rules_key"),
        comment=(
            "해외연구소 이상 감지 기준(급변·합계 불일치·사유 누락·단위 의심). 금액 단위는 K$. "
            "행이 없으면 app/core/anomaly.py의 기본값으로 동작한다."
        ),
    )
    op.create_table(
        "overseas_anomaly_rule_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("rule_key", sa.String(length=20), nullable=False, comment="바뀐 기준 키(overseas_anomaly_rules.rule_key)"),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment="바뀌기 전 값(enabled·pct·amount·ratio)"),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment="바뀐 뒤 값"),
        sa.Column(
            "source",
            sa.String(length=10),
            nullable=False,
            server_default="screen",
            comment="chat(챗봇) | screen(화면) | reset(기본값 복구)",
        ),
        sa.Column(
            "note",
            sa.String(length=200),
            nullable=False,
            server_default="",
            comment='무슨 말로 바꿨는지(예: 챗봇 요청 "50% 넘는것만 알려줘")',
        ),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="바꾼 시각",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="이상 감지 기준 변경 이력(before/after + 누가 어떤 경로로 바꿨는지).",
    )
    # 기본값을 미리 넣어 둔다 — 화면·챗봇이 "지금 기준"을 물을 때 빈 목록을 돌려주지 않게.
    # (코드에도 같은 기본값이 있어서 이 행이 없어도 동작하지만, 조회 결과가 비면 사람이 "기준이
    #  없다"로 읽는다.)
    op.execute(
        """
        INSERT INTO overseas_anomaly_rules (rule_key, enabled, pct, amount, ratio) VALUES
          ('jump',           true, 10.00, 10.00,    NULL),
          ('sum_mismatch',   true, NULL,   0.50,    NULL),
          ('missing_reason', true, NULL,   NULL,    NULL),
          ('unit_suspect',   true, NULL,   1000000, 1000)
        ON CONFLICT (rule_key) DO NOTHING
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("overseas_anomaly_rule_logs")
    op.drop_table("overseas_anomaly_rules")
