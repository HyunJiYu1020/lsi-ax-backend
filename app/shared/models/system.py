"""계정 데이터가 아닌 시스템 전반 공용 테이블: 공통코드, 에러 로그."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CommonCodeGroup(Base):
    """공통코드 그룹 레지스트리. common_codes.group_code가 참조하는 "그룹 자체"의 목록이라,
    그룹코드 오타(예: "Version" vs "VERSION")를 DB가 바로 잡아준다."""

    __tablename__ = "common_code_groups"
    __table_args__ = {"comment": "공통코드 그룹 레지스트리. common_codes.group_code가 FK로 참조한다."}

    group_code: Mapped[str] = mapped_column(
        String(40), primary_key=True, comment='그룹코드 ("ACCOUNT_STATUS", "VERSION_STATUS", "OVERSEAS_CATEGORY", ...)'
    )
    name: Mapped[str] = mapped_column(String(100), comment="그룹명 (한글)")
    description: Mapped[str | None] = mapped_column(Text, comment="설명")


class CommonCode(Base):
    """전사 공통코드. group_code(코드그룹) 안에 code(코드값)들이 속하는 구조 + 계층이 필요하면
    parent_code로 표현한다(같은 group_code 안의 다른 code를 가리킴). 19개 계정·화면이 공유한다."""

    __tablename__ = "common_codes"
    __table_args__ = (
        UniqueConstraint("group_code", "code"),
        {"comment": "전사 공통코드(코드그룹×코드값). 계정 상태·버전 상태·해외연구소 비용항목·시트 종류 등."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_code: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("common_code_groups.group_code"),
        comment='코드그룹 ("ACCOUNT_STATUS", "VERSION_STATUS", "OVERSEAS_CATEGORY", ...)',
    )
    code: Mapped[str] = mapped_column(String(40), comment="코드값")
    name: Mapped[str] = mapped_column(String(100), comment="코드명(한글)")
    parent_code: Mapped[str | None] = mapped_column(String(40), comment="같은 group_code 내 상위 코드(계층 구조용)")
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0, comment="표시 순서")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="사용 여부")
    description: Mapped[str | None] = mapped_column(Text, comment="설명")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="최초 등록 시각"
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="등록한 사용자 (users.id)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")


class ErrorLog(Base):
    """API 서버 로그. 컬럼 이름·의미는 사내 표준 DDL(sait `error_log`)에 맞췄다 — 나중에 운영
    로그를 한 곳으로 모을 때 조회 쿼리·대시보드를 그대로 쓸 수 있어야 하기 때문이다. 테이블명만
    이 프로젝트 관례(복수형)를 따라 `error_logs`로 둔다.

    log4j 방식으로 적는다: 전역 예외 핸들러만이 아니라 `logger.warning()`/`logger.error()` 호출을
    DBLogHandler(shared/core/logging_db.py)가 가로채 저장한다. 그래서 미처리 500뿐 아니라
    **의도적으로 거절한 4xx도 남는다** — 500만 적으면 저장이 400으로 조용히 거부되는 사고가
    로그에 아무 흔적도 남기지 않는다."""

    __tablename__ = "error_logs"
    __table_args__ = {
        "comment": "API 서버 로그(WARNING 이상). 컬럼은 사내 표준 error_log DDL과 같은 이름·의미."
    }

    log_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="로그 id")

    # --- 추적 정보: SSO 전까지 user_id·session_id는 비어 있다(컬럼만 미리 맞춰 둔다) ---
    trace_id: Mapped[str | None] = mapped_column(String(50), comment="요청 1건을 잇는 추적 id (요청마다 생성)")
    user_id: Mapped[str | None] = mapped_column(
        String(50), comment="요청한 사용자 식별자(사번). SSO 연동 전까지는 NULL — 표준이 문자열 사번이라 users.id FK가 아니다"
    )
    session_id: Mapped[str | None] = mapped_column(String(100), comment="세션 id. SSO 연동 전까지는 NULL")

    # --- 시각 ---
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), comment="발생 시각")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="로그 적재 시각"
    )

    # --- 요청 ---
    client_ip: Mapped[str | None] = mapped_column(String(45), comment="클라이언트 IP (IPv6 길이까지 수용)")
    user_agent: Mapped[str | None] = mapped_column(String(500), comment="User-Agent 헤더")
    api_url: Mapped[str | None] = mapped_column(String(500), comment="요청 경로 (쿼리스트링 제외)")
    http_method: Mapped[str | None] = mapped_column(String(10), comment="HTTP 메서드")
    params: Mapped[str | None] = mapped_column(
        Text, comment="쿼리스트링 + 요청 본문(재현에 필요). 본문을 읽을 수 없으면 쿼리스트링만 남는다"
    )

    # --- 결과 ---
    log_level: Mapped[str] = mapped_column(String(20), comment="WARNING(거절된 4xx) | ERROR(미처리 500) | CRITICAL")
    http_status: Mapped[int | None] = mapped_column(Integer, comment="응답 상태 코드")
    error_code: Mapped[str | None] = mapped_column(
        String(50), comment="예외 클래스명 (표준의 error_code 자리 — 사내 코드 체계가 생기면 그 값으로 바꾼다)"
    )
    error_message: Mapped[str | None] = mapped_column(String(1000), comment="예외 메시지 (1000자에서 자른다)")
    stack_trace: Mapped[str | None] = mapped_column(Text, comment="스택트레이스. 4xx는 비어 있을 수 있다")
    server_name: Mapped[str | None] = mapped_column(String(100), comment="처리한 서버 호스트명")
    response_time: Mapped[int | None] = mapped_column(Integer, comment="응답 소요 시간(ms)")

    # --- 운영 ---
    process_status: Mapped[str | None] = mapped_column(
        String(20), server_default=text("'UNPROCESSED'"), comment="운영 처리 상태 (UNPROCESSED | ...)"
    )
    handler: Mapped[str | None] = mapped_column(String(50), comment="이 로그를 남긴 로거 이름")
