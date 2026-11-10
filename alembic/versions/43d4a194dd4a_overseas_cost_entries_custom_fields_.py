"""overseas cost entries custom_fields drop col_key

Revision ID: 43d4a194dd4a
Revises: e423ec91abdc
Create Date: 2026-11-09 13:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '43d4a194dd4a'
down_revision: Union[str, Sequence[str], None] = 'e423ec91abdc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema. custom_fields 배열 원소에서 col_key를 뺀다 — label 자체가 열 식별자가 된다."""
    op.execute("""
        UPDATE overseas_cost_entries e
        SET custom_fields = sub.arr
        FROM (
            SELECT oce.id, jsonb_agg(elem - 'col_key') AS arr
            FROM overseas_cost_entries oce
            CROSS JOIN LATERAL jsonb_array_elements(oce.custom_fields) AS elem
            WHERE jsonb_typeof(oce.custom_fields) = 'array' AND jsonb_array_length(oce.custom_fields) > 0
            GROUP BY oce.id
        ) sub
        WHERE e.id = sub.id
    """)
    op.alter_column(
        'overseas_cost_entries', 'custom_fields',
        comment='사용자가 추가한 열. [{label, value}, ...] — label 자체가 열 식별자',
        existing_comment='사용자가 추가한 열. [{col_key, label, value}, ...]',
    )


def downgrade() -> None:
    """Downgrade schema.

    주의: col_key는 되살릴 수 없으므로 label의 md5 해시로 새로 만든다 — upgrade 전 원본 col_key와
    다르다(값·label은 보존됨). upgrade 직후 상태를 되돌리는 용도로만 쓸 것.
    """
    op.execute("""
        UPDATE overseas_cost_entries e
        SET custom_fields = sub.arr
        FROM (
            SELECT oce.id, jsonb_agg(elem || jsonb_build_object('col_key', 'col_' || md5(elem ->> 'label'))) AS arr
            FROM overseas_cost_entries oce
            CROSS JOIN LATERAL jsonb_array_elements(oce.custom_fields) AS elem
            WHERE jsonb_typeof(oce.custom_fields) = 'array' AND jsonb_array_length(oce.custom_fields) > 0
            GROUP BY oce.id
        ) sub
        WHERE e.id = sub.id
    """)
    op.alter_column(
        'overseas_cost_entries', 'custom_fields',
        comment='사용자가 추가한 열. [{col_key, label, value}, ...]',
        existing_comment='사용자가 추가한 열. [{label, value}, ...] — label 자체가 열 식별자',
    )
