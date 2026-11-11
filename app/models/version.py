from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Version(Base):
    """연월 × 속보(forecast)·실적(actual) 버전의 진행 상태. 경영계획(P00~) 버전은
    ManagementPlanVersion이 담당하고 실제 금액은 cost_entries가 담당하므로, 이 테이블은 그 연월
    버전이 열렸는지·상태가 뭔지만 추적한다. 아직 열지 않은(예정) 연월은 행 자체가 없다 —
    프론트에서 그 버전을 열 때 status=drafting으로 행을 만들고, 확정하면 done으로 바꾼다
    (status=not_started는 실무상 저장되지 않음, common_codes의 라벨 용도로만 존재)."""

    __tablename__ = "versions"
    __table_args__ = (
        UniqueConstraint("year", "month", "kind", name="uq_versions_year_month_kind"),
        {
            "comment": "연월×속보/실적(forecast/actual) 진행 상태. 기준정보 › 경영계획 연간 달력에 쓰인다. "
            "열지 않은 연월은 행이 없다(=예정)."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger, comment="대상 연도")
    month: Mapped[int] = mapped_column(SmallInteger, comment="대상 월(1~12)")
    kind: Mapped[str] = mapped_column(String(10), comment="forecast(속보) | actual(실적)")
    status: Mapped[str] = mapped_column(
        String(12),
        default="drafting",
        comment="진행 상태. common_codes(group_code='VERSION_STATUS')의 code 값(drafting|done)",
    )
    confirmed_at: Mapped[date | None] = mapped_column(Date, comment="완료(확정) 처리일")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="생성 시각")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="이 버전을 연 사용자 (users.id)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")
