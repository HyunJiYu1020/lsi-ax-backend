"""renumber cost_entries and cost_entry_details ids from 1

Revision ID: 2c83a3287aa6
Revises: 80375cbe3150
Create Date: 2026-11-11 00:08:36.421320

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '2c83a3287aa6'
down_revision: Union[str, Sequence[str], None] = '80375cbe3150'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CE_COLS = (
    "yyyymm, account_code, scope_code, version, category_code, "
    "label, label_en, anchor_key, updated_at, updated_by_id, created_at, created_by_id"
)
CED_COLS = "year, period_type, period, col_name, amount, updated_at, updated_by_id"


def upgrade() -> None:
    """Upgrade schema.

    cost_entry_details.cost_entry_id가 cost_entries.id를 FK로 참조하므로, 49f565230cf8처럼 단순
    TRUNCATE+재삽입만 하면 참조가 깨진다. 백업 시점에 새 id(1..N)를 미리 매겨서 두 테이블을
    같이 비우고, 그 매핑으로 FK를 다시 이어 붙인다.
    """
    op.execute(f"""
        CREATE TEMP TABLE ce_backup ON COMMIT DROP AS
        SELECT id AS old_id, ROW_NUMBER() OVER (ORDER BY id) AS new_id, {CE_COLS}
        FROM cost_entries
    """)
    op.execute(f"""
        CREATE TEMP TABLE ced_backup ON COMMIT DROP AS
        SELECT ROW_NUMBER() OVER (ORDER BY ced.id) AS new_id, ceb.new_id AS new_cost_entry_id,
               {", ".join(f"ced.{c}" for c in CED_COLS.split(", "))}
        FROM cost_entry_details ced
        JOIN ce_backup ceb ON ceb.old_id = ced.cost_entry_id
    """)

    op.execute("TRUNCATE TABLE cost_entry_details, cost_entries RESTART IDENTITY")

    op.execute(f"""
        INSERT INTO cost_entries (id, {CE_COLS})
        SELECT new_id, {CE_COLS} FROM ce_backup ORDER BY new_id
    """)
    op.execute("SELECT setval(pg_get_serial_sequence('cost_entries', 'id'), (SELECT MAX(id) FROM cost_entries))")

    op.execute(f"""
        INSERT INTO cost_entry_details (id, cost_entry_id, {CED_COLS})
        SELECT new_id, new_cost_entry_id, {CED_COLS} FROM ced_backup ORDER BY new_id
    """)
    op.execute("SELECT setval(pg_get_serial_sequence('cost_entry_details', 'id'), (SELECT MAX(id) FROM cost_entry_details))")

    # management_plan_entries/management_plan_entry_details는 현재 데이터가 없지만, 예전 테스트
    # 삽입·삭제로 시퀀스만 앞서 있던 걸 같이 1부터 다시 시작하게 맞춘다.
    op.execute("TRUNCATE TABLE management_plan_entry_details, management_plan_entries RESTART IDENTITY")


def downgrade() -> None:
    """Downgrade schema. id 재번호는 의미상 되돌릴 대상이 없어 no-op."""
    pass
