from datetime import date, datetime

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

# 경영계획 버전(P00~P03 등). cost_entries.version의 "plan" 역할태그는 그 순간의 계획 하나만
# 가리키는 덮어쓰기 슬롯이라, 버전별로 영구 보관·비교해야 하는 경영계획은 따로 둔다.


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


class ManagementPlanEntry(Base):
    """계정×행×비용항목×경영계획버전 값(월은 1~12 고정이라 컬럼으로 펼침). cost_entries와 거의 같은
    모양이지만 version 역할태그 대신 실제 경영계획 버전(year+plan_no)을 가리키고,
    cur 역할에서만 쓰는 actual_thru_month(속보/실적 구분)는 의미가 없어 뺐다."""

    __tablename__ = "management_plan_entries"
    __table_args__ = (
        UniqueConstraint("account_key", "scope_key", "year", "plan_no", "category_key"),
        ForeignKeyConstraint(
            ["year", "plan_no"], ["management_plan_versions.year", "management_plan_versions.plan_no"]
        ),
        {"comment": "계정×행×비용항목×경영계획버전 값(month1~month12 컬럼). 19개 계정이 공유."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.key"), comment='accounts.key ("overseas", "dep", ...)'
    )
    scope_key: Mapped[str] = mapped_column(String(40), comment="계정 안에서의 행 단위 식별자")
    year: Mapped[int] = mapped_column(SmallInteger, comment="management_plan_versions.year")
    plan_no: Mapped[str] = mapped_column(String(4), comment="management_plan_versions.plan_no")
    category_key: Mapped[str] = mapped_column(String(40), comment="비용항목 키. 고정 enum 아닌 자유 문자열")
    month1: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="1월 금액")
    month2: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="2월 금액")
    month3: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="3월 금액")
    month4: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="4월 금액")
    month5: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="5월 금액")
    month6: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="6월 금액")
    month7: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="7월 금액")
    month8: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="8월 금액")
    month9: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="9월 금액")
    month10: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="10월 금액")
    month11: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="11월 금액")
    month12: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="12월 금액")
    extra: Mapped[dict | None] = mapped_column(JSONB, comment="엑셀 업로드에서 인식 못한 열 보관용")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="최초 입력 시각"
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="최초 입력한 사용자 (users.id)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")
