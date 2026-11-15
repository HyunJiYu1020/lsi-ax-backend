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


class OverseasReasonTranslation(Base):
    """증감사유 영문 원문 → 한국어 번역 캐시(원문 1건당 1행).

    왜 저장하는가: 증감분석 화면은 연구소 12개 × 계정 20여 개의 사유를 한 번에 그려서, 번역을
    매번 LLM에 물으면 화면이 뜨는 데만 수십 초가 걸리고 호출 비용도 그만큼 든다. 더 중요한 건
    일관성이다 — 같은 회신 문장이 어제와 오늘 다른 한국어로 보이면 보고서 설명이 흔들린다.
    한 번 번역한 원문은 이 테이블의 값을 계속 쓰고, 다시 번역해야 할 때만(모델 교체·오역 수정)
    같은 행을 덮어쓴다.

    원문(source_text)을 번역문과 같은 행에 남기는 건 공통 RULE(원문 보관) 때문이다 — 번역이
    이상할 때 연구소가 실제로 뭐라고 썼는지 되짚을 수 있어야 한다. 식별은 정규화한 원문의
    해시로 한다(엑셀에서 긁은 원문은 줄바꿈·연속 공백이 제각각이라 원문 그대로는 같은 문장이
    여러 행으로 갈라진다 — app/core/reason_translate.py의 normalize 참고)."""

    __tablename__ = "overseas_reason_translations"
    __table_args__ = {
        "comment": "해외연구소 증감사유 영문 원문 → 한국어 번역 캐시(정규화한 원문 해시로 식별, 원문 같이 보관)."
    }

    id: Mapped[int] = mapped_column(primary_key=True)
    source_hash: Mapped[str] = mapped_column(
        String(64), unique=True, comment="정규화(연속 공백 1칸)한 원문의 SHA-256 16진 문자열 — 같은 문장을 두 번 번역하지 않게 하는 키"
    )
    source_text: Mapped[str] = mapped_column(String(600), comment="정규화한 영문 원문(보관용). 번역이 이상할 때 되짚는 근거")
    ko_text: Mapped[str] = mapped_column(String(600), comment="한국어 번역문(보고용 명사형 간결체)")
    engine: Mapped[str] = mapped_column(
        String(40), default="", comment='번역한 모델("gemini:gemini-3.5-flash-lite" 등). 모델을 바꾼 뒤 옛 번역을 구분·재번역할 때 쓴다'
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="처음 번역한 시각")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="다시 번역해서 덮어쓴 시각"
    )


