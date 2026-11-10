from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Numeric, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

# 새 환율 체계(프론트 S.fx2). 계정에 속하지 않는 전사 공통 기준정보라 overseas 쪽 테이블과는 분리한다.
# "버전"(cur/prev/prior/plan) 축이 아니라 "업로드 종류 × 통화 × 연월" 축으로 돌아간다 —
# 기존 fx_rates(해외연구소용, 3개 통화·버전 고정)와는 완전히 다른 모양이라 재사용하지 않았다.
# 지원팀 업로드는 실적(월평균·말일) · 속보(=실행) · 경영 3갈래이고, 전부 "연월(또는 연도) × 통화당
# 값 1개"로 끝난다 — 당초 실적을 일별 RAW 피드로 가정했던 fx_daily_rates는 실제로는 말일 값
# 하나만 월 1회 올라오는 걸로 확인돼 fx_monthly_avg와 그레인이 같아 fx_actual_rates로 합쳤다.


class FxActualRate(Base):
    """실적 환율(연월 × 통화). 월 평균 실적 환율 파일(avg_*)과 말일 환율 파일(eom_*)은 서로 다른
    시점에 따로 올라올 수 있어, 같은 (연월, 통화) 행 안에서도 두 값을 독립적으로 채운다 —
    한쪽만 먼저 올라오면 그 칸만 upsert되고 나머지는 비워(0) 둔다. 둘 다 통화당 값 1개, 월 1회."""

    __tablename__ = "fx_actual_rates"
    __table_args__ = (
        UniqueConstraint("year_month", "currency_code", name="uq_fx_actual_rates_ym_currency"),
        CheckConstraint(r"year_month ~ '^\d{4}-(0[1-9]|1[0-2])$'", name="ck_fx_actual_rates_year_month_format"),
        {"comment": "실적 환율(연월×통화) — 월평균 실적 환율 + 말일 환율. 둘 다 월 1회, 통화당 값 1개."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    year_month: Mapped[str] = mapped_column(String(7), comment="연월 \"YYYY-MM\"")
    currency_code: Mapped[str] = mapped_column(String(3))
    avg_rate: Mapped[float] = mapped_column(Numeric(14, 6), default=0, comment="월 평균 실적 환율")
    avg_source_file: Mapped[str | None] = mapped_column(String(255), comment="월평균 실적 환율 업로드 파일명")
    avg_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="월평균 실적 환율 업로드 시각")
    eom_rate: Mapped[float] = mapped_column(Numeric(14, 6), default=0, comment="말일(월말) 환율")
    eom_source_file: Mapped[str | None] = mapped_column(String(255), comment="말일 환율 업로드 파일명")
    eom_uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="말일 환율 업로드 시각")


class FxForecastRate(Base):
    """속보(=실행, forecast) 환율. 부문에서 매월 받는 값으로, 연월 × 통화당 값이 1개뿐이다."""

    __tablename__ = "fx_forecast_rates"
    __table_args__ = (
        UniqueConstraint("year_month", "currency_code", name="uq_fx_forecast_rates_ym_currency"),
        CheckConstraint(r"year_month ~ '^\d{4}-(0[1-9]|1[0-2])$'", name="ck_fx_forecast_rates_year_month_format"),
        {"comment": "속보(=실행) 환율 업로드. 연월×통화당 값 1개"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    year_month: Mapped[str] = mapped_column(String(7), comment="편성월 \"YYYY-MM\"")
    currency_code: Mapped[str] = mapped_column(String(3))
    rate: Mapped[float] = mapped_column(Numeric(14, 4), default=0, comment="속보(=실행) 환율")
    source_file: Mapped[str] = mapped_column(String(255), comment="업로드한 원본 파일명")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    basis_kind: Mapped[str] = mapped_column(String(10), comment="sok(속보) | act(실적) | plan(경영)")
    rule: Mapped[str] = mapped_column(String(10), default="avg", comment="과거 대상월의 실적 기준: avg(평균) | last(말일)")
    target_month: Mapped[int] = mapped_column(SmallInteger, comment="적용 환율이 매겨지는 대상 월(1~12)")
    currency_code: Mapped[str] = mapped_column(String(3))
    rate: Mapped[float] = mapped_column(Numeric(14, 4), default=0, comment="원/통화 적용 환율")
    source: Mapped[str] = mapped_column(String(10), comment="이 값의 출처: actual | forecast | plan")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FxPlanRate(Base):
    """경영환율 (연간, 통화당 12개월). 화면에서 직접 입력하거나 업로드로 받는다."""

    __tablename__ = "fx_plan_rates"
    __table_args__ = (
        UniqueConstraint("year", "currency_code", name="uq_fx_plan_rates_year_currency"),
        {"comment": "경영환율 (연간, 통화당 1~12월)"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[str] = mapped_column(String(4))
    currency_code: Mapped[str] = mapped_column(String(3))
    month1: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    month2: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    month3: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    month4: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    month5: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    month6: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    month7: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    month8: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    month9: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    month10: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    month11: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    month12: Mapped[float] = mapped_column(Numeric(14, 4), default=0)
    source_file: Mapped[str] = mapped_column(String(255), default="")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
