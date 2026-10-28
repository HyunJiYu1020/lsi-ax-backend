from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Account(Base):
    """19개 비용계정 레지스트리. 실제 업무 로직이 있는 계정(해외연구소 등)은
    data_type에 맞는 전용 테이블(overseas_* 등)을 따로 갖고, 아직 전용 화면이
    없는 계정은 이 테이블의 메타정보(상태·메모)만으로 "준비 중" 스텁을 보여준다."""

    __tablename__ = "accounts"
    __table_args__ = {
        "comment": "19개 비용계정 레지스트리. 전용 화면이 없는 계정은 이 테이블의 상태·메모만으로 "
        "'준비 중' 스텁을 보여준다."
    }

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, comment="계정 코드 (overseas, dep, outsource 등). 프론트 라우팅에 쓰임"
    )
    name: Mapped[str] = mapped_column(String(100), comment="계정명 (한글)")
    data_type: Mapped[str] = mapped_column(
        String(20), comment="데이터 형태: matrix(해외연구소) | line_items | monthly_total"
    )
    color: Mapped[str | None] = mapped_column(String(16), comment="대시보드 카드 색상 (hex)")
    status: Mapped[str] = mapped_column(
        String(20),
        default="drafting",
        comment="편성 진행 상태. common_codes(group_code='ACCOUNT_STATUS')의 code 값 "
        "(drafting|drafted|not_submitted|submitted|reviewed|rejected)",
    )
    memo: Mapped[str | None] = mapped_column(Text, comment="비고 메모")


class AccountStep(Base):
    """계정별 편성 프로세스 단계 정의. 계정마다 단계 수·이름이 전부 다르고
    (해외연구소 3단계, 감가상각비 4단계, 위탁용역비·생소비 6단계 등) 앞으로도
    늘어날 수 있어서, 고정 개수 컬럼이 아니라 행으로 둔다 — 단계가 늘어도
    이 테이블에 행만 추가하면 되고 스키마는 안 바뀐다."""

    __tablename__ = "account_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(SmallInteger, comment="accounts.id FK")
    step_order: Mapped[int] = mapped_column(SmallInteger, comment="단계 순서 (0-based)")
    tag: Mapped[str] = mapped_column(String(20), comment="단계 태그 (취합/편성/검토/분석 등)")
    title: Mapped[str] = mapped_column(String(100), comment="단계 제목 (예: '기초자료 업로드 · 맵핑')")

    __table_args__ = (
        UniqueConstraint("account_id", "step_order"),
        ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        {
            "comment": "계정별 편성 프로세스 단계 정의. 계정마다 단계 수·이름이 달라서 "
            "고정 컬럼이 아니라 행으로 관리한다."
        },
    )


class AccountProgress(Base):
    """계정별 현재 진행 단계. current_step_order는 반드시 그 계정의
    account_steps에 실제로 정의된 단계여야 한다 (복합 FK로 강제).
    REQ_013: 조회·갱신 API 대상."""

    __tablename__ = "account_progress"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(SmallInteger, unique=True, comment="accounts.id FK, 계정당 1행")
    current_step_order: Mapped[int] = mapped_column(
        SmallInteger,
        default=0,
        comment="현재 단계 (account_steps.step_order 참조, 복합 FK로 유효 단계만 허용)",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="단계를 이동시킨 사용자 (users.id)")

    __table_args__ = (
        ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        ForeignKeyConstraint(
            ["account_id", "current_step_order"],
            ["account_steps.account_id", "account_steps.step_order"],
        ),
        {"comment": "계정별 현재 진행 단계 (마지막으로 머문 단계)."},
    )