class OverseasRowAlias(Base):
    """"이 행 이름은 저 행과 같은 행"이라고 사람이 한 번 답한 결과(원문 이름 1개당 1행).

    연구소마다 같은 비용을 다른 이름으로 적어 보낸다 — 고정 계정 출장비의 영문명은 'Travel Exp'인데
    'Travel Expense'로 적어 보내는 연구소가 있다. 업로드할 때 비슷한 이름을 찾아 "같은 행인가요?"를
    묻고(app/core/row_match.py), 그 대답을 여기 남긴다. 안 남기면 다음 달에 12개 연구소에 같은
    질문을 처음부터 다시 하게 된다.

    "아니다(별개 행)"도 같이 저장한다(target_kind='distinct') — 그것도 사람이 판단한 결과라서,
    안 남기면 매달 "이거 같은 행 아니에요?"를 또 묻는다. 묻지 않기 위해 저장하는 테이블이다.

    alias_norm은 정규화한(공백·기호 뺀 소문자) 이름이라 'Travel Expense'와 'travel  expense'가
    한 행으로 합쳐진다. 한 이름이 두 행을 가리킬 수는 없어서 unique다 — 바꾸려면 덮어쓴다."""

    __tablename__ = "overseas_row_aliases"
    __table_args__ = (
        CheckConstraint("target_kind IN ('category', 'extra_row', 'distinct')", name="ck_overseas_row_aliases_target_kind"),
        {
            "comment": "업로드 파일의 행 이름 → 이미 있는 행 매핑(사람이 확인한 결과). "
            "target_kind=category(고정 계정) | extra_row(추가 행) | distinct(별개 행이라고 답함)."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    alias_norm: Mapped[str] = mapped_column(
        String(100), unique=True, comment="파일에 적혀 온 이름을 정규화한 값(공백·기호 제거 + 소문자). 이게 매칭 키다"
    )
    alias_label: Mapped[str] = mapped_column(String(100), comment="파일에 적혀 온 이름 원문(보관용 — 어떤 표기에 답한 건지 되짚을 때)")
    target_kind: Mapped[str] = mapped_column(
        String(12), comment="category(고정 계정과 같은 행) | extra_row(이미 있는 추가 행과 같은 행) | distinct(별개 행)"
    )
    target_key: Mapped[str] = mapped_column(
        String(100), default="", comment="category면 비용항목 키(travel 등), extra_row면 정식 이름의 정규화값. distinct면 빈 값"
    )
    target_label: Mapped[str] = mapped_column(
        String(100), default="", comment="합쳐 넣을 정식 이름(화면·집계에서 쓰는 이름). distinct면 빈 값"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="처음 답한 시각")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="답을 바꿔서 덮어쓴 시각"
    )


class OverseasCarryDef(Base):
    """집계 화면에서 만든 파생 행·열 중 "다음 버전에도 유지"를 승인한 것(1건당 1행).

    파생 행·열은 버전×기준연월 단위로 저장돼서, 새 버전을 열면 지난 버전에서 만든 계산 행이
    사라진다 — 매달 보는 지표(인당 인건비 등)는 매번 다시 만들어야 했다. 그렇다고 전부 자동으로
    따라가게 하면, 그 달만 보려고 만든 일회성 지표까지 계속 끌려다닌다. 그래서 만들 때 한 번 묻고
    (승인한 것만) 여기 남겨서, 새 버전을 열 때 그것만 복제한다.

    값이 아니라 정의(계산식)만 따라간다 — 재료(업로드 값)가 새 버전 것으로 바뀌면 계산 결과도
    그 버전 숫자로 다시 나온다. def_key는 파생 행이면 row_key, 파생 열이면 label이다(각자 그
    종류 안에서 식별자 역할을 하는 값)."""

    __tablename__ = "overseas_carry_defs"
    __table_args__ = (
        UniqueConstraint("kind", "def_key", name="uq_overseas_carry_defs_key"),
        CheckConstraint("kind IN ('row', 'col')", name="ck_overseas_carry_defs_kind"),
        {"comment": "다음 버전에도 유지하기로 승인한 집계 파생 행·열. 새 버전을 열 때 이 정의만 복제한다."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(3), comment="row(파생 행) | col(파생 열)")
    def_key: Mapped[str] = mapped_column(String(100), comment="파생 행이면 row_key, 파생 열이면 label")
    label: Mapped[str] = mapped_column(String(100), default="", comment="사람이 읽을 이름(어떤 지표를 유지 중인지 보여줄 때)")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="승인한 시각")


class OverseasVarianceReason(Base):
    """증감분석 화면의 증감사유 한 칸 — "회신 원문 → 번역 + AI 초안 → 담당자 수정 → [저장]"의 상태 전부.

    왜 notes 테이블로 안 되는가: notes는 (계정·비교기준·기간·행·항목)만 키로 잡고 텍스트 한 줄을
    담는다. 증감사유는 그 한 줄에 이르는 과정 자체가 보고 자료다 — 연구소가 영문으로 써 보낸 원문,
    그 번역, AI가 뽑은 보고용 초안, 담당자가 고쳐서 확정한 문장이 다 남아야 "이 숫자를 왜 이렇게
    설명했나"를 되짚을 수 있다(공통 RULE: 원문 보관). 게다가 notes에는 기준연월이 없어서 9월 속보에
    쓴 사유가 10월 속보에도 그대로 붙는다 — 금액이 바뀌었는데 사유가 따라오면 틀린 설명이 된다.

    saved_diff가 핵심이다: [저장]을 누른 순간의 증감액을 같이 박아 둔다. 그 뒤 연구소가 파일을 다시
    올려서 금액이 바뀌면 사유를 지우지 않고(담당자가 쓴 글을 서버가 버리면 안 된다) needs_recheck만
    켜서 화면에 "재확인 필요"로 띄운다 — 사유는 유지, 확인은 사람이 다시 한다.

    stage는 실적 버전의 1차/2차를 가른다. 1차(ERP 약식)는 연구소 총액만 보고 쓰는 사유이고 2차(PL)는
    계정까지 보고 쓰는 사유라서, 같은 연구소·같은 달이어도 서로 덮어쓰면 안 된다(1차는 보고까지 끝난
    값이다). 속보 버전은 1차/2차 구분이 없어 빈 문자열을 쓴다."""

    __tablename__ = "overseas_variance_reasons"
    __table_args__ = (
        UniqueConstraint(
            "basis_year", "basis_month", "kind", "stage", "base_version", "period", "lab_scope", "category_key",
            name="uq_overseas_variance_reasons_key",
        ),
        CheckConstraint("kind IN ('forecast', 'actual')", name="ck_overseas_variance_reasons_kind"),
        CheckConstraint("stage IN ('', 'first', 'second')", name="ck_overseas_variance_reasons_stage"),
        CheckConstraint("base_version IN ('prev', 'prior', 'plan')", name="ck_overseas_variance_reasons_base_version"),
        {
            "comment": "증감분석 증감사유 한 칸의 전체 상태(회신 원문·번역·AI 초안·확정 문장). "
            "저장 시점 증감액(saved_diff)을 같이 남겨 재업로드로 금액이 바뀌면 needs_recheck로 '재확인 필요'를 띄운다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    basis_year: Mapped[int] = mapped_column(SmallInteger, comment="편성 기준연도")
    basis_month: Mapped[int] = mapped_column(SmallInteger, comment="편성 기준월(1~12)")
    kind: Mapped[str] = mapped_column(String(10), comment="forecast(속보) | actual(실적) — versions.kind와 같은 값")
    stage: Mapped[str] = mapped_column(
        String(8), default="", comment="실적 분석 단계: first(1차 ERP 약식) | second(2차 PL). 속보는 빈 문자열"
    )
    base_version: Mapped[str] = mapped_column(String(10), comment="비교 기준: prev(전실행) | prior(전년) | plan(경영계획)")
    period: Mapped[str] = mapped_column(String(4), comment="기간: y(연간) | q1~q4 | m1~m12")
    lab_scope: Mapped[str] = mapped_column(
        String(20), comment='화면에서 고른 연구소 범위. "ALL"(전체) | 연구소 코드 | 연구소 pk("SARC:CPU")'
    )
    category_key: Mapped[str] = mapped_column(String(40), comment="비용항목 키(labor · travel · tot ...)")
    source_text: Mapped[str] = mapped_column(
        Text, default="", comment="연구소가 써 보낸 회신 원문(보통 영문). 번역·초안의 근거라 지우지 않고 같이 남긴다"
    )
    source_ko: Mapped[str] = mapped_column(Text, default="", comment="회신 원문의 한국어 번역(overseas_reason_translations와 같은 규칙)")
    ai_draft: Mapped[str] = mapped_column(Text, default="", comment="번역 + 증감 숫자로 AI가 만든 보고용 사유 초안")
    reason_text: Mapped[str] = mapped_column(
        Text, default="", comment="담당자가 고쳐서 [저장]한 확정 사유. 보고서에 실리는 문장이다"
    )
    saved_diff: Mapped[float | None] = mapped_column(
        Numeric(18, 2),
        comment="[저장] 시점의 증감액(원). 지금 증감액과 다르면 재업로드로 금액이 바뀐 것이라 needs_recheck를 켠다",
    )
    needs_recheck: Mapped[bool] = mapped_column(
        Boolean, default=False, comment='True면 저장 뒤 금액이 바뀌었다는 뜻 — 화면에 "재확인 필요"로 표시한다(사유는 유지)'
    )
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="[저장]을 누른 시각. 초안만 있으면 NULL")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="이 칸이 처음 생긴 시각")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )


