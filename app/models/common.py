"""19개 비용계정이 공유하는 범용 데이터 테이블. account_key로 계정을 구분한다.
scope_key는 계정 안의 행 단위 식별자(해외연구소는 연구소 pk)로, 계정마다 의미가 달라 FK로 강제하지
않고 애플리케이션이 검증한다."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SheetRow(Base):
    """열 구성이 확정되지 않은 시트(해외연구소 '증감분석' 등)의 원본 행을 헤더 행 키 기준 JSON 한
    덩어리로 보관한다. 열 구성이 확정되면 전용 테이블로 옮긴다(외주용역 → overseas_vendor_rows)."""

    __tablename__ = "sheet_rows"
    __table_args__ = (
        UniqueConstraint("account_key", "scope_key", "version", "sheet_key", "row_key"),
        {
            "comment": "열 구성이 아직 확정되지 않은 새 시트의 원본 행(JSON) 보관소. 19개 계정이 공유. "
            "구성이 확정되면 전용 테이블로 옮긴다(외주용역 → overseas_vendor_rows)."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(32), ForeignKey("accounts.key"), comment="accounts.key")
    scope_key: Mapped[str] = mapped_column(String(40), comment="계정 안에서의 행 단위 식별자 (해외연구소는 연구소 pk)")
    version: Mapped[str] = mapped_column(String(10), comment="cur | prev | prior | plan")
    sheet_key: Mapped[str] = mapped_column(String(20), comment="시트 종류 (해외연구소는 variance|vendor 등)")
    row_key: Mapped[str] = mapped_column(String(40), comment="시트 안에서의 행 식별자 (업로드 시 행 순서 등)")
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0, comment="시트 내 표시 순서")
    data: Mapped[dict] = mapped_column(JSONB, comment="열이름 -> 값. 열 구조가 확정되지 않아 구조화 테이블 대신 JSON으로 보관")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="최초 입력 시각"
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="최초 입력한 사용자 (users.id)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")


class CostEntry(Base):
    """계정×행×비용항목×시점의 편성 회차 헤더. yyyymm은 편성 기준 연월(예: "202609"), version은
    forecast(속보)|actual(실적)만 저장한다(경영계획은 ManagementPlanEntry가 따로 담당).
    scope_code는 계정 안의 행 단위 식별자로 계정마다 의미가 달라 FK로 강제하지 않고 애플리케이션이
    검증한다. label·label_en·anchor_key는 사용자가 추가한 커스텀 행일 때만 채워지고, 고정 항목은
    NULL이다."""

    __tablename__ = "cost_entries"
    __table_args__ = (
        UniqueConstraint("account_code", "scope_code", "category_code", "yyyymm", "version"),
        CheckConstraint(r"yyyymm ~ '^\d{4}(0[1-9]|1[0-2])$'", name="ck_cost_entries_yyyymm_format"),
        CheckConstraint("version IN ('forecast', 'actual')", name="ck_cost_entries_version"),
        {"comment": "계정×행×비용항목×편성시점 헤더. 실제 월별 금액은 cost_entry_details 참조. 19개 계정이 공유."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    yyyymm: Mapped[str] = mapped_column(String(6), comment='편성 기준 연월 "YYYYMM" (예: "202609")')
    account_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.key"), comment='accounts.key ("overseas", "dep", ...) — 가장 큰 분류'
    )
    scope_code: Mapped[str] = mapped_column(
        String(40), comment="계정 안의 행 그룹 식별자 (해외연구소는 연구소 pk, 없는 계정은 상수 \"ALL\")"
    )
    version: Mapped[str] = mapped_column(String(10), comment="forecast(속보) | actual(실적)")
    category_code: Mapped[str] = mapped_column(
        String(40), comment="scope 안의 세부 비용항목 키. 고정 enum 아닌 자유 문자열(커스텀 행은 임의 id)"
    )
    label: Mapped[str | None] = mapped_column(
        String(100), comment="커스텀 category_code의 표시명(한글). 고정 항목은 NULL"
    )
    label_en: Mapped[str | None] = mapped_column(
        String(100), comment="커스텀 category_code의 표시명(영문). 고정 항목은 NULL"
    )
    anchor_key: Mapped[str | None] = mapped_column(
        String(40), comment="커스텀 행을 끼워 넣을 고정 category_code 바로 뒤. NULL이면 표 맨 끝"
    )
    # 취합에서 추가한 행(원시값)과 집계에서 추가한 행(파생값)을 저장 후에도 구분하기 위한 플래그.
    # 추가 당시의 화면 단계가 아니라 "이 값이 원시냐 파생이냐"를 저장한다 — 단계는 종류가 만들어지는
    # 장소일 뿐이고, 단계가 개편돼도 종류는 그대로라서 삭제 권한·표시 규칙을 여기서 다 끌어낼 수 있다.
    is_derived: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        comment="True면 다른 항목에서 계산해 만든 파생 행(집계 전용). 월별 금액을 저장하지 않고 formula로 매번 계산",
    )
    formula: Mapped[dict | None] = mapped_column(
        JSONB,
        comment='파생 행의 계산식 {"op":"div_elementwise","operands":["labor","hc"]}. 원시 행은 NULL',
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="최초 입력 시각"
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="최초 입력한 사용자 (users.id)")


class CostEntryDetail(Base):
    """CostEntry 헤더에 달리는 디테일 행. period_type으로 월·분기·연간·커스텀열을 구분한다 —
    MONTH(period 1~12) | QUARTER(period 1~4) | YEAR(period 0) | CUSTOM(period 0, col_name에 라벨).
    분기·연간 행은 공용 엔드포인트(cost.py)로 월 값을 쓸 때마다 그 해 12개월 합으로 재계산돼 덮어쓰인다
    (cost.py의 _resync_summary). 단 해외연구소 저장 경로(/overseas/state)는 월 행만 쓰고 이 재계산을
    하지 않아, 그 계정의 분기·연간 행은 한 번 백필된 값에서 갱신되지 않는다. col_name은
    CUSTOM이 아니면 빈 문자열(NULL 아님) — Postgres UNIQUE/ON CONFLICT가 NULL을 서로 다른 값으로
    취급해 중복 삽입될 수 있기 때문이다."""

    __tablename__ = "cost_entry_details"
    __table_args__ = (
        UniqueConstraint("cost_entry_id", "year", "period_type", "period", "col_name"),
        CheckConstraint("period_type IN ('MONTH', 'QUARTER', 'YEAR', 'CUSTOM')", name="ck_cost_entry_details_period_type"),
        {"comment": "cost_entries 헤더별 디테일 행(월·분기·연간·커스텀열). 분기·연간도 원본 숫자를 그대로 저장."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cost_entry_id: Mapped[int] = mapped_column(
        ForeignKey("cost_entries.id", ondelete="CASCADE"), comment="cost_entries.id (헤더 삭제 시 디테일도 같이 삭제)"
    )
    year: Mapped[int] = mapped_column(SmallInteger, default=0, comment="이 금액이 속한 연도")
    period_type: Mapped[str] = mapped_column(String(10), comment="MONTH | QUARTER | YEAR | CUSTOM")
    period: Mapped[int] = mapped_column(
        SmallInteger, default=0, comment="MONTH:1~12, QUARTER:1~4, YEAR:0, CUSTOM:0"
    )
    col_name: Mapped[str] = mapped_column(
        String(100), default="", comment='CUSTOM일 때만 그 열의 라벨, 나머지는 ""'
    )
    amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="금액")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")


class Upload(Base):
    """업로드 이력 (계정×행×버전별로 받은 파일명·시각)."""

    __tablename__ = "uploads"
    __table_args__ = {"comment": "계정×행×버전별 업로드 이력. 19개 계정이 공유."}

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(32), ForeignKey("accounts.key"), comment="accounts.key")
    scope_key: Mapped[str] = mapped_column(String(40), comment="계정 안에서의 행 단위 식별자")
    version: Mapped[str] = mapped_column(String(10), comment="cur | prev | prior | plan")
    filename: Mapped[str] = mapped_column(String(255), comment="업로드한 원본 파일명")
    s3_key: Mapped[str | None] = mapped_column(
        String(512), comment="원본 파일이 보관된 S3 객체 키. S3 미설정이거나 업로드 실패 시 NULL(파일명·파싱 결과만 남음)"
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="업로드 시각"
    )
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="업로드한 사용자 (users.id)")


class Note(Base):
    """증감분석 비고(사유) 메모."""

    __tablename__ = "notes"
    __table_args__ = (
        UniqueConstraint("account_key", "base_version", "period", "scope", "category_key"),
        {"comment": "증감분석 화면의 비고(사유) 메모. 19개 계정이 공유."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(32), ForeignKey("accounts.key"), comment="accounts.key")
    base_version: Mapped[str] = mapped_column(String(10), comment="비교 기준: prev | prior | plan")
    period: Mapped[str] = mapped_column(String(4), comment="기간: y(연간) | q1~q4(분기) | m1~m12(월)")
    scope: Mapped[str] = mapped_column(String(20), comment='"ALL"(전체) 또는 특정 행 scope_key')
    category_key: Mapped[str] = mapped_column(String(40), comment="비용항목 키")
    note_text: Mapped[str] = mapped_column(Text, comment="증감 사유 텍스트")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="최초 작성 시각"
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="최초 작성한 사용자 (users.id)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")


class ExtraCol(Base):
    """편성 그리드에 추가한 열(행×열 교차점에 숫자가 들어가는, 화면에 실제로 보이는 열).

    이 테이블이 생기기 전에는 열이라는 개체가 DB에 없었다 — 같은 col_name을 가진
    cost_entry_details(period_type="CUSTOM") 칸들의 집합이 곧 "그 열"이어서, 열 단위 속성
    (파생 여부·계산식)을 걸 자리가 없었다. 그래서 MemoCol과 같은 모양의 레지스트리를 둔다.

    원시 열(is_derived=False)의 칸 값은 지금까지와 같이 cost_entry_details에 저장되고 여기는
    존재·이름만 관리한다. 파생 열(is_derived=True)은 칸 값을 저장하지 않고 formula로 매번 계산한다.
    label 자체가 열 식별자라 이름 변경은 지원하지 않는다(지우고 새 이름으로 다시 만들면 된다)."""

    __tablename__ = "extra_cols"
    __table_args__ = (
        UniqueConstraint("account_key", "scope_key", "version", "yyyymm", "label"),
        CheckConstraint("version IN ('forecast', 'actual')", name="ck_extra_cols_version"),
        CheckConstraint(r"yyyymm ~ '^\d{4}(0[1-9]|1[0-2])$'", name="ck_extra_cols_yyyymm_format"),
        {"comment": "편성 그리드 추가 열(존재·이름·파생 여부). 원시 열의 칸 값은 cost_entry_details 참조."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(32), ForeignKey("accounts.key"), comment="accounts.key")
    scope_key: Mapped[str] = mapped_column(String(40), comment="계정 안에서의 행 단위 식별자 (해외연구소는 연구소 pk)")
    version: Mapped[str] = mapped_column(String(10), comment="forecast(속보) | actual(실적)")
    yyyymm: Mapped[str] = mapped_column(String(6), comment='편성 기준 연월 "YYYYMM" (예: "202609")')
    label: Mapped[str] = mapped_column(String(100), comment="사용자가 입력한 열 이름. 이 값 자체가 열 식별자")
    is_derived: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        comment="True면 월을 묶어 계산한 파생 열(집계 전용). 칸 값을 저장하지 않고 formula로 매번 계산",
    )
    formula: Mapped[dict | None] = mapped_column(
        JSONB,
        comment='파생 열의 계산식 {"op":"sum_months","months":[7,8,9]}. 원시 열은 NULL',
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="열 추가 시각"
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="열을 추가한 사용자 (users.id)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")


class MemoCol(Base):
    """편성 화면에 추가한 메모 전용 열(집계·증감분석에는 들어가지 않음)."""

    __tablename__ = "memo_cols"
    __table_args__ = (
        UniqueConstraint("account_key", "scope_key", "version", "col_key"),
        {"comment": "메모 전용 열(존재·이름만). 셀 내용은 memos 참조."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(32), ForeignKey("accounts.key"), comment="accounts.key")
    scope_key: Mapped[str] = mapped_column(String(40), comment="계정 안에서의 행 단위 식별자")
    version: Mapped[str] = mapped_column(String(10), comment="cur | prev | prior | plan")
    col_key: Mapped[str] = mapped_column(String(40), comment='클라이언트가 생성한 열 id ("c...")')
    label: Mapped[str] = mapped_column(String(100), default="", comment="사용자가 입력한 열 이름")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="열 추가 시각"
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="열을 추가한 사용자 (users.id)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")


class Memo(Base):
    """메모 셀 (행×열 교차점의 자유 텍스트). 행은 고정 계정 key 또는 사용자가 추가한 행의 키
    (cost_entries.category_code — label이 채워진 행)."""

    __tablename__ = "memos"
    __table_args__ = (
        UniqueConstraint("account_key", "scope_key", "version", "row_key", "col_key"),
        {"comment": "메모 셀. 집계·증감분석 계산에는 들어가지 않는 참고용 텍스트."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(32), ForeignKey("accounts.key"), comment="accounts.key")
    scope_key: Mapped[str] = mapped_column(String(40), comment="계정 안에서의 행 단위 식별자")
    version: Mapped[str] = mapped_column(String(10), comment="cur | prev | prior | plan")
    row_key: Mapped[str] = mapped_column(String(40), comment="고정 계정 key 또는 사용자 추가 행의 키(cost_entries.category_code)")
    col_key: Mapped[str] = mapped_column(String(40), comment="memo_cols.col_key")
    text: Mapped[str] = mapped_column(Text, comment="메모 텍스트")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="최초 작성 시각"
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="최초 작성한 사용자 (users.id)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")
