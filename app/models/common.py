"""19개 비용계정이 공유하는 범용 데이터 테이블.
계정마다 전용 테이블을 또 만드는 대신, account_key로 구분해서 모든 계정이 같은 테이블을 쓴다.
scope_key는 계정 안에서의 "행 단위" 식별자(해외연구소는 연구소 pk, 다른 계정은 부서코드·계약번호 등)로,
계정마다 의미가 다르므로 여기서는 FK로 강제하지 않고 애플리케이션(각 계정 라우터)이 검증한다."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, SmallInteger, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SheetRow(Base):
    """정형 테이블(cost_entries 등)로 아직 못 옮긴 시트의 원본 행 저장소.
    해외연구소의 '증감분석'·'외주용역업체' 시트처럼 열 구성이 아직 확정되지 않았거나
    자유 양식인 경우, 헤더 행을 키로 쓰는 JSON 한 덩어리로 우선 보관한다 — 나중에 열 구조가
    확정되면 이 테이블은 그대로 두고 그 계정의 조회 로직만 바꿔서 정형 테이블로 옮기면 된다."""

    __tablename__ = "sheet_rows"
    __table_args__ = (
        UniqueConstraint("account_key", "scope_key", "version", "sheet_key", "row_key"),
        {"comment": "정형화 안 된 시트의 원본 행(JSON) 저장소. 19개 계정이 공유."},
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
    """계정×행×비용항목×버전의 "편성 회차" 헤더 한 장. year·month는 금액이 아니라 "이 연간 데이터를
    몇년 몇월 기준으로 편성했는지"를 가리킨다(기존 actual_thru_month 역할을 더 분명한 이름으로 대체) —
    매달 새로 편성하는 형식이라 월 축이 꼭 필요하다. 실제 월별 금액은 이 헤더를 FK로 참조하는
    CostEntryMonth에 행으로 들어가고(target_year·target_month·amount), 같은 헤더에 당해 12개월과
    9월 속보부터 같이 들어오는 차년도 월(target_year = year+1)이 함께 달릴 수 있다."""

    __tablename__ = "cost_entries"
    __table_args__ = (
        UniqueConstraint("account_key", "scope_key", "version", "category_key", "year", "month"),
        {"comment": "계정×행×비용항목×버전×편성월 헤더. 실제 월별 금액은 cost_entry_months 참조. 19개 계정이 공유."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(
        String(32), ForeignKey("accounts.key"), comment='accounts.key ("overseas", "dep", ...)'
    )
    scope_key: Mapped[str] = mapped_column(String(40), comment="계정 안에서의 행 단위 식별자 (해외연구소는 연구소 pk)")
    version: Mapped[str] = mapped_column(String(10), comment="cur(당월 속보) | prev(전실행) | prior(전년 실적) | plan(경영계획)")
    category_key: Mapped[str] = mapped_column(String(40), comment="비용항목 키. 고정 enum 아닌 자유 문자열")
    year: Mapped[int] = mapped_column(SmallInteger, comment="편성 기준 연도")
    month: Mapped[int] = mapped_column(
        SmallInteger,
        comment="편성 기준월(1~12). cur 버전에서 같은 카테고리가 '실적'(기준월-1)과 '속보'(기준월)로 "
        "동시에 존재할 때 구분용으로도 쓰인다. 그 외 버전은 관례상 1(연초 1회 편성)",
    )
    extra: Mapped[dict | None] = mapped_column(JSONB, comment="엑셀 업로드에서 인식 못한 열 보관용")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="최초 입력 시각"
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="최초 입력한 사용자 (users.id)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")


class CostEntryMonth(Base):
    """CostEntry 헤더 1장에 달리는 월별 금액 행. target_year/target_month는 CostEntry.year/month(편성
    기준월)와 다른 축이다 — 당해 1~12월 12행 + (9월 속보부터) 차년도(target_year=year+1) 1~12월 최대
    12행까지 같은 cost_entry_id 아래 같이 들어갈 수 있다. 화면에 뿌릴 때는 cost_entries와 join한 뒤
    이 행들을 target_year·target_month 기준으로 행렬전환(pivot)해서 그리드 열로 조합한다."""

    __tablename__ = "cost_entry_months"
    __table_args__ = (
        UniqueConstraint("cost_entry_id", "target_year", "target_month"),
        {"comment": "cost_entries 헤더별 월 금액 행. 당해 12개월 + 차년도(9월 속보~) 월이 함께 달릴 수 있다."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cost_entry_id: Mapped[int] = mapped_column(
        ForeignKey("cost_entries.id", ondelete="CASCADE"), comment="cost_entries.id (헤더 삭제 시 월별 금액도 같이 삭제)"
    )
    target_year: Mapped[int] = mapped_column(SmallInteger, comment="이 금액이 속한 연도 (당해 또는 차년도)")
    target_month: Mapped[int] = mapped_column(SmallInteger, comment="이 금액이 속한 달(1~12)")
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


class CustomRow(Base):
    """사용자가 편성 화면에서 직접 추가한 행(계정에 없는 비용 항목). 월별 값은
    cost_entries(헤더)+cost_entry_months(월별 금액)에 category_key=row_key로 같이 저장되고,
    여기는 존재·이름만 관리한다. row_key는 자유 문자열이라 DB가 FK로 강제하지 못하고
    애플리케이션이 값으로 매칭한다(계정마다 월별 금액이 들어갈 실제 테이블이 다를 수 있어서 —
    해외연구소는 cost_entries가 아니라 overseas_cost_entries를 쓴다)."""

    __tablename__ = "custom_rows"
    __table_args__ = (
        UniqueConstraint("account_key", "scope_key", "version", "row_key"),
        {"comment": "사용자가 직접 추가한 행(존재·이름만). 월별 값은 cost_entries/cost_entry_months 참조."},
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
