"""sync stale table/column comments to models

삭제·개편된 대상을 가리키던 DB 코멘트를 모델의 현재 설명으로 맞춘다:
- versions 테이블: kind 값을 옛 코드(sok/act)로 설명하던 것 → forecast/actual
- common_codes/common_code_groups.group_code: 삭제된 그룹(FX_RATE_TYPE·CURRENCY)·만든 적 없는 DEPT 예시 제거
- cost_entry_details.col_name: 삭제된 custom_fields 테이블 참조 제거
- fx_applied_rates.basis_kind/source: 저장되지 않는 plan 값 제거(코드는 forecast/actual만 만든다)
- memos.row_key: 삭제된 custom_rows 테이블 참조 → cost_entries.category_code
- overseas_labs: 하드코딩된 "13개"(실제 14개 + _ETC) 제거, lab_code에 워크북 단위 설명 추가
- management_plan_entries.year/plan_no 등: 가리키는 FK를 분명히

Revision ID: e1a2b3c4d5f6
Revises: b4c1e8a07d52
"""

from alembic import op
import sqlalchemy as sa

revision = "e1a2b3c4d5f6"
down_revision = "b4c1e8a07d52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table_comment(
        "common_codes",
        "전사 공통코드(코드그룹×코드값). 계정 상태·버전 상태·해외연구소 비용항목·시트 종류 등.",
        existing_comment="전사 공통코드(코드그룹×코드값). 버전·환율종류·통화·부서코드·계정과목 등.",
    )
    op.create_table_comment(
        "overseas_labs",
        "해외연구소·파트 기준정보(SARC는 CPU·GPU로 분리) + 연구소에 속하지 않는 계정 전체 단위 값을 담는 가상 행(_ETC). 프론트 탭은 이 테이블을 lab_code로 묶어서 그린다.",
        existing_comment="13개 해외연구소·파트 기준정보(SARC는 CPU·GPU로 분리) + 연구소에 속하지 않는 계정 전체 단위 값을 담는 가상 행(_ETC).",
    )
    op.create_table_comment(
        "versions",
        "연월×속보/실적(forecast/actual) 진행 상태. 기준정보 › 경영계획 연간 달력에 쓰인다. 열지 않은 연월은 행이 없다(=예정).",
        existing_comment="연월×속보/실적(sok/act) 진행 상태. 기준정보 › 경영계획 연간 달력에 쓰인다. 열지 않은 연월은 행이 없다(=예정).",
    )
    op.alter_column(
        "common_code_groups",
        "group_code",
        existing_type=sa.String(40),
        comment="그룹코드 (\"ACCOUNT_STATUS\", \"VERSION_STATUS\", \"OVERSEAS_CATEGORY\", ...)",
        existing_comment="그룹코드 (\"VERSION\", \"FX_RATE_TYPE\", \"ACCOUNT_STATUS\", ...)",
        existing_nullable=False,
    )
    op.alter_column(
        "common_codes",
        "group_code",
        existing_type=sa.String(40),
        comment="코드그룹 (\"ACCOUNT_STATUS\", \"VERSION_STATUS\", \"OVERSEAS_CATEGORY\", ...)",
        existing_comment="코드그룹 (\"VERSION\", \"FX_RATE_TYPE\", \"CURRENCY\", \"DEPT\", ...)",
        existing_nullable=False,
    )
    op.alter_column(
        "cost_entry_details",
        "col_name",
        existing_type=sa.String(100),
        comment="CUSTOM일 때만 그 열의 라벨, 나머지는 \"\"",
        existing_comment="CUSTOM일 때만 그 열의 라벨(옛 custom_fields.label), 나머지는 \"\"",
        existing_nullable=False,
    )
    op.alter_column(
        "fx_applied_rates",
        "basis_kind",
        existing_type=sa.String(10),
        comment="forecast(속보) | actual(실적)",
        existing_comment="forecast(속보) | actual(실적) | plan(경영)",
        existing_nullable=False,
    )
    op.alter_column(
        "fx_applied_rates",
        "source",
        existing_type=sa.String(10),
        comment="이 값의 출처: forecast | actual",
        existing_comment="이 값의 출처: actual | forecast | plan",
        existing_nullable=False,
    )
    op.alter_column(
        "management_plan_entries",
        "year",
        existing_type=sa.SmallInteger(),
        comment="management_plan_versions.year",
        existing_comment="편성 기준 연도",
        existing_nullable=False,
    )
    op.alter_column(
        "management_plan_entries",
        "plan_no",
        existing_type=sa.String(4),
        comment="management_plan_versions.plan_no",
        existing_comment="경영계획 버전 (P00~P03 등)",
        existing_nullable=False,
    )
    op.alter_column(
        "management_plan_entry_details",
        "year",
        existing_type=sa.SmallInteger(),
        comment="이 금액이 속한 연도(헤더의 year와 동일)",
        existing_comment="이 금액이 속한 연도",
        existing_nullable=False,
    )
    op.alter_column(
        "memos",
        "row_key",
        existing_type=sa.String(40),
        comment="고정 계정 key 또는 사용자 추가 행의 키(cost_entries.category_code)",
        existing_comment="고정 계정 key 또는 custom_rows.row_key",
        existing_nullable=False,
    )
    op.alter_column(
        "overseas_labs",
        "lab_code",
        existing_type=sa.String(10),
        comment="연구소 코드 (\"SARC\"). 워크북 1개 = lab_code 1개 — 프론트 탭은 이 값으로 묶는다",
        existing_comment="연구소 코드 (\"SARC\")",
        existing_nullable=False,
    )
    op.alter_column(
        "versions",
        "created_by_id",
        existing_type=sa.Integer(),
        comment="이 버전을 연 사용자 (users.id)",
        existing_comment="만든 사용자 (users.id)",
        existing_nullable=True,
    )


