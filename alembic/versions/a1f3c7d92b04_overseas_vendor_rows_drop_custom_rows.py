"""외주용역 전용 테이블(overseas_vendor_rows + 월별 디테일) 신설, custom_rows 제거, sheet_rows 비우기

세 가지를 함께 한다 — 모두 "사용자가 추가한 행·시트"를 어디에 저장하는가에 대한 정리다:

1. overseas_vendor_rows / overseas_vendor_row_details 신설
   외주용역 시트는 열이 사실상 고정(Category · Project · Company · Description + 12개월)이라
   정형화 안 된 시트용 JSON 보관소(sheet_rows)에 둘 이유가 없다. 월 금액은 cost_entries /
   management_plan_entries와 같은 마스터-디테일 + 월별 세로 적재로 통일한다.

2. custom_rows 삭제
   "사용자가 추가한 행"은 처음부터 cost_entries(label · label_en · anchor_key · is_derived ·
   formula)에 저장돼 왔고, /overseas/custom-rows 엔드포인트도 cost_entries만 읽고 썼다.
   custom_rows는 한 번도 쓰이지 않은 빈 테이블(0건)이라 지운다 — 남겨두면 version 값 체계도
   어긋난 채(cur/prev/prior/plan vs cost_entries의 forecast/actual) 오해를 남긴다.

3. sheet_rows 비우기(id 1부터)
   유일한 입주자였던 외주용역 예시 데이터가 1번 테이블로 옮겨간다. 테이블 자체는 "열 구성이
   아직 확정되지 않은 새 시트"가 들어올 때의 보관소로 남겨두고, 내용만 비우고 id를 1부터
   다시 쌓게 한다.

Revision ID: a1f3c7d92b04
Revises: 39465d4a1419
Create Date: 2026-11-12 01:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f3c7d92b04'
down_revision: Union[str, Sequence[str], None] = '39465d4a1419'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'overseas_vendor_rows',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('lab_code', sa.String(length=10), nullable=False, comment='연구소 코드(overseas_labs.lab_code, 파트 없이 — SARC는 CPU+GPU 합계)'),
        sa.Column('version', sa.String(length=10), nullable=False, comment='cur(당월 속보) | prev(전실행) | prior(전년 실적) | plan(경영계획)'),
        sa.Column('row_no', sa.SmallInteger(), nullable=False, comment='업로드한 시트 안에서의 행 순서(1부터)'),
        sa.Column('category', sa.String(length=50), nullable=False, server_default='', comment='Category(외주용역 구분)'),
        sa.Column('project', sa.String(length=100), nullable=False, server_default='', comment='Project name(과제명)'),
        sa.Column('company', sa.String(length=100), nullable=False, server_default='', comment='Company / Institution(업체·기관명)'),
        sa.Column('description', sa.String(length=500), nullable=False, server_default='', comment='Description / Purpose(내용·목적)'),
        sa.Column('source_file', sa.String(length=255), nullable=False, server_default='', comment='업로드한 원본 파일명'),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('lab_code', 'version', 'row_no', name='uq_overseas_vendor_rows_key'),
        comment='해외연구소 외주용역 업로드(연구소×버전×행). 월별 금액은 overseas_vendor_row_details 참조.',
    )
    op.create_table(
        'overseas_vendor_row_details',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('vendor_row_id', sa.Integer(), nullable=False, comment='overseas_vendor_rows.id (행 삭제 시 월 금액도 같이 삭제)'),
        sa.Column('year', sa.SmallInteger(), nullable=False, comment='이 금액이 속한 연도'),
        sa.Column('month', sa.SmallInteger(), nullable=False, comment='월(1~12)'),
        sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False, server_default='0', comment='금액'),
        sa.ForeignKeyConstraint(['vendor_row_id'], ['overseas_vendor_rows.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('vendor_row_id', 'year', 'month', name='uq_overseas_vendor_row_details_key'),
        sa.CheckConstraint('month BETWEEN 1 AND 12', name='ck_overseas_vendor_row_details_month'),
        comment='외주용역 행별 월 금액(월 1행). 마스터는 overseas_vendor_rows.',
    )

    op.drop_table('custom_rows')

    # 외주용역 행이 전용 테이블로 옮겨가 비는 테이블 — 내용만 비우고 id를 1부터 다시 쌓는다.
    op.execute('TRUNCATE TABLE sheet_rows RESTART IDENTITY')
    op.execute(
        "COMMENT ON TABLE sheet_rows IS "
        "'열 구성이 아직 확정되지 않은 새 시트의 원본 행(JSON) 보관소. 19개 계정이 공유. "
        "구성이 확정되면 전용 테이블로 옮긴다(외주용역 → overseas_vendor_rows).'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "COMMENT ON TABLE sheet_rows IS "
        "'정형화 안 된 시트의 원본 행(JSON) 저장소. 19개 계정이 공유.'"
    )
    op.create_table(
        'custom_rows',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('account_key', sa.String(length=32), nullable=False, comment='accounts.key'),
        sa.Column('scope_key', sa.String(length=40), nullable=False, comment='계정 안에서의 행 단위 식별자'),
        sa.Column('version', sa.String(length=10), nullable=False, comment='cur | prev | prior | plan'),
        sa.Column('row_key', sa.String(length=40), nullable=False, comment='클라이언트가 생성한 행 id ("cx...")'),
        sa.Column('label', sa.String(length=100), nullable=False, server_default='', comment='사용자가 입력한 행 이름'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='행 추가 시각'),
        sa.Column('created_by_id', sa.Integer(), nullable=True, comment='행을 추가한 사용자 (users.id)'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False, comment='마지막 갱신 시각'),
        sa.Column('updated_by_id', sa.Integer(), nullable=True, comment='마지막으로 수정한 사용자 (users.id)'),
        sa.ForeignKeyConstraint(['account_key'], ['accounts.key']),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['updated_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('account_key', 'scope_key', 'version', 'row_key'),
        comment='사용자가 직접 추가한 행(존재·이름만). 월별 값은 cost_entries/cost_entry_details 참조.',
    )
    op.drop_table('overseas_vendor_row_details')
    op.drop_table('overseas_vendor_rows')
