"""overseas cost entries custom_fields dict to array

Revision ID: 1e5fa83c144d
Revises: 576b6b9dc751
Create Date: 2026-11-09 13:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1e5fa83c144d'
down_revision: Union[str, Sequence[str], None] = '576b6b9dc751'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # custom_fields를 {col_key: {label, value}} 객체에서 [{col_key, label, value}, ...] 배열로 바꾼다.
    op.execute("""
        UPDATE overseas_cost_entries e
        SET custom_fields = sub.arr
        FROM (
            SELECT oce.id,
                jsonb_agg(jsonb_build_object('col_key', kv.key, 'label', kv.value -> 'label', 'value', kv.value -> 'value')) AS arr
            FROM overseas_cost_entries oce
            CROSS JOIN LATERAL jsonb_each(oce.custom_fields) AS kv
            WHERE jsonb_typeof(oce.custom_fields) = 'object' AND oce.custom_fields <> '{}'::jsonb
            GROUP BY oce.id
        ) sub
        WHERE e.id = sub.id
    """)
    op.execute("""
        UPDATE overseas_cost_entries
        SET custom_fields = '[]'::jsonb
        WHERE jsonb_typeof(custom_fields) = 'object'
    """)
    op.alter_column(
        'overseas_cost_entries', 'custom_fields',
        comment='사용자가 추가한 열. [{col_key, label, value}, ...]',
        existing_comment='사용자가 추가한 열. col_key -> {label, value}',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("""
        UPDATE overseas_cost_entries e
        SET custom_fields = sub.obj
        FROM (
            SELECT oce.id,
                jsonb_object_agg(elem ->> 'col_key', jsonb_build_object('label', elem -> 'label', 'value', elem -> 'value')) AS obj
            FROM overseas_cost_entries oce
            CROSS JOIN LATERAL jsonb_array_elements(oce.custom_fields) AS elem
            WHERE jsonb_typeof(oce.custom_fields) = 'array' AND jsonb_array_length(oce.custom_fields) > 0
            GROUP BY oce.id
        ) sub
        WHERE e.id = sub.id
    """)
    op.execute("""
        UPDATE overseas_cost_entries
        SET custom_fields = '{}'::jsonb
        WHERE jsonb_typeof(custom_fields) = 'array'
    """)
    op.alter_column(
        'overseas_cost_entries', 'custom_fields',
        comment='사용자가 추가한 열. col_key -> {label, value}',
        existing_comment='사용자가 추가한 열. [{col_key, label, value}, ...]',
    )
