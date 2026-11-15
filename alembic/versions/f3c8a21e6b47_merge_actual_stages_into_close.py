"""Phase 4: overseas_actual_stages를 close_account_stages로 병합

Revision ID: f3c8a21e6b47
Revises: e1a4c7b93d28
Create Date: 2026-11-16 01:10:00.000000

왜 (PLAN.md P2-6): 같은 사실("1차 완료")을 두 테이블이 나눠 들고 애플리케이션이 동기화할 의무를
지고 있었다. models/close.py의 주석이 스스로 "해외연구소의 first_done_at은
overseas_actual_stages.close_first_done과 짝이다"라고 적을 만큼 노골적인 중복이다. 실제로
close.py의 매핑 완료 핸들러가 두 테이블을 같이 써야 했고, 한쪽만 바뀌면 화면의 [1차 분석] 탭이
열렸는데 값은 없거나 그 반대가 된다.

병합 규칙:
- close_first_done → close_account_stages.mapping_done (account_key='overseas')
  "1차 완료"의 진실이 한 곳이 된다. 해외연구소는 매핑 완료가 곧 1차 완료라 이미 같은 사실이었다.
- first_locked · first_locked_at → close_account_stages로 이동 (이 둘만 고유한 정보였다)
- overseas_actual_stages drop

first_locked를 계정 공용 테이블에 두는 게 맞는가: 맞다. "보고한 1차 값을 더 고치지 못하게 잠근다"는
개념은 계정에 고유하지 않다(전표를 받아 1차를 보고하는 계정이면 다 필요하다). 지금 쓰는 계정이
해외연구소뿐이라 다른 계정 행에서는 계속 false로 남는다 — 그건 "아직 안 쓴다"는 뜻이고, 컬럼을
따로 만들 이유는 아니다.

2단계 배포(읽기 전환 → 검증 → drop)를 하지 않는다: 개발용 데이터라 재시드 가능하다는 결정
(PLAN.md 열린 질문 1, 2026-11-16). 한 리비전에서 컬럼을 추가하고 옛 테이블을 지운다.
운영 데이터가 생긴 뒤에 이 병합을 하게 되면 PLAN의 4a/4b 절차를 따라야 한다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3c8a21e6b47"
down_revision: Union[str, Sequence[str], None] = "e1a4c7b93d28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "close_account_stages",
        sa.Column(
            "first_locked", sa.Boolean(), nullable=False, server_default=sa.false(),
            comment="True면 [1차 고정] — 1차 값·사유 쓰기를 거부한다(해제해야 수정 가능). "
            "versions.status와 무관하다: 1차 고정과 버전 확정은 다른 층의 결정이다",
        ),
    )
    op.add_column(
        "close_account_stages",
        sa.Column(
            "first_locked_at", sa.DateTime(timezone=True), nullable=True,
            comment="[1차 고정]을 누른 시각. 해제하면 NULL로 지운다",
        ),
    )
    # 재시드 전제라 데이터를 옮기지 않고 버린다. 옮겨야 하는 상황이면 아래가 그 쿼리다:
    #   UPDATE close_account_stages c SET first_locked = o.first_locked,
    #          first_locked_at = o.first_locked_at, mapping_done = o.close_first_done
    #     FROM overseas_actual_stages o
    #    WHERE c.account_key = 'overseas'
    #      AND c.basis_year = o.basis_year AND c.basis_month = o.basis_month;
    op.drop_table("overseas_actual_stages")


def downgrade() -> None:
    op.create_table(
        "overseas_actual_stages",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("basis_year", sa.SmallInteger(), nullable=False, comment="실적 대상 연도"),
        sa.Column("basis_month", sa.SmallInteger(), nullable=False, comment="실적 대상 월(1~12)"),
        sa.Column(
            "close_first_done", sa.Boolean(), nullable=False, server_default=sa.false(),
            comment="실적결산 1차가 끝났는지. False면 화면의 1차 분석 탭을 비활성으로 둔다",
        ),
        sa.Column(
            "first_locked", sa.Boolean(), nullable=False, server_default=sa.false(),
            comment="True면 [1차 고정] 상태 — 1차 값·사유 쓰기를 거부한다",
        ),
        sa.Column("first_locked_at", sa.DateTime(timezone=True), nullable=True, comment="[1차 고정]을 누른 시각"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("basis_year", "basis_month", name="uq_overseas_actual_stages_key"),
        comment="실적 연월의 1차 분석 상태. close_first_done=실적결산 1차 완료 여부(화면 1차 탭 활성), "
        "first_locked=[1차 고정](보고한 1차 값·사유 수정 금지). versions.status에는 영향 없음.",
    )
    # 되돌릴 때는 해외연구소 행만 옛 테이블로 복원한다(그 테이블은 계정 구분이 없었다).
    op.execute(
        sa.text(
            "INSERT INTO overseas_actual_stages "
            "(basis_year, basis_month, close_first_done, first_locked, first_locked_at) "
            "SELECT basis_year, basis_month, mapping_done, first_locked, first_locked_at "
            "FROM close_account_stages WHERE account_key = 'overseas'"
        )
    )
    op.drop_column("close_account_stages", "first_locked_at")
    op.drop_column("close_account_stages", "first_locked")
