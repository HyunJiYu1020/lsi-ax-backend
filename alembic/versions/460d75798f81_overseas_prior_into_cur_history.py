"""overseas prior(전년 실적) 데이터를 cur 역사로 옮김 (prev/prior를 cur에서 유도하도록 변경)

Revision ID: 460d75798f81
Revises: c7f1a9d3b5e2
Create Date: 2026-11-10 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '460d75798f81'
down_revision: Union[str, Sequence[str], None] = 'c7f1a9d3b5e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # get_overseas_state()가 이제 prev(전실행)·prior(전년 실적)를 독립 슬롯이 아니라 cur의 과거
    # actual_thru_month 스냅샷에서 유도한다(전실행=가장 최근 마감월, 전년 실적=전년도 마감 시점).
    # prev/2026은 이미 cur의 실적 스냅샷과 값이 동일해서(그대로 복사해 올렸던 것) 옮길 필요 없지만,
    # prior/2025는 독립적인 실제 데이터라 cur/2025 · actual_thru_month=12(그 해 마감)로 복사해둬야
    # 새 유도 로직이 찾을 수 있다. 이미 cur/2025 데이터가 있으면(충돌) 건너뛴다.
    op.execute("""
        INSERT INTO overseas_cost_entries (
            scope_key, version, category_key, label, label_en, anchor_key, year, actual_thru_month,
            month1, month2, month3, month4, month5, month6, month7, month8, month9, month10, month11, month12,
            q1, q2, q3, q4, annual, custom_fields, created_at, created_by_id, updated_at, updated_by_id
        )
        SELECT
            scope_key, 'cur', category_key, label, label_en, anchor_key, year, 12,
            month1, month2, month3, month4, month5, month6, month7, month8, month9, month10, month11, month12,
            q1, q2, q3, q4, annual, custom_fields, created_at, created_by_id, updated_at, updated_by_id
        FROM overseas_cost_entries
        WHERE version = 'prior' AND year = 2025
        ON CONFLICT (scope_key, version, category_key, actual_thru_month, year) DO NOTHING
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DELETE FROM overseas_cost_entries WHERE version = 'cur' AND year = 2025 AND actual_thru_month = 12")
