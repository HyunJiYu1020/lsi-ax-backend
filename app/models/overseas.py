from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class OverseasLab(Base):
    """연구소·파트 기준정보(SARC는 CPU·GPU로 분리). 프론트는 이 테이블을 그대로 읽어 탭을 그린다.
    cost_entries 등 공용 테이블의 scope_key("SARC:CPU" 등)가 이 테이블의 pk를 가리키지만, scope_key는
    계정마다 의미가 달라 DB가 FK로 강제하지 못해 애플리케이션이 이 테이블로 검증한다."""

    __tablename__ = "overseas_labs"
    __table_args__ = {
        "comment": "해외연구소·파트 기준정보(SARC는 CPU·GPU로 분리) + 연구소에 속하지 않는 "
        "계정 전체 단위 값을 담는 가상 행(_ETC). 프론트 탭은 이 테이블을 lab_code로 묶어서 그린다."
    }

    id: Mapped[int] = mapped_column(primary_key=True)
    pk: Mapped[str] = mapped_column(
        String(20), unique=True, comment="연구소 식별 키 (\"SARC:CPU\", \"DSRA\", ...). cost_entries 등의 scope_key로 쓰인다"
    )
    lab_code: Mapped[str] = mapped_column(
        String(10), comment="연구소 코드 (\"SARC\"). 워크북 1개 = lab_code 1개 — 프론트 탭은 이 값으로 묶는다"
    )
    part_code: Mapped[str | None] = mapped_column(String(10), comment="파트 코드 (\"CPU\" | \"GPU\"), 없으면 NULL")
    name: Mapped[str] = mapped_column(String(50), comment="연구소명")
    country: Mapped[str] = mapped_column(String(20), comment="소재 국가")
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, default=0, comment="탭·집계표 표시 순서(lab_code 단위, 같은 lab_code의 파트들은 같은 값)"
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="USD", comment="현지통화 (연구소 기준정보 편집 화면에서 변경 가능, 기본은 국가별 통화)"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, comment="사용 여부. false면 취합·집계표·증감분석 화면에서 숨기지만 기존 데이터는 보존"
    )


