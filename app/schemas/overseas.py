from datetime import datetime

from pydantic import BaseModel


class FileInfo(BaseModel):
    file: str
    at: str


class VersionData(BaseModel):
    d: dict[str, dict[str, list[float]]]  # pk -> category_key -> [12 months]
    etc: list[float]  # 원화 기타, 12개월


class ActualSnapshot(BaseModel):
    """'N월실적' 헤더로 업로드된, 확정된 실적 스냅샷. version='cur'에서만 쓰인다
    (예: 지금 기준월이 10월이고 9월 실적이 확정됐으면 thru_month=9)."""

    thru_month: int
    d: dict[str, dict[str, list[float]]]
    etc: list[float]


class ReasonRow(BaseModel):
    """증감사유 업로드 1행(연구소×버전×비용항목×구간). overseas_reason_rows와 1:1.
    lab_code는 파트 없는 연구소 코드(SARC는 CPU+GPU 합계 기준)."""

    lab_code: str
    version: str
    category_key: str
    block: str  # "y"(연간) | "m"(월별)
    mon_label: str = ""
    plan_value: float = 0
    prev_value: float = 0
    cur_value: float = 0
    diff_plan: float = 0
    diff_prev: float = 0
    remark_plan: str = ""
    remark_prev: str = ""
    prev_label: str = ""
    cur_label: str = ""
    source_file: str = ""


class VendorRow(BaseModel):
    """외주용역 상세 1행(연구소×버전×행). DB는 마스터(overseas_vendor_rows) + 월별 디테일
    (overseas_vendor_row_details)로 나눠 저장하지만, 화면은 한 행 = 12개월 배열로 다루므로
    API에서는 합쳐서 주고받는다. lab_code는 증감사유와 같이 파트 없는 연구소 코드다 —
    외주용역 시트도 SARC는 CPU+GPU를 합쳐 연구소 단위로만 올라온다."""

    lab_code: str
    version: str
    row_no: int  # 업로드한 시트 안에서의 행 순서(1부터)
    category: str = ""  # Category
    project: str = ""  # Project name
    company: str = ""  # Company / Institution
    description: str = ""  # Description / Purpose
    m: list[float] = []  # 1~12월 금액
    source_file: str = ""


class CpRow(BaseModel):
    """CP 계약 업로드 1행(POC×직급). overseas_cp_rows와 1:1 — POC 헤더값은 각 행에 반복된다."""

    year: str
    poc_type: str = ""
    poc_proj: str = ""
    poc_desc: str = ""
    title: str
    dedicated: float = 0
    common: float = 0
    man_month: float = 0
    cost_year: float = 0
    poc_cost_sum: float = 0
    poc_mm_sum: float = 0
    poc_direct: float = 0
    poc_ovh: float = 0
    poc_total: float = 0
    source_file: str = ""


class OverseasState(BaseModel):
    step: int
    basis_year: int  # 편성 기준연도(프론트 S.basis 연도). cur/prev/plan은 이 연도, prior는 이 연도-1
    basis_month: int = 0  # 편성 기준월(1~12, 프론트 S.basis). cur의 "속보" 스냅샷을 이 달 기준으로 저장한다
    V: dict[str, VersionData]  # version -> VersionData (cur는 "속보" = 기준월까지 반영)
    actualV: dict[str, ActualSnapshot] = {}  # version -> 실적 스냅샷. 지금은 cur만 쓰임
    files: dict[str, dict[str, FileInfo]]  # version -> pk -> FileInfo
    note: dict[str, dict[str, str]]  # "base|per|alab" -> category_key -> text
    reason: list[ReasonRow] = []  # 증감사유 업로드 (연구소×버전×항목×구간 키로 upsert, cost처럼 누적)
    vendor: list[VendorRow] = []  # 외주용역 상세 (연구소×버전 단위로 전체 교체 — 업로드마다 행 수가 달라짐)
    cp: list[CpRow] = []  # CP 계약 업로드 (연도 단위로 전체 교체 — 연 1회 올리는 파일)


class StepUpdate(BaseModel):
    step: int


class LabOut(BaseModel):
    pk: str
    lab_code: str
    part_code: str | None
    name: str
    country: str
    sort_order: int
    currency: str
    is_active: bool


class LabIn(BaseModel):
    """연구소 기준정보 편집 화면(ovLabPage) 저장 payload. lab_code 단위 1행 = 화면의 1행이고,
    SARC처럼 lab_code 하나에 파트(pk)가 여러 개면 currency·is_active·sort_order는 그 파트들에
    동일하게 적용된다 (화면에 파트별로 나눠서 고치는 입력란이 없음)."""

    lab_code: str
    name: str
    currency: str
    is_active: bool = True


class LabSheetOut(BaseModel):
    lab_code: str
    sheet_key: str
    name: str
    sheet_order: int