def downgrade() -> None:
    op.create_table_comment(
        "common_codes",
        "전사 공통코드(코드그룹×코드값). 버전·환율종류·통화·부서코드·계정과목 등.",
        existing_comment="전사 공통코드(코드그룹×코드값). 계정 상태·버전 상태·해외연구소 비용항목·시트 종류 등.",
    )
    op.create_table_comment(
        "overseas_labs",
        "13개 해외연구소·파트 기준정보(SARC는 CPU·GPU로 분리) + 연구소에 속하지 않는 계정 전체 단위 값을 담는 가상 행(_ETC).",
        existing_comment="해외연구소·파트 기준정보(SARC는 CPU·GPU로 분리) + 연구소에 속하지 않는 계정 전체 단위 값을 담는 가상 행(_ETC). 프론트 탭은 이 테이블을 lab_code로 묶어서 그린다.",
    )
    op.create_table_comment(
        "versions",
        "연월×속보/실적(sok/act) 진행 상태. 기준정보 › 경영계획 연간 달력에 쓰인다. 열지 않은 연월은 행이 없다(=예정).",
        existing_comment="연월×속보/실적(forecast/actual) 진행 상태. 기준정보 › 경영계획 연간 달력에 쓰인다. 열지 않은 연월은 행이 없다(=예정).",
    )
    op.alter_column(
        "common_code_groups",
        "group_code",
        existing_type=sa.String(40),
        comment="그룹코드 (\"VERSION\", \"FX_RATE_TYPE\", \"ACCOUNT_STATUS\", ...)",
        existing_comment="그룹코드 (\"ACCOUNT_STATUS\", \"VERSION_STATUS\", \"OVERSEAS_CATEGORY\", ...)",
        existing_nullable=False,
    )
    op.alter_column(
        "common_codes",
        "group_code",
        existing_type=sa.String(40),
        comment="코드그룹 (\"VERSION\", \"FX_RATE_TYPE\", \"CURRENCY\", \"DEPT\", ...)",
        existing_comment="코드그룹 (\"ACCOUNT_STATUS\", \"VERSION_STATUS\", \"OVERSEAS_CATEGORY\", ...)",
        existing_nullable=False,
    )
    op.alter_column(
        "cost_entry_details",
        "col_name",
        existing_type=sa.String(100),
        comment="CUSTOM일 때만 그 열의 라벨(옛 custom_fields.label), 나머지는 \"\"",
        existing_comment="CUSTOM일 때만 그 열의 라벨, 나머지는 \"\"",
        existing_nullable=False,
    )
    op.alter_column(
        "fx_applied_rates",
        "basis_kind",
        existing_type=sa.String(10),
        comment="forecast(속보) | actual(실적) | plan(경영)",
        existing_comment="forecast(속보) | actual(실적)",
        existing_nullable=False,
    )
    op.alter_column(
        "fx_applied_rates",
        "source",
        existing_type=sa.String(10),
        comment="이 값의 출처: actual | forecast | plan",
        existing_comment="이 값의 출처: forecast | actual",
        existing_nullable=False,
    )
    op.alter_column(
        "management_plan_entries",
        "year",
        existing_type=sa.SmallInteger(),
        comment="편성 기준 연도",
        existing_comment="management_plan_versions.year",
        existing_nullable=False,
    )
    op.alter_column(
        "management_plan_entries",
        "plan_no",
        existing_type=sa.String(4),
        comment="경영계획 버전 (P00~P03 등)",
        existing_comment="management_plan_versions.plan_no",
        existing_nullable=False,
    )
    op.alter_column(
        "management_plan_entry_details",
        "year",
        existing_type=sa.SmallInteger(),
        comment="이 금액이 속한 연도",
        existing_comment="이 금액이 속한 연도(헤더의 year와 동일)",
        existing_nullable=False,
    )
    op.alter_column(
        "memos",
        "row_key",
        existing_type=sa.String(40),
        comment="고정 계정 key 또는 custom_rows.row_key",
        existing_comment="고정 계정 key 또는 사용자 추가 행의 키(cost_entries.category_code)",
        existing_nullable=False,
    )
    op.alter_column(
        "overseas_labs",
        "lab_code",
        existing_type=sa.String(10),
        comment="연구소 코드 (\"SARC\")",
        existing_comment="연구소 코드 (\"SARC\"). 워크북 1개 = lab_code 1개 — 프론트 탭은 이 값으로 묶는다",
        existing_nullable=False,
    )
    op.alter_column(
        "versions",
        "created_by_id",
        existing_type=sa.Integer(),
        comment="만든 사용자 (users.id)",
        existing_comment="이 버전을 연 사용자 (users.id)",
        existing_nullable=True,
    )
