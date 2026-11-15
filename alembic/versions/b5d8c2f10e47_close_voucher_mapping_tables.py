"""실적결산 · 전표매핑 테이블 5개 — ERP 전표 + 매핑 판단 + AI 규칙 + 분류 기준정보 + 결산 상태

전표(close_vouchers)와 매핑 판단(close_voucher_maps)을 가른 게 핵심이다. ERP가 같은 달 전표를
재전송하면 전표는 덮어쓰지만 담당자가 손으로 고친 매핑은 살아남아야 한다(공통 RULE: 전표 재수신 시
기존 매핑 유지, 신규만 매칭) — 그래서 매핑의 키가 전표 id가 아니라 (연월·전표번호·라인)이다.

해외연구소는 close_account_stages에서 '1차 완료'까지만 간다. 계정 완료는 PL이 나온 뒤 실적
시뮬레이션 [확정]으로만 되므로 version_account_status와 다른 층에 둔다.

Revision ID: b5d8c2f10e47
Revises: a2c7d9e41b63
Create Date: 2026-11-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5d8c2f10e47"
down_revision: Union[str, None] = "a2c7d9e41b63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "close_vouchers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("basis_year", sa.SmallInteger(), nullable=False, comment="결산 대상 연도(전기일이 아니라 결산 귀속 연도)"),
        sa.Column("basis_month", sa.SmallInteger(), nullable=False, comment="결산 대상 월(1~12)"),
        sa.Column("voucher_no", sa.String(length=20), nullable=False, comment="ERP 전표번호"),
        sa.Column(
            "line_no",
            sa.SmallInteger(),
            nullable=False,
            server_default="1",
            comment="전표 안의 라인 번호(한 전표가 여러 줄로 온다)",
        ),
        sa.Column("posted_on", sa.Date(), nullable=True, comment="전기일. 결산 귀속월과 다를 수 있어 따로 담는다"),
        sa.Column(
            "account_code", sa.String(length=20), nullable=False, server_default="", comment="ERP 비용계정 코드(53840100 등)"
        ),
        sa.Column("account_name", sa.String(length=100), nullable=False, server_default="", comment="ERP 비용계정명"),
        sa.Column("cost_center", sa.String(length=20), nullable=False, server_default="", comment="코스트센터(부서코드)"),
        sa.Column("cost_center_name", sa.String(length=100), nullable=False, server_default="", comment="부서명"),
        sa.Column(
            "project_code",
            sa.String(length=40),
            nullable=False,
            server_default="",
            comment="과제코드(WBS). 해외연구소 전표는 이 단위로 떨어져서 연구소 매핑의 주 단서다",
        ),
        sa.Column(
            "vendor", sa.String(length=100), nullable=False, server_default="", comment="거래처명. AI 매칭의 가장 강한 단서"
        ),
        sa.Column("text", sa.Text(), nullable=False, server_default="", comment="적요. 연구소 이름·CP 표시가 여기만 있는 전표가 있다"),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="KRW", comment="거래 통화"),
        sa.Column("fc_amount", sa.Numeric(precision=18, scale=2), nullable=False, server_default="0", comment="거래통화 금액(원본)"),
        sa.Column(
            "krw_amount",
            sa.Numeric(precision=18, scale=0),
            nullable=False,
            server_default="0",
            comment="원화 금액(원본). 집계·다운로드가 쓰는 값",
        ),
        sa.Column("ref_no", sa.String(length=40), nullable=False, server_default="", comment="품의번호"),
        sa.Column(
            "account_key",
            sa.String(length=32),
            nullable=False,
            server_default="",
            comment="자동 분류된 19개 계정 key(overseas 등). 빈 문자열이면 미분류",
        ),
        sa.Column(
            "classify_by",
            sa.String(length=10),
            nullable=False,
            server_default="",
            comment="분류 근거: rule(부서코드+비용계정 기준정보) | account(비용계정만) | user(직접 지정)",
        ),
        sa.Column(
            "classify_note",
            sa.String(length=200),
            nullable=False,
            server_default="",
            comment="분류 근거 설명(화면에 그대로 보여준다)",
        ),
        sa.Column(
            "source_file",
            sa.String(length=255),
            nullable=False,
            server_default="",
            comment="업로드 파일명. ERP I/F로 받으면 'ERP I/F'",
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="이 전표를 받은 시각(재수신하면 갱신된다)",
        ),
        sa.CheckConstraint("basis_month BETWEEN 1 AND 12", name="ck_close_vouchers_basis_month"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("basis_year", "basis_month", "voucher_no", "line_no", name="uq_close_vouchers_key"),
        comment="실적결산 ERP 전표 라인(연월×전표번호×라인). 받은 값은 고치지 않고, 분류 결과"
        "(account_key)만 채운다. 매핑 판단은 close_voucher_maps에 따로 둔다.",
    )
    op.create_index("ix_close_vouchers_account_key", "close_vouchers", ["account_key"])
    op.create_index(
        "ix_close_vouchers_period_account", "close_vouchers", ["basis_year", "basis_month", "account_key"]
    )

    op.create_table(
        "close_voucher_maps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("basis_year", sa.SmallInteger(), nullable=False, comment="결산 대상 연도"),
        sa.Column("basis_month", sa.SmallInteger(), nullable=False, comment="결산 대상 월(1~12)"),
        sa.Column(
            "voucher_no",
            sa.String(length=20),
            nullable=False,
            comment="ERP 전표번호(close_vouchers와 같은 값, FK는 아니다)",
        ),
        sa.Column("line_no", sa.SmallInteger(), nullable=False, server_default="1", comment="전표 라인 번호"),
        sa.Column(
            "account_key",
            sa.String(length=32),
            nullable=False,
            server_default="",
            comment="이 매핑이 속한 계정 key(이관 후에는 받은 계정)",
        ),
        sa.Column(
            "scope_key",
            sa.String(length=50),
            nullable=False,
            server_default="",
            comment='붙일 곳. 해외연구소는 overseas_labs.pk("SARC:CPU"), 부서별 계정은 코스트센터. 빈 문자열이면 미매핑',
        ),
        sa.Column(
            "decided_by",
            sa.String(length=4),
            nullable=False,
            server_default="ai",
            comment="ai(AI 1차) | rule(승인된 규칙 자동 적용) | user(담당자 직접)",
        ),
        sa.Column(
            "confidence",
            sa.String(length=4),
            nullable=False,
            server_default="",
            comment="AI 신뢰도 high|mid|low. 담당자가 고른 뒤에는 빈 문자열(신뢰도 개념이 없다)",
        ),
        sa.Column(
            "reason",
            sa.String(length=200),
            nullable=False,
            server_default="",
            comment="판단 근거 한 줄(화면 'AI 추천 · 근거' 칸)",
        ),
        sa.Column(
            "excluded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="제외 여부. True면 어느 연구소에도 붙지 않지만 제외분 합계로는 보여준다",
        ),
        sa.Column(
            "exclude_reason",
            sa.String(length=200),
            nullable=False,
            server_default="",
            comment="제외 사유(NRE 공용 · 적요에 CP 없음 등)",
        ),
        sa.Column(
            "transferred_to",
            sa.String(length=32),
            nullable=False,
            server_default="",
            comment="이관 보낸 대상 계정 key. 채워져 있으면 이 계정 집계에서 빠진다",
        ),
        sa.Column(
            "transferred_from",
            sa.String(length=32),
            nullable=False,
            server_default="",
            comment="이관 받은 원래 계정 key. 채워져 있으면 '이관 받음'으로 표시한다",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="마지막으로 고친 시각",
        ),
        sa.CheckConstraint("decided_by IN ('ai', 'rule', 'user')", name="ck_close_voucher_maps_decided_by"),
        sa.CheckConstraint("confidence IN ('high', 'mid', 'low', '')", name="ck_close_voucher_maps_confidence"),
        sa.CheckConstraint("NOT (excluded AND scope_key <> '')", name="ck_close_voucher_maps_excluded_no_scope"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("basis_year", "basis_month", "voucher_no", "line_no", name="uq_close_voucher_maps_key"),
        comment="전표 한 줄의 매핑 판단(붙일 곳·제외·이관). 전표와 테이블을 갈라 ERP 재수신에도 "
        "담당자 수정이 살아남게 한다 — 키가 전표 id가 아니라 (연월·전표번호·라인)이다.",
    )

    op.create_table(
        "close_match_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_key", sa.String(length=32), nullable=False, comment="이 규칙이 적용되는 계정 key(overseas 등)"),
        sa.Column(
            "match_field",
            sa.String(length=12),
            nullable=False,
            comment="무엇으로 거는지: vendor(거래처) | text(적요 포함) | cost_center(코스트센터) | project(과제코드)",
        ),
        sa.Column(
            "match_value",
            sa.String(length=100),
            nullable=False,
            comment="비교할 값. text는 '포함'으로, 나머지는 '같음'으로 본다",
        ),
        sa.Column(
            "scope_key",
            sa.String(length=50),
            nullable=False,
            server_default="",
            comment="붙일 곳(연구소 pk 등). excluded가 True면 빈 문자열",
        ),
        sa.Column(
            "excluded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="True면 '이 조건은 제외한다'는 규칙",
        ),
        sa.Column(
            "hit_count",
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
            comment="규칙을 만들게 한 반복 수정 횟수(화면에 '3번 같은 수정'으로 보여준다)",
        ),
        sa.Column(
            "note",
            sa.String(length=200),
            nullable=False,
            server_default="",
            comment="규칙 설명. 나중에 왜 만든 규칙인지 알아보려면 필요하다",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="승인한 시각",
        ),
        sa.CheckConstraint(
            "match_field IN ('vendor', 'text', 'cost_center', 'project')", name="ck_close_match_rules_match_field"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_key", "match_field", "match_value", name="uq_close_match_rules_key"),
        comment="담당자가 승인한 전표→편성항목 매칭 규칙. 반복 수정을 감지해 물어보고, 승인한 "
        "것만 남겨 다음 달부터 자동 적용한다(승인 없이 자동 생성하지 않는다).",
    )

    op.create_table(
        "close_dept_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "cost_center",
            sa.String(length=20),
            nullable=False,
            server_default="",
            comment="코스트센터(부서코드). 빈 문자열이면 비용계정만으로 분류한다",
        ),
        sa.Column("account_code", sa.String(length=20), nullable=False, comment="ERP 비용계정 코드"),
        sa.Column("account_key", sa.String(length=32), nullable=False, comment="이 조합이 가는 19개 계정 key"),
        sa.Column(
            "is_shared",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="공용 부서코드 여부(NRE 공용 등). True면 전표만으로 연구소를 가를 수 없어 2단계에서 적요까지 본다",
        ),
        sa.Column(
            "note",
            sa.String(length=200),
            nullable=False,
            server_default="",
            comment="기준 설명(화면 분류 근거에 그대로 쓰인다)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="마지막으로 고친 시각",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cost_center", "account_code", name="uq_close_dept_rules_key"),
        comment="전표 계정 자동 분류 기준정보(부서코드×비용계정 → 계정 key). cost_center가 빈 "
        "문자열이면 그 비용계정 전체를 뜻하고, 구체적인 행이 먼저 이긴다.",
    )

    op.create_table(
        "close_account_stages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("basis_year", sa.SmallInteger(), nullable=False, comment="결산 대상 연도"),
        sa.Column("basis_month", sa.SmallInteger(), nullable=False, comment="결산 대상 월(1~12)"),
        sa.Column("account_key", sa.String(length=32), nullable=False, comment="계정 key"),
        sa.Column(
            "mapping_done",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="전표 매핑을 끝냈는지. 해외연구소는 이게 곧 '1차 완료'다",
        ),
        sa.Column(
            "first_done_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="매핑 완료(해외연구소는 1차 완료) 시각. 되돌리면 NULL로 지운다",
        ),
        sa.Column(
            "note",
            sa.String(length=200),
            nullable=False,
            server_default="",
            comment="완료 당시 메모(미매핑 n건 남김 등)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="마지막으로 고친 시각",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("basis_year", "basis_month", "account_key", name="uq_close_account_stages_key"),
        comment="계정별 결산 전표매핑 상태. 해외연구소는 여기서 '1차 완료'까지만 하고 계정 완료는 "
        "실적 시뮬레이션 [확정]으로만 된다(versions 쪽과 다른 층).",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("close_account_stages")
    op.drop_table("close_dept_rules")
    op.drop_table("close_match_rules")
    op.drop_table("close_voucher_maps")
    op.drop_index("ix_close_vouchers_period_account", table_name="close_vouchers")
    op.drop_index("ix_close_vouchers_account_key", table_name="close_vouchers")
    op.drop_table("close_vouchers")
