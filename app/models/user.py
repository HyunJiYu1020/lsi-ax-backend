"""로그인/인증 붙기 전까지도 created_by/updated_by가 참조할 수 있도록 먼저 만드는 사용자 테이블.
인증 방식(자체 ID/PW vs 사내 SSO)이 아직 미정이라 password_hash는 nullable로 두고,
SSO 연동 시 필요한 외부 식별자(employee_no)도 같이 둔다."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = {
        "comment": "로그인 사용자. 인증 방식(자체 ID/PW | 사내 SSO) 미정이라 "
        "password_hash는 nullable, SSO용 employee_no도 같이 둔다."
    }

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, comment="로그인 ID")
    employee_no: Mapped[str | None] = mapped_column(
        String(20), unique=True, comment="사내 사번 (SSO 연동 시 매칭 키, 자체 로그인만 쓰면 NULL)"
    )
    email: Mapped[str | None] = mapped_column(String(100), comment="이메일")
    name: Mapped[str] = mapped_column(String(50), comment="이름")
    department: Mapped[str | None] = mapped_column(String(50), comment="소속 부서")
    password_hash: Mapped[str | None] = mapped_column(
        String(255), comment="자체 로그인용 비밀번호 해시 (SSO만 쓰면 NULL)"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="로그인 가능 여부")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="계정 생성 시각"
    )
