"""overseas reason rows and cp rows

Revision ID: 0e2e2f6f3e34
Revises: edcb75f1ce13
Create Date: 2026-11-05 14:31:27.024549

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0e2e2f6f3e34'
down_revision: Union[str, Sequence[str], None] = 'edcb75f1ce13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('overseas_cp_rows',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('year', sa.String(length=4), nullable=False, comment='CP 계약 연도'),
    sa.Column('poc_type', sa.String(length=40), nullable=False, comment='POC 구분(Type)'),
    sa.Column('poc_proj', sa.String(length=100), nullable=False, comment='프로젝트명(Projects)'),
    sa.Column('poc_desc', sa.String(length=500), nullable=False, comment='POC Description'),
    sa.Column('title', sa.String(length=100), nullable=False, comment='직급(Job Title)'),
    sa.Column('dedicated', sa.Numeric(precision=10, scale=2), nullable=False, comment='전담(Dedicated) 인원'),
    sa.Column('common', sa.Numeric(precision=10, scale=2), nullable=False, comment='공통(Common) 인원'),
    sa.Column('man_month', sa.Numeric(precision=10, scale=2), nullable=False, comment='맨먼스(Man-month)'),
    sa.Column('cost_year', sa.Numeric(precision=14, scale=2), nullable=False, comment='연간 비용(Cost Year)'),
    sa.Column('poc_cost_sum', sa.Numeric(precision=14, scale=2), nullable=False, comment='POC 비용 합계(각 행에 반복 저장)'),
    sa.Column('poc_mm_sum', sa.Numeric(precision=10, scale=2), nullable=False, comment='POC 맨먼스 합계(각 행에 반복 저장)'),
    sa.Column('poc_direct', sa.Numeric(precision=14, scale=2), nullable=False, comment='POC Direct R&D(각 행에 반복 저장)'),
    sa.Column('poc_ovh', sa.Numeric(precision=14, scale=2), nullable=False, comment='POC Overhead(각 행에 반복 저장)'),
    sa.Column('poc_total', sa.Numeric(precision=14, scale=2), nullable=False, comment='POC Total(각 행에 반복 저장)'),
    sa.Column('source_file', sa.String(length=255), nullable=False, comment='업로드한 원본 파일명'),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('year', 'poc_type', 'poc_proj', 'title', name='uq_overseas_cp_rows_key'),
    comment='CP 계약 업로드(연간). POC(프로젝트)×직급 하나당 1행, POC 헤더값은 각 행에 반복 저장.'
    )
    op.create_table('overseas_reason_rows',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('lab_code', sa.String(length=10), nullable=False, comment='연구소 코드(overseas_labs.lab_code, 파트 없이 — SARC는 CPU+GPU 합계)'),
    sa.Column('version', sa.String(length=10), nullable=False, comment='cur(당월 속보) | prev(전실행) | prior(전년 실적) | plan(경영계획)'),
    sa.Column('category_key', sa.String(length=40), nullable=False, comment='비용항목 키'),
    sa.Column('block', sa.String(length=1), nullable=False, comment='y(연간 블록) | m(월별 블록)'),
    sa.Column('mon_label', sa.String(length=10), nullable=False, comment='월별 블록일 때의 월 라벨(파일 머리글 원문)'),
    sa.Column('plan_value', sa.Numeric(precision=14, scale=2), nullable=False, comment='PLAN 값'),
    sa.Column('prev_value', sa.Numeric(precision=14, scale=2), nullable=False, comment='전기 값'),
    sa.Column('cur_value', sa.Numeric(precision=14, scale=2), nullable=False, comment='당기 값'),
    sa.Column('diff_plan', sa.Numeric(precision=14, scale=2), nullable=False, comment='당기 - PLAN'),
    sa.Column('diff_prev', sa.Numeric(precision=14, scale=2), nullable=False, comment='당기 - 전기'),
    sa.Column('remark_plan', sa.String(length=500), nullable=False, comment='vs PLAN 사유'),
    sa.Column('remark_prev', sa.String(length=500), nullable=False, comment='vs 전기 사유'),
    sa.Column('prev_label', sa.String(length=20), nullable=False, comment="전기 열 머리글 원문(예: 'M2608A')"),
    sa.Column('cur_label', sa.String(length=20), nullable=False, comment="당기 열 머리글 원문(예: 'M2609A')"),
    sa.Column('source_file', sa.String(length=255), nullable=False, comment='업로드한 원본 파일명'),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('lab_code', 'version', 'category_key', 'block', name='uq_overseas_reason_rows_key'),
    comment='해외연구소 증감사유 업로드(연구소×버전×비용항목×구간). PLAN·전기·당기 비교값과 사유 텍스트.'
    )
    # snapshot->forecast 리네임(edcb75f1ce13)에서 테이블/컬럼은 옮겼지만 이 코멘트는 그때 빠뜨림 — 같이 정리.
    op.alter_column('fx_applied_rates', 'source',
               existing_type=sa.VARCHAR(length=10),
               comment='이 값의 출처: actual | forecast | plan',
               existing_comment='이 값의 출처: actual | snapshot | plan',
               existing_nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('fx_applied_rates', 'source',
               existing_type=sa.VARCHAR(length=10),
               comment='이 값의 출처: actual | snapshot | plan',
               existing_comment='이 값의 출처: actual | forecast | plan',
               existing_nullable=False)
    op.drop_table('overseas_reason_rows')
    op.drop_table('overseas_cp_rows')
