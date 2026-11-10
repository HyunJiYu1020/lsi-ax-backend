"""dep account_steps fix to match frontend 5 steps

Revision ID: 3d5471b47348
Revises: 5929930a9de9
Create Date: 2026-11-09 14:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3d5471b47348'
down_revision: Union[str, Sequence[str], None] = '5929930a9de9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# frontend-html/shared/core.js의 STEPS 배열(dep 전용, line 93)과 1:1로 맞춘다.
# d54c29d3b4e6에서 한 "취합->편성 병합"은 frontend 확인 전의 잘못된 추측이었음 — 여기서 바로잡는다.
DEP_STEPS = [
    ("취합", "기초자료 업로드"),
    ("가공", "기준정보 맵핑 · 상각 스케줄"),
    ("검증", "데이터 검증 · 수정"),
    ("확정", "최종 편성안 확정"),
    ("분석", "증감분석"),
]


def upgrade() -> None:
    """Upgrade schema. dep의 account_steps를 frontend와 맞는 5단계로 통째로 교체한다."""
    op.drop_constraint('account_progress_account_id_current_step_order_fkey', 'account_progress', type_='foreignkey')

    op.execute("""
        UPDATE account_progress p SET current_step_order = 0
        FROM accounts a WHERE p.account_id = a.id AND a.key = 'dep'
    """)
    op.execute("""
        DELETE FROM account_steps s USING accounts a
        WHERE s.account_id = a.id AND a.key = 'dep'
    """)
    for order, (tag, title) in enumerate(DEP_STEPS):
        op.execute(sa.text("""
            INSERT INTO account_steps (account_id, step_order, tag, title)
            SELECT a.id, :step_order, :tag, :title FROM accounts a WHERE a.key = 'dep'
        """).bindparams(step_order=order, tag=tag, title=title))

    op.create_foreign_key(
        'account_progress_account_id_current_step_order_fkey', 'account_progress', 'account_steps',
        ['account_id', 'current_step_order'], ['account_id', 'step_order'],
    )


def downgrade() -> None:
    """Downgrade schema. d54c29d3b4e6가 만들어둔 3단계(편성/검토/분석)로 되돌린다."""
    op.drop_constraint('account_progress_account_id_current_step_order_fkey', 'account_progress', type_='foreignkey')

    op.execute("""
        UPDATE account_progress p SET current_step_order = 0
        FROM accounts a WHERE p.account_id = a.id AND a.key = 'dep'
    """)
    op.execute("""
        DELETE FROM account_steps s USING accounts a
        WHERE s.account_id = a.id AND a.key = 'dep'
    """)
    for order, (tag, title) in enumerate([
        ("편성", "감가상각 스케줄"),
        ("검토", "검증 · 편성안 확정"),
        ("분석", "증감분석 · 업로드"),
    ]):
        op.execute(sa.text("""
            INSERT INTO account_steps (account_id, step_order, tag, title)
            SELECT a.id, :step_order, :tag, :title FROM accounts a WHERE a.key = 'dep'
        """).bindparams(step_order=order, tag=tag, title=title))

    op.create_foreign_key(
        'account_progress_account_id_current_step_order_fkey', 'account_progress', 'account_steps',
        ['account_id', 'current_step_order'], ['account_id', 'step_order'],
    )
