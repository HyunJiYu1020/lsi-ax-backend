"""공통코드 FX_RATE_TYPE 제거 (fx_rates.rate_type 컬럼이 없어진 뒤 남아 있던 옛 체계 잔재)

환율은 fx_rates.kind(actual_avg · actual_eom · forecast · plan)로 구분한다 —
end(월말환율)·act(실적환율)·cp(CP환율)를 담던 rate_type 컬럼은 레거시 fx_rates 테이블과 함께
없어졌는데, 그 값을 설명하는 공통코드 그룹만 DB에 남아 있었다. 화면·도구 어디서도 안 읽는다.

Revision ID: b4c1e8a07d52
Revises: a1f3c7d92b04
Create Date: 2026-11-12 03:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b4c1e8a07d52'
down_revision: Union[str, Sequence[str], None] = 'a1f3c7d92b04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("DELETE FROM common_codes WHERE group_code = 'FX_RATE_TYPE'")
    op.execute("DELETE FROM common_code_groups WHERE group_code = 'FX_RATE_TYPE'")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "INSERT INTO common_code_groups (group_code, name, description) VALUES "
        "('FX_RATE_TYPE', '환율 종류', 'fx_rates.rate_type: end(월말환율)·act(실적환율)·cp(CP환율)')"
    )
    op.execute(
        "INSERT INTO common_codes (group_code, code, name, sort_order, is_active) VALUES "
        "('FX_RATE_TYPE', 'end', '월말환율', 10, true), "
        "('FX_RATE_TYPE', 'act', '실적환율', 20, true), "
        "('FX_RATE_TYPE', 'cp', 'CP환율', 30, true)"
    )
