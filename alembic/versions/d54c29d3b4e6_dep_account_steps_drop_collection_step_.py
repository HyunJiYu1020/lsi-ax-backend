"""dep account_steps drop collection step, merge into step0

Revision ID: d54c29d3b4e6
Revises: 43d4a194dd4a
Create Date: 2026-11-09 14:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd54c29d3b4e6'
down_revision: Union[str, Sequence[str], None] = '43d4a194dd4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    dep 0단계(취합: 기초자료 업로드 · 맵핑)를 없애고, 취합 작업은 기존 1단계(편성: 감가상각 스케줄)에서
    같이 진행하는 것으로 합친다. overseas에 적용한 것과 동일한 처리(e423ec91abdc) — 화면 문구(tag/title)는
    그대로 두고 단계 수·번호만 바꾼다 (4단계 -> 3단계: 편성=0, 검토=1, 분석=2).
    """
    op.drop_constraint('account_progress_account_id_current_step_order_fkey', 'account_progress', type_='foreignkey')

    op.execute("""
        UPDATE account_progress p
        SET current_step_order = CASE WHEN p.current_step_order <= 1 THEN 0 ELSE p.current_step_order - 1 END
        FROM accounts a
        WHERE p.account_id = a.id AND a.key = 'dep'
    """)
    op.execute("""
        DELETE FROM account_steps s
        USING accounts a
        WHERE s.account_id = a.id AND a.key = 'dep' AND s.step_order = 0
    """)
    op.execute("""
        UPDATE account_steps s
        SET step_order = 0
        FROM accounts a
        WHERE s.account_id = a.id AND a.key = 'dep' AND s.step_order = 1
    """)
    op.execute("""
        UPDATE account_steps s
        SET step_order = 1
        FROM accounts a
        WHERE s.account_id = a.id AND a.key = 'dep' AND s.step_order = 2
    """)
    op.execute("""
        UPDATE account_steps s
        SET step_order = 2
        FROM accounts a
        WHERE s.account_id = a.id AND a.key = 'dep' AND s.step_order = 3
    """)

    op.create_foreign_key(
        'account_progress_account_id_current_step_order_fkey', 'account_progress', 'account_steps',
        ['account_id', 'current_step_order'], ['account_id', 'step_order'],
    )


def downgrade() -> None:
    """Downgrade schema. 취합(0단계)을 다시 앞에 끼워넣고 나머지를 하나씩 뒤로 민다.

    주의: upgrade에서 진행 상태가 0·1 둘 다 0으로 합쳐졌기 때문에, 원래 0(취합)이었는지
    1(편성)이었는지는 구분할 수 없다 — 여기서는 전부 1(편성)이었던 것으로 복원한다.
    upgrade 직후 상태를 되돌리는 용도로만 쓸 것.
    """
    op.drop_constraint('account_progress_account_id_current_step_order_fkey', 'account_progress', type_='foreignkey')

    op.execute("""
        UPDATE account_steps s
        SET step_order = 3
        FROM accounts a
        WHERE s.account_id = a.id AND a.key = 'dep' AND s.step_order = 2
    """)
    op.execute("""
        UPDATE account_steps s
        SET step_order = 2
        FROM accounts a
        WHERE s.account_id = a.id AND a.key = 'dep' AND s.step_order = 1
    """)
    op.execute("""
        UPDATE account_steps s
        SET step_order = 1
        FROM accounts a
        WHERE s.account_id = a.id AND a.key = 'dep' AND s.step_order = 0
    """)
    op.execute("""
        INSERT INTO account_steps (account_id, step_order, tag, title)
        SELECT a.id, 0, '취합', '기초자료 업로드 · 맵핑'
        FROM accounts a WHERE a.key = 'dep'
    """)
    op.execute("""
        UPDATE account_progress p
        SET current_step_order = p.current_step_order + 1
        FROM accounts a
        WHERE p.account_id = a.id AND a.key = 'dep'
    """)

    op.create_foreign_key(
        'account_progress_account_id_current_step_order_fkey', 'account_progress', 'account_steps',
        ['account_id', 'current_step_order'], ['account_id', 'step_order'],
    )
