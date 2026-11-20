"""add indexes for the hot read paths (cost_entries lookups, upload history)

지금은 데이터가 적어 체감이 없지만 세 쿼리 모두 순차 스캔(Seq Scan)으로 돌고 있고, 대상 테이블이
편성 회차·연구소·비용항목이 쌓일수록 단조 증가해서 그대로 두면 같이 느려진다.

- cost_entries: UNIQUE 인덱스가 (account_code, scope_code, ...) 순서라 selectivity가 거의 없는
  account_code(지금은 전부 'overseas')만 걸러지고 version·yyyymm은 못 쓴다. /overseas/state와
  list_cost_entries가 쓰는 (account_code, version, yyyymm) 조합으로 인덱스를 따로 둔다.
- cost_entries 커스텀 행: label IS NOT NULL인 행은 956건 중 8건인데 전체를 훑고 있었다
  (buffers 362 → 8행). 부분 인덱스로 커스텀 행만 담는다 — get_overseas_state의 custom_keys,
  list_extra_rows, _check_category가 모두 이 조건을 쓴다.
- uploads: PK 말고 인덱스가 없다. 지울 일 없이 계속 쌓이는 이력 테이블이라, (version, scope_key)별
  최신 1건을 뽑는 DISTINCT ON 쿼리가 쌓인 만큼 정렬 비용을 그대로 떠안는다.

Revision ID: f2b7c9d14e08
Revises: e1a2b3c4d5f6
"""

import sqlalchemy as sa
from alembic import op

revision = "f2b7c9d14e08"
down_revision = "e1a2b3c4d5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_cost_entries_account_version_yyyymm",
        "cost_entries",
        ["account_code", "version", "yyyymm"],
    )
    # 커스텀 행(사용자가 추가한 행)만 담는 부분 인덱스 — 전체의 1% 미만이라 인덱스도 아주 작다.
    op.create_index(
        "ix_cost_entries_custom_rows",
        "cost_entries",
        ["account_code", "scope_code", "category_code"],
        postgresql_where=sa.text("label IS NOT NULL"),
    )
    op.create_index(
        "ix_uploads_account_version_scope_recent",
        "uploads",
        ["account_key", "version", "scope_key", "uploaded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_uploads_account_version_scope_recent", table_name="uploads")
    op.drop_index("ix_cost_entries_custom_rows", table_name="cost_entries")
    op.drop_index("ix_cost_entries_account_version_yyyymm", table_name="cost_entries")
