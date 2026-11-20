"""증감분석 화면 테이블 4개 — 증감사유 워크플로 · 증감 동인 표 · 실적 1차 값 · 실적 1차 상태

왜 네 개가 같이 들어가는가: 전부 "증감분석 한 화면"을 위한 것이고 서로 맞물린다 —
증감사유는 실적일 때 1차/2차(stage)로 갈리고, 1차 사유 쓰기는 1차 고정(overseas_actual_stages)
상태를 본다. 한 덩어리로 올려야 중간 상태(1차 사유는 저장되는데 고정은 못 하는 등)가 안 생긴다.

Revision ID: a2c7d9e41b63
Revises: a1c5d93f6b24
Create Date: 2026-11-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a2c7d9e41b63"
down_revision: Union[str, None] = "a1c5d93f6b24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "overseas_variance_reasons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("basis_year", sa.SmallInteger(), nullable=False, comment="편성 기준연도"),
        sa.Column("basis_month", sa.SmallInteger(), nullable=False, comment="편성 기준월(1~12)"),
        sa.Column(
            "kind",
            sa.String(length=10),
            nullable=False,
            comment="forecast(속보) | actual(실적) — versions.kind와 같은 값",
        ),
        sa.Column(
            "stage",
            sa.String(length=8),
            nullable=False,
            server_default="",
            comment="실적 분석 단계: first(1차 ERP 약식) | second(2차 PL). 속보는 빈 문자열",
        ),
        sa.Column(
            "base_version",
            sa.String(length=10),
            nullable=False,
            comment="비교 기준: prev(전실행) | prior(전년) | plan(경영계획)",
        ),
        sa.Column("period", sa.String(length=4), nullable=False, comment="기간: y(연간) | q1~q4 | m1~m12"),
        sa.Column(
            "lab_scope",
            sa.String(length=20),
            nullable=False,
            comment='화면에서 고른 연구소 범위. "ALL"(전체) | 연구소 코드 | 연구소 pk("SARC:CPU")',
        ),
        sa.Column(
            "category_key", sa.String(length=40), nullable=False, comment="비용항목 키(labor · travel · tot ...)"
        ),
        sa.Column(
            "source_text",
            sa.Text(),
            nullable=False,
            server_default="",
            comment="연구소가 써 보낸 회신 원문(보통 영문). 번역·초안의 근거라 지우지 않고 같이 남긴다",
        ),
        sa.Column(
            "source_ko",
            sa.Text(),
            nullable=False,
            server_default="",
            comment="회신 원문의 한국어 번역(overseas_reason_translations와 같은 규칙)",
        ),
        sa.Column(
            "ai_draft",
            sa.Text(),
            nullable=False,
            server_default="",
            comment="번역 + 증감 숫자로 AI가 만든 보고용 사유 초안",
        ),
        sa.Column(
            "reason_text",
            sa.Text(),
            nullable=False,
            server_default="",
            comment="담당자가 고쳐서 [저장]한 확정 사유. 보고서에 실리는 문장이다",
        ),
        sa.Column(
            "saved_diff",
            sa.Numeric(precision=18, scale=2),
            nullable=True,
            comment="[저장] 시점의 증감액(원). 지금 증감액과 다르면 재업로드로 금액이 바뀐 것이라 needs_recheck를 켠다",
        ),
        sa.Column(
            "needs_recheck",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment='True면 저장 뒤 금액이 바뀌었다는 뜻 — 화면에 "재확인 필요"로 표시한다(사유는 유지)',
        ),
        sa.Column(
            "saved_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="[저장]을 누른 시각. 초안만 있으면 NULL",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="이 칸이 처음 생긴 시각",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="마지막 갱신 시각",
        ),
        sa.CheckConstraint("kind IN ('forecast', 'actual')", name="ck_overseas_variance_reasons_kind"),
        sa.CheckConstraint("stage IN ('', 'first', 'second')", name="ck_overseas_variance_reasons_stage"),
        sa.CheckConstraint(
            "base_version IN ('prev', 'prior', 'plan')", name="ck_overseas_variance_reasons_base_version"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "basis_year",
            "basis_month",
            "kind",
            "stage",
            "base_version",
            "period",
            "lab_scope",
            "category_key",
            name="uq_overseas_variance_reasons_key",
        ),
        comment=(
            "증감분석 증감사유 한 칸의 전체 상태(회신 원문·번역·AI 초안·확정 문장). 저장 시점 "
            "증감액(saved_diff)을 같이 남겨 재업로드로 금액이 바뀌면 needs_recheck로 '재확인 필요'를 띄운다."
        ),
    )

    op.create_table(
        "overseas_variance_drivers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("basis_year", sa.SmallInteger(), nullable=False, comment="이 표를 붙인 편성 기준연도"),
        sa.Column("basis_month", sa.SmallInteger(), nullable=False, comment="이 표를 붙인 편성 기준월(1~12)"),
        sa.Column("kind", sa.String(length=10), nullable=False, comment="forecast(속보) | actual(실적)"),
        sa.Column(
            "lab_scope",
            sa.String(length=20),
            nullable=False,
            server_default="ALL",
            comment='연구소 범위("ALL" | 연구소 코드 | pk)',
        ),
        sa.Column(
            "driver_key",
            sa.String(length=40),
            nullable=False,
            comment='표 식별 키(같은 버전 안에서 유일). 예: "labor_hc_rate"',
        ),
        sa.Column(
            "title", sa.String(length=100), nullable=False, comment='표 제목("인건비 동인 — 인력수 × 인당 단가")'
        ),
        sa.Column(
            "target_key",
            sa.String(length=40),
            nullable=False,
            comment="분해 대상 비용항목 키(labor 등). 이 행 아래에 표를 붙인다",
        ),
        sa.Column(
            "factors",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                '동인 행 목록. [{"label":"인력수","op":"ref","operands":["hc"],"unit":"hc"}, '
                '{"label":"인당 단가","op":"div_elementwise","operands":["labor","hc"],"unit":"krw"}]'
            ),
        ),
        sa.Column(
            "requested_text",
            sa.String(length=500),
            nullable=False,
            server_default="",
            comment="담당자가 요청한 원문 문장. 나중에 이 표가 왜 생겼는지 되짚는 근거",
        ),
        sa.Column(
            "carry_over",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment='True면 "다음 버전에도?"에 승인한 표 — 이후 버전 조회에도 따라온다',
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="표를 붙인 시각",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="마지막 갱신 시각",
        ),
        sa.CheckConstraint("kind IN ('forecast', 'actual')", name="ck_overseas_variance_drivers_kind"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "basis_year", "basis_month", "kind", "lab_scope", "driver_key", name="uq_overseas_variance_drivers_key"
        ),
        comment=(
            "증감분석 화면에 AI가 덧붙인 증감 동인 분해 표. carry_over=True면 다음 버전에도 따라온다. "
            "금액이 아니라 계산식(factors)만 담아 원본이 바뀌면 같이 바뀐다."
        ),
    )

    op.create_table(
        "overseas_actual_first_rows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("basis_year", sa.SmallInteger(), nullable=False, comment="실적 대상 연도"),
        sa.Column("basis_month", sa.SmallInteger(), nullable=False, comment="실적 대상 월(1~12)"),
        sa.Column(
            "lab_code",
            sa.String(length=10),
            nullable=False,
            comment="연구소 코드(overseas_labs.lab_code, 파트 없이 — 1차는 연구소 총액만 올라온다)",
        ),
        sa.Column(
            "local_amount",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
            server_default="0",
            comment="그 달 연구소 총액(현지통화). 통화는 그 시점 연구소 기준정보의 currency",
        ),
        sa.Column(
            "usd_amount",
            sa.Numeric(precision=18, scale=2),
            nullable=True,
            comment="ERP가 $ 환산액을 같이 준 경우의 값(K$). NULL이면 환율로 계산한다",
        ),
        sa.Column(
            "reason_text",
            sa.Text(),
            nullable=False,
            server_default="",
            comment="1차 사유. 2차 사유(overseas_variance_reasons)와 별도로 보관한다",
        ),
        sa.Column(
            "source_file",
            sa.String(length=255),
            nullable=False,
            server_default="",
            comment="업로드한 ERP 약식 파일명",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="처음 저장한 시각",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="마지막 갱신 시각",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("basis_year", "basis_month", "lab_code", name="uq_overseas_actual_first_rows_key"),
        comment=(
            "실적 1차 분석(ERP 약식)의 연구소별 그 달 총액 + 1차 사유. 2차(PL, cost_entries)와 별도로 "
            "저장해 이미 보고한 1차 값이 2차 업로드에 덮이지 않게 한다."
        ),
    )

    op.create_table(
        "overseas_actual_stages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("basis_year", sa.SmallInteger(), nullable=False, comment="실적 대상 연도"),
        sa.Column("basis_month", sa.SmallInteger(), nullable=False, comment="실적 대상 월(1~12)"),
        sa.Column(
            "close_first_done",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="실적결산 1차가 끝났는지. False면 화면의 1차 분석 탭을 비활성으로 둔다",
        ),
        sa.Column(
            "first_locked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="True면 [1차 고정] 상태 — 1차 값·사유 쓰기를 거부한다(해제해야 수정 가능)",
        ),
        sa.Column(
            "first_locked_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="[1차 고정]을 누른 시각. 해제하면 NULL로 지운다",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="이 행이 생긴 시각",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="마지막 갱신 시각",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("basis_year", "basis_month", name="uq_overseas_actual_stages_key"),
        comment=(
            "실적 연월의 1차 분석 상태. close_first_done=실적결산 1차 완료 여부(화면 1차 탭 활성), "
            "first_locked=[1차 고정](보고한 1차 값·사유 수정 금지). versions.status에는 영향 없음."
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("overseas_actual_stages")
    op.drop_table("overseas_actual_first_rows")
    op.drop_table("overseas_variance_drivers")
    op.drop_table("overseas_variance_reasons")
