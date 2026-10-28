"""common code groups registry

Revision ID: 6848d4480249
Revises: e69cb580bc6d
Create Date: 2026-10-28 18:35:34.695735

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6848d4480249'
down_revision: Union[str, Sequence[str], None] = 'e69cb580bc6d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'common_code_groups',
        sa.Column('group_code', sa.String(length=40), nullable=False, comment='그룹코드 ("VERSION", "FX_RATE_TYPE", "ACCOUNT_STATUS", ...)'),
        sa.Column('name', sa.String(length=100), nullable=False, comment='그룹명 (한글)'),
        sa.Column('description', sa.Text(), nullable=True, comment='설명'),
        sa.PrimaryKeyConstraint('group_code'),
        comment='공통코드 그룹 레지스트리. common_codes.group_code가 FK로 참조한다.',
    )

    # 그룹 테이블에 데이터가 있어야 common_codes.group_code FK가 성립하니, 그룹 먼저 채운다.
    # VERSION · CURRENCY · FX_RATE_TYPE은 이미 common_codes에 값이 들어가 있던 그룹이라 같이 등록한다.
    op.execute(
        "INSERT INTO common_code_groups (group_code, name, description) VALUES "
        "('ACCOUNT_STATUS', '계정 진행 상태', 'accounts.status에 저장되는 편성 진행 상태 코드'), "
        "('VERSION', '버전', 'cost_entries 등의 version 컬럼: cur(당월 속보)·prev(전실행)·prior(전년 실적)·plan(경영계획)'), "
        "('CURRENCY', '통화', 'fx_rates.currency: USD·ILS(이스라엘)·INR(인도)'), "
        "('FX_RATE_TYPE', '환율 종류', 'fx_rates.rate_type: end(월말환율)·act(실적환율)·cp(CP환율)')"
    )

    # 최초 마이그레이션(1608bd61f4a7)에서 DB 서버 기본값이 '작성중'으로 박혀 있던 걸
    # 지금까지 데이터만 바꾸고 안 고쳤던 부분. 코멘트와 함께 서버 기본값도 영문 코드로 맞춘다.
    op.alter_column(
        'accounts', 'status',
        existing_type=sa.VARCHAR(length=20),
        server_default='drafting',
        comment="편성 진행 상태. common_codes(group_code='ACCOUNT_STATUS')의 code 값 (drafting|drafted|not_submitted|submitted|reviewed|rejected)",
        existing_comment='편성 진행 상태',
        existing_nullable=False,
    )

    op.create_foreign_key(
        'fk_common_codes_group_code_common_code_groups',
        'common_codes', 'common_code_groups', ['group_code'], ['group_code'],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_common_codes_group_code_common_code_groups', 'common_codes', type_='foreignkey')
    op.alter_column(
        'accounts', 'status',
        existing_type=sa.VARCHAR(length=20),
        server_default="작성중",
        comment='편성 진행 상태',
        existing_comment="편성 진행 상태. common_codes(group_code='ACCOUNT_STATUS')의 code 값 (drafting|drafted|not_submitted|submitted|reviewed|rejected)",
        existing_nullable=False,
    )
    op.drop_table('common_code_groups')