class OverseasLabSheet(Base):
    """연구소(lab_code)별 워크북 시트 구성. 시트 수·이름이 연구소마다 다를 수 있어서
    (SARC+ACL만 비용 1개, 나머지는 비용·증감분석·외주용역업체 3개) account_steps처럼
    고정 컬럼이 아니라 행으로 둔다 — 시트가 늘어나도 이 테이블에 행만 추가하면 된다."""

    __tablename__ = "overseas_lab_sheets"
    __table_args__ = (
        UniqueConstraint("lab_code", "sheet_key"),
        {"comment": "연구소(lab_code)별 워크북 시트 구성(비용|증감분석|외주용역업체 등). 시트가 늘어도 행만 추가하면 된다."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lab_code: Mapped[str] = mapped_column(String(10), comment="overseas_labs.lab_code (파트 구분 없이 연구소 단위)")
    sheet_key: Mapped[str] = mapped_column(
        String(20), comment="시트 종류. common_codes(group_code='OVERSEAS_SHEET_TYPE')의 code 값 (cost|variance|vendor)"
    )
    sheet_order: Mapped[int] = mapped_column(SmallInteger, default=0, comment="그 연구소 안에서 시트 탭 표시 순서")


class OverseasReasonRow(Base):
    """증감사유 업로드 — 연구소×버전×비용항목×구간(연간 y | 월별 m) 하나당 1행. PLAN·전기·당기 값과
    그 차이(vs PLAN · vs 전기), 사유 텍스트를 담는다(덮어쓰기 upsert). lab_code는 파트 없는 연구소
    코드다 — 증감사유·외주용역은 SARC도 CPU+GPU를 합쳐 연구소 단위로만 올라온다."""

    __tablename__ = "overseas_reason_rows"
    __table_args__ = (
        UniqueConstraint("lab_code", "version", "category_key", "block", name="uq_overseas_reason_rows_key"),
        {"comment": "해외연구소 증감사유 업로드(연구소×버전×비용항목×구간). PLAN·전기·당기 비교값과 사유 텍스트."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lab_code: Mapped[str] = mapped_column(String(10), comment="연구소 코드(overseas_labs.lab_code, 파트 없이 — SARC는 CPU+GPU 합계)")
    version: Mapped[str] = mapped_column(String(10), comment="cur(당월 속보) | prev(전실행) | prior(전년 실적) | plan(경영계획)")
    category_key: Mapped[str] = mapped_column(String(40), comment="비용항목 키")
    block: Mapped[str] = mapped_column(String(1), comment="y(연간 블록) | m(월별 블록)")
    mon_label: Mapped[str] = mapped_column(String(10), default="", comment="월별 블록일 때의 월 라벨(파일 머리글 원문)")
    plan_value: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="PLAN 값")
    prev_value: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="전기 값")
    cur_value: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="당기 값")
    diff_plan: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="당기 - PLAN")
    diff_prev: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="당기 - 전기")
    remark_plan: Mapped[str] = mapped_column(String(500), default="", comment="vs PLAN 사유")
    remark_prev: Mapped[str] = mapped_column(String(500), default="", comment="vs 전기 사유")
    prev_label: Mapped[str] = mapped_column(String(20), default="", comment="전기 열 머리글 원문(예: 'M2608A')")
    cur_label: Mapped[str] = mapped_column(String(20), default="", comment="당기 열 머리글 원문(예: 'M2609A')")
    source_file: Mapped[str] = mapped_column(String(255), default="", comment="업로드한 원본 파일명")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OverseasCpRow(Base):
    """CP 계약 업로드 — POC(프로젝트) 헤더 값을 각 직급 행에 반복 저장하는 flat 방식.
    연 1회 전체를 올리는 파일이라 연도당 POC+직급 조합이 1행."""

    __tablename__ = "overseas_cp_rows"
    __table_args__ = (
        UniqueConstraint("year", "poc_type", "poc_proj", "title", name="uq_overseas_cp_rows_key"),
        {"comment": "CP 계약 업로드(연간). POC(프로젝트)×직급 하나당 1행, POC 헤더값은 각 행에 반복 저장."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[str] = mapped_column(String(4), comment="CP 계약 연도")
    poc_type: Mapped[str] = mapped_column(String(40), default="", comment="POC 구분(Type)")
    poc_proj: Mapped[str] = mapped_column(String(100), default="", comment="프로젝트명(Projects)")
    poc_desc: Mapped[str] = mapped_column(String(500), default="", comment="POC Description")
    title: Mapped[str] = mapped_column(String(100), comment="직급(Job Title)")
    dedicated: Mapped[float] = mapped_column(Numeric(10, 2), default=0, comment="전담(Dedicated) 인원")
    common: Mapped[float] = mapped_column(Numeric(10, 2), default=0, comment="공통(Common) 인원")
    man_month: Mapped[float] = mapped_column(Numeric(10, 2), default=0, comment="맨먼스(Man-month)")
    cost_year: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="연간 비용(Cost Year)")
    poc_cost_sum: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="POC 비용 합계(각 행에 반복 저장)")
    poc_mm_sum: Mapped[float] = mapped_column(Numeric(10, 2), default=0, comment="POC 맨먼스 합계(각 행에 반복 저장)")
    poc_direct: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="POC Direct R&D(각 행에 반복 저장)")
    poc_ovh: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="POC Overhead(각 행에 반복 저장)")
    poc_total: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="POC Total(각 행에 반복 저장)")
    source_file: Mapped[str] = mapped_column(String(255), default="", comment="업로드한 원본 파일명")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OverseasVendorRow(Base):
    """외주용역 업로드 — 연구소×버전×행 하나당 1건(업체·과제·내용). 월별 금액은 이 테이블에 두지
    않고 overseas_vendor_row_details가 월 1행씩 담는다(cost_entries/cost_entry_details,
    management_plan_entries/management_plan_entry_details와 같은 마스터-디테일 구조).

    lab_code는 증감사유와 같이 파트 없는 연구소 코드다 — 외주용역 시트도 SARC는 CPU+GPU를 합쳐
    연구소 단위로만 올라온다. row_no는 업로드한 시트 안에서의 행 순서로, 같은 연구소·버전을 다시
    올리면 행 수 자체가 달라지므로 "연구소×버전 단위 전체 교체"로 저장한다."""

    __tablename__ = "overseas_vendor_rows"
    __table_args__ = (
        UniqueConstraint("lab_code", "version", "row_no", name="uq_overseas_vendor_rows_key"),
        {"comment": "해외연구소 외주용역 업로드(연구소×버전×행). 월별 금액은 overseas_vendor_row_details 참조."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lab_code: Mapped[str] = mapped_column(String(10), comment="연구소 코드(overseas_labs.lab_code, 파트 없이 — SARC는 CPU+GPU 합계)")
    version: Mapped[str] = mapped_column(String(10), comment="cur(당월 속보) | prev(전실행) | prior(전년 실적) | plan(경영계획)")
    row_no: Mapped[int] = mapped_column(SmallInteger, comment="업로드한 시트 안에서의 행 순서(1부터)")
    category: Mapped[str] = mapped_column(String(50), default="", comment="Category(외주용역 구분)")
    project: Mapped[str] = mapped_column(String(100), default="", comment="Project name(과제명)")
    company: Mapped[str] = mapped_column(String(100), default="", comment="Company / Institution(업체·기관명)")
    description: Mapped[str] = mapped_column(String(500), default="", comment="Description / Purpose(내용·목적)")
    source_file: Mapped[str] = mapped_column(String(255), default="", comment="업로드한 원본 파일명")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OverseasVendorRowDetail(Base):
    """외주용역 행의 월별 금액(월 1행). 외주용역 시트는 월 금액만 받고 분기·연간은 화면이 월 합으로
    계산하기 때문에 cost_entry_details의 period_type에 해당하는 구분을 두지 않는다 — 분기·연간
    원본 숫자를 따로 받게 되면 그때 같은 모양으로 컬럼을 늘리면 된다."""

    __tablename__ = "overseas_vendor_row_details"
    __table_args__ = (
        UniqueConstraint("vendor_row_id", "year", "month", name="uq_overseas_vendor_row_details_key"),
        CheckConstraint("month BETWEEN 1 AND 12", name="ck_overseas_vendor_row_details_month"),
        {"comment": "외주용역 행별 월 금액(월 1행). 마스터는 overseas_vendor_rows."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_row_id: Mapped[int] = mapped_column(
        ForeignKey("overseas_vendor_rows.id", ondelete="CASCADE"), comment="overseas_vendor_rows.id (행 삭제 시 월 금액도 같이 삭제)"
    )
    year: Mapped[int] = mapped_column(SmallInteger, comment="이 금액이 속한 연도")
    month: Mapped[int] = mapped_column(SmallInteger, comment="월(1~12)")
    amount: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="금액")
