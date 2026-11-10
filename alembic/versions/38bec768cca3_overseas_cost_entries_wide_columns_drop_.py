"""overseas cost entries wide columns, drop cost months and extras

Revision ID: 38bec768cca3
Revises: 570ed91cc53a
Create Date: 2026-11-09 12:39:26.373538

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '38bec768cca3'
down_revision: Union[str, Sequence[str], None] = '570ed91cc53a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('overseas_cost_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('scope_key', sa.String(length=40), nullable=False, comment='연구소 pk (overseas_labs.pk)'),
    sa.Column('version', sa.String(length=10), nullable=False, comment='cur(당월 속보) | prev(전실행) | prior(전년 실적) | plan(경영계획)'),
    sa.Column('category_key', sa.String(length=40), nullable=False, comment='비용항목 키. 고정 enum 아닌 자유 문자열'),
    sa.Column('label', sa.String(length=100), nullable=True, comment='사용자가 직접 추가한 행의 이름(구분, 한글). 고정 계정(category_key가 OV_EL에 있는 키)은 NULL'),
    sa.Column('label_en', sa.String(length=100), nullable=True, comment='사용자가 직접 추가한 행의 영문 이름(Cost Element). 고정 계정은 NULL'),
    sa.Column('year', sa.SmallInteger(), nullable=True, comment='이 금액이 속한 연도'),
    sa.Column('actual_thru_month', sa.SmallInteger(), nullable=False, comment="실적이 반영된 마지막 달(1~12). cur 버전에서 같은 카테고리가 '실적'과 '속보'로 동시에 존재할 때 구분용. 그 외 버전은 0(해당 없음)"),
    sa.Column('month1', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='1월 금액'),
    sa.Column('month2', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='2월 금액'),
    sa.Column('month3', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='3월 금액'),
    sa.Column('month4', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='4월 금액'),
    sa.Column('month5', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='5월 금액'),
    sa.Column('month6', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='6월 금액'),
    sa.Column('month7', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='7월 금액'),
    sa.Column('month8', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='8월 금액'),
    sa.Column('month9', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='9월 금액'),
    sa.Column('month10', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='10월 금액'),
    sa.Column('month11', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='11월 금액'),
    sa.Column('month12', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='12월 금액'),
    sa.Column('q1', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='1분기(1~3월) 합계'),
    sa.Column('q2', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='2분기(4~6월) 합계'),
    sa.Column('q3', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='3분기(7~9월) 합계'),
    sa.Column('q4', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='4분기(10~12월) 합계'),
    sa.Column('annual', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='연간(1~12월) 합계'),
    sa.Column('custom_fields', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='사용자가 추가한 열. col_key -> {label, value}'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='최초 입력 시각'),
    sa.Column('created_by_id', sa.Integer(), nullable=True, comment='최초 입력한 사용자 (users.id)'),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'),
    sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('scope_key', 'version', 'category_key', 'actual_thru_month', 'year'),
    comment='해외연구소 비용 데이터 행(연구소×버전×비용항목×속보실적구분×연도). 월1~12·분기·연간 금액과 사용자 추가 열(custom_fields)을 한 행에 담는다.'
    )

    # ---- 데이터 이동: overseas_cost_lines(+months, +extras) -> overseas_cost_entries 한 행 ----
    # 기준 행(line) 하나는 실제로는 항상 연도 1개에만 월별 금액을 가지고 있어(line_id별 distinct year는
    # 늘 1개) 1:1로 평탄화할 수 있다. 월이 하나도 없는 행(hc 앵커용으로만 쓰인 행 등)은 year가 NULL로 남고
    # month1~12는 기본값 0을 그대로 쓴다.
    op.execute("""
        INSERT INTO overseas_cost_entries (
            scope_key, version, category_key, label, label_en, year, actual_thru_month,
            month1, month2, month3, month4, month5, month6, month7, month8, month9, month10, month11, month12,
            q1, q2, q3, q4, annual, custom_fields, created_at, created_by_id, updated_at, updated_by_id
        )
        SELECT
            l.scope_key, l.version, l.category_key, l.label, l.label_en, m.year, l.actual_thru_month,
            COALESCE(m.month1, 0), COALESCE(m.month2, 0), COALESCE(m.month3, 0), COALESCE(m.month4, 0),
            COALESCE(m.month5, 0), COALESCE(m.month6, 0), COALESCE(m.month7, 0), COALESCE(m.month8, 0),
            COALESCE(m.month9, 0), COALESCE(m.month10, 0), COALESCE(m.month11, 0), COALESCE(m.month12, 0),
            COALESCE(m.month1, 0) + COALESCE(m.month2, 0) + COALESCE(m.month3, 0) AS q1,
            COALESCE(m.month4, 0) + COALESCE(m.month5, 0) + COALESCE(m.month6, 0) AS q2,
            COALESCE(m.month7, 0) + COALESCE(m.month8, 0) + COALESCE(m.month9, 0) AS q3,
            COALESCE(m.month10, 0) + COALESCE(m.month11, 0) + COALESCE(m.month12, 0) AS q4,
            COALESCE(m.month1, 0) + COALESCE(m.month2, 0) + COALESCE(m.month3, 0) + COALESCE(m.month4, 0)
                + COALESCE(m.month5, 0) + COALESCE(m.month6, 0) + COALESCE(m.month7, 0) + COALESCE(m.month8, 0)
                + COALESCE(m.month9, 0) + COALESCE(m.month10, 0) + COALESCE(m.month11, 0) + COALESCE(m.month12, 0) AS annual,
            x.fields, l.created_at, l.created_by_id, l.updated_at, l.updated_by_id
        FROM overseas_cost_lines l
        LEFT JOIN (
            SELECT line_id, max(year) AS year,
                sum(amount) FILTER (WHERE month = 1) AS month1,
                sum(amount) FILTER (WHERE month = 2) AS month2,
                sum(amount) FILTER (WHERE month = 3) AS month3,
                sum(amount) FILTER (WHERE month = 4) AS month4,
                sum(amount) FILTER (WHERE month = 5) AS month5,
                sum(amount) FILTER (WHERE month = 6) AS month6,
                sum(amount) FILTER (WHERE month = 7) AS month7,
                sum(amount) FILTER (WHERE month = 8) AS month8,
                sum(amount) FILTER (WHERE month = 9) AS month9,
                sum(amount) FILTER (WHERE month = 10) AS month10,
                sum(amount) FILTER (WHERE month = 11) AS month11,
                sum(amount) FILTER (WHERE month = 12) AS month12
            FROM overseas_cost_months GROUP BY line_id
        ) m ON m.line_id = l.id
        LEFT JOIN (
            SELECT line_id, jsonb_object_agg(col_key, jsonb_build_object('label', label, 'value', value)) AS fields
            FROM overseas_cost_extras GROUP BY line_id
        ) x ON x.line_id = l.id
    """)

    op.drop_table('overseas_cost_months')
    op.drop_table('overseas_cost_extras')
    op.drop_table('overseas_cost_lines')


def downgrade() -> None:
    """Downgrade schema.

    주의: custom_fields의 created_at/updated_at/created_by_id/updated_by_id(옛 overseas_cost_extras의
    감사 컬럼)는 되살릴 자리가 없어 기본값(now()/NULL)으로 채운다 — upgrade 직후 상태를 되돌리는
    용도로만 쓸 것.
    """
    op.create_table('overseas_cost_lines',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('scope_key', sa.VARCHAR(length=40), autoincrement=False, nullable=False, comment='연구소 pk (overseas_labs.pk)'),
    sa.Column('version', sa.VARCHAR(length=10), autoincrement=False, nullable=False, comment='cur(당월 속보) | prev(전실행) | prior(전년 실적) | plan(경영계획)'),
    sa.Column('category_key', sa.VARCHAR(length=40), autoincrement=False, nullable=False, comment='비용항목 키. 고정 enum 아닌 자유 문자열'),
    sa.Column('actual_thru_month', sa.SMALLINT(), autoincrement=False, nullable=False, comment="실적이 반영된 마지막 달(1~12). cur 버전에서 같은 카테고리가 '실적'과 '속보'로 동시에 존재할 때 구분용. 그 외 버전은 0(해당 없음)"),
    sa.Column('extra', postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=True, comment='엑셀 업로드에서 인식 못한 열 보관용'),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False, comment='최초 입력 시각'),
    sa.Column('created_by_id', sa.INTEGER(), autoincrement=False, nullable=True, comment='최초 입력한 사용자 (users.id)'),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False, comment='마지막 갱신 시각'),
    sa.Column('updated_by_id', sa.INTEGER(), autoincrement=False, nullable=True, comment='마지막으로 수정한 사용자 (users.id)'),
    sa.Column('label', sa.VARCHAR(length=100), autoincrement=False, nullable=True, comment='사용자가 직접 추가한 행의 이름. 고정 계정(category_key가 OV_EL에 있는 키)은 NULL'),
    sa.Column('label_en', sa.VARCHAR(length=100), autoincrement=False, nullable=True, comment='사용자가 직접 추가한 행의 영문 이름(Cost Element). 고정 계정은 NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('overseas_cost_lines_created_by_id_fkey')),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('overseas_cost_lines_updated_by_id_fkey')),
    sa.PrimaryKeyConstraint('id', name=op.f('overseas_cost_lines_pkey')),
    sa.UniqueConstraint('scope_key', 'version', 'category_key', 'actual_thru_month', name=op.f('overseas_cost_lines_scope_key_version_category_key_actual_t_key')),
    comment='해외연구소 비용 데이터의 기준 행(연구소×버전×비용항목×속보실적구분). 월별 금액은 overseas_cost_months에.'
    )
    op.create_table('overseas_cost_months',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('line_id', sa.INTEGER(), autoincrement=False, nullable=False, comment='overseas_cost_lines.id (헤더 삭제 시 월별 금액도 같이 삭제)'),
    sa.Column('year', sa.SMALLINT(), autoincrement=False, nullable=False, comment='연도'),
    sa.Column('month', sa.SMALLINT(), autoincrement=False, nullable=False, comment='월(1~12)'),
    sa.Column('amount', sa.NUMERIC(precision=14, scale=2), autoincrement=False, nullable=False, comment='금액'),
    sa.ForeignKeyConstraint(['line_id'], ['overseas_cost_lines.id'], name=op.f('overseas_cost_months_line_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('overseas_cost_months_pkey')),
    sa.UniqueConstraint('line_id', 'year', 'month', name=op.f('overseas_cost_months_line_id_year_month_key')),
    comment='overseas_cost_lines 한 행의 연·월별 금액. 연도 경계 없이 임의 개수로 쌓인다.'
    )
    op.create_table('overseas_cost_extras',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('line_id', sa.INTEGER(), autoincrement=False, nullable=False, comment='overseas_cost_lines.id (헤더 삭제 시 같이 삭제)'),
    sa.Column('col_key', sa.VARCHAR(length=40), autoincrement=False, nullable=False, comment='클라이언트가 생성한 열 id ("c...")'),
    sa.Column('label', sa.VARCHAR(length=100), autoincrement=False, nullable=False, comment='열 이름'),
    sa.Column('value', sa.NUMERIC(precision=14, scale=2), autoincrement=False, nullable=False, comment='값'),
    sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False, comment='최초 입력 시각'),
    sa.Column('updated_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), autoincrement=False, nullable=False, comment='마지막 갱신 시각'),
    sa.Column('created_by_id', sa.INTEGER(), autoincrement=False, nullable=True, comment='최초 입력한 사용자 (users.id). 로그인 인증 붙기 전까지는 항상 NULL'),
    sa.Column('updated_by_id', sa.INTEGER(), autoincrement=False, nullable=True, comment='마지막으로 수정한 사용자 (users.id). 로그인 인증 붙기 전까지는 항상 NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], name=op.f('fk_overseas_cost_extras_created_by_id_users')),
    sa.ForeignKeyConstraint(['line_id'], ['overseas_cost_lines.id'], name=op.f('overseas_cost_extras_line_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['updated_by_id'], ['users.id'], name=op.f('fk_overseas_cost_extras_updated_by_id_users')),
    sa.PrimaryKeyConstraint('id', name=op.f('overseas_cost_extras_pkey')),
    sa.UniqueConstraint('line_id', 'col_key', name=op.f('overseas_cost_extras_line_id_col_key_key')),
    comment='overseas_cost_lines 한 행에 추가된 열(존재·이름·값). 1~12월과 별개 축, 집계에는 안 들어감.'
    )

    conn = op.get_bind()
    entries = sa.table(
        "overseas_cost_entries", sa.column("id"), sa.column("scope_key"), sa.column("version"),
        sa.column("category_key"), sa.column("label"), sa.column("label_en"), sa.column("actual_thru_month"),
        sa.column("custom_fields"), sa.column("year"), sa.column("created_at"), sa.column("created_by_id"),
        sa.column("updated_at"), sa.column("updated_by_id"), *[sa.column(f"month{m}") for m in range(1, 13)],
    )
    lines_table = sa.table(
        "overseas_cost_lines", sa.column("id"), sa.column("scope_key"), sa.column("version"),
        sa.column("category_key"), sa.column("label"), sa.column("label_en"), sa.column("actual_thru_month"),
        sa.column("created_at"), sa.column("created_by_id"), sa.column("updated_at"), sa.column("updated_by_id"),
    )
    months_table = sa.table(
        "overseas_cost_months", sa.column("id"), sa.column("line_id"), sa.column("year"),
        sa.column("month"), sa.column("amount"),
    )
    extras_table = sa.table(
        "overseas_cost_extras", sa.column("id"), sa.column("line_id"), sa.column("col_key"),
        sa.column("label"), sa.column("value"),
    )

    rows = conn.execute(sa.select(entries)).mappings().all()
    new_lines = [
        {
            "scope_key": row["scope_key"], "version": row["version"], "category_key": row["category_key"],
            "label": row["label"], "label_en": row["label_en"], "actual_thru_month": row["actual_thru_month"],
            "created_at": row["created_at"], "created_by_id": row["created_by_id"],
            "updated_at": row["updated_at"], "updated_by_id": row["updated_by_id"],
        }
        for row in rows
    ]
    if not new_lines:
        return

    line_ids = conn.execute(sa.insert(lines_table).returning(lines_table.c.id), new_lines).scalars().all()

    new_months = [
        {"line_id": line_id, "year": row["year"], "month": m, "amount": row[f"month{m}"]}
        for row, line_id in zip(rows, line_ids)
        if row["year"] is not None
        for m in range(1, 13)
    ]
    if new_months:
        conn.execute(sa.insert(months_table), new_months)

    new_extras = [
        {"line_id": line_id, "col_key": col_key, "label": field.get("label", ""), "value": field.get("value", 0)}
        for row, line_id in zip(rows, line_ids)
        if row["custom_fields"]
        for col_key, field in row["custom_fields"].items()
    ]
    if new_extras:
        conn.execute(sa.insert(extras_table), new_extras)

    op.drop_table('overseas_cost_entries')
