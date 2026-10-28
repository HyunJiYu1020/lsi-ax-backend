"""계정 데이터가 아닌 시스템 전반 공용 테이블: 공통코드, 에러 로그."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, SmallInteger, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CommonCodeGroup(Base):
    """공통코드 그룹 레지스트리. common_codes.group_code가 참조하는 "그룹 자체"의 목록이라,
    그룹코드 오타(예: "Version" vs "VERSION")를 DB가 바로 잡아준다."""

    __tablename__ = "common_code_groups"
    __table_args__ = {"comment": "공통코드 그룹 레지스트리. common_codes.group_code가 FK로 참조한다."}

    group_code: Mapped[str] = mapped_column(
        String(40), primary_key=True, comment='그룹코드 ("VERSION", "FX_RATE_TYPE", "ACCOUNT_STATUS", ...)'
    )
    name: Mapped[str] = mapped_column(String(100), comment="그룹명 (한글)")
    description: Mapped[str | None] = mapped_column(Text, comment="설명")


class CommonCode(Base):
    """전사 공통코드. group_code(코드그룹) 안에 code(코드값)들이 속하는 구조 + 계층이 필요하면
    parent_code로 표현한다(같은 group_code 안의 다른 code를 가리킴). 19개 계정·화면이 공유한다."""

    __tablename__ = "common_codes"
    __table_args__ = (
        UniqueConstraint("group_code", "code"),
        {"comment": "전사 공통코드(코드그룹×코드값). 버전·환율종류·통화·부서코드·계정과목 등."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_code: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("common_code_groups.group_code"),
        comment='코드그룹 ("VERSION", "FX_RATE_TYPE", "CURRENCY", "DEPT", ...)',
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
    """API 서버에서 발생한 처리되지 않은 예외(500) 로그. main.py의 전역 예외 핸들러가 기록한다."""

    __tablename__ = "error_logs"
    __table_args__ = {"comment": "API 서버 미처리 예외(500) 로그."}

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="발생 시각"
    )
    method: Mapped[str] = mapped_column(String(10), comment="HTTP 메서드")
    path: Mapped[str] = mapped_column(String(255), comment="요청 경로")
    status_code: Mapped[int] = mapped_column(SmallInteger, comment="응답 상태 코드")
    error_type: Mapped[str] = mapped_column(String(100), comment="예외 클래스명")
    message: Mapped[str] = mapped_column(Text, comment="예외 메시지")
    traceback: Mapped[str] = mapped_column(Text, comment="스택트레이스")
