"""rename cost_entry_detail and management_plan_entry_detail to plural

Revision ID: 80375cbe3150
Revises: f80ba7f4e039
Create Date: 2026-11-11 00:08:00.147821

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '80375cbe3150'
down_revision: Union[str, Sequence[str], None] = 'f80ba7f4e039'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    헤더 테이블(cost_entries, management_plan_entries)은 1:N 관계의 디테일 쪽이라 다른 테이블들과
    같이 복수형이어야 하는데, 두 디테일 테이블만 단수형(detail)으로 남아 있던 걸 맞춘다.
    """
    op.rename_table('cost_entry_detail', 'cost_entry_details')
    op.execute("ALTER TABLE cost_entry_details RENAME CONSTRAINT cost_entry_detail_pkey TO cost_entry_details_pkey")
    op.execute("ALTER TABLE cost_entry_details RENAME CONSTRAINT fk_cost_entry_detail_cost_entry_id TO fk_cost_entry_details_cost_entry_id")
    op.execute("ALTER TABLE cost_entry_details RENAME CONSTRAINT fk_cost_entry_detail_updated_by_id_users TO fk_cost_entry_details_updated_by_id_users")
    op.execute("ALTER TABLE cost_entry_details RENAME CONSTRAINT uq_cost_entry_detail_cost_entry_year_period_col TO uq_cost_entry_details_cost_entry_year_period_col")
    op.execute("ALTER TABLE cost_entry_details RENAME CONSTRAINT ck_cost_entry_detail_period_type TO ck_cost_entry_details_period_type")
    op.execute("ALTER SEQUENCE cost_entry_detail_id_seq RENAME TO cost_entry_details_id_seq")
    # f80ba7f4e039(cost_entry_month -> cost_entry_detail)에서 빠뜨린 헤더 코멘트도 같이 고친다.
    op.execute("COMMENT ON TABLE cost_entries IS '계정×행×비용항목×편성시점 헤더. 실제 월별 금액은 cost_entry_details 참조. 19개 계정이 공유.'")

    op.rename_table('management_plan_entry_detail', 'management_plan_entry_details')
    op.execute("ALTER TABLE management_plan_entry_details RENAME CONSTRAINT management_plan_entry_detail_pkey TO management_plan_entry_details_pkey")
    op.execute("ALTER TABLE management_plan_entry_details RENAME CONSTRAINT fk_management_plan_entry_detail_plan_entry_id TO fk_management_plan_entry_details_plan_entry_id")
    op.execute("ALTER TABLE management_plan_entry_details RENAME CONSTRAINT fk_management_plan_entry_detail_updated_by_id_users TO fk_management_plan_entry_details_updated_by_id_users")
    op.execute("ALTER TABLE management_plan_entry_details RENAME CONSTRAINT uq_management_plan_entry_detail_entry_year_period_col TO uq_management_plan_entry_details_entry_year_period_col")
    op.execute("ALTER TABLE management_plan_entry_details RENAME CONSTRAINT ck_management_plan_entry_detail_period_type TO ck_management_plan_entry_details_period_type")
    op.execute("ALTER SEQUENCE management_plan_entry_detail_id_seq RENAME TO management_plan_entry_details_id_seq")
    op.execute("COMMENT ON TABLE management_plan_entries IS '계정×행×비용항목×경영계획버전 헤더. 실제 금액은 management_plan_entry_details 참조. 19개 계정이 공유.'")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("COMMENT ON TABLE management_plan_entries IS '계정×행×비용항목×경영계획버전 헤더. 실제 금액은 management_plan_entry_detail 참조. 19개 계정이 공유.'")
    op.execute("ALTER SEQUENCE management_plan_entry_details_id_seq RENAME TO management_plan_entry_detail_id_seq")
    op.execute("ALTER TABLE management_plan_entry_details RENAME CONSTRAINT ck_management_plan_entry_details_period_type TO ck_management_plan_entry_detail_period_type")
    op.execute("ALTER TABLE management_plan_entry_details RENAME CONSTRAINT uq_management_plan_entry_details_entry_year_period_col TO uq_management_plan_entry_detail_entry_year_period_col")
    op.execute("ALTER TABLE management_plan_entry_details RENAME CONSTRAINT fk_management_plan_entry_details_updated_by_id_users TO fk_management_plan_entry_detail_updated_by_id_users")
    op.execute("ALTER TABLE management_plan_entry_details RENAME CONSTRAINT fk_management_plan_entry_details_plan_entry_id TO fk_management_plan_entry_detail_plan_entry_id")
    op.execute("ALTER TABLE management_plan_entry_details RENAME CONSTRAINT management_plan_entry_details_pkey TO management_plan_entry_detail_pkey")
    op.rename_table('management_plan_entry_details', 'management_plan_entry_detail')

    op.execute("COMMENT ON TABLE cost_entries IS '계정×행×비용항목×편성시점 헤더. 실제 월별 금액은 cost_entry_month 참조. 19개 계정이 공유.'")
    op.execute("ALTER SEQUENCE cost_entry_details_id_seq RENAME TO cost_entry_detail_id_seq")
    op.execute("ALTER TABLE cost_entry_details RENAME CONSTRAINT ck_cost_entry_details_period_type TO ck_cost_entry_detail_period_type")
    op.execute("ALTER TABLE cost_entry_details RENAME CONSTRAINT uq_cost_entry_details_cost_entry_year_period_col TO uq_cost_entry_detail_cost_entry_year_period_col")
    op.execute("ALTER TABLE cost_entry_details RENAME CONSTRAINT fk_cost_entry_details_updated_by_id_users TO fk_cost_entry_detail_updated_by_id_users")
    op.execute("ALTER TABLE cost_entry_details RENAME CONSTRAINT fk_cost_entry_details_cost_entry_id TO fk_cost_entry_detail_cost_entry_id")
    op.execute("ALTER TABLE cost_entry_details RENAME CONSTRAINT cost_entry_details_pkey TO cost_entry_detail_pkey")
    op.rename_table('cost_entry_details', 'cost_entry_detail')
