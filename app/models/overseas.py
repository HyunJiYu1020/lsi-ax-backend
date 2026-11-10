from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, SmallInteger, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class OverseasLab(Base):
    """연구소·파트 기준정보(SARC는 CPU·GPU로 분리). 지금은 12개 연구소지만 앞으로 늘어날 수 있어서
    프론트는 이 테이블을 그대로 읽어 탭을 그린다(코드 수정 없이 행 추가만으로 탭이 늘어남).
    cost_entries 등 공용 테이블의 scope_key("SARC:CPU" 등)가 이 테이블의 pk를 가리키는데,
    scope_key는 계정마다 의미가 달라 DB가 FK로 강제하지 못하므로 애플리케이션이 이 테이블로 검증한다."""

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


class OverseasCostEntry(Base):
    """해외연구소 비용 데이터 행(연구소×버전×비용항목×속보실적구분×연도) 하나. 월1~12·분기(1~4Q)·연간
    금액을 모두 이 행의 컬럼으로 들고 있어 별도 월별 테이블이 없고, 사용자가 추가한 열(존재·이름·값)도
    custom_fields([{label, value}, ...])에 JSONB로 같이 저장한다 — label 자체가 열 식별자다."""

    __tablename__ = "overseas_cost_entries"
    __table_args__ = (
        UniqueConstraint("scope_key", "version", "category_key", "actual_thru_month", "year"),
        {
            "comment": "해외연구소 비용 데이터 행(연구소×버전×비용항목×속보실적구분×연도). 월1~12·분기·연간 금액과 "
            "사용자 추가 열(custom_fields)을 한 행에 담는다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(40), comment="연구소 pk (overseas_labs.pk)")
    version: Mapped[str] = mapped_column(String(10), comment="cur(당월 속보) | prev(전실행) | prior(전년 실적) | plan(경영계획)")
    category_key: Mapped[str] = mapped_column(String(40), comment="비용항목 키. 고정 enum 아닌 자유 문자열")
    label: Mapped[str | None] = mapped_column(
        String(100), comment="사용자가 직접 추가한 행의 이름(구분, 한글). 고정 계정(category_key가 OV_EL에 있는 키)은 NULL"
    )
    label_en: Mapped[str | None] = mapped_column(
        String(100), comment="사용자가 직접 추가한 행의 영문 이름(Cost Element). 고정 계정은 NULL"
    )
    anchor_key: Mapped[str | None] = mapped_column(
        String(40), comment="추가 행을 이 계정(core.js OV_EL의 k) 바로 뒤에 끼워 넣는다. NULL이면 표 맨 끝"
    )
    year: Mapped[int] = mapped_column(SmallInteger, comment="이 금액이 속한 연도. custom_fields만 있는 행도 예외 없이 항상 있어야 함")
    actual_thru_month: Mapped[int] = mapped_column(
        SmallInteger,
        default=0,
        comment="실적이 반영된 마지막 달(1~12). cur 버전에서 같은 카테고리가 '실적'과 '속보'로 "
        "동시에 존재할 때 구분용. 그 외 버전은 0(해당 없음)",
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
    q1: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="1분기(1~3월) 합계")
    q2: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="2분기(4~6월) 합계")
    q3: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="3분기(7~9월) 합계")
    q4: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="4분기(10~12월) 합계")
    annual: Mapped[float] = mapped_column(Numeric(14, 2), default=0, comment="연간(1~12월) 합계")
    custom_fields: Mapped[list | None] = mapped_column(
        JSONB, comment="사용자가 추가한 열. [{label, value}, ...] — label 자체가 열 식별자"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="최초 입력 시각"
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="최초 입력한 사용자 (users.id)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")


class OverseasReasonRow(Base):
    """증감사유 업로드 — 연구소×버전×비용항목×구간(연간 y | 월별 m) 하나당 1행. PLAN·전기·당기 값과
    그 차이(vs PLAN · vs 전기), 사유 텍스트까지 통째로 담는다. 원본 비용 숫자(overseas_cost_entries)가
    아니라 업로드 파일의 "비교·사유" 스냅샷이라 별도 테이블로 둔다 — 덮어쓰기 upsert.
    lab_code는 overseas_cost_entries.scope_key(=overseas_labs.pk, 파트 포함)와 달리 파트 없는
    연구소 코드다 — 증감사유·외주용역은 SARC도 CPU+GPU를 합쳐 연구소 단위로만 올라온다."""

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
    """CP 계약 업로드 — POC(프로젝트) 헤더 값을 각 직급 행에 반복 저장하는 flat 방식
    (overseas_cost_entries와 같은 컨벤션). 연 1회 전체를 올리는 파일이라 연도당 POC+직급 조합이 1행."""

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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="최초 입력 시각"
    )
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="최초 입력한 사용자 (users.id)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), comment="마지막으로 수정한 사용자 (users.id)")

    __table_args__ = (
        UniqueConstraint("version", "currency", "rate_type"),
        {"comment": "버전×통화×환율종류 환율 기준정보(월은 month1~month12 컬럼). 해외연구소 원화 환산·환차 분석에 쓰인다."},
    )
