from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Numeric, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class FxRate(Base):
    """실적(월평균·말일)·속보·경영 환율의 원본 입력(연월×통화×종류). kind로 구분한다 —
    actual_avg(실적 월평균) | actual_eom(실적 말일) | forecast(속보=실행) | plan(경영)."""

    __tablename__ = "fx_rates"
    __table_args__ = (
        UniqueConstraint("year_month", "currency_code", "kind", name="uq_fx_rates_ym_currency_kind"),
        CheckConstraint(r"year_month ~ '^\d{4}-(0[1-9]|1[0-2])$'", name="ck_fx_rates_year_month_format"),
        CheckConstraint(
            "kind IN ('actual_avg', 'actual_eom', 'forecast', 'plan')", name="ck_fx_rates_kind"
        ),
        {"comment": "실적(월평균·말일)·속보·경영 환율 원본 입력(연월×통화×종류, 값 1개+업로드 정보)."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    year_month: Mapped[str] = mapped_column(String(7), comment='연월 "YYYY-MM"')
    currency_code: Mapped[str] = mapped_column(String(3))
    kind: Mapped[str] = mapped_column(String(10), comment="actual_avg | actual_eom | forecast | plan")
    rate: Mapped[float] = mapped_column(Numeric(14, 6), default=0, comment="환율 값")
    source_file: Mapped[str | None] = mapped_column(String(255), comment="업로드한 원본 파일명")
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="업로드 시각")


class FxAppliedRate(Base):
    """적용 환율(기준시점 × 대상월 × 통화) — 실적/속보/경영을 화면의 "적용 환율" 탭과 같은 날짜
    규칙으로 조합한 계산 결과. 기준(basis_year·basis_month·basis_kind)이 가리키는 시점 이전 대상월은
    실적(rule에 따라 평균 또는 말일), 이후 대상월은 그 시점 속보 값을 쓴다. 원본이 아니라 파생값이라
    실적·속보·경영 쪽 데이터가 바뀌면 다시 계산해 채워야 한다(원본 3개 테이블이 source of truth)."""

    __tablename__ = "fx_applied_rates"
    __table_args__ = (
        UniqueConstraint(
            "basis_year", "basis_month", "basis_kind", "rule", "target_month", "currency_code",
            name="uq_fx_applied_rates_basis_rule_month_currency",
        ),
        {"comment": "적용 환율(기준시점×대상월×통화) — 실적·속보·경영 조합 계산 결과 스냅샷(파생 테이블)"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    basis_year: Mapped[int] = mapped_column(SmallInteger, comment="기준 연도")
    basis_month: Mapped[int] = mapped_column(SmallInteger, comment="기준 월(1~12)")
    basis_kind: Mapped[str] = mapped_column(String(10), comment="forecast(속보) | actual(실적)")
    rule: Mapped[str] = mapped_column(String(10), default="avg", comment="과거 대상월의 실적 기준: avg(평균) | last(말일)")
    target_month: Mapped[int] = mapped_column(SmallInteger, comment="적용 환율이 매겨지는 대상 월(1~12)")
    currency_code: Mapped[str] = mapped_column(String(3))
    rate: Mapped[float] = mapped_column(Numeric(14, 4), default=0, comment="원/통화 적용 환율")
    source: Mapped[str] = mapped_column(String(10), comment="이 값의 출처: forecast | actual")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OverseasFxRuleOverride(Base):
    """해외연구소 실적 구간 환산 기준(말일 vs 월평균)의 월 단위 예외 — "이 달만" 기본값과 다르게 쓸 때
    그 연월로 한 줄 넣는다. 예외가 없는 달은 common_codes(group_code='OVERSEAS_FX_RULE', code='default')의
    name을 기본값으로 쓴다. 즉 어떤 달의 rule은 "예외가 있으면 그 rule, 없으면 기본값"이다."""

    __tablename__ = "overseas_fx_rule_overrides"
    __table_args__ = (
        UniqueConstraint("year_month", name="uq_overseas_fx_rule_overrides_year_month"),
        CheckConstraint(r"year_month ~ '^\d{4}-(0[1-9]|1[0-2])$'", name="ck_overseas_fx_rule_overrides_year_month_format"),
        CheckConstraint("rule IN ('avg', 'last')", name="ck_overseas_fx_rule_overrides_rule"),
        {"comment": "해외연구소 환산 환율 기준의 월 단위 예외. 예외가 없는 달은 common_codes(OVERSEAS_FX_RULE/default)의 기본값을 쓴다."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    year_month: Mapped[str] = mapped_column(String(7), comment='이 달만 기본값 대신 rule을 쓴다 "YYYY-MM"')
    rule: Mapped[str] = mapped_column(String(10), comment="avg(월평균) | last(말일)")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="결정된 시각")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
