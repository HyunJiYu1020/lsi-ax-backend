"""drop fx_currencies and CURRENCY common codes (통화 추가 셀렉트는 업로드된 데이터에서 직접 뽑음)

Revision ID: c7f1a9d3b5e2
Revises: 82b996f20cec
Create Date: 2026-11-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7f1a9d3b5e2'
down_revision: Union[str, Sequence[str], None] = '82b996f20cec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# fx_currencies·CURRENCY 공통코드 둘 다 "알려진 통화 전체 목록"을 따로 들고 있었는데, 실제로 쓰는
# 화면(기준정보 › 환율의 "통화 추가")은 fx_actual_rates/fx_forecast_rates/fx_plan_rates에 실제로
# 업로드된 통화만 보여주면 되므로 더 이상 필요 없다. CURRENCY 공통코드는 옛 overseas_labs.currency/
# fx_rates.currency 쪽에서만 참고용으로 두고 있었을 뿐 어떤 엔드포인트도 읽지 않는다.
CURRENCY_CODES = [
    ("USD", "미국 달러", 0),
    ("ILS", "이스라엘 셰켈", 1),
    ("INR", "인도 루피", 2),
    ("GBP", "영국 파운드", 3),
    ("CNY", "중국 위안", 4),
    ("TWD", "대만 달러", 5),
    ("PLN", "폴란드 즐로티", 6),
    ("RUB", "러시아 루블", 7),
    ("JPY", "일본 옌", 8),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DELETE FROM common_codes WHERE group_code = 'CURRENCY'")
    op.execute("DELETE FROM common_code_groups WHERE group_code = 'CURRENCY'")
    op.drop_table('fx_currencies')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        'fx_currencies',
        sa.Column('currency_code', sa.String(length=3), nullable=False, comment='통화 코드 ("USD" 등)'),
        sa.Column('country_name', sa.String(length=20), nullable=False, comment='국가명'),
        sa.PrimaryKeyConstraint('currency_code'),
        comment='통화 코드 → 국가명 (fx2 업로드 과정에서 채워지는 참고용 매핑)',
    )

    op.execute(
        "INSERT INTO common_code_groups (group_code, name, description) VALUES "
        "('CURRENCY', '통화', 'fx_rates.currency, overseas_labs.currency: USD·ILS(이스라엘)·INR(인도)·GBP(영국)·"
        "CNY(중국)·TWD(대만)·PLN(폴란드)·RUB(러시아)·JPY(일본)')"
    )

    common_codes = sa.table(
        "common_codes",
        sa.column("group_code", sa.String),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("sort_order", sa.SmallInteger),
        sa.column("is_active", sa.Boolean),
    )
    op.bulk_insert(
        common_codes,
        [
            {"group_code": "CURRENCY", "code": code, "name": name, "sort_order": sort_order, "is_active": True}
            for code, name, sort_order in CURRENCY_CODES
        ],
    )
