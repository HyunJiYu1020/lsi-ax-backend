"""fx_applied_rates.rate를 nullable로 — "환율 없음"과 "환율 0"을 구분

적용 환율 계산은 원본(fx_rates)에 그 달 값이 없으면 0을 돌려주는데, 그 0을 그대로 저장하면
읽는 쪽이 "환율이 0원"으로 읽어 금액을 0으로 환산한다(실제로 금액이 전부 0이 된 적이 있다).
환율 0원은 존재하지 않으므로 "없음"은 NULL로 남긴다.

기존 rate=0 행은 전부 "원본에 값이 없어서 0"이었으므로 NULL로 올린다.

Revision ID: f3c7a1d9b204
Revises: 4a1b09ce6ef1
"""

from alembic import op
import sqlalchemy as sa

revision = "f3c7a1d9b204"
down_revision = "4a1b09ce6ef1"
branch_labels = None
depends_on = None

_NEW_COMMENT = "원/통화 적용 환율. NULL은 '그 달 원본 환율이 없어 적용할 환율이 없다'는 뜻이다 — 0(환율이 0원)과 구분한다"
_OLD_COMMENT = "원/통화 적용 환율"


def upgrade() -> None:
    op.alter_column(
        "fx_applied_rates",
        "rate",
        existing_type=sa.Numeric(14, 4),
        nullable=True,
        # server_default를 같이 떨어뜨린다 — NOT NULL만 풀고 기본값 0을 남겨두면, 값을 넣지 않은
        # INSERT가 NULL이 아니라 0으로 채워져서 "없음"과 "0"을 다시 못 가른다.
        server_default=None,
        existing_server_default=sa.text("'0'::numeric"),
        comment=_NEW_COMMENT,
        existing_comment=_OLD_COMMENT,
    )
    op.execute("UPDATE fx_applied_rates SET rate = NULL WHERE rate = 0")


def downgrade() -> None:
    op.execute("UPDATE fx_applied_rates SET rate = 0 WHERE rate IS NULL")
    op.alter_column(
        "fx_applied_rates",
        "rate",
        existing_type=sa.Numeric(14, 4),
        nullable=False,
        server_default=sa.text("'0'::numeric"),
        comment=_OLD_COMMENT,
        existing_comment=_NEW_COMMENT,
    )
