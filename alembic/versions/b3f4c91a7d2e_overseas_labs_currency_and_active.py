"""overseas labs currency and active flag

Revision ID: b3f4c91a7d2e
Revises: a7e2c84f1b3d
Create Date: 2026-11-03 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b3f4c91a7d2e'
down_revision: Union[str, Sequence[str], None] = 'a7e2c84f1b3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 연구소 기준정보 편집 화면(프론트 ovLabPage)이 국가별 기본 통화로 미리 채워주던 매핑.
# 기존 행 백필용 — 매핑에 없는 국가(미국 등)는 컬럼 기본값 USD로 남는다.
COUNTRY_CURRENCY = {
    "인도": "INR",
    "이스라엘": "ILS",
    "영국": "GBP",
    "중국": "CNY",
    "대만": "TWD",
    "폴란드": "PLN",
    "러시아": "RUB",
    "일본": "JPY",
}


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'overseas_labs',
        sa.Column(
            'currency', sa.String(length=3), server_default='USD', nullable=False,
            comment='현지통화 (연구소 기준정보 편집 화면에서 변경 가능, 기본은 국가별 통화)',
        ),
    )
    op.add_column(
        'overseas_labs',
        sa.Column(
            'is_active', sa.Boolean(), server_default=sa.true(), nullable=False,
            comment='사용 여부. false면 취합·집계표·증감분석 화면에서 숨기지만 기존 데이터는 보존',
        ),
    )
    for country, currency in COUNTRY_CURRENCY.items():
        op.execute(
            sa.text("UPDATE overseas_labs SET currency = :currency WHERE country = :country")
            .bindparams(currency=currency, country=country)
        )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('overseas_labs', 'is_active')
    op.drop_column('overseas_labs', 'currency')