class OverseasVarianceDriver(Base):
    """증감 동인 표 — "인건비를 인력수 × 인당 단가로 쪼개 보여줘" 같은 요청으로 증감분석 화면에
    덧붙인 분해 표 하나.

    왜 추가 행(cost_entries + formula)이 아닌가: 추가 행은 편성 그리드의 한 줄이고, 이건 증감분석
    화면에만 붙는 별도 표다(기준·당월·증감을 동인별로 가른 작은 표). 무엇보다 추가 행에는 "다음
    버전에도 유지할까"라는 개념이 없다 — 여기서는 그게 핵심 상태다.

    carry_over가 그 상태다. AI가 표를 붙일 때는 지금 보고 있는 버전에만 붙이고(carry_over=False),
    담당자가 "다음 버전에도?"에 승인하면 켠다. 조회할 때 "이 버전에 붙인 표 + 이전 버전에서
    carry_over로 켜 둔 표"를 합쳐 주므로, 승인한 표는 다음 달에도 저절로 따라온다. 승인 안 한 표는
    그 버전에서만 보이고 사라진다 — 한 번 물어본 분해를 매달 영구 고정하지 않으려는 것이다.

    factors에는 숫자가 아니라 계산식만 담는다(파생 행이 formula만 저장하는 것과 같은 이유) —
    연구소가 파일을 다시 올리면 동인 금액도 따라 바뀌어야 한다."""

    __tablename__ = "overseas_variance_drivers"
    __table_args__ = (
        UniqueConstraint(
            "basis_year", "basis_month", "kind", "lab_scope", "driver_key", name="uq_overseas_variance_drivers_key"
        ),
        CheckConstraint("kind IN ('forecast', 'actual')", name="ck_overseas_variance_drivers_kind"),
        {
            "comment": "증감분석 화면에 AI가 덧붙인 증감 동인 분해 표. carry_over=True면 다음 버전에도 따라온다. "
            "금액이 아니라 계산식(factors)만 담아 원본이 바뀌면 같이 바뀐다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    basis_year: Mapped[int] = mapped_column(SmallInteger, comment="이 표를 붙인 편성 기준연도")
    basis_month: Mapped[int] = mapped_column(SmallInteger, comment="이 표를 붙인 편성 기준월(1~12)")
    kind: Mapped[str] = mapped_column(String(10), comment="forecast(속보) | actual(실적)")
    lab_scope: Mapped[str] = mapped_column(String(20), default="ALL", comment='연구소 범위("ALL" | 연구소 코드 | pk)')
    driver_key: Mapped[str] = mapped_column(String(40), comment='표 식별 키(같은 버전 안에서 유일). 예: "labor_hc_rate"')
    title: Mapped[str] = mapped_column(String(100), comment='표 제목("인건비 동인 — 인력수 × 인당 단가")')
    target_key: Mapped[str] = mapped_column(String(40), comment="분해 대상 비용항목 키(labor 등). 이 행 아래에 표를 붙인다")
    factors: Mapped[list] = mapped_column(
        JSONB,
        comment='동인 행 목록. [{"label":"인력수","op":"ref","operands":["hc"],"unit":"hc"}, '
        '{"label":"인당 단가","op":"div_elementwise","operands":["labor","hc"],"unit":"krw"}]',
    )
    requested_text: Mapped[str] = mapped_column(
        String(500), default="", comment="담당자가 요청한 원문 문장. 나중에 이 표가 왜 생겼는지 되짚는 근거"
    )
    carry_over: Mapped[bool] = mapped_column(
        Boolean, default=False, comment='True면 "다음 버전에도?"에 승인한 표 — 이후 버전 조회에도 따라온다'
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="표를 붙인 시각")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )


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


class OverseasActualStage(Base):
    """실적 한 달(연월)의 1차 분석 진행 상태 — 1차를 열 수 있는지, 1차를 고정했는지.

    두 값이 서로 다른 일을 한다:
    - close_first_done: 실적결산 쪽의 1차가 끝났는지. 끝나지 않았으면 가져올 ERP 값이 아예 없으므로
      화면의 [1차 분석] 탭을 비활성으로 둔다(눌러서 빈 표를 보여주면 "왜 아무것도 없지"가 된다).
    - first_locked: [1차 고정]. 1차 값·사유를 보고까지 했으면 더 고쳐지면 안 된다. 켜져 있으면
      1차 쓰기(값·사유)를 서버가 거부하고, 해제해야 다시 쓸 수 있다.

    first_locked는 versions.status와 무관하다 — 버전 확정과 1차 고정은 다른 층의 결정이라서,
    1차를 고정해도 버전은 작성중 그대로다(2차 작업이 남아 있다).

    행이 없으면 "실적결산 1차 미완료 + 고정 안 됨"이다 — 달마다 미리 행을 만들지 않는다."""

    __tablename__ = "overseas_actual_stages"
    __table_args__ = (
        UniqueConstraint("basis_year", "basis_month", name="uq_overseas_actual_stages_key"),
        {
            "comment": "실적 연월의 1차 분석 상태. close_first_done=실적결산 1차 완료 여부(화면 1차 탭 활성), "
            "first_locked=[1차 고정](보고한 1차 값·사유 수정 금지). versions.status에는 영향 없음."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    basis_year: Mapped[int] = mapped_column(SmallInteger, comment="실적 대상 연도")
    basis_month: Mapped[int] = mapped_column(SmallInteger, comment="실적 대상 월(1~12)")
    close_first_done: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="실적결산 1차가 끝났는지. False면 화면의 1차 분석 탭을 비활성으로 둔다"
    )
    first_locked: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="True면 [1차 고정] 상태 — 1차 값·사유 쓰기를 거부한다(해제해야 수정 가능)"
    )
    first_locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="[1차 고정]을 누른 시각. 해제하면 NULL로 지운다"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="이 행이 생긴 시각")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )


class OverseasAnomalyRule(Base):
    """이상 감지 기준(기준 1건당 1행). 급변 몇 % · 합계 오차 얼마 같은 값을 담는다.

    왜 테이블인가: 코드에 박아두면 못 바꾸고, 브라우저에 두면 창을 닫으면 날아가고 사람마다 다른
    기준으로 보게 된다. 챗봇에서 "이제 50% 넘는것만 알려줘"를 승인하면 이 행이 바뀌고, 그 뒤로는
    창을 새로 열어도·다른 담당자가 봐도 같은 기준이 적용된다(해외연구소 공통).

    쓰는 파라미터만 채우고 나머지는 NULL이다 — 급변은 pct+amount, 합계 불일치는 amount(오차),
    사유 누락은 켜고/끄기만, 단위 의심은 amount(절대값)+ratio(배수). 기본값과 각 기준의 뜻은
    app/core/anomaly.py의 DEFAULTS에 있다(행이 없으면 그 값으로 동작한다)."""

    __tablename__ = "overseas_anomaly_rules"
    __table_args__ = {
        "comment": "해외연구소 이상 감지 기준(급변·합계 불일치·사유 누락·단위 의심). 금액 단위는 K$. "
        "행이 없으면 app/core/anomaly.py의 기본값으로 동작한다."
    }

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_key: Mapped[str] = mapped_column(
        String(20), unique=True, comment="jump(급변) | sum_mismatch(합계 불일치) | missing_reason(사유 누락) | unit_suspect(단위 의심)"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, comment="사용 여부. false면 그 기준은 검사하지 않는다")
    pct: Mapped[float | None] = mapped_column(Numeric(6, 2), comment="비율 기준(%). 급변만 쓴다")
    amount: Mapped[float | None] = mapped_column(
        Numeric(14, 2), comment="금액 기준(K$). 급변=변동액 하한, 합계 불일치=허용 오차, 단위 의심=절대값 상한"
    )
    ratio: Mapped[float | None] = mapped_column(Numeric(10, 2), comment="배수 기준. 단위 의심만 쓴다(전 버전 대비 N배·1/N)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막으로 바꾼 시각"
    )