class LabSheetIn(BaseModel):
    """연구소(lab_code)에 시트(sheet_key)를 배정한다. sheet_key는 미리 common_codes
    (group_code='OVERSEAS_SHEET_TYPE')에 등록돼 있어야 한다."""

    lab_code: str
    sheet_key: str
    sheet_order: int = 0


class SheetRowIn(BaseModel):
    lab_pk: str
    version: str
    sheet_key: str
    row_key: str
    sort_order: int = 0
    data: dict


class SheetRowOut(SheetRowIn):
    pass


class CostEntryIn(BaseModel):
    lab_pk: str
    version: str  # forecast(속보) | actual(실적)
    category_key: str
    yyyymm: str  # 편성 기준 연월 "YYYYMM" (cost_entries.yyyymm)
    month: int  # 이 금액이 속한 달. 기본 1~12는 yyyymm 연도 그대로, 13~24는 그다음 연도 1~12월(기간을
    # 12개월 너머로 늘려 받을 때 씀), 25~36은 그다음다음 연도… 식으로 12개씩 다음 해로 넘어간다.
    amount: float


class CostEntryOut(BaseModel):
    lab_pk: str
    version: str
    category_key: str
    yyyymm: str
    year: int
    month: int
    amount: float


class UploadIn(BaseModel):
    lab_pk: str
    version: str
    filename: str


class UploadOut(UploadIn):
    uploaded_at: datetime
    s3_key: str | None = None


class NoteIn(BaseModel):
    base_version: str
    period: str
    lab_scope: str
    category_key: str
    note_text: str


class NoteOut(NoteIn):
    pass


class MemoColIn(BaseModel):
    lab_pk: str
    version: str
    col_key: str
    label: str


class MemoColOut(MemoColIn):
    pass


class MemoIn(BaseModel):
    lab_pk: str
    version: str
    row_key: str
    col_key: str
    text: str


class MemoOut(MemoIn):
    pass


class ExtraRowIn(BaseModel):
    lab_pk: str
    version: str  # forecast(속보) | actual(실적)
    row_key: str
    label: str  # 구분(한글)
    label_en: str = ""  # Cost Element(영문)
    yyyymm: str  # v가 속한 편성 기준 연월 "YYYYMM" (cost_entries.yyyymm)
    anchor_key: str | None = None  # 표에서 이 계정(core.js OV_EL의 k) 바로 뒤에 끼워 넣는다. None이면 맨 끝
    # 원시 행(is_derived=False)의 월별 값. 12개월(기본) 또는 12의 배수(24·36…, 기간을 12개월 너머로
    # 늘려 받을 때). 파생 행은 값을 저장하지 않고 formula로 매번 계산하므로 비워 보내면 된다.
    v: list[float] = []
    # True면 실제로 수집한 비용이 아니라 다른 계정들을 조합(합산·비율 등)해 계산한 파생 행이라는 뜻.
    # 취합에서 추가한 원시 행과 집계에서 추가한 파생 행을 저장 후에도 구분하기 위해 DB에 남긴다 —
    # 삭제 권한(원시=취합에서만, 파생=집계에서만)과 표시 규칙(원시=양쪽, 파생=집계만), 그리고
    # '개발비(A)' 합계에서 파생값을 빼는 판단이 모두 이 플래그에서 나온다.
    is_derived: bool = False
    # 파생 행의 계산식. 계정을 세로로 묶는다 — {"op":"div_elementwise","operands":["labor","hc"]}는
    # 인건비 ÷ 인력(= 인당인건비). op: sum_elementwise | div_elementwise. 원시 행은 None.
    formula: dict | None = None


class ExtraRowOut(ExtraRowIn):
    # 파생 행일 때만 채워지는 계산 근거 — "인건비 1,000 ÷ 인력 10 = 100"을 화면·챗봇이 그대로
    # 보여줄 수 있도록 재료별 월 값과 월별/연간 계산 결과를 서버가 풀어서 내려준다.
    basis: dict | None = None


class ExtraColIn(BaseModel):
    """편성 그리드에 추가한 열. label 자체가 열 식별자라 이름 변경은 지원하지 않는다
    (바꾸려면 옛 이름을 지우고 새 이름으로 다시 만들면 된다)."""

    lab_pk: str
    version: str  # forecast(속보) | actual(실적)
    yyyymm: str
    label: str
    # True면 월을 가로로 묶어 계산한 파생 열(집계 전용). 칸 값을 저장하지 않고 formula로 매번 계산한다.
    is_derived: bool = False
    # 파생 열의 계산식. 월을 가로로 묶는다 — {"op":"sum_months","months":[7,8,9]}는 3분기 합계.
    # op: sum_months | avg_months. 원시 열은 None.
    formula: dict | None = None


class ExtraColOut(ExtraColIn):
    pass


