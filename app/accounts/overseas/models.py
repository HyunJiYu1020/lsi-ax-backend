from datetime import datetime

from sqlalchemy import (
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
)
from sqlalchemy.dialects.postgresql import JSONB
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


class OverseasLabRevision(Base):
    """연구소 기준정보(이름·통화·사용여부·순서)가 "언제부터" 그 값이었는지의 이력.

    왜 필요한가: overseas_labs는 "지금 값" 한 줄뿐이라, 통화를 USD→ILS로 바꾸면 지난달에 보고한
    전실행·전년실적 증감분석까지 현지통화 연구소로 다시 계산돼서 이미 마감한 숫자가 바뀐다.
    공통 RULE("기준정보 변경: 변경한 버전부터 적용, 지난 버전 영향 없음")을 지키려면 버전이 보는
    시점(effective_from 이하의 가장 최근 행)의 값으로 읽어야 한다 — fx의 overseas_fx_rule_overrides가
    환산 기준을 달 단위로 고정하는 것과 같은 방식이다.

    바꿀 때 두 줄이 들어간다: 바뀌기 전 값을 '0000-01'(처음부터)로 한 줄 박아 과거를 고정하고,
    새 값을 변경 시점(effective_from)으로 한 줄 넣는다. 그래서 과거 달을 조회하면 항상 바뀌기 전
    값이 나온다. 한 번도 안 바뀐 연구소는 이 테이블에 행이 없고, 그때는 overseas_labs를 그대로 쓴다."""

    __tablename__ = "overseas_lab_revisions"
    __table_args__ = (
        UniqueConstraint("lab_code", "effective_from", name="uq_overseas_lab_revisions_key"),
        CheckConstraint(
            r"effective_from ~ '^\d{4}-(0[1-9]|1[0-2])$'", name="ck_overseas_lab_revisions_effective_from_format"
        ),
        {
            "comment": "연구소 기준정보(이름·통화·사용여부·순서)의 시점별 이력. 어떤 버전을 볼 때 "
            "effective_from이 그 버전 시점 이하인 가장 최근 행의 값을 쓴다 — 기준정보를 바꿔도 지난 "
            "버전 숫자가 소급해서 바뀌지 않게 하려는 테이블."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lab_code: Mapped[str] = mapped_column(String(10), comment="연구소 코드(overseas_labs.lab_code). 파트가 여러 개면 파트 전부에 같이 적용된다")
    effective_from: Mapped[str] = mapped_column(
        String(7), comment='이 값이 적용되기 시작하는 편성 기준연월 "YYYY-MM". \'0000-01\'은 "처음부터"(바뀌기 전 값)를 뜻한다'
    )
    name: Mapped[str] = mapped_column(String(50), comment="그 시점의 연구소명")
    currency: Mapped[str] = mapped_column(String(3), comment="그 시점의 현지통화")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="그 시점의 사용 여부")
    sort_order: Mapped[int] = mapped_column(SmallInteger, default=0, comment="그 시점의 표시 순서")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="이 이력이 기록된 시각")


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
        UniqueConstraint("lab_code", "yyyymm", "category_key", "block", name="uq_overseas_reason_rows_key"),
        CheckConstraint(r"yyyymm ~ '^\d{6}$'", name="ck_overseas_reason_rows_yyyymm_format"),
        {"comment": "해외연구소 증감사유 업로드(연구소×버전×기준월×비용항목×구간). PLAN·전기·당기 비교값과 사유 텍스트."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lab_code: Mapped[str] = mapped_column(String(10), comment="연구소 코드(overseas_labs.lab_code, 파트 없이 — SARC는 CPU+GPU 합계)")
    version: Mapped[str] = mapped_column(String(10), comment="forecast(속보) | actual(실적)")
    # 어느 달 회신인지. version(역할값)만으로는 월을 알 수 없어 읽는 쪽이 "DB의 가장 최근 속보월
    # 것"이라고 짐작하게 되고, 그러면 9월에 올린 증감사유가 10월 자료로 잡혀 9월 업로드 현황이
    # '미'로 남는다. 비용(cost_entries.yyyymm)과 같은 기준을 쓴다.
    yyyymm: Mapped[str] = mapped_column(
        String(6), default="", comment='이 회신이 속한 편성 기준연월 "YYYYMM"(cost_entries.yyyymm과 같은 기준)'
    )
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


# "처음부터" — CP 계약 이력이 생기기 전부터 쓰던 세트를 가리키는 effective_from.
# services/labs.py의 LAB_EPOCH와 같은 관례다(그쪽은 연구소 기준정보 이력에 쓴다).
CP_EPOCH = "0000-01"


class OverseasCpRow(Base):
    """CP 계약 업로드 — POC(프로젝트) 헤더 값을 각 직급 행에 반복 저장하는 flat 방식.
    연 1회 전체를 올리는 파일이라 (연도 × 적용 시점)당 POC+직급 조합이 1행.

    왜 effective_from이 있나: CP 계약액은 저장된 값이 아니라 **조회할 때마다 다시 계산되는** 값의
    재료다(services/aggregate.cp_quarter_amounts가 연 금액 ÷ 4를 3·6·9·12월에 넣는다). 그래서 이
    테이블에 "지금 값" 한 세트만 두면, 계약액을 고치는 순간 이미 마감해서 보고한 과거 버전의 분기
    정산액까지 소급해서 바뀐다. 공통 RULE("기준정보 변경은 변경한 버전부터 적용, 지난 버전 영향
    없음")을 지키려면 버전이 보는 시점의 세트를 읽어야 한다 — overseas_lab_revisions가 연구소
    기준정보에 하는 것과 같은 일이다.

    연구소와 다른 점은 "한 줄"이 아니라 "행 집합"이라는 것이다. CP는 연 1회 전체를 올리는 파일이라
    변경분만 남기는 것보다 그 시점의 세트를 통째로 한 벌 더 넣는 **스냅샷** 방식이 자연스럽다 —
    읽을 때는 effective_from이 그 버전 시점 이하인 가장 최근 세트 하나를 통째로 쓴다."""

    __tablename__ = "overseas_cp_rows"
    __table_args__ = (
        UniqueConstraint(
            "year", "effective_from", "poc_type", "poc_proj", "title", name="uq_overseas_cp_rows_key"
        ),
        CheckConstraint(
            r"effective_from ~ '^\d{4}-(0[1-9]|1[0-2])$'", name="ck_overseas_cp_rows_effective_from_format"
        ),
        {
            "comment": "CP 계약 업로드(연간) 시점별 스냅샷. POC(프로젝트)×직급 하나당 1행, POC 헤더값은 "
            "각 행에 반복 저장. 어떤 버전을 볼 때 effective_from이 그 버전 시점 이하인 가장 최근 "
            "세트를 통째로 쓴다 — 계약액을 고쳐도 지난 버전의 분기 정산액이 소급해서 바뀌지 않게 한다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[str] = mapped_column(String(4), comment="CP 계약 연도")
    effective_from: Mapped[str] = mapped_column(
        String(7),
        default=CP_EPOCH,
        comment='이 세트가 적용되기 시작하는 편성 기준연월 "YYYY-MM". \'0000-01\'은 "처음부터"(이력이 '
        "생기기 전부터 쓰던 값)를 뜻한다",
    )
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
        UniqueConstraint("lab_code", "yyyymm", "row_no", name="uq_overseas_vendor_rows_key"),
        CheckConstraint(r"yyyymm ~ '^\d{6}$'", name="ck_overseas_vendor_rows_yyyymm_format"),
        {"comment": "해외연구소 외주용역 업로드(연구소×버전×기준월×행). 월별 금액은 overseas_vendor_row_details 참조."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lab_code: Mapped[str] = mapped_column(String(10), comment="연구소 코드(overseas_labs.lab_code, 파트 없이 — SARC는 CPU+GPU 합계)")
    version: Mapped[str] = mapped_column(String(10), comment="forecast(속보) | actual(실적)")
    # 증감사유와 같은 이유로 둔 컬럼이다(OverseasReasonRow.yyyymm 주석 참고).
    yyyymm: Mapped[str] = mapped_column(
        String(6), default="", comment='이 회신이 속한 편성 기준연월 "YYYYMM"(cost_entries.yyyymm과 같은 기준)'
    )
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


class OverseasActualFirstRow(Base):
    """실적 1차 분석(ERP 약식) — 연구소 하나의 그 달 총액 한 줄.

    1차와 2차는 자료 출처가 다르다. 1차는 결산 초기에 ERP에서 약식으로 뽑은 연구소 총액뿐이고(계정
    상세도, 외주용역도 없다), 2차는 PL이 확정된 뒤의 계정별 상세다. 그래서 1차 값을 cost_entries에
    넣으면 안 된다 — 같은 (연구소·연월·실적) 자리를 2차가 덮어써서, 이미 보고한 1차 값이 사라진다.
    별도 테이블에 따로 담아야 1차 → 2차 변동을 나중에도 보여줄 수 있다.

    금액은 현지통화로 담는다 — ERP가 현지 법인 기준으로 주고, 화면이 "현지 → $ → 원화"로 환산하며
    환차를 현지→$ / $→원으로 가르기 때문이다. usd_amount는 ERP가 $ 환산액을 같이 준 경우에만 채우고,
    비어 있으면 화면·서버가 환율로 계산한다(둘이 다를 때 ERP가 준 값을 이기게 하려고 따로 둔다).

    누계(YTD)는 컬럼으로 두지 않는다 — 같은 연도의 1~기준월 행을 더하면 나오는 값이라, 따로 저장하면
    월 값을 고쳤을 때 누계만 옛 값으로 남는 사고가 난다."""

    __tablename__ = "overseas_actual_first_rows"
    __table_args__ = (
        UniqueConstraint("basis_year", "basis_month", "lab_code", name="uq_overseas_actual_first_rows_key"),
        {
            "comment": "실적 1차 분석(ERP 약식)의 연구소별 그 달 총액 + 1차 사유. 2차(PL, cost_entries)와 "
            "별도로 저장해 이미 보고한 1차 값이 2차 업로드에 덮이지 않게 한다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    basis_year: Mapped[int] = mapped_column(SmallInteger, comment="실적 대상 연도")
    basis_month: Mapped[int] = mapped_column(SmallInteger, comment="실적 대상 월(1~12)")
    lab_code: Mapped[str] = mapped_column(
        String(10), comment="연구소 코드(overseas_labs.lab_code, 파트 없이 — 1차는 연구소 총액만 올라온다)"
    )
    local_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), default=0, comment="그 달 연구소 총액(현지통화). 통화는 그 시점 연구소 기준정보의 currency"
    )
    usd_amount: Mapped[float | None] = mapped_column(
        Numeric(18, 2), comment="ERP가 $ 환산액을 같이 준 경우의 값(K$). NULL이면 환율로 계산한다"
    )
    reason_text: Mapped[str] = mapped_column(
        Text, default="", comment="1차 사유. 2차 사유(overseas_variance_reasons)와 별도로 보관한다"
    )
    source_file: Mapped[str] = mapped_column(String(255), default="", comment="업로드한 ERP 약식 파일명")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="처음 저장한 시각")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )




class OverseasCostHeader(Base):
    """연구소가 보낸 비용 시트의 **기간 열 머리글 원문**(1월…12월 · 1Q~4Q · 연간).

    왜 저장하나: 취합 › 비용 표는 연구소 파일 하나를 그대로 비춰 보는 자리인데, 열 이름은 화면
    코드가 만든 글자("1월")였다. 연구소가 "'26.1월 실적" · "M2601A" · "202601"처럼 적어 보내면
    담당자가 보는 표와 연구소 보고서의 머리글이 달라져서, 어느 열이 파일의 어느 열인지 눈으로
    맞춰야 했다. 증감사유(overseas_reason_rows)가 prev_label · cur_label · mon_label로 "파일
    머리글 원문"을 이미 저장하는 것과 같은 이유다.

    집계·증감분석 표에는 쓰지 않는다 — 18개 연구소를 합친 표라 파일마다 머리글이 다르면 어느
    것을 대표로 쓸지 정할 수 없고, 한 연구소가 머리글을 바꿔 보내면 합친 표의 머리글이 그 달에만
    바뀐다. 그 표들은 지금처럼 버전이 정하는 머리글을 쓴다(프론트 ovActualThru).

    lab_pk는 파트까지 포함한 연구소 키("SARC:CPU")다 — 증감사유·외주용역과 달리 비용 시트는
    파트 단위로 올라오고, 취합 화면도 파트별로 표를 그린다.

    업로드 한 번이 그 시트의 머리글 전체를 **교체**한다(외주용역 행과 같은 방식) — 파일마다 열
    구성 자체가 달라질 수 있어서 열별 upsert로는 지난 업로드의 열이 남는다.
    """

    __tablename__ = "overseas_cost_headers"
    __table_args__ = (
        UniqueConstraint("lab_pk", "version", "yyyymm", "col_key", name="uq_overseas_cost_headers_key"),
        CheckConstraint("version IN ('forecast', 'actual')", name="ck_overseas_cost_headers_version"),
        CheckConstraint(r"yyyymm ~ '^\d{4}(0[1-9]|1[0-2])$'", name="ck_overseas_cost_headers_yyyymm_format"),
        CheckConstraint(r"col_key ~ '^(m[1-9][0-9]?|q[1-4]|y)$'", name="ck_overseas_cost_headers_col_key"),
        {
            "comment": "연구소 비용 시트의 기간 열 머리글 원문(연구소×버전×기준월×열). 취합 비용 표가 "
            "코드가 만든 '1월' 대신 이 글자를 그대로 보여준다. 집계·증감분석 표는 쓰지 않는다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lab_pk: Mapped[str] = mapped_column(
        String(20), comment="연구소 식별 키(overseas_labs.pk — 파트까지, 예 \"SARC:CPU\")"
    )
    version: Mapped[str] = mapped_column(String(10), comment="forecast(속보) | actual(실적)")
    yyyymm: Mapped[str] = mapped_column(
        String(6), comment='이 파일이 속한 편성 기준연월 "YYYYMM"(cost_entries.yyyymm과 같은 기준)'
    )
    col_key: Mapped[str] = mapped_column(
        String(10),
        comment='열 자리. "m1".."m12"는 월(기간을 12개월 너머로 받으면 "m13"…), "q1".."q4"는 분기, "y"는 연간',
    )
    label: Mapped[str] = mapped_column(String(100), comment="파일 머리글 글자 그대로(예 \"'26.1월 실적\")")
    source_file: Mapped[str] = mapped_column(String(255), default="", comment="이 머리글을 읽어 온 원본 파일명")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
