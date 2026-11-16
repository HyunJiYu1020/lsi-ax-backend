from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ManagementPlanVersion(Base):
    """경영계획 버전 목록 (연도별 P00, P01, P02, ...). 확정되면 그 시점 값을 영구 보관한다.
    plan_no("P01" 등)는 매년 반복되는 값이라 year와 묶어야 유일해서, 둘을 합쳐 복합 PK로 쓴다
    (year만 또는 plan_no만 따로 떼서 의미 있는 키가 아니라 별도 surrogate id를 두지 않음)."""

    __tablename__ = "management_plan_versions"
    __table_args__ = {"comment": "경영계획 버전(P00~P03 등) 목록 — 연도별로 여러 버전이 쌓이고 확정되면 보관된다"}

    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True, comment="경영계획 대상 연도")
    plan_no: Mapped[str] = mapped_column(String(4), primary_key=True, comment='연도 안에서의 차수 ("P00" | "P01" | ...)')
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, comment="확정 여부 (false=작성 중)")
    is_final: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="그 연도의 경영계획 대비 비교 기준이 되는 공식 버전인지"
    )
    finalized_at: Mapped[date | None] = mapped_column(Date, comment="확정일")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="최초 생성 시각"
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="최초 생성한 사용자 (users.id)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")


class ManagementPlanEntry(Base):
    """계정×행×비용항목×경영계획버전의 헤더 한 장. cost_entries와 같은 구조로, "편성 시점" 역할을
    yyyymm 대신 year+plan_no가 맡는다. 실제 월별 금액은 이 헤더를 FK로 참조하는
    ManagementPlanEntryDetail에 행으로 들어간다(period_type=MONTH|QUARTER|YEAR|CUSTOM)."""

    __tablename__ = "management_plan_entries"
    __table_args__ = (
        UniqueConstraint(
            "account_code", "scope_code", "category_code", "year", "plan_no",
            name="uq_management_plan_entries_account_scope_category_year_plan",
        ),
        ForeignKeyConstraint(
            ["year", "plan_no"], ["management_plan_versions.year", "management_plan_versions.plan_no"]
        ),
        {"comment": "계정×행×비용항목×경영계획버전 헤더. 실제 금액은 management_plan_entry_details 참조. 19개 계정이 공유."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger, comment="management_plan_versions.year")
    plan_no: Mapped[str] = mapped_column(String(4), comment="management_plan_versions.plan_no")
    account_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.key"), comment='accounts.key ("overseas", "dep", ...) — 가장 큰 분류'
    )
    scope_code: Mapped[str] = mapped_column(
        String(40), comment="계정 안의 행 그룹 식별자 (해외연구소는 연구소 pk, 없는 계정은 상수 \"ALL\")"
    )
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="최초 입력 시각"
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="최초 입력한 사용자 (users.id)")


class ManagementPlanEntryDetail(Base):
    """ManagementPlanEntry 헤더 1장에 달리는 디테일 행. cost_entry_details와 동일한 모양
    (period_type=MONTH|QUARTER|YEAR|CUSTOM) — 경영계획은 연도 하나만 다루므로 year는 항상 헤더의
    year와 같지만, cost_entry_details와 완전히 같은 구조를 유지하려고 그대로 둔다."""

    __tablename__ = "management_plan_entry_details"
    __table_args__ = (
        UniqueConstraint(
            "plan_entry_id", "year", "period_type", "period", "col_name",
            name="uq_management_plan_entry_details_entry_year_period_col",
        ),
        CheckConstraint(
            "period_type IN ('MONTH', 'QUARTER', 'YEAR', 'CUSTOM')", name="ck_management_plan_entry_details_period_type"
        ),
        {"comment": "management_plan_entries 헤더별 디테일 행(월·분기·연간·커스텀열)."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_entry_id: Mapped[int] = mapped_column(
        ForeignKey("management_plan_entries.id", ondelete="CASCADE"),
        comment="management_plan_entries.id (헤더 삭제 시 디테일도 같이 삭제)",
    )
    year: Mapped[int] = mapped_column(SmallInteger, default=0, comment="이 금액이 속한 연도(헤더의 year와 동일)")
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
