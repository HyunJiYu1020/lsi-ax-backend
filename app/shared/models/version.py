from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, SmallInteger, String, UniqueConstraint, func
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


class VersionAccountStatus(Base):
    """버전 × 계정의 "계정 확정" 상태와 그 일시.

    버전 전체의 확정(versions.status·confirmed_at)과는 다른 층이다 — 19개 계정 담당자가 각자
    자기 계정을 먼저 확정하고, 전부 완료되면 버전을 확정한다. 누가 언제 확정했는지는 여러 사람이
    같이 보는 값이라 브라우저(localStorage)에 두면 안 된다(화면을 지우면 사라지고, 옆자리에서는
    다른 상태가 보인다). 그래서 상태·일시를 서버에 둔다.

    note는 "무엇을 알고도 확정했는지"를 남기는 칸이다 — 미업로드 연구소가 있는데도 확정했다면
    "미업로드 2개 포함 확정"이 남아야, 나중에 그 총액을 전체 합계로 오해하지 않는다. 확정을
    취소하면 일시와 함께 지운다(공통 RULE: 완료 후 수정 시 작성중 + 일자 삭제).

    행이 없으면 "작성 중"이다 — 계정 19개 × 버전마다 미리 행을 만들지 않는다."""

    __tablename__ = "version_account_status"
    __table_args__ = (
        UniqueConstraint("version_id", "account_code", name="uq_version_account_status_key"),
        CheckConstraint("status IN ('drafting', 'done')", name="ck_version_account_status_status"),
        {"comment": "버전×계정의 계정 확정 상태·확정 일시·확정 당시 단서(note). 행이 없으면 작성 중."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(
        ForeignKey("versions.id", ondelete="CASCADE"), comment="versions.id (버전을 지우면 계정 상태도 같이 지운다)"
    )
    account_code: Mapped[str] = mapped_column(String(32), comment="accounts.key (overseas, dep, outsource ...)")
    status: Mapped[str] = mapped_column(String(10), default="drafting", comment="drafting(작성 중) | done(완료)")
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="완료로 바꾼 시각. 작성중으로 되돌리면 NULL로 지운다"
    )
    note: Mapped[str] = mapped_column(
        String(200), default="", comment='확정 당시 단서("미업로드 2개 포함 확정" 등). 작성중으로 되돌리면 비운다'
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
