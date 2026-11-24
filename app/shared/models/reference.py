"""기준정보 — 비용계정(Cost Elem.) · 자산 Class 매핑 · 부서코드 · PLM 과제.

화면(기준정보 › 비용계정 / 부서코드 / PLM 과제, 감가상각비 [기준정보] 팝업)이 쓰는 마스터다.
여태 이 네 표만 브라우저(localStorage)에 있었다. 그러면 ① 옆자리 담당자는 다른 부서코드표를
보고, ② 브라우저를 비우면 표가 사라지고, ③ 챗봇·집계가 서버에서 계정명·FA를 못 찾는다.
그래서 화면 양식(2026-11 개편)대로 서버에 옮긴다.

**적용 시점 규칙**(공통 RULE: "기준정보 변경은 변경한 버전부터, 지난 버전은 그대로")을 표마다
다르게 푼다 — 화면이 그렇게 갈라 놓았고, 그 이유가 표마다 다르다:

* 비용계정은 **연 단위**다(RefCostElement). 계정 체계는 회계연도 단위로 바뀌고 월마다 손대는
  표가 아니다. 대신 버전을 만들 때 그 시점 연도표를 복사해 둔다(RefCostElementSnapshot) —
  연도표를 나중에 고쳐도 이미 만든 속보·실적의 계정명이 소급해서 바뀌면 안 된다.
* 부서코드·PLM 과제는 **버전 단위**다(RefDeptCode·RefPlmTask). 조직 개편·과제 Drop은 달마다
  생기고, 그게 그 달 편성값의 귀속부서·과제코드 검증 결과를 바꾼다.
* 자산 Class 매핑은 **시점이 없다**(RefAssetClass). 감가상각비가 자산 Class로 비용계정을 찾는
  고정 변환표라서, 버전마다 다른 값일 이유가 없다.

버전 식별은 (basis_year, version_kind, version_seq) 세 칼럼으로 한다. 속보·실적은 seq가 월(1~12),
경영계획은 seq가 P 번호(P01 → 1)다. 프런트의 버전 id("2026-09-S")를 그대로 담지 않는 이유는,
그 문자열이 화면 표기 규칙이라 바뀔 수 있고 DB에서 연·월로 범위 조회를 못 하게 되기 때문이다.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

# 버전 종류 — cost_entries.version(forecast|actual)에 경영계획(plan)을 더한 집합.
# 경영계획도 기준정보를 보고 계정명·FA를 붙이므로 같은 규칙을 적용해야 한다.
VERSION_KINDS = ("forecast", "actual", "plan")
_CK_KIND = "version_kind IN ('forecast', 'actual', 'plan')"
# 속보·실적은 월(1~12), 경영계획은 P 번호(0~99). 둘을 한 칼럼에 담되 범위는 넉넉히 둔다.
_CK_SEQ = "version_seq BETWEEN 0 AND 99"


class RefCostElement(Base):
    """비용계정(Cost Elem.) 연 단위 마스터 — 구분 · Cost Elem. · 계정(1)~(5).

    키가 code가 아니라 (year, sort_order)인 이유: 같은 Cost Elem.이 두 줄 있을 수 있다(감가상각비
    53450300은 기계장치 Class와 신규투자 기본계정으로 두 번 나온다). 화면도 code 중복을 막지 않고
    표의 순서를 그대로 보여준다 — 표 순서가 곧 담당자가 보는 계정 체계의 순서다.

    그래서 쓰기는 '그 연도 전체 교체'다(PUT /reference/cost-elements). 한 줄만 고치는 PATCH를 두면
    중복 code를 어떤 줄로 보내는지 가릴 수 없다.
    """

    __tablename__ = "ref_cost_elements"
    __table_args__ = (
        UniqueConstraint("year", "sort_order", name="uq_ref_cost_elements_key"),
        CheckConstraint("code <> ''", name="ck_ref_cost_elements_code_not_empty"),
        Index("ix_ref_cost_elements_year_code", "year", "code"),
        {
            "comment": "기준정보 › 비용계정(Cost Elem.) 연 단위 마스터. 표 순서가 키라 연도 전체를 "
            "교체해서 쓴다 — 같은 Cost Elem.이 구분만 달리 두 줄 있을 수 있다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger, comment="이 계정 체계를 쓰는 회계연도")
    sort_order: Mapped[int] = mapped_column(SmallInteger, comment="그 연도 표 안의 행 순서(0부터). code가 아니라 이 값이 키다")
    gb: Mapped[str] = mapped_column(String(20), default="", comment='구분("감가상각비" · "위탁용역비" · "생소비" …). 어느 계정 화면이 쓰는 계정인지 알려주는 꼬리표')
    code: Mapped[str] = mapped_column(String(20), comment='Cost Elem.(ERP 비용계정 코드, "53450300"). 모든 계정 화면이 이 값으로 계정명을 찾는다')
    a1: Mapped[str] = mapped_column(String(40), default="", comment='계정(1) — 최상위 분류("03 경비")')
    a2: Mapped[str] = mapped_column(String(40), default="", comment='계정(2)("05 자산성경비")')
    a3: Mapped[str] = mapped_column(String(40), default="", comment='계정(3)("21 감가상각비")')
    a4: Mapped[str] = mapped_column(String(40), default="", comment='계정(4)("36 감가상각비")')
    name: Mapped[str] = mapped_column(String(100), comment='계정(5) = 계정명("감가상각비-기계장치"). 표·엑셀에 찍히는 이름')
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막으로 고친 시각"
    )


class RefCostElementSnapshot(Base):
    """버전을 만든 시점의 비용계정 사본.

    왜 사본이 필요한가: 연도표(RefCostElement)는 계속 고친다. 11월에 계정명을 고쳤는데 9월 속보가
    그 이름으로 다시 그려지면, 이미 보고한 표와 화면이 달라진다. 그래서 버전을 처음 열 때(=처음
    조회할 때) 그 시점 연도표를 이 테이블로 복사해 고정한다 — 이후 연도표를 고쳐도 지난 버전은
    복사본을 계속 쓴다. overseas_lab_revisions가 연구소 기준정보에 하는 일과 같은 규칙이다.

    source_year를 같이 남긴다. 2027년 1월 속보를 2026년 계정표로 만든 경우(2027년 표를 아직 안
    만들었을 때) "어느 연도 표에서 떠 온 사본인지"가 화면 안내문에 그대로 나가야 한다.
    """

    __tablename__ = "ref_cost_element_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "basis_year", "version_kind", "version_seq", "sort_order", name="uq_ref_cost_element_snapshots_key"
        ),
        CheckConstraint(_CK_KIND, name="ck_ref_cost_element_snapshots_kind"),
        CheckConstraint(_CK_SEQ, name="ck_ref_cost_element_snapshots_seq"),
        {
            "comment": "버전(연·종류·월/P번호)을 처음 열 때 복사한 비용계정 사본. 연도표를 나중에 "
            "고쳐도 지난 버전 계정명이 소급해 바뀌지 않게 한다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    basis_year: Mapped[int] = mapped_column(SmallInteger, comment="버전의 대상 연도")
    version_kind: Mapped[str] = mapped_column(String(10), comment="forecast(속보) | actual(실적) | plan(경영계획)")
    version_seq: Mapped[int] = mapped_column(SmallInteger, comment="속보·실적이면 월(1~12), 경영계획이면 P 번호(P01 → 1)")
    source_year: Mapped[int] = mapped_column(SmallInteger, comment="이 사본을 떠 온 연도표의 연도(그 연도 표가 없으면 그 이하 가장 최근 연도)")
    sort_order: Mapped[int] = mapped_column(SmallInteger, comment="사본 안의 행 순서(원본 연도표 순서 그대로)")
    gb: Mapped[str] = mapped_column(String(20), default="", comment="복사 시점의 구분")
    code: Mapped[str] = mapped_column(String(20), comment="복사 시점의 Cost Elem.")
    a1: Mapped[str] = mapped_column(String(40), default="", comment="복사 시점의 계정(1)")
    a2: Mapped[str] = mapped_column(String(40), default="", comment="복사 시점의 계정(2)")
    a3: Mapped[str] = mapped_column(String(40), default="", comment="복사 시점의 계정(3)")
    a4: Mapped[str] = mapped_column(String(40), default="", comment="복사 시점의 계정(4)")
    name: Mapped[str] = mapped_column(String(100), comment="복사 시점의 계정(5) = 계정명")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="사본을 만든 시각(=그 버전을 처음 연 시각)"
    )


class RefAssetClass(Base):
    """자산 Class → 비용계정 매핑(감가상각비 전용).

    감가상각비만 자산 Class(ERP Asset Class, "16110100")로 비용계정을 찾는다. 신규 투자는 Asset
    Class가 없어서 "신규투자"라는 약속된 이름으로 한 줄 둔다(프런트 NW_CLS와 같은 값) — 이 행이
    없으면 신규 투자 자산 전체가 계정 미지정으로 떨어진다.

    버전·연도 구분이 없는 표다. 자산 Class는 ERP 코드 체계이고 그게 가리키는 상각비 계정도 함께
    고정이라, 달마다 다른 값일 이유가 없다(그래서 화면도 이 표만 버전 선택 없이 보여준다).
    """

    __tablename__ = "ref_asset_classes"
    __table_args__ = (
        UniqueConstraint("asset_class", name="uq_ref_asset_classes_key"),
        CheckConstraint("asset_class <> ''", name="ck_ref_asset_classes_class_not_empty"),
        {
            "comment": "자산 Class → 비용계정(Cost Elem.) 매핑. 감가상각비만 쓴다. 신규 투자는 Asset "
            "Class가 없어 '신규투자' 한 줄을 약속된 키로 둔다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_class: Mapped[str] = mapped_column(
        String(20), comment='자산 Class("16110100") 또는 Class가 없는 경우의 약속된 키("신규투자" · "전산기기(HPC)")'
    )
    class_text: Mapped[str] = mapped_column(String(50), default="", comment='자산 Class명("기계장치")')
    code: Mapped[str] = mapped_column(String(20), comment="이 Class가 쓰는 Cost Elem.(비용계정 코드)")
    name: Mapped[str] = mapped_column(String(100), default="", comment="그 비용계정의 계정(5) = 계정명(확인용 사본)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막으로 고친 시각"
    )


class RefDeptCode(Base):
    """부서코드(Cost Center) — 버전별. 이름 · Budget Unit · 팀실(小·中·大) · FA · 사용/폐기.

    버전별인 이유: 조직 개편이 달마다 생긴다. 9월 속보는 그때 쓰던 부서코드로 검증해야 하고,
    10월에 폐기된 코드가 9월 편성값을 오류로 만들면 안 된다.

    is_rep("대표코드")는 신규 투자 때문에 있다. 신규 투자는 업로드 양식에 부서코드가 없고 조직
    이름(팀실 小)만 온다 — 그 조직을 대표하는 Cost Center 한 개로 맵핑해야 금액이 부서별 표에
    들어간다. 그래서 팀실 小마다 is_rep=True가 하나여야 하고, 둘 이상이면 화면이 경고를 띄운다
    (DB로는 막지 않는다 — 막으면 개편 중간 상태를 저장할 수 없어 담당자가 표를 못 고친다).

    fam3는 화면 표에 없지만 생소비·정률기술료가 쓰는 제품군 코드라 데이터로는 남긴다.
    """

    __tablename__ = "ref_dept_codes"
    __table_args__ = (
        UniqueConstraint("basis_year", "version_kind", "version_seq", "code", name="uq_ref_dept_codes_key"),
        CheckConstraint(_CK_KIND, name="ck_ref_dept_codes_kind"),
        CheckConstraint(_CK_SEQ, name="ck_ref_dept_codes_seq"),
        CheckConstraint("state IN ('사용', '폐기')", name="ck_ref_dept_codes_state"),
        {
            "comment": "기준정보 › 부서코드(Cost Center) — 버전별. 조직 개편이 지난 버전 검증 결과를 "
            "바꾸지 않도록 버전마다 따로 둔다. 새 버전은 직전 버전을 복사해 시작한다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    basis_year: Mapped[int] = mapped_column(SmallInteger, comment="버전의 대상 연도")
    version_kind: Mapped[str] = mapped_column(String(10), comment="forecast(속보) | actual(실적) | plan(경영계획)")
    version_seq: Mapped[int] = mapped_column(SmallInteger, comment="속보·실적이면 월(1~12), 경영계획이면 P 번호")
    code: Mapped[str] = mapped_column(String(20), comment='Cost Center("C10I3000"). 업로드 편성값의 귀속부서와 맞대는 키')
    job_category: Mapped[str] = mapped_column(String(20), default="", comment='Job Category("AAA")')
    name: Mapped[str] = mapped_column(String(60), default="", comment='Name = 부서명("SoC설계그룹")')
    budget_unit: Mapped[str] = mapped_column(String(20), default="", comment='Budget Unit("C100GEAA")')
    team_s: Mapped[str] = mapped_column(String(60), default="", comment='팀실 小("SOC개발실"). 신규 투자의 조직 이름과 맞대는 키')
    team_m: Mapped[str] = mapped_column(String(60), default="", comment="팀실 中")
    team_l: Mapped[str] = mapped_column(String(60), default="", comment='팀실 大("System LSI사업부")')
    functional_area: Mapped[str] = mapped_column(String(10), default="", comment='Functional Area("6000"). 집계·분석의 FA 묶음 키')
    fam3: Mapped[str] = mapped_column(String(30), default="", comment="제품군 FAM3 코드(화면 표에는 없지만 생소비·정률기술료가 쓴다)")
    is_rep: Mapped[bool] = mapped_column(
        default=False, comment="대표코드 여부. 팀실 小 하나당 하나여야 한다(둘 이상이면 화면이 경고 — DB는 막지 않는다)"
    )
    state: Mapped[str] = mapped_column(String(4), default="사용", comment="사용 | 폐기. 폐기 코드로 올라온 편성값은 [검증]에서 대체 부서를 지정한다")
    updated_on: Mapped[str] = mapped_column(String(10), default="", comment='업데이트 일자("2026.01.26") — 인사·조직에서 받은 표의 기준일 그대로')
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막으로 고친 시각"
    )


class RefPlmTask(Base):
    """PLM 과제 — 버전별. 과제 코드 · 제품 · 상태(Drop/Hold/정상·지연) · 일정 · 담당.

    버전별인 이유: 과제 Drop은 달마다 생기고, 그게 그 달 위탁용역비 [검증]의 "Drop 과제에 걸린
    계약" 목록을 바꾼다. 지난 버전을 지금 Drop 목록으로 다시 검증하면 이미 확정한 편성안이 오류로
    바뀐다.

    예전 화면은 이 표를 'Drop 과제 목록'으로만 들고 있었다. 2026-11 개편으로 PLM 과제 전체를
    받아 status로 Drop을 가려내는 방식이 됐다 — Drop만 받으면 "Drop이 아닌데 왜 안 보이냐"를
    담당자가 확인할 방법이 없다(목록에 없는 과제와 정상 과제가 구분되지 않는다).
    """

    __tablename__ = "ref_plm_tasks"
    __table_args__ = (
        UniqueConstraint("basis_year", "version_kind", "version_seq", "task_code", name="uq_ref_plm_tasks_key"),
        CheckConstraint(_CK_KIND, name="ck_ref_plm_tasks_kind"),
        CheckConstraint(_CK_SEQ, name="ck_ref_plm_tasks_seq"),
        CheckConstraint("task_code <> ''", name="ck_ref_plm_tasks_code_not_empty"),
        Index("ix_ref_plm_tasks_status", "basis_year", "version_kind", "version_seq", "status"),
        {
            "comment": "기준정보 › PLM 과제 — 버전별. status='Drop'인 과제가 위탁용역비 [검증]의 "
            "Drop 과제 판정에 쓰인다(과제 코드 기준)."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    basis_year: Mapped[int] = mapped_column(SmallInteger, comment="버전의 대상 연도")
    version_kind: Mapped[str] = mapped_column(String(10), comment="forecast(속보) | actual(실적) | plan(경영계획)")
    version_seq: Mapped[int] = mapped_column(SmallInteger, comment="속보·실적이면 월(1~12), 경영계획이면 P 번호")
    task_code: Mapped[str] = mapped_column(String(40), comment='과제 코드("S5E9965-A01"). 계약의 PLM Code와 맞대는 키')
    product: Mapped[str] = mapped_column(String(60), default="", comment='제품("APPLICATION PROCESSOR")')
    name: Mapped[str] = mapped_column(String(80), default="", comment="과제명")
    nickname: Mapped[str] = mapped_column(String(40), default="", comment='Nickname("Vanguard")')
    status: Mapped[str] = mapped_column(
        String(10), default="", comment="상태(Drop | Hold | 정상완료 | 정상진행 | 지연완료 | 지연진행). 'Drop'만 검증에 쓰인다"
    )
    plan_start: Mapped[str] = mapped_column(String(10), default="", comment="과제 시작 계획일")
    plan_end: Mapped[str] = mapped_column(String(10), default="", comment="과제 완료 계획일")
    stage: Mapped[str] = mapped_column(String(10), default="", comment='현재 Stage("PI" · "ES" · "CS")')
    target_type: Mapped[str] = mapped_column(String(20), default="", comment='대상 유형("SOC" · "SENSOR" · "LSI")')
    target_subtype: Mapped[str] = mapped_column(String(30), default="", comment='대상 상세 유형("Mobile AP")')
    stage_start: Mapped[str] = mapped_column(String(10), default="", comment="현재 Stage 시작 계획일")
    stage_end: Mapped[str] = mapped_column(String(10), default="", comment="현재 Stage 완료 계획일")
    project_manager: Mapped[str] = mapped_column(String(60), default="", comment="Project Manager")
    project_leader: Mapped[str] = mapped_column(String(60), default="", comment="Project Leader")
    owner: Mapped[str] = mapped_column(String(60), default="", comment="소유자")
    created_on: Mapped[str] = mapped_column(String(10), default="", comment="PLM에서의 과제 생성일")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막으로 고친 시각"
    )


class RefProduct(Base):
    """제품정보(SC_FMMS) — 제품(device) → 제품군(fam1~fam4) 매핑. 버전별.

    여러 계정이 같이 쓴다(정률기술료가 FAM3로 NRE를 가리고 DIST 배부를 검증하고, 생소비가 제품군
    묶음을 쓴다). 그래서 브라우저에 두면 계정마다 다른 제품군 표를 보게 된다.

    버전별인 이유는 부서코드와 같다 — 신제품이 매달 들어오고 제품군 코드도 바뀐다. 9월 속보를
    11월 제품표로 다시 계산하면 이미 보고한 배부가 바뀐다. 새 버전은 직전 버전을 복사해 시작한다.

    키가 (버전, device)인 이유: SC_FMMS 양식의 한 줄이 device 하나다. sale_device·gr_device는
    여러 device가 같은 값을 가질 수 있어 키가 될 수 없다.
    """

    __tablename__ = "ref_products"
    __table_args__ = (
        UniqueConstraint("basis_year", "version_kind", "version_seq", "device", name="uq_ref_products_key"),
        CheckConstraint(_CK_KIND, name="ck_ref_products_kind"),
        CheckConstraint(_CK_SEQ, name="ck_ref_products_seq"),
        CheckConstraint("device <> ''", name="ck_ref_products_device_not_empty"),
        {
            "comment": "기준정보 › 제품정보(SC_FMMS). 제품 → 제품군(fam1~fam4) 매핑을 여러 계정이 "
            "같이 쓴다. 버전별이고 새 버전은 직전 버전을 복사해 시작한다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    basis_year: Mapped[int] = mapped_column(SmallInteger, comment="버전의 대상 연도")
    version_kind: Mapped[str] = mapped_column(String(10), comment="forecast(속보) | actual(실적) | plan(경영계획)")
    version_seq: Mapped[int] = mapped_column(SmallInteger, comment="속보·실적이면 월(1~12), 경영계획이면 P 번호")
    device: Mapped[str] = mapped_column(String(60), comment='제품 코드(SC_FMMS의 device, "SA59955"). 이 표의 키')
    yyyy: Mapped[str] = mapped_column(String(4), default="", comment="SC_FMMS 파일의 연도 칼럼")
    sel_code: Mapped[str] = mapped_column(String(20), default="", comment='SC_FMMS 추출 코드("M2609A")')
    section: Mapped[str] = mapped_column(String(20), default="", comment='구분("FABLSS")')
    sale_device: Mapped[str] = mapped_column(String(60), default="", comment="판매 제품 코드")
    gr_device: Mapped[str] = mapped_column(String(60), default="", comment="그룹 제품 코드")
    fam1_code: Mapped[str] = mapped_column(String(30), default="", comment="제품군 FAM1")
    fam2_code: Mapped[str] = mapped_column(String(30), default="", comment="제품군 FAM2")
    fam3_code: Mapped[str] = mapped_column(String(30), default="", comment="제품군 FAM3 — 정률기술료의 NRE 판정·배부 검증이 쓰는 값")
    fam4_code: Mapped[str] = mapped_column(String(30), default="", comment="제품군 FAM4")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막으로 고친 시각"
    )


# 이력을 남기는 표. 화면 "기준정보"로 불리는 것 전부 + 전표 분류 기준(close_dept_rules) —
# 그 표가 바뀌면 "왜 이 계정으로 갔나"라는 같은 질문을 받는다.
CHANGE_TABLES = ("cost_element", "asset_class", "dept", "plm", "product", "close_dept_rule")
# 행 단위 before/after를 남기는 상한. 넘으면 요약만 남기고 "상세 생략"을 로그에 적는다 —
# 엑셀 한 번에 수천 행이 갈리면 상세가 로그를 다 먹는다. 조용히 자르지는 않는다(생략 사실을 남긴다).
CHANGE_DETAIL_LIMIT = 200


class RefChangeLog(Base):
    """기준정보 변경 한 건(= 저장 한 번). 무엇을 · 어느 범위를 · 어떻게 · 누가 · 언제.

    왜 필요한가: 기준정보 다섯 표는 모두 **표 전체 교체**로 저장된다. 그래서 지금은 "한 줄만
    고쳤는지, 엑셀을 잘못 올려 표가 통째로 바뀐 건지" 구분할 수 없다. 실제로 받는 질문이
    세 가지다 — 부서코드가 18개에서 3개로 왜 줄었나 · 이 계정명은 언제부터 이랬나 ·
    이 과제를 누가 Drop으로 바꿨나. 셋 다 "바뀐 행 + 누가·언제·어떻게"가 있으면 답한다.

    **변경이 없으면 행을 만들지 않는다.** 화면은 [저장]을 누를 때마다 PUT을 보내므로, 그걸 그대로
    쌓으면 로그가 "아무 일도 없었음"으로 가득 차서 진짜 변경을 못 찾는다.

    actor가 문자열인 이유: 이 플랫폼에 아직 로그인이 없다(users 테이블은 있지만 화면 인증이 없다).
    화면이 보내는 이름을 그대로 담고, 안 보내면 빈 문자열이다 — 가짜 이름을 서버가 박으면 감사
    기록에 거짓 주체가 들어간다(빈칸보다 나쁘다). 인증이 붙으면 users.id로 승격한다.
    """

    __tablename__ = "ref_change_logs"
    __table_args__ = (
        CheckConstraint(
            "table_key IN ('cost_element', 'asset_class', 'dept', 'plm', 'product', 'close_dept_rule')",
            name="ck_ref_change_logs_table_key",
        ),
        CheckConstraint("how IN ('screen', 'upload', 'copy', 'seed', 'chat')", name="ck_ref_change_logs_how"),
        Index("ix_ref_change_logs_table_scope", "table_key", "scope", "changed_at"),
        {
            "comment": "기준정보 변경 이력 헤더(저장 한 번 = 한 행). 바뀐 행 상세는 ref_change_rows. "
            "변경이 없는 저장은 기록하지 않는다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    table_key: Mapped[str] = mapped_column(
        String(20), comment="바뀐 표: cost_element | asset_class | dept | plm | product | close_dept_rule"
    )
    scope: Mapped[str] = mapped_column(
        String(24),
        default="",
        comment='적용 범위. 비용계정은 연도("2026"), 버전별 표는 버전 키("2026-09-forecast"), '
        "자산 Class·전표 분류 기준은 범위가 없어 빈 문자열",
    )
    how: Mapped[str] = mapped_column(
        String(8),
        default="screen",
        comment="screen(화면에서 표 저장) | upload(엑셀·텍스트 업로드) | copy(전월·전년 복사) | seed | chat",
    )
    actor: Mapped[str] = mapped_column(
        String(60), default="", comment="고친 사람(화면이 보낸 이름). 로그인이 없어 빈 문자열일 수 있다"
    )
    note: Mapped[str] = mapped_column(
        String(200), default="", comment="업로드면 파일명, 복사면 복사 출처 버전·연도 등 그 저장의 단서"
    )
    rows_before: Mapped[int] = mapped_column(Integer, default=0, comment="저장 전 표의 행 수")
    rows_after: Mapped[int] = mapped_column(Integer, default=0, comment="저장 후 표의 행 수")
    added: Mapped[int] = mapped_column(Integer, default=0, comment="추가된 행 수")
    removed: Mapped[int] = mapped_column(Integer, default=0, comment="없어진 행 수")
    changed: Mapped[int] = mapped_column(Integer, default=0, comment="값이 바뀐 행 수")
    detail_omitted: Mapped[bool] = mapped_column(
        default=False,
        comment=f"True면 바뀐 행이 상한({CHANGE_DETAIL_LIMIT}행)을 넘어 상세를 남기지 않았다는 뜻. "
        "상세가 없는 것과 안 바뀐 것을 화면이 구분해야 해서 플래그로 남긴다",
    )
    summary: Mapped[str] = mapped_column(
        Text, default="", comment='한 줄 요약("부서코드 18행 → 3행 · 삭제 15"). 목록 화면이 그대로 쓴다'
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="저장한 시각"
    )


class RefChangeRow(Base):
    """그 변경에서 **실제로 바뀐 행** 하나. before/after는 바뀐 칸만 담는다.

    전체 스냅샷을 매번 저장하지 않는 이유는 용량이다(제품정보 수천 행 × 저장마다). 바뀐 행만
    남겨도 "무엇이 어떻게 바뀌었나"에는 답할 수 있다. 추가된 행은 before가 비어 있고, 없어진 행은
    after가 비어 있다.
    """

    __tablename__ = "ref_change_rows"
    __table_args__ = (
        CheckConstraint("change IN ('add', 'del', 'chg')", name="ck_ref_change_rows_change"),
        Index("ix_ref_change_rows_log", "log_id"),
        {"comment": "기준정보 변경에서 바뀐 행의 before/after(바뀐 칸만). 상한을 넘으면 행이 없다."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    log_id: Mapped[int] = mapped_column(ForeignKey("ref_change_logs.id", ondelete="CASCADE"), comment="ref_change_logs.id")
    row_key: Mapped[str] = mapped_column(
        String(80), comment='그 표에서 행을 가리키는 키(부서코드 · 과제코드 · device · "Cost Elem.|구분#n")'
    )
    change: Mapped[str] = mapped_column(String(3), comment="add(추가) | del(삭제) | chg(값 변경)")
    before: Mapped[dict] = mapped_column(JSONB, default=dict, comment="바뀐 칸의 이전 값(추가된 행은 빈 객체)")
    after: Mapped[dict] = mapped_column(JSONB, default=dict, comment="바뀐 칸의 새 값(없어진 행은 빈 객체)")
