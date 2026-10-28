"""19개 비용계정이 공유하는 범용 데이터 테이블.
계정마다 전용 테이블을 또 만드는 대신, account_key로 구분해서 모든 계정이 같은 테이블을 쓴다.
scope_key는 계정 안에서의 "행 단위" 식별자(해외연구소는 연구소 pk, 다른 계정은 부서코드·계약번호 등)로,
계정마다 의미가 다르므로 여기서는 FK로 강제하지 않고 애플리케이션(각 계정 라우터)이 검증한다."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, SmallInteger, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CostEntry(Base):
    """계정×행×비용항목×버전 비용 데이터 (월은 1~12 고정 범위라 행이 아니라 컬럼으로 펼친 wide-format).
    한 행 = 한 카테고리의 1년치 데이터. 업로드·조회가 항상 "카테고리 하나당 12개월"
    단위로 오가는 실제 접근 패턴과 맞춰 month1~month12 컬럼으로 둔다."""

    __tablename__ = "cost_entries"
    __table_args__ = (
        UniqueConstraint("account_key", "scope_key", "version", "category_key", "actual_thru_month"),
        {"comment": "계정×행×비용항목×버전 비용 데이터(월은 month1~month12 컬럼). 19개 계정이 공유."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.key"), comment='accounts.key ("overseas", "dep", ...)'
    )
    scope_key: Mapped[str] = mapped_column(String(40), comment="계정 안에서의 행 단위 식별자 (해외연구소는 연구소 pk)")
    version: Mapped[str] = mapped_column(String(10), comment="cur(당월 속보) | prev(전실행) | prior(전년 실적) | plan(경영계획)")
    category_key: Mapped[str] = mapped_column(String(40), comment="비용항목 키. 고정 enum 아닌 자유 문자열")
    actual_thru_month: Mapped[int] = mapped_column(
        SmallInteger,
        default=0,
        comment="실적이 반영된 마지막 달(1~12). cur 버전에서 같은 카테고리가 '실적'(기준월-1)과 "
        "'속보'(기준월)로 동시에 존재할 때 구분용. 그 외 버전은 0(해당 없음)",
    )
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


class Upload(Base):
    """업로드 이력 (계정×행×버전별로 받은 파일명·시각)."""

    __tablename__ = "uploads"
    __table_args__ = {"comment": "계정×행×버전별 업로드 이력. 19개 계정이 공유."}

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(32), ForeignKey("accounts.key"), comment="accounts.key")
    scope_key: Mapped[str] = mapped_column(String(40), comment="계정 안에서의 행 단위 식별자")
    version: Mapped[str] = mapped_column(String(10), comment="cur | prev | prior | plan")
    filename: Mapped[str] = mapped_column(String(255), comment="업로드한 원본 파일명")
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


class CustomRow(Base):
    """사용자가 편성 화면에서 직접 추가한 행(계정에 없는 비용 항목). 월별 값은
    cost_entries에 category_key=row_key로 같이 저장되고, 여기는 존재·이름만 관리한다."""

    __tablename__ = "custom_rows"
    __table_args__ = (
        UniqueConstraint("account_key", "scope_key", "version", "row_key"),
        {"comment": "사용자가 직접 추가한 행(존재·이름만). 월별 값은 cost_entries 참조."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(32), ForeignKey("accounts.key"), comment="accounts.key")
    scope_key: Mapped[str] = mapped_column(String(40), comment="계정 안에서의 행 단위 식별자")
    version: Mapped[str] = mapped_column(String(10), comment="cur | prev | prior | plan")
    row_key: Mapped[str] = mapped_column(String(40), comment='클라이언트가 생성한 행 id ("cx...")')
    label: Mapped[str] = mapped_column(String(100), default="", comment="사용자가 입력한 행 이름")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="행 추가 시각"
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="행을 추가한 사용자 (users.id)")
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
    """메모 셀 (행×열 교차점의 자유 텍스트). 행은 고정 계정 key 또는 custom_rows.row_key."""

    __tablename__ = "memos"
    __table_args__ = (
        UniqueConstraint("account_key", "scope_key", "version", "row_key", "col_key"),
        {"comment": "메모 셀. 집계·증감분석 계산에는 들어가지 않는 참고용 텍스트."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(32), ForeignKey("accounts.key"), comment="accounts.key")
    scope_key: Mapped[str] = mapped_column(String(40), comment="계정 안에서의 행 단위 식별자")
    version: Mapped[str] = mapped_column(String(10), comment="cur | prev | prior | plan")
    row_key: Mapped[str] = mapped_column(String(40), comment="고정 계정 key 또는 custom_rows.row_key")
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
