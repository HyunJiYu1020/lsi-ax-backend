"""versions table (속보·실적 연월 진행 상태) and VERSION_STATUS common codes

Revision ID: d4e8b2f6a1c3
Revises: 460d75798f81
Create Date: 2026-11-10 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e8b2f6a1c3'
down_revision: Union[str, Sequence[str], None] = '460d75798f81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION_STATUS_CODES = [
    ("not_started", "예정", 1),
    ("drafting", "작성중", 2),
    ("done", "완료", 3),
]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('year', sa.SmallInteger(), nullable=False, comment='대상 연도'),
        sa.Column('month', sa.SmallInteger(), nullable=False, comment='대상 월(1~12)'),
        sa.Column('kind', sa.String(length=10), nullable=False, comment='sok(속보) | act(실적)'),
        sa.Column(
            'status', sa.String(length=12), nullable=False, server_default='drafting',
            comment="진행 상태. common_codes(group_code='VERSION_STATUS')의 code 값(drafting|done)",
        ),
        sa.Column('confirmed_at', sa.Date(), nullable=True, comment='완료(확정) 처리일'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='생성 시각'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('year', 'month', 'kind', name='uq_versions_year_month_kind'),
        comment='연월×속보/실적(sok/act) 진행 상태. 기준정보 › 경영계획 연간 달력에 쓰인다. 열지 않은 연월은 행이 없다(=예정).',
    )

    op.execute(
        "INSERT INTO common_code_groups (group_code, name, description) VALUES "
        "('VERSION_STATUS', '버전 진행 상태', 'versions.status에 저장되는 속보·실적 버전 진행 상태 코드')"
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
            {"group_code": "VERSION_STATUS", "code": code, "name": name, "sort_order": sort_order, "is_active": True}
            for code, name, sort_order in VERSION_STATUS_CODES
        ],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM common_codes WHERE group_code = 'VERSION_STATUS'")
    op.execute("DELETE FROM common_code_groups WHERE group_code = 'VERSION_STATUS'")
    op.drop_table('versions')
