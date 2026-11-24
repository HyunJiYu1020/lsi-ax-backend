"""연구소 비용 시트의 기간 열 머리글 원문을 저장한다

왜: 취합 › 비용 표는 연구소 파일 하나를 그대로 비춰 보는 자리인데, 열 이름은 화면 코드가 만든
글자("1월")였다. 연구소가 "'26.1월 실적" · "M2601A"처럼 적어 보내면 담당자가 보는 표와 연구소
보고서의 머리글이 달라져서, 어느 열이 파일의 어느 열인지 눈으로 맞춰야 했다. 증감사유가
prev_label · cur_label · mon_label로 "파일 머리글 원문"을 이미 저장하는 것과 같은 자리를 비용
시트에도 둔다.

집계·증감분석 표는 이 값을 쓰지 않는다 — 18개 연구소를 합친 표라 어느 파일의 머리글을 대표로
쓸지 정할 수 없다(models.OverseasCostHeader 주석).

Revision ID: c1d4e7f20a93
Revises: b8d1f3a90c27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1d4e7f20a93"
down_revision: Union[str, None] = "b8d1f3a90c27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "overseas_cost_headers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "lab_pk", sa.String(length=20), nullable=False,
            comment='연구소 식별 키(overseas_labs.pk — 파트까지, 예 "SARC:CPU")',
        ),
        sa.Column("version", sa.String(length=10), nullable=False, comment="forecast(속보) | actual(실적)"),
        sa.Column(
            "yyyymm", sa.String(length=6), nullable=False,
            comment='이 파일이 속한 편성 기준연월 "YYYYMM"(cost_entries.yyyymm과 같은 기준)',
        ),
        sa.Column(
            "col_key", sa.String(length=10), nullable=False,
            comment='열 자리. "m1".."m12"는 월(기간을 12개월 너머로 받으면 "m13"…), "q1".."q4"는 분기, "y"는 연간',
        ),
        sa.Column("label", sa.String(length=100), nullable=False, comment="파일 머리글 글자 그대로(예 \"'26.1월 실적\")"),
        sa.Column(
            "source_file", sa.String(length=255), nullable=False, server_default="",
            comment="이 머리글을 읽어 온 원본 파일명",
        ),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lab_pk", "version", "yyyymm", "col_key", name="uq_overseas_cost_headers_key"),
        sa.CheckConstraint("version IN ('forecast', 'actual')", name="ck_overseas_cost_headers_version"),
        sa.CheckConstraint(r"yyyymm ~ '^\d{4}(0[1-9]|1[0-2])$'", name="ck_overseas_cost_headers_yyyymm_format"),
        sa.CheckConstraint(r"col_key ~ '^(m[1-9][0-9]?|q[1-4]|y)$'", name="ck_overseas_cost_headers_col_key"),
        comment=(
            "연구소 비용 시트의 기간 열 머리글 원문(연구소×버전×기준월×열). 취합 비용 표가 코드가 만든 "
            "'1월' 대신 이 글자를 그대로 보여준다. 집계·증감분석 표는 쓰지 않는다."
        ),
    )


def downgrade() -> None:
    op.drop_table("overseas_cost_headers")