class ExtraValueIn(BaseModel):
    """추가 열의 칸 하나(행×열) 값. row_key는 cost_entries.category_code(고정 계정 또는 사용자 추가 행의 키),
    yyyymm은 그 행이 속한 편성 기준 연월(다른 모든 행과 마찬가지로 항상 있어야 함). label 자체가 열
    식별자라 열 이름을 바꾸는 건 지원하지 않는다 — 바꾸려면 옛 이름을 지우고 새 이름으로 다시 넣으면 된다."""

    lab_pk: str
    version: str
    row_key: str
    yyyymm: str
    label: str
    value: float = 0


class ExtraValueOut(ExtraValueIn):
    # 파생 열의 칸일 때만 채워지는 계산 근거 — "7월 100 + 8월 110 + 9월 120 = 330".
    basis: dict | None = None


class LaborSplitLab(BaseModel):
    """인건비 분해의 연구소 한 줄. amount 단위는 전부 원이고 인원만 명이다."""

    lab_pk: str
    lab_name: str
    currency: str
    hc_base: float
    hc_cur: float
    per_head_base: float  # 원/명 — 비교 버전 환율로 환산한 인건비 ÷ 평균 인원
    per_head_cur: float
    hc_effect: float
    rate_effect: float
    # 인력(hc)이 없어서 인원·단가로 나눌 수 없는 연구소의 환차外 몫. 나눌 수 있으면 0이다.
    unsplit: float
    fx_outside: float  # = hc_effect + rate_effect + unsplit


class LaborSplitResult(BaseModel):
    """'인건비가 왜 늘었나'를 인원 효과와 단가 효과로 나눈 결과.

    화면(분석 › 증감분석 › 계정별 증감분석 › 계정별 상세)의 인건비 행 아래 줄들과 같은 계산이다.
    환율이 움직여서 변한 몫(환차)은 빼고, 환차外만 나눈다:
      환차外 = (당월인원 − 비교인원) × 비교단가 + 당월인원 × (당월단가 − 비교단가)
    두 효과의 합(+ 인력 없는 연구소 몫)은 fx_outside와 정확히 일치한다."""

    basis_year: int
    period: str  # y(연간) | q1~q4 | m1~m12
    base_version: str  # prev(전실행) | prior(전년 실적) | plan(경영계획)
    lab_scope: str  # "ALL" 또는 연구소 코드/pk
    hc_base: float
    hc_cur: float
    per_head_base: float
    per_head_cur: float
    hc_effect: float
    rate_effect: float
    unsplit: float
    fx_outside: float
    # 화면의 "환차外" 칸과 맞춰볼 수 있게 같이 돌려준다 — 이 값이 화면과 다르면 분해값도 다르다.
    labor_cur: float  # 당월 인건비 (원, 비교 버전 환율로 환산)
    labor_base: float  # 비교 버전 인건비 (원)
    labs: list[LaborSplitLab]
    note: str = ""


class VendorProjectRow(BaseModel):
    """외주용역비를 과제(project) 단위로 묶은 한 줄. 금액 단위는 백만원(화면 외주용역 표와 같다)."""

    project: str
    amount: float
    item_count: int  # 그 과제에 달린 외주용역 건수
    labs: list[str]
    company_count: int
    # 아래 셋은 비교 버전을 지정했고 그 데이터가 실제로 있을 때만 값이 들어온다. 없으면 0이 아니라
    # null이다 — 0으로 내려보내면 "증감이 없다"로 읽혀서, 못 구한 걸 구한 것처럼 답하게 된다
    # (실제로 비교 데이터가 없는데 모든 과제 diff가 0.0으로 나가고 있었다).
    base_amount: float | None = None
    diff: float | None = None
    status: str | None = None  # 신규 | 종료 | 변동 | 동일


class VendorByProjectResult(BaseModel):
    """외주용역비 과제별 집계. 비용 본체(cost_entries)에는 과제 칸이 없어서 과제 기준 분석은
    외주용역 시트에서만 된다 — 그래서 이 결과는 개발비 전체가 아니라 외주용역비만 다룬다는 걸
    scope_note로 분명히 밝힌다(챗봇이 '개발비를 과제별로 쪼갰다'고 오해해서 답하면 안 된다)."""

    version: str
    base_version: str | None = None
    period: str
    lab_code: str | None = None
    unit: str = "백만원"
    total: float
    # 비교하지 못했으면 null (위 base_amount 주석과 같은 이유)
    base_total: float | None = None
    diff: float | None = None
    # 비교 버전을 요청했고 그 데이터가 실제로 있어서 증감을 낸 경우에만 True.
    compared: bool = False
    projects: list[VendorProjectRow]
    scope_note: str = "외주용역비만. 개발비 전체가 아니다(비용 데이터에는 과제 칸이 없다)."
    note: str = ""