class OverseasAnomalyRuleLog(Base):
    """이상 감지 기준 변경 이력(변경 1건당 1행).

    기준이 바뀌면 경고 개수가 달라지고, 그러면 "지난달엔 왜 이게 안 걸렸지"라는 질문이 나온다.
    그때 "9월에 급변 기준을 10% → 50%로 올렸다"를 댈 수 있어야 한다. 챗봇으로 바꾸는 경로가
    열려 있어서(말 한마디로 바뀐다) 더 필요하다 — 무엇이 어떻게 바뀌었는지 before/after로 남긴다."""

    __tablename__ = "overseas_anomaly_rule_logs"
    __table_args__ = {"comment": "이상 감지 기준 변경 이력(before/after + 누가 어떤 경로로 바꿨는지)."}

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_key: Mapped[str] = mapped_column(String(20), comment="바뀐 기준 키(overseas_anomaly_rules.rule_key)")
    before: Mapped[dict | None] = mapped_column(JSONB, comment="바뀌기 전 값(enabled·pct·amount·ratio)")
    after: Mapped[dict | None] = mapped_column(JSONB, comment="바뀐 뒤 값")
    source: Mapped[str] = mapped_column(String(10), default="screen", comment="chat(챗봇) | screen(화면) | reset(기본값 복구)")
    note: Mapped[str] = mapped_column(String(200), default="", comment='무슨 말로 바꿨는지(예: 챗봇 요청 "50% 넘는것만 알려줘")')
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="바꾼 시각")
