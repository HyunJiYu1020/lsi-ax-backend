from sqlalchemy import Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class OverseasLab(Base):
    """13개 연구소·파트 (SARC는 CPU·GPU로 분리). 기준정보라 자주 안 바뀜.
    cost_entries 등 공용 테이블의 scope_key("SARC:CPU" 등)가 이 테이블의 pk를 가리키는데,
    scope_key는 계정마다 의미가 달라 DB가 FK로 강제하지 못하므로 애플리케이션이 이 테이블로 검증한다."""

    __tablename__ = "overseas_labs"
    __table_args__ = {
        "comment": "13개 해외연구소·파트 기준정보(SARC는 CPU·GPU로 분리) + 연구소에 속하지 않는 "
        "계정 전체 단위 값을 담는 가상 행(_ETC)."
    }

    id: Mapped[int] = mapped_column(primary_key=True)
    pk: Mapped[str] = mapped_column(
        String(20), unique=True, comment="연구소 식별 키 (\"SARC:CPU\", \"DSRA\", ...). cost_entries 등의 scope_key로 쓰인다"
    )
    lab_code: Mapped[str] = mapped_column(String(10), comment="연구소 코드 (\"SARC\")")
    part_code: Mapped[str | None] = mapped_column(String(10), comment="파트 코드 (\"CPU\" | \"GPU\"), 없으면 NULL")
    name: Mapped[str] = mapped_column(String(50), comment="연구소명")
    country: Mapped[str] = mapped_column(String(20), comment="소재 국가")


class FxRate(Base):
    """원/$ · ILS · INR 환율 (버전×통화×종류, 월은 1~12 고정이라 행이 아니라 month1~month12 컬럼).
    계정에 속하지 않는 전사 공통 기준정보."""

    __tablename__ = "fx_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(
        String(10), comment="cur(당월 속보) | prev(전실행) | prior(전년 실적) | plan(경영계획)"
    )
    currency: Mapped[str] = mapped_column(String(6), comment="USD | ILS(이스라엘) | INR(인도)")
    rate_type: Mapped[str] = mapped_column(String(10), comment="end(월말환율) | act(실적환율) | cp(CP환율)")
    month1: Mapped[float] = mapped_column(Numeric(12, 4), default=0, comment="1월 환율")
    month2: Mapped[float] = mapped_column(Numeric(12, 4), default=0, comment="2월 환율")
    month3: Mapped[float] = mapped_column(Numeric(12, 4), default=0, comment="3월 환율")
    month4: Mapped[float] = mapped_column(Numeric(12, 4), default=0, comment="4월 환율")
    month5: Mapped[float] = mapped_column(Numeric(12, 4), default=0, comment="5월 환율")
    month6: Mapped[float] = mapped_column(Numeric(12, 4), default=0, comment="6월 환율")
    month7: Mapped[float] = mapped_column(Numeric(12, 4), default=0, comment="7월 환율")
    month8: Mapped[float] = mapped_column(Numeric(12, 4), default=0, comment="8월 환율")
    month9: Mapped[float] = mapped_column(Numeric(12, 4), default=0, comment="9월 환율")
    month10: Mapped[float] = mapped_column(Numeric(12, 4), default=0, comment="10월 환율")
    month11: Mapped[float] = mapped_column(Numeric(12, 4), default=0, comment="11월 환율")
    month12: Mapped[float] = mapped_column(Numeric(12, 4), default=0, comment="12월 환율")

    __table_args__ = (
        UniqueConstraint("version", "currency", "rate_type"),
        {"comment": "버전×통화×환율종류 환율 기준정보(월은 month1~month12 컬럼). 해외연구소 원화 환산·환차 분석에 쓰인다."},
    )
