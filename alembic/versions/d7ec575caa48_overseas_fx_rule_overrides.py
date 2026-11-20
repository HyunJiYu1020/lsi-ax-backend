"""overseas_fx_rule_overrides (해외연구소 환산 환율 기준 월 단위 예외)

기준(말일 vs 월평균)은 2층으로 관리한다:
- 기본값: common_codes(group_code='OVERSEAS_FX_RULE', code='default')의 name — 예외가 없는 달이 쓴다.
- 예외:   이 테이블 — "이 달만" 기본값과 다르게 쓸 때 연월 단위로 한 줄.

Revision ID: d7ec575caa48
Revises: 295b0de39cf1
Create Date: 2026-11-11 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7ec575caa48'
down_revision: Union[str, Sequence[str], None] = '295b0de39cf1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'overseas_fx_rule_overrides',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('year_month', sa.String(length=7), nullable=False, comment='이 달만 기본값 대신 rule을 쓴다 "YYYY-MM"'),
        sa.Column('rule', sa.String(length=10), nullable=False, comment='avg(월평균) | last(말일)'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='결정된 시각'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('year_month', name='uq_overseas_fx_rule_overrides_year_month'),
        sa.CheckConstraint(r"year_month ~ '^\d{4}-(0[1-9]|1[0-2])$'", name='ck_overseas_fx_rule_overrides_year_month_format'),
        sa.CheckConstraint("rule IN ('avg', 'last')", name='ck_overseas_fx_rule_overrides_rule'),
        comment="해외연구소 환산 환율 기준의 월 단위 예외. 예외가 없는 달은 common_codes(OVERSEAS_FX_RULE/default)의 기본값을 쓴다.",
    )

    # 기본값(code='default')의 설명을 새 2층 구조에 맞게 갱신한다. 그룹·행 자체는 이전부터 있던 것이라
    # 새로 만들지 않고(이미 있으면 UPDATE), 혹시 없는 환경에서는 넣어준다.
    op.execute(
        "UPDATE common_code_groups SET description = "
        "'해외연구소 화면이 실적 구간(마감된 달) 환산에 말일(last)·월평균(avg) 중 뭘 쓸지. "
        "code=''default'' 행의 name(avg|last)이 예외가 없는 모든 달에 적용되는 기본값이고, "
        "특정 달만 다르게 쓸 때는 overseas_fx_rule_overrides에 그 연월 행을 넣는다.' "
        "WHERE group_code = 'OVERSEAS_FX_RULE'"
    )
    op.execute(
        "INSERT INTO common_codes (group_code, code, name, sort_order, is_active) "
        "SELECT 'OVERSEAS_FX_RULE', 'default', 'last', 10, true "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM common_codes WHERE group_code = 'OVERSEAS_FX_RULE' AND code = 'default'"
        ")"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('overseas_fx_rule_overrides')
