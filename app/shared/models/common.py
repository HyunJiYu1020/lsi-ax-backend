"""19개 비용계정이 공유하는 범용 데이터 테이블. account_key로 계정을 구분한다.
scope_key는 계정 안의 행 단위 식별자(해외연구소는 연구소 pk)로, 계정마다 의미가 달라 FK로 강제하지
않고 애플리케이션이 검증한다."""

from datetime import datetime

from sqlalchemy import (
    Index,
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
        # 읽기 핫패스 인덱스(마이그레이션 f2b7c9d14e08). 위 UNIQUE는 (account_code, scope_code, ...)
        # 순서라 selectivity가 거의 없는 account_code만 걸러지고 version·yyyymm은 못 쓴다 —
        # /overseas/state와 list_cost_entries가 쓰는 조합으로 따로 둔다.
        Index("ix_cost_entries_account_version_yyyymm", "account_code", "version", "yyyymm"),
        # 사용자가 추가한 행(label IS NOT NULL)만 담는 부분 인덱스 — 전체의 1% 미만이라 아주 작다.
        # 상태 조립의 custom_keys · 추가 행 조회 · 비용항목 검증이 모두 이 조건을 쓴다.
        Index(
            "ix_cost_entries_custom_rows", "account_code", "scope_code", "category_code",
            postgresql_where=text("label IS NOT NULL"),
        ),
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
    분기·연간 행은 월 값을 쓸 때마다 그 해 12개월 합으로 재계산돼 덮어쓰인다 — 공용 엔드포인트는
    cost.py의 _resync_summary가 한다. 해외연구소는 **만들지 않는다** — 연구소가 파일에 적어 보낸
    칸만 저장하고 분기·연간은 보는 쪽이 월에서 계산한다(services/overseas/entries.py 머리 주석).
    col_name은 CUSTOM이 아니면 빈 문자열(NULL 아님) — Postgres UNIQUE/ON CONFLICT가 NULL을 서로 다른 값으로
    취급해 중복 삽입될 수 있기 때문이다."""

    __tablename__ = "cost_entry_details"
    __table_args__ = (
        UniqueConstraint("cost_entry_id", "year", "period_type", "period", "col_name"),
        CheckConstraint("period_type IN ('MONTH', 'QUARTER', 'YEAR', 'CUSTOM')", name="ck_cost_entry_details_period_type"),
        CheckConstraint("data_type IN ('number', 'text')", name="ck_cost_entry_details_data_type"),
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
    # 추가 열(CUSTOM) 칸은 연구소가 뭘 적어 보낼지 모른다 — 금액일 수도, 사용률(0.825)일 수도,
    # "환율 반영" 같은 글자일 수도 있다. 숫자를 amount에만 담으면 Numeric(14,2)가 소수 2자리에서
    # 깎고(0.825 → 0.83) 글자는 담을 자리가 없어서, 원문은 value에 글자 그대로 두고 어느 쪽인지를
    # data_type으로 박아 둔다. 타입 판정(숫자로 읽히는가)은 쓰는 쪽 한 곳(services/extra_rows.
    # classify_extra_value)에서만 하고, 읽는 쪽은 저장된 data_type을 믿고 분기만 한다 — 숫자면
    # 계산에 쓰고 글자면 그대로 그린다. 검증 대상 기간 행(MONTH·QUARTER·YEAR)은 지금처럼 amount가
    # 참값이고 value는 NULL이다(이 컬럼이 생기기 전 저장분도 같다).
    data_type: Mapped[str] = mapped_column(
        String(10), default="number", server_default=text("'number'"),
        comment="number | text — 읽는 쪽 분기 기준. CUSTOM 칸만 text가 될 수 있다",
    )
    value: Mapped[str | None] = mapped_column(
        String(500), comment='CUSTOM 칸의 원문("0.825" · "환율 반영"). NULL이면 amount가 참값(월·분기·연간 행과 과거 저장분)'
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")


class Upload(Base):
    """업로드 이력 (계정×행×기준월별로 받은 파일명·시각).

    기준월(yyyymm)이 키다 — 비용(cost_entries) · 증감사유 · 외주용역과 같은 축이다. 자리
    이름(cur|prev|prior|plan)은 저장하지 않고 읽을 때 달로 정한다: 자리는 보는 달에 따라 달라지는
    값이라(9월에 올린 파일이 11월 화면에서도 cur이다) 저장 키로 쓰면 같은 행이 아무 달에나
    "이번에 받은 파일"로 보인다."""

    __tablename__ = "uploads"
    __table_args__ = (
        # 읽기 핫패스 인덱스(마이그레이션 f2b7c9d14e08 · a7c4e0f21d58). PK 말고 인덱스가 없었다 —
        # 지울 일 없이 계속 쌓이는 이력 테이블이라, (scope_key, yyyymm)별 최신 1건을 뽑는
        # DISTINCT ON 쿼리가 쌓인 만큼 정렬 비용을 그대로 떠안았다.
        Index(
            "ix_uploads_account_scope_month_recent",
            "account_key", "scope_key", "yyyymm", "uploaded_at",
        ),
        {"comment": "계정×행×기준월별 업로드 이력. 19개 계정이 공유."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(32), ForeignKey("accounts.key"), comment="accounts.key")
    scope_key: Mapped[str] = mapped_column(String(40), comment="계정 안에서의 행 단위 식별자")
    # 달이 없는 자료(경영계획)는 빈 문자열이고, 그때는 version이 그 해의 차수(P02)를 가리킨다.
    yyyymm: Mapped[str] = mapped_column(
        String(6), default="", comment='이 파일이 속한 기준연월 "YYYYMM". 경영계획처럼 달이 없는 자료는 빈 문자열'
    )
    # 같은 달에 **몇 번째로 받은 파일인지**. 경영계획이 차수를 P00·P01·P02로 관리하는 것과 같은
    # 관례다 — 연구소가 같은 달에 두 번 보내면 v1 · v2로 남아 "무엇을 받아 지금 숫자가 됐나"를
    # 되짚을 수 있다 — 최신 파일명만 남기면 그 전 파일은 자리를 잃는다.
    version: Mapped[str] = mapped_column(String(10), comment="그 기준월의 차수(v1 · v2 …). 경영계획은 P02 같은 계획 차수")
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

    열 자체를 행으로 갖는 레지스트리다. 이 테이블이 없으면 같은 col_name을 가진
    cost_entry_details(period_type="CUSTOM") 칸들의 집합이 곧 "그 열"이어서, 열 단위 속성
    (파생 여부·계산식)을 걸 자리가 없다.

    원시 열(is_derived=False)의 칸 값은 cost_entry_details에 저장되고 여기는
    존재·이름만 관리한다. 파생 열(is_derived=True)은 칸 값을 저장하지 않고 formula로 매번 계산한다.
    label 자체가 열 식별자라 이름 변경은 지원하지 않는다(지우고 새 이름으로 다시 만들면 된다)."""

    __tablename__ = "extra_cols"
    __table_args__ = (
        UniqueConstraint("account_key", "scope_key", "version", "yyyymm", "label"),
        CheckConstraint("version IN ('forecast', 'actual')", name="ck_extra_cols_version"),
        CheckConstraint(r"yyyymm ~ '^\d{4}(0[1-9]|1[0-2])$'", name="ck_extra_cols_yyyymm_format"),
        CheckConstraint("fmt IN ('raw', 'pct_ratio', 'pct_plain')", name="ck_extra_cols_fmt"),
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
    # 값이 아니라 **그리는 방법**이다. 양식 밖 열에는 비율이 연구소마다 다른 모양으로 오는데
    # ("0.825" · "82.5"), 값만 보고는 어느 쪽인지 알 수 없어 사람이 열마다 한 번 골라 둔다.
    # 글자 값은 어떤 형식이든 원문 그대로 그린다(형식은 숫자 칸에만 걸린다).
    fmt: Mapped[str] = mapped_column(
        String(16),
        default="raw",
        server_default=text("'raw'"),
        comment="raw(그대로) | pct_ratio(0.825→82.5%) | pct_plain(82.5→82.5%)",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="열 추가 시각"
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="열을 추가한 사용자 (users.id)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")
