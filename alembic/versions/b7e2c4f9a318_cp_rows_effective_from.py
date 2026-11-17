"""overseas_cp_rows에 effective_from — CP 계약 변경이 과거 버전으로 소급되지 않게

CP 분기 정산액은 저장된 값이 아니라 조회할 때마다 다시 계산되는 값이다(연 계약액 ÷ 4를
3·6·9·12월에 넣는다). 그래서 이 테이블에 "지금 값" 한 세트만 두면, 계약액을 고치는 순간 이미
마감해서 보고한 과거 버전의 분기 정산액까지 새 계약액으로 다시 계산된다.

공통 RULE("기준정보 변경은 변경한 버전부터 적용, 지난 버전 영향 없음")을 지키려고 시점별 스냅샷으로
바꾼다 — 읽을 때는 effective_from이 그 버전 시점 이하인 가장 최근 세트를 통째로 쓴다. 연구소
기준정보(overseas_lab_revisions)와 같은 방식이고, 차이는 "한 줄"이 아니라 "행 집합"이라 변경분이
아니라 세트를 통째로 한 벌 더 넣는다는 점이다(CP는 연 1회 전체를 올리는 파일이라 그게 자연스럽다).

기존 행은 전부 '0000-01'("처음부터")로 올린다 — 이력이 생기기 전부터 쓰던 값이라, 어느 버전에서
읽어도 지금과 같은 값이 나온다(= 이 마이그레이션만으로는 아무 숫자도 바뀌지 않는다).

Revision ID: b7e2c4f9a318
Revises: a9d4e71c58b3
"""

from alembic import op
import sqlalchemy as sa

revision = "b7e2c4f9a318"
down_revision = "a9d4e71c58b3"
branch_labels = None
depends_on = None

_EPOCH = "0000-01"
_COMMENT = (
    '이 세트가 적용되기 시작하는 편성 기준연월 "YYYY-MM". \'0000-01\'은 "처음부터"(이력이 '
    "생기기 전부터 쓰던 값)를 뜻한다"
)
_TABLE_COMMENT = (
    "CP 계약 업로드(연간) 시점별 스냅샷. POC(프로젝트)×직급 하나당 1행, POC 헤더값은 각 행에 "
    "반복 저장. 어떤 버전을 볼 때 effective_from이 그 버전 시점 이하인 가장 최근 세트를 통째로 "
    "쓴다 — 계약액을 고쳐도 지난 버전의 분기 정산액이 소급해서 바뀌지 않게 한다."
)
_OLD_TABLE_COMMENT = "CP 계약 업로드(연간). POC(프로젝트)×직급 하나당 1행, POC 헤더값은 각 행에 반복 저장."


def upgrade() -> None:
    # server_default로 기존 행을 '처음부터'로 채운 뒤 기본값을 뗀다 — 기본값을 남겨 두면 적용
    # 시점을 넣지 않은 INSERT가 조용히 '0000-01'로 들어가서, 새 계약이 과거 버전까지 소급된다.
    op.add_column(
        "overseas_cp_rows",
        sa.Column("effective_from", sa.String(7), nullable=False, server_default=_EPOCH, comment=_COMMENT),
    )
    op.alter_column("overseas_cp_rows", "effective_from", server_default=None)
    op.create_check_constraint(
        "ck_overseas_cp_rows_effective_from_format",
        "overseas_cp_rows",
        r"effective_from ~ '^\d{4}-(0[1-9]|1[0-2])$'",
    )
    # 유니크 키에 적용 시점을 넣는다 — 같은 연도·POC·직급이 시점마다 한 벌씩 존재해야 한다.
    op.drop_constraint("uq_overseas_cp_rows_key", "overseas_cp_rows", type_="unique")
    op.create_unique_constraint(
        "uq_overseas_cp_rows_key",
        "overseas_cp_rows",
        ["year", "effective_from", "poc_type", "poc_proj", "title"],
    )
    op.create_table_comment("overseas_cp_rows", _TABLE_COMMENT, existing_comment=_OLD_TABLE_COMMENT)


def downgrade() -> None:
    # 시점이 여러 벌이면 옛 유니크 키(연도×POC×직급)를 다시 걸 수 없다 — 가장 최근 세트만 남긴다.
    op.execute(
        """
        DELETE FROM overseas_cp_rows a
        USING overseas_cp_rows b
        WHERE a.year = b.year AND a.poc_type = b.poc_type AND a.poc_proj = b.poc_proj
          AND a.title = b.title AND a.effective_from < b.effective_from
        """
    )
    op.create_table_comment("overseas_cp_rows", _OLD_TABLE_COMMENT, existing_comment=_TABLE_COMMENT)
    op.drop_constraint("uq_overseas_cp_rows_key", "overseas_cp_rows", type_="unique")
    op.create_unique_constraint(
        "uq_overseas_cp_rows_key", "overseas_cp_rows", ["year", "poc_type", "poc_proj", "title"]
    )
    op.drop_constraint("ck_overseas_cp_rows_effective_from_format", "overseas_cp_rows", type_="check")
    op.drop_column("overseas_cp_rows", "effective_from")
