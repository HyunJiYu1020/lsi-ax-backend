"""cost_entries is_derived+formula, extra_cols registry

취합에서 추가한 행/열(원시값)과 집계에서 추가한 행/열(파생값)을 저장 후에도 구분하기 위한 변경.
저장하는 값은 "추가 당시의 화면 단계"가 아니라 "이 값이 원시냐 파생이냐"다 — 단계는 종류가
만들어지는 장소일 뿐이고, 삭제 권한(원시=취합에서만, 파생=집계에서만)과 표시 규칙(원시=양쪽,
파생=집계만)을 모두 이 종류 하나에서 끌어낼 수 있다.

파생 행/열은 월별 금액을 저장하지 않고 formula만 저장해서 조회할 때마다 다시 계산한다
(원본이 바뀌면 자동으로 따라 바뀌어야 하므로).

extra_cols는 새 테이블이다 — 지금까지 "추가 열"은 DB에 개체가 없었고 같은 col_name을 가진
cost_entry_details(period_type="CUSTOM") 칸들의 집합이 곧 그 열이어서, 열 단위 속성(파생 여부·
계산식)을 걸 자리가 없었다. 기존 열 데이터는 그 칸들에서 원시 열로 백필한다.

Revision ID: 39465d4a1419
Revises: d7ec575caa48
Create Date: 2026-11-11 22:18:45.205906

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '39465d4a1419'
down_revision: Union[str, Sequence[str], None] = 'd7ec575caa48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'extra_cols',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_key', sa.String(length=32), nullable=False, comment='accounts.key'),
        sa.Column('scope_key', sa.String(length=40), nullable=False, comment='계정 안에서의 행 단위 식별자 (해외연구소는 연구소 pk)'),
        sa.Column('version', sa.String(length=10), nullable=False, comment='forecast(속보) | actual(실적)'),
        sa.Column('yyyymm', sa.String(length=6), nullable=False, comment='편성 기준 연월 "YYYYMM" (예: "202609")'),
        sa.Column('label', sa.String(length=100), nullable=False, comment='사용자가 입력한 열 이름. 이 값 자체가 열 식별자'),
        sa.Column(
            'is_derived', sa.Boolean(), server_default=sa.text('false'), nullable=False,
            comment='True면 월을 묶어 계산한 파생 열(집계 전용). 칸 값을 저장하지 않고 formula로 매번 계산',
        ),
        sa.Column(
            'formula', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
            comment='파생 열의 계산식 {"op":"sum_months","months":[7,8,9]}. 원시 열은 NULL',
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='열 추가 시각'),
        sa.Column('created_by_id', sa.Integer(), nullable=True, comment='열을 추가한 사용자 (users.id)'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'),
        sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'),
        sa.CheckConstraint("version IN ('forecast', 'actual')", name='ck_extra_cols_version'),
        sa.CheckConstraint("yyyymm ~ '^\\d{4}(0[1-9]|1[0-2])$'", name='ck_extra_cols_yyyymm_format'),
        sa.ForeignKeyConstraint(['account_key'], ['accounts.key'], ),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('account_key', 'scope_key', 'version', 'yyyymm', 'label'),
        comment='편성 그리드 추가 열(존재·이름·파생 여부). 원시 열의 칸 값은 cost_entry_details 참조.',
    )

    # 이미 화면에서 만들어 쓰고 있는 열들을 레지스트리에 원시 열(is_derived=false)로 옮겨 담는다 —
    # 안 하면 기존 열이 전부 "등록되지 않은 열"이 되어 화면에서 사라진다. 파생 열 기능은 이 변경과
    # 함께 처음 생기는 것이므로 기존 열 중 파생인 것은 있을 수 없다.
    op.execute(
        """
        INSERT INTO extra_cols (account_key, scope_key, version, yyyymm, label, is_derived)
        SELECT DISTINCT e.account_code, e.scope_code, e.version, e.yyyymm, d.col_name, false
        FROM cost_entry_details d
        JOIN cost_entries e ON e.id = d.cost_entry_id
        WHERE d.period_type = 'CUSTOM' AND d.col_name <> ''
        """
    )

    op.add_column('cost_entries', sa.Column(
        'is_derived', sa.Boolean(), server_default=sa.text('false'), nullable=False,
        comment='True면 다른 항목에서 계산해 만든 파생 행(집계 전용). 월별 금액을 저장하지 않고 formula로 매번 계산',
    ))
    op.add_column('cost_entries', sa.Column(
        'formula', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
        comment='파생 행의 계산식 {"op":"div_elementwise","operands":["labor","hc"]}. 원시 행은 NULL',
    ))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('cost_entries', 'formula')
    op.drop_column('cost_entries', 'is_derived')
    op.drop_table('extra_cols')
