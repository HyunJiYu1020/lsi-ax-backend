"""overseas account_steps drop collection step, merge into step0

Revision ID: e423ec91abdc
Revises: 1e5fa83c144d
Create Date: 2026-11-09 13:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e423ec91abdc'
down_revision: Union[str, Sequence[str], None] = '1e5fa83c144d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    overseas 0단계(취합: 연구소 데이터 업로드)를 없애고, 취합 작업은 기존 1단계(집계: 집계표)에서
    같이 진행하는 것으로 합친다. 화면 문구(tag/title)는 그대로 두고 단계 수·번호만 바꾼다
    (3단계 -> 2단계: 집계=0, 증감분석=1).
    """
    op.drop_constraint('account_progress_account_id_current_step_order_fkey', 'account_progress', type_='foreignkey')

    # 0단계에 머물러 있던 진행 상태는 합쳐진 0단계(옛 집계)에 그대로 머무르고,
    # 그 뒤 단계에 있던 진행 상태는 번호가 하나씩 당겨진다.
    op.execute("""
        UPDATE account_progress p
        SET current_step_order = CASE WHEN p.current_step_order <= 1 THEN 0 ELSE p.current_step_order - 1 END
        FROM accounts a
        WHERE p.account_id = a.id AND a.key = 'overseas'
    """)
    op.execute("""
        DELETE FROM account_steps s
        USING accounts a
        WHERE s.account_id = a.id AND a.key = 'overseas' AND s.step_order = 0
    """)
    op.execute("""
        UPDATE account_steps s
        SET step_order = 0
        FROM accounts a
        WHERE s.account_id = a.id AND a.key = 'overseas' AND s.step_order = 1
    """)
    op.execute("""
        UPDATE account_steps s
        SET step_order = 1
        FROM accounts a
        WHERE s.account_id = a.id AND a.key = 'overseas' AND s.step_order = 2
    """)

    op.create_foreign_key(
        'account_progress_account_id_current_step_order_fkey', 'account_progress', 'account_steps',
        ['account_id', 'current_step_order'], ['account_id', 'step_order'],
    )


def downgrade() -> None:
    """Downgrade schema. 취합(0단계)을 다시 앞에 끼워넣고 나머지를 하나씩 뒤로 민다.

    주의: upgrade에서 진행 상태가 0·1 둘 다 0으로 합쳐졌기 때문에, 원래 0(취합)이었는지
    1(집계)이었는지는 구분할 수 없다 — 여기서는 전부 1(집계)이었던 것으로 복원한다.
    upgrade 직후 상태를 되돌리는 용도로만 쓸 것.
    """
    op.drop_constraint('account_progress_account_id_current_step_order_fkey', 'account_progress', type_='foreignkey')

    op.execute("""
        UPDATE account_steps s
        SET step_order = 2
        FROM accounts a
        WHERE s.account_id = a.id AND a.key = 'overseas' AND s.step_order = 1
    """)
    op.execute("""
        UPDATE account_steps s
        SET step_order = 1
        FROM accounts a
        WHERE s.account_id = a.id AND a.key = 'overseas' AND s.step_order = 0
    """)
    op.execute("""
        INSERT INTO account_steps (account_id, step_order, tag, title)
        SELECT a.id, 0, '취합', '연구소 데이터 업로드'
        FROM accounts a WHERE a.key = 'overseas'
    """)
    op.execute("""
        UPDATE account_progress p
        SET current_step_order = p.current_step_order + 1
        FROM accounts a
        WHERE p.account_id = a.id AND a.key = 'overseas'
    """)

    op.create_foreign_key(
        'account_progress_account_id_current_step_order_fkey', 'account_progress', 'account_steps',
        ['account_id', 'current_step_order'], ['account_id', 'step_order'],
    )
