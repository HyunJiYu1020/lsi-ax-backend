from datetime import datetime

from pydantic import BaseModel


class FileInfo(BaseModel):
    file: str
    at: str


class VersionData(BaseModel):
    """월 값은 float | None이다 — None은 "미등록/미업로드"(저장된 칸이 아예 없음)이고 0.0은
    "0으로 올라왔다"는 뜻이라 서로 다르다. 화면은 None을 빈칸으로 그리고, 합계는 있는 값만 더한다
    (공통 RULE: 빈칸 처리). PUT으로 None을 보내면 그 달은 저장하지 않고 미등록으로 둔다."""

    d: dict[str, dict[str, list[float | None]]]  # pk -> category_key -> [12 months]
    etc: list[float | None]  # 원화 기타, 12개월


class ActualSnapshot(BaseModel):
    """'N월실적' 헤더로 업로드된, 확정된 실적 스냅샷. version='cur'에서만 쓰인다
    (예: 지금 기준월이 10월이고 9월 실적이 확정됐으면 thru_month=9)."""

    thru_month: int
    d: dict[str, dict[str, list[float | None]]]  # None = 미등록 (VersionData 주석 참고)
    etc: list[float | None]


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


class VendorRowsIn(BaseModel):
    """외주용역 전용 저장 요청(PUT /overseas/vendor-rows). PLAN.md Phase 5.

    rows에 들어온 (연구소, 버전) 조합만 통째로 바뀐다 — 보내지 않은 조합은 건드리지 않는다.
    빈 rows로 한 조합을 비울 수는 없다(무엇을 비울지 알 수 없으므로). 그건 행 삭제 API의 일이다.
    """

    basis_year: int  # 월 금액을 어느 해 디테일로 저장할지
    rows: list[VendorRow]
    # 동시 편집 보호 — /overseas/state와 같은 지문을 쓴다(한 화면의 자료라 충돌 판정도 같아야 한다).
    # 빈 문자열이면 검사하지 않는다(토큰을 모르는 클라이언트 호환).
    state_token: str = ""


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


class ReasonRowsIn(BaseModel):
    """증감사유 전용 저장 요청(PUT /overseas/reason-rows). PLAN.md Phase 5.

    외주용역·CP와 달리 **전체 교체가 아니라 보낸 것만 누적 반영**이다 — 연구소 회신을 항목별로
    쌓아 가는 자료라, 한 시트를 올렸을 때 다른 항목의 사유가 사라지면 안 된다.
    """

    rows: list[ReasonRow]
    state_token: str = ""  # /overseas/state와 같은 지문. 빈 문자열이면 검사하지 않는다


class CpRowsIn(BaseModel):
    """CP 계약 전용 저장 요청(PUT /overseas/cp-rows). PLAN.md Phase 5.

    rows에 들어온 연도만 통째로 바뀐다 — 연 1회 올리는 파일이라 사라진 POC·직급은 지워야 한다.
    보내지 않은 연도는 건드리지 않는다.
    """

    rows: list[CpRow]
    state_token: str = ""  # /overseas/state와 같은 지문. 빈 문자열이면 검사하지 않는다


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
    # 동시 편집 보호(PLAN.md P1-2). GET이 "지금 상태의 지문"을 담아 내려주고, PUT은 그걸 그대로
    # 돌려보낸다. 그 사이 다른 세션이 저장했으면 지문이 달라져 있어 409로 막는다.
    # 빈 문자열이면 검사하지 않는다 — 토큰을 모르는 기존 클라이언트를 깨지 않기 위함이고,
    # 서버 쪽에서 검증만 끄면 롤백도 된다(PLAN Phase 2의 롤백 조건).
    state_token: str = ""


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
    # 이 변경이 적용되기 시작하는 편성 기준연월 "YYYY-MM". 공통 RULE("기준정보 변경은 변경한
    # 버전부터 적용, 지난 버전 영향 없음")대로, 이 달보다 이전 버전은 바뀌기 전 값으로 계속 계산된다.
    # 안 주면 지금 편성 중인 기준월(= "이 버전부터")로 본다. 과거를 고쳐야 하는 경우에만 직접 준다.
    effective_from: str | None = None


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
    # None = 그 달 칸이 아예 등록되지 않았다는 뜻(미업로드). 0.0("0으로 올라옴")과 구분해야 하므로
    # 0으로 치환하지 않는다 — 챗봇도 이 값을 "없음"으로 읽고 "0원"이라고 답하면 안 된다.
    amount: float | None


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
    # None인 달은 미등록(빈칸)이라 저장하지 않는다 — 0으로 올라온 달과 구분된다.
    v: list[float | None] = []
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


class OverseasSummaryLab(BaseModel):
    """히어로 카드의 "업로드 n/12" 진척을 연구소 단위로 푼 것. 파트가 여러 개인 연구소(SARC)는
    파트가 모두 올라와야 uploaded=True다 — 화면 ovHas 판정과 같은 규칙."""

    lab_code: str
    name: str
    # 그 연구소(파트 전부)에 등록된 칸이 하나라도 있으면 True. "값이 0이 아닌 칸"이 아니라
    # "칸이 존재하는가"로 본다 — 전부 0으로 올라온 연구소도 업로드는 된 것이다.
    uploaded: bool


class OverseasSummary(BaseModel):
    """해외연구소 화면 맨 위 히어로 카드 한 장에 들어가는 값 전부.

    화면(frontend/src/components/OverseasApp.jsx)이 ovAgg/ov17로 브라우저에서 계산하던 숫자를
    서버에서 같은 식으로 낸 것이다 — 챗봇·MCP가 말하는 총액과 사용자가 보는 카드가 어긋나면 안 되므로
    식을 바꿀 때는 화면 쪽(OverseasApp.jsx, logic/calc.js)도 같이 고칠 것."""

    basis_year: int
    basis_month: int
    basis_label: str  # "26.09" — 카드 제목의 "(26.09 속보 기준)"
    period: str  # y(연간) | q1~q4 | m1~m12
    version: str  # cur | prev | prior | plan
    step: int  # 0-based 화면 단계 인덱스 (0 취합 · 1 집계 · 2 증감분석)
    step_count: int
    total: float  # 개발비(매출) = 연구소 합계 + 원화 기타 (원)
    etc_total: float  # 그중 원화 기타 (원)
    labor: float  # 인건비 (원, CP 분기 정산 반영)
    non_labor: float  # 인건비외 (원, CP 분기 정산 반영)
    labor_pct: float
    non_labor_pct: float
    uploaded: int  # 데이터가 올라온 연구소 수
    lab_count: int  # 전체 연구소 수 (원화 기타 제외)
    labs: list[OverseasSummaryLab]
    # 위 금액이 "연구소 전체 합계"가 아니라 "올라온 것만 더한 합계"라는 걸 말로 적은 것. 화면
    # 히어로 카드의 "12개 중 n개 기준" 문구와 같은 내용이다 — 챗봇이 이 총액을 전체 합계라고
    # 단정해서 답하면 안 되므로, 세는 쪽이 아니라 답하는 쪽이 읽을 문장으로 같이 내려준다.
    coverage_note: str = ""
    note: str = ""


class ReasonTranslateIn(BaseModel):
    """증감사유 번역 요청. texts는 연구소 회신 원문(영문)이고, 같은 문장이 여러 번 들어와도 된다 —
    서버가 정규화·중복 제거해서 LLM에는 한 번만 묻는다."""

    texts: list[str]
    provider: str | None = None  # "anthropic" | "ollama" | "gemini" — 생략 시 settings.chat_provider
    # True면 캐시(overseas_reason_translations)를 무시하고 다시 번역해서 덮어쓴다 — 모델을 바꿨거나
    # 번역이 이상해서 다시 돌릴 때만 쓴다(기본은 캐시 우선: 같은 원문은 항상 같은 번역이어야 한다).
    refresh: bool = False


class ReasonTranslateItem(BaseModel):
    """원문 1건의 번역 결과. ko가 null이면 번역하지 못한 것이고, 그때 화면은 원문을 그대로 보여준다
    (번역 실패를 빈칸으로 두면 연구소가 사유를 안 쓴 것처럼 보인다)."""

    source: str  # 정규화한 원문 (요청에 넣은 문장과 공백만 다를 수 있다)
    ko: str | None = None
    engine: str = ""
    cached: bool = False  # True면 이번에 LLM을 부르지 않고 저장된 번역을 그대로 돌려준 것


class ReasonTranslateResult(BaseModel):
    """번역 결과 묶음. LLM 호출이 실패해도 200으로 돌려주고 error에 이유를 담는다 — 증감분석 화면은
    번역 없이도 원문으로 그려져야 하는데, 여기서 4xx/5xx를 던지면 화면 쪽에서 구분하기 어렵다."""

    items: list[ReasonTranslateItem]
    provider: str
    translated: int = 0  # 이번에 LLM을 불러 새로 번역한 건수
    cached: int = 0  # 저장된 번역을 그대로 쓴 건수
    failed: int = 0  # 번역하지 못한 건수(ko=null)
    error: str = ""


class RowMatchSuggestion(BaseModel):
    """"이 행과 같은 행인가요?"로 보여줄 후보 하나. score는 후보를 고르는 기준이 아니라 정렬용이다 —
    합칠지 말지는 항상 사람이 답한다(자동 병합은 Travel Exp / Test Exp처럼 다른 행을 합칠 수 있다)."""

    kind: str  # "category"(고정 계정) | "extra_row"(이미 있는 추가 행)
    key: str  # category면 비용항목 키(travel), extra_row면 정식 이름의 정규화값
    label: str  # 화면에 보여줄 정식 이름(한글)
    label_en: str = ""
    matched_on: str = ""  # 후보의 어느 이름에 걸렸는지(한글명 / 영문명) — 사람이 판단할 근거
    score: float = 0
    # 그 추가 행을 이미 쓰고 있는 연구소(고정 계정이면 빈 목록) — "SARC·DSRA가 쓰는 행"이라는 걸
    # 알면 같은 행인지 판단하기 쉽다.
    labs: list[str] = []


class RowMatchItem(BaseModel):
    """업로드 파일의 행 이름 1건에 대한 판정 결과.

    resolved가 있으면 이미 사람이 답해 둔 이름이라 묻지 않고 그대로 반영한다(별칭 재사용).
    resolved가 없고 suggestions가 있으면 [반영] 전에 물어본다. 둘 다 없으면 그냥 새 행이다."""

    label: str
    resolved: RowMatchSuggestion | None = None
    # resolved.kind가 "distinct"면 "별개 행"이라고 이미 답한 이름이다 — 묻지 않고 새 행으로 둔다.
    suggestions: list[RowMatchSuggestion] = []


class RowMatchIn(BaseModel):
    """labels: 업로드 파일에서 계정 목록에 없던 행 이름들. version·yyyymm은 후보로 쓸 "이미 있는
    추가 행"을 어느 버전에서 찾을지 정한다(그 달에 실제로 쓰이는 행만 후보로 올린다)."""

    labels: list[str]
    yyyymm: str
    version: str | None = None


class RowMatchResult(BaseModel):
    items: list[RowMatchItem]


class RowAliasIn(BaseModel):
    """"같은 행" 또는 "별개 행"이라고 답한 결과를 저장한다. target_kind="distinct"면 key·label은 비운다."""

    alias_label: str
    target_kind: str  # "category" | "extra_row" | "distinct"
    target_key: str = ""
    target_label: str = ""


class RowAliasOut(BaseModel):
    alias_norm: str
    alias_label: str
    target_kind: str
    target_key: str = ""
    target_label: str = ""


class CarryDefIn(BaseModel):
    """집계 파생 행·열을 "다음 버전에도 유지"할지 정한다. carry=False면 저장해 둔 승인을 지운다."""

    kind: str  # "row"(파생 행) | "col"(파생 열)
    def_key: str  # 파생 행이면 row_key, 파생 열이면 label
    label: str = ""
    carry: bool = True


class CarryDefOut(BaseModel):
    kind: str
    def_key: str
    label: str = ""


class CarryForwardIn(BaseModel):
    """새 버전을 열 때, 유지 승인한 파생 행·열의 정의(계산식)만 그 버전으로 복제한다.
    값은 복제하지 않는다 — 재료가 새 버전 업로드 값으로 바뀌면 계산 결과도 그 버전 숫자로 나와야 한다."""

    from_yyyymm: str
    from_version: str
    to_yyyymm: str
    to_version: str


class CarryForwardResult(BaseModel):
    rows: int = 0  # 복제한 파생 행 수
    cols: int = 0  # 복제한 파생 열 수
    skipped: list[str] = []  # 이미 그 버전에 있어서 건너뛴 것(이름)


# ---------- 비교 기준 (전실행 · 전년 · 경영계획) ----------
class PlanRevisionOut(BaseModel):
    """경영계획 차수 하나. 증감분석 화면의 "경영계획 대비"가 어느 차수를 보는지 고르는 선택지다."""

    plan_no: str  # "P00" | "P01" ...
    label: str  # 화면에 보일 이름. 공식 비교 기준(is_final)이면 "P01 ★"
    is_final: bool  # 그 연도의 공식 비교 기준(★). 화면 기본 선택이 이 차수다
    is_confirmed: bool
    finalized_at: str = ""


class VarianceBasisOut(BaseModel):
    """비교 기준 한 칸이 "실제로 무엇과 비교하는지"를 풀어 놓은 것.

    화면 버튼에 "전실행"이라고만 쓰면 담당자는 그게 8월 실적인지 9월 속보인지 알 수 없다. 1월 속보의
    전실행은 전년 12월 실적이라 연도까지 넘어가므로 더더욱 그렇다. 그래서 라벨을 서버가 만들어 준다 —
    화면·챗봇·엑셀이 같은 문장을 쓰게 하려는 것이다."""

    key: str  # prev | prior | plan
    label: str  # "전실행" | "전년" | "경영계획"
    resolved_label: str  # 실제 비교 대상 "25.12 실적" | "P01 ★"
    year: int = 0
    month: int = 0
    kind: str = ""  # forecast | actual | plan
    plan_no: str = ""  # key=plan일 때의 차수
    has_data: bool = True  # 그 버전에 저장된 데이터가 있는지. False면 증감을 낼 수 없다
    # 원래 기준(전년 12월 실적 등)이 없어서 다른 버전으로 대체한 경우의 안내 문구.
    # 예: "전년 12월 실적이 없어 26.11 속보 기준으로 비교합니다." 화면은 이 문장을 뱃지로 띄운다.
    fallback_note: str = ""


class VarianceBasisResult(BaseModel):
    basis_year: int
    basis_month: int
    kind: str  # forecast | actual | plan
    basis_label: str  # "26.09 속보"
    bases: list[VarianceBasisOut]
    plan_revisions: list[PlanRevisionOut] = []
    default_plan_no: str = ""  # ★ 차수(없으면 가장 최근 차수)


# ---------- 증감분석 표 (근거 데이터) ----------
class VarianceRow(BaseModel):
    """계정 한 줄의 증감 분해. 금액은 모두 원(KRW)이고, 화면이 억원/백만원으로 줄여 보여준다
    (공통 RULE: 저장·전송은 원금액, 표시만 줄인다).

    환차를 두 칸으로 가르는 이유: USD가 아닌 통화를 쓰는 연구소는 "현지통화가 $ 대비 움직인 몫"과
    "$가 원 대비 움직인 몫"이 섞여 있어서, 합쳐 놓으면 어느 환율 때문에 변했는지 알 수 없다.
    USD 연구소는 현지→$ 단계가 없으니 fx_local이 항상 0이다."""

    category_key: str
    label: str
    label_en: str = ""
    level: int = 0  # 손익계산서형 들여쓰기 단계
    unit: str = "krw"  # krw(금액) | hc(명) | pct(%)
    base: float = 0  # 비교 기준 금액
    cur: float = 0  # 지금 보고 있는 버전 금액
    diff: float = 0  # cur - base
    diff_pct: float | None = None  # 증감률 %. 기준이 0이면 낼 수 없어 null
    fx: float = 0  # 환차 계 = fx_local + fx_usd
    fx_local: float = 0  # 환차 현지→$ (USD 연구소는 0)
    fx_usd: float = 0  # 환차 $→원
    fx_outside: float = 0  # 환차外 = 금액 자체의 증감


class VarianceResult(BaseModel):
    """증감분석 표 한 장 전체. 챗봇이 "SARC 인건비 왜 늘었어?"에 답할 때 근거로 쓰는 데이터이기도
    해서, 화면이 보여주는 것과 같은 값을 같은 식으로 낸다(식을 바꿀 때는 logic/calc.js도 같이 고칠 것)."""

    basis_year: int
    basis_month: int
    kind: str
    stage: str = ""  # 실적의 first(1차) | second(2차). 속보는 빈 문자열
    base_version: str
    base_label: str  # "25.12 실적" — 표 머리글에 그대로 쓴다
    period: str
    lab_scope: str
    plan_no: str = ""
    rows: list[VarianceRow]
    fallback_note: str = ""
    note: str = ""


# ---------- 증감사유 (회신 원문 → 번역 + AI 초안 → 수정 후 저장) ----------
class VarianceReasonKey(BaseModel):
    """사유 한 칸의 자리. 화면의 "비교 기준 | 기간 | 연구소" 선택 + 계정 한 줄에 대응한다."""

    basis_year: int
    basis_month: int
    kind: str = "forecast"  # forecast | actual
    stage: str = ""  # 실적일 때 first(1차) | second(2차). 속보는 빈 문자열
    base_version: str  # prev | prior | plan
    period: str  # y | q1~q4 | m1~m12
    lab_scope: str = "ALL"
    category_key: str


class VarianceReasonOut(VarianceReasonKey):
    """사유 한 칸의 상태 전부. 화면은 source_text(회신 원문)와 source_ko(번역)를 읽기 전용으로 보여주고,
    ai_draft를 [AI 초안 넣기]로 편집칸에 복사하고, 담당자가 고친 결과를 reason_text로 저장한다."""

    source_text: str = ""
    source_ko: str = ""
    ai_draft: str = ""
    reason_text: str = ""
    saved_diff: float | None = None
    needs_recheck: bool = False
    saved_at: str = ""
    # 지금 증감액(원). saved_diff와 다르면 재업로드로 금액이 바뀐 것이다 — 화면이 두 값을 같이 보여
    # 준다("저장 시점 ▲12억 → 지금 ▲19억").
    current_diff: float | None = None


class VarianceReasonIn(VarianceReasonKey):
    """[저장] 또는 초안 보관. reason_text를 보내면 그 문장으로 저장하고, 저장 시점 증감액(current_diff)을
    같이 받아 스냅샷으로 남긴다 — 그래야 다음 업로드에서 금액이 바뀐 걸 서버가 알아챈다.

    current_diff를 생략하면 서버가 직접 계산한다(화면이 안 보냈을 때의 안전장치)."""

    source_text: str | None = None
    source_ko: str | None = None
    ai_draft: str | None = None
    reason_text: str | None = None
    current_diff: float | None = None
    # True면 [저장]을 누른 것(saved_at·saved_diff를 갱신하고 재확인 표시를 지운다).
    # False면 초안만 보관한다(원문·번역·AI 초안만 채울 때).
    commit: bool = True


class VarianceReasonDraftIn(VarianceReasonKey):
    """회신 원문을 번역하고 보고용 사유 초안까지 만들어 달라는 요청.

    증감 금액을 같이 받는 이유: "채용 증가"라는 원문만으로는 보고용 문장이 안 된다 — "신규 채용 3명에
    따른 인건비 1.2억원 증가"처럼 숫자가 붙어야 보고서에 실린다. 그 숫자는 화면이 이미 계산해서
    보여주고 있으니 그대로 받아 쓴다(서버가 다시 계산하면 화면과 어긋날 수 있다)."""

    source_text: str
    diff: float | None = None  # 증감액(원)
    diff_pct: float | None = None  # 증감률 %
    provider: str | None = None
    save: bool = True  # True면 번역·초안을 그 칸에 보관한다(확정 사유는 건드리지 않는다)


class VarianceReasonDraftResult(BaseModel):
    source_text: str
    source_ko: str = ""
    ai_draft: str = ""
    engine: str = ""
    error: str = ""  # 번역·초안 생성이 실패해도 200으로 돌려주고 이유를 담는다(화면은 원문으로 그려진다)


class VarianceReasonGap(BaseModel):
    """"증감률이 큰데 사유가 없는 칸" 하나. 확정 직전 경고에 쓴다."""

    category_key: str
    label: str
    diff: float
    diff_pct: float
    reason_text: str = ""
    needs_recheck: bool = False


class VarianceReasonCheck(BaseModel):
    """확정 경고 — 증감률이 기준치를 넘었는데 사유가 비어 있거나 재확인이 필요한 칸 목록.

    확정을 서버가 막지는 않는다(공통 RULE: 미업로드가 있어도 이동·확정을 막지 않고 밝힌다). 대신
    무엇을 알고도 확정하는지 화면이 보여줄 수 있게 목록과 문장을 돌려준다."""

    threshold_pct: float
    missing: list[VarianceReasonGap] = []  # 사유가 비어 있는 칸
    stale: list[VarianceReasonGap] = []  # 사유는 있지만 금액이 바뀌어 재확인이 필요한 칸
    note: str = ""


# ---------- 증감 동인 표 (AI) ----------
class VarianceDriverFactor(BaseModel):
    """동인 한 줄. 금액이 아니라 계산식을 담는다 — 원본이 업로드로 바뀌면 같이 바뀌어야 한다."""

    label: str
    op: str = "ref"  # ref(그 항목 그대로) | div_elementwise(월별 나누기) | sub(차) | sum(합)
    operands: list[str] = []  # 비용항목 키(labor · hc ...)
    unit: str = "krw"  # krw | usd | hc | pct


class VarianceDriverIn(BaseModel):
    basis_year: int
    basis_month: int
    kind: str = "forecast"
    lab_scope: str = "ALL"
    driver_key: str
    title: str
    target_key: str
    factors: list[VarianceDriverFactor]
    requested_text: str = ""
    # "다음 버전에도?"의 답. True로 저장하면 이후 버전 조회에도 이 표가 따라온다.
    carry_over: bool = False


class VarianceDriverRow(BaseModel):
    """동인 한 줄의 계산 결과(기준 · 당월 · 증감). 근거를 같이 보여주려고 재료 값도 담는다."""

    label: str
    unit: str = "krw"
    base: float | None = None
    cur: float | None = None
    diff: float | None = None
    diff_pct: float | None = None
    basis_text: str = ""  # "인건비 1,000 ÷ 인력 10 = 100" — 챗봇·화면이 근거로 그대로 보여준다


class VarianceDriverOut(VarianceDriverIn):
    rows: list[VarianceDriverRow] = []
    # 이 표가 지금 보고 있는 버전에서 만들어진 것인지(False면 이전 버전에서 carry_over로 따라온 것).
    # 화면은 따라온 표에 "이전 버전에서 유지" 뱃지를 붙이고, 지울 때 원래 버전을 알려 준다.
    created_here: bool = True
    created_basis: str = ""  # 이 표를 처음 붙인 버전 "26.09 속보"
    created_at: str = ""


# ---------- 실적 1차/2차 분석 ----------
class ActualFirstRow(BaseModel):
    """1차(ERP 약식) 연구소 한 줄. 현지통화 총액 → $ → 원화로 환산한 값과 환차 분해까지 담는다.

    1차에는 계정 상세도 외주용역도 없다 — ERP 약식이 연구소 총액만 주기 때문이다. 그래서 이 한 줄이
    1차 분석의 전부다."""

    lab_code: str
    lab_name: str = ""
    currency: str = "USD"
    local_amount: float = 0  # 현지통화 총액 (그 달)
    usd_amount: float = 0  # $ 환산 (K$)
    krw_amount: float = 0  # 원화 환산 (원)
    base_krw: float = 0  # 비교 기준 금액 (원)
    diff: float = 0
    diff_pct: float | None = None
    fx: float = 0
    fx_local: float = 0  # 현지→$ (USD 연구소는 0)
    fx_usd: float = 0  # $→원
    fx_outside: float = 0
    reason_text: str = ""
    # ERP가 $ 환산액을 같이 줬는지. False면 usd_amount는 서버가 환율로 계산한 값이다.
    usd_from_source: bool = False


class ActualFirstIn(BaseModel):
    """1차 값·사유 저장. [1차 고정] 상태면 서버가 409로 거부한다 — 보고까지 끝난 값이라서다."""

    basis_year: int
    basis_month: int
    lab_code: str
    local_amount: float | None = None
    usd_amount: float | None = None
    reason_text: str | None = None
    source_file: str = ""


class ActualStageOut(BaseModel):
    """실적 한 달의 1차/2차 상태. 화면이 1차 탭을 열 수 있는지, 수정할 수 있는지를 이걸로 판단한다."""

    basis_year: int
    basis_month: int
    # 실적결산 1차가 끝났는지. False면 화면의 [1차 분석] 탭을 비활성으로 둔다(가져올 ERP 값이 없다).
    close_first_done: bool = False
    first_locked: bool = False
    first_locked_at: str = ""
    # 1차에 저장된 연구소 수 / 전체 연구소 수 — "12개 중 9개 입력" 안내용.
    first_filled: int = 0
    lab_count: int = 0
    note: str = ""


class ActualStageIn(BaseModel):
    basis_year: int
    basis_month: int
    close_first_done: bool | None = None
    first_locked: bool | None = None


class ActualFirstResult(BaseModel):
    """1차 분석 표 한 장. 합계는 연구소 줄을 더한 값이다(1차에는 '원화 기타'가 없다 — 그건 2차에서
    더하는 값이라 1차 보고 범위에 들어가지 않는다)."""

    basis_year: int
    basis_month: int
    base_version: str
    base_label: str
    stage: ActualStageOut
    rows: list[ActualFirstRow]
    total: ActualFirstRow
    fallback_note: str = ""
    note: str = ""


class ActualStageShift(BaseModel):
    """2차 화면 맨 위의 "1차 → 2차 변동" 한 줄(연구소별 + 합계).

    1차는 ERP 약식, 2차는 PL이라 금액이 달라지는 게 정상이다. 그 차이를 2차 화면에서 먼저 보여주지
    않으면, 1차로 이미 보고한 숫자와 2차 숫자가 왜 다른지 설명할 수 없다."""

    lab_code: str
    lab_name: str = ""
    first_krw: float = 0  # 1차 원화 환산
    second_krw: float = 0  # 2차(PL) 원화 환산
    diff: float = 0
    diff_pct: float | None = None
    first_reason: str = ""


class ActualStageShiftResult(BaseModel):
    basis_year: int
    basis_month: int
    rows: list[ActualStageShift]
    total: ActualStageShift
    # 1차가 없으면(실적결산 1차 미완료) 비교할 대상이 없다 — 화면은 이 경우 변동 줄을 숨긴다.
    has_first: bool = True
    note: str = ""


# ---------- 집계 화면: CP 계약 · 확정 사전점검 · 인건비율 ----------
class AggregateRow(BaseModel):
    """집계표 한 줄 × 17칸(월 12 + 분기 4 + 연간).

    values의 None은 미등록(빈칸)이고 0.0은 "0원으로 올라왔다"는 뜻이다 — 화면이 둘을 다르게
    그려야 한다(공통 RULE: 빈칸 처리).
    """

    key: str
    label: str
    label_en: str = ""
    level: int = 0  # 손익계산서형 들여쓰기 단계
    format: str = "krw"  # krw(금액) | usd(K$) | hc(명) | pct(%)
    memo: bool = False  # 참고 행(IT 관련 비용·외주용역비) — 개발비 합계에 들어가지 않는다
    values: list[float | None]


class AggregateTable(BaseModel):
    """집계 화면 표 한 장. 캡션·안내 문장까지 서버가 만들어 준다 — 화면과 챗봇이 같은 설명을
    쓰게 하려는 것이다(/overseas/summary의 coverage_note와 같은 방식)."""

    basis_year: int
    basis_month: int
    version: str
    mode: str  # usd | krw
    caption: str
    note: str
    rows: list[AggregateRow]


class CpContractPoc(BaseModel):
    """CP 계약의 POC 한 건(직급 행을 접은 값). 금액 단위는 저장값과 같은 $다."""

    poc_type: str = ""
    poc_proj: str = ""
    total: float = 0  # POC Total (연 계약 총액)
    cost_sum: float = 0  # Cost Sum POC (그중 인건비)


class CpContractOut(BaseModel):
    """그 해 CP 계약(기준정보 › CP 계약) 요약 — 집계 ④ CP 보정 탭이 이 금액으로 분기 정산한다.

    registered=False는 "계약 금액이 0원"이 아니라 "아직 안 올라왔다"는 뜻이다. 화면은 그때 CP 정산
    칸을 0이 아니라 빈칸으로 두고 알린다(공통 RULE: CP 계약 미등록이면 CP 정산 칸 빈칸 + 알림) —
    0으로 채우면 "CP는 계약이 없다"가 아니라 "CP 비용이 0원"으로 읽혀서 총액을 잘못 말하게 된다.

    quarter_*는 연 금액 ÷ 4다(분기 정산 계약이라 3 · 6 · 9 · 12월에 한 번씩 발생한다)."""

    year: int
    registered: bool
    poc_count: int = 0
    year_total: float = 0  # 연 계약 총액($)
    year_labor: float = 0  # 그중 인건비($)
    quarter_total: float = 0  # 분기 하나치($)
    quarter_labor: float = 0
    source_file: str = ""
    uploaded_at: str = ""
    pocs: list[CpContractPoc] = []
    note: str = ""


class OverseasConfirmCheck(BaseModel):
    """[계정 확정]을 눌러도 되는지 — 서버가 판정한 결과.

    blocking이 비어 있으면 확정할 수 있다. 지금 막는 것은 "환율 미등록 월"뿐이다 — 환율이 없는 달은
    원화 환산 자체가 안 돼서 확정해도 그 달 숫자가 없다(공통 RULE: 환율 미등록 월 있으면 확정 불가).

    warnings는 "알고도 확정할 수 있는" 것들이다(미업로드 연구소 · 증감사유 미작성 · 금액이 바뀌어
    재확인이 필요한 사유 · CP 계약 미등록). 서버가 막지 않고 밝히기만 하는 이유는 공통 RULE이
    그렇게 정하기 때문이다 — 대신 확정할 때 note("미업로드 2개 포함 확정")를 같이 남겨서, 나중에
    그 총액을 연구소 전체 합계로 오해하지 않게 한다."""

    basis_year: int
    basis_month: int
    kind: str  # forecast(속보 편성) | actual(실적 시뮬레이션)
    can_confirm: bool
    blocking: list[str] = []
    warnings: list[str] = []
    # 확정 시 version_account_status.note에 그대로 넣을 문장. 경고가 없으면 빈 문자열이다.
    note: str = ""
    fx_missing_months: list[int] = []  # 적용 환율(원/$)이 없는 달
    missing_labs: list[str] = []  # 아직 안 올라온 연구소 이름
    lab_count: int = 0
    uploaded: int = 0
    reason_missing: int = 0  # 증감률이 기준치를 넘었는데 사유가 빈 항목 수
    reason_stale: int = 0  # 사유 저장 뒤 금액이 바뀌어 재확인이 필요한 항목 수
    cp_contract_registered: bool = True


class OverseasLaborRatioRow(BaseModel):
    """인건비율 한 줄(전체 또는 연구소). ratio가 null이면 분모(개발비)가 0이거나 아직 안 올라와서
    비율을 낼 수 없다는 뜻이다 — 화면은 0%가 아니라 '—'로 적는다."""

    lab_code: str  # "ALL"이면 전체 연구소 합계
    name: str
    total: float  # 개발비(매출), 원
    labor: float  # 인건비, 원
    ratio: float | None = None  # 인건비 ÷ 개발비 × 100


class OverseasLaborRatio(BaseModel):
    """인건비율 = 인건비 ÷ 개발비(매출). 집계 화면의 인건비율 팝업과 같은 값이다.

    분기 · 연간은 월별 비율을 더하거나 평균 내지 않고 "합계끼리" 나눈다(공통 RULE) — 달마다 금액이
    다르면 월별 비율의 평균은 실제 비율과 어긋난다. 그래서 period를 받아 그 기간 합계로 계산한다.
    기준은 원화 · CP 분기 정산 반영(히어로 카드 구성 비율과 같은 기준)이다."""

    basis_year: int
    basis_month: int
    period: str  # y(연간) | q1~q4 | m1~m12
    version: str
    rows: list[OverseasLaborRatioRow]
    note: str = ""


class AnomalyRuleOut(BaseModel):
    """이상 감지 기준 1건. 쓰지 않는 파라미터는 null이다(급변은 pct+amount, 합계 불일치는 amount …).
    desc는 지금 값이 들어간 설명 문장이라 화면·챗봇이 그대로 읽어 쓸 수 있다."""

    rule_key: str
    label: str
    enabled: bool = True
    pct: float | None = None
    amount: float | None = None  # K$
    ratio: float | None = None
    desc: str = ""


class AnomalyRuleIn(BaseModel):
    """기준 변경. 보낸 항목만 바뀐다(pct만 보내면 amount는 그대로) — "50% 넘는것만 알려줘"는
    비율만 바꾸는 말이고, 그때 금액 하한까지 초기화되면 경고 범위가 엉뚱하게 달라진다.
    reset=true면 그 기준을 기본값으로 되돌린다("기준 원래대로")."""

    rule_key: str
    enabled: bool | None = None
    pct: float | None = None
    amount: float | None = None
    ratio: float | None = None
    reset: bool = False
    source: str = "screen"  # chat | screen | reset — 이력에 남는다
    note: str = ""  # 무슨 말로 바꿨는지(챗봇 요청 문장)


class AnomalyRuleLogOut(BaseModel):
    rule_key: str
    before: dict | None = None
    after: dict | None = None
    source: str = ""
    note: str = ""
    changed_at: datetime


class AnomalyFinding(BaseModel):
    """이상 감지 1건. category_key·month로 화면이 어느 칸을 표시할지 정한다(month=null이면 행 전체)."""

    rule: str
    rule_label: str
    category_key: str
    category_label: str
    month: int | None = None
    message: str
    value: float | None = None
    base: float | None = None
    rate: float | None = None


class AnomalyScanIn(BaseModel):
    """반영 전 업로드 값을 검사한다. vals를 주면 그 값으로(=대기 중인 파일), 안 주면 이미 저장된
    값으로 검사한다([계정 확정] 전에 "남아 있는 이상"을 세는 쪽이 이 경로를 쓴다).

    base_yyyymm·base_version은 급변을 비교할 전실행이다 — 역할(전실행/전년)과 실제 버전의 매핑은
    화면이 갖고 있어서 서버가 추측하지 않고 받는다. 없으면 급변·단위(배수) 검사를 건너뛴다."""

    lab_pk: str
    version: str
    yyyymm: str
    vals: dict[str, list[float | None]] | None = None
    # 파일에 적혀 온 소계·합계 행(계정 키 → 12개월). 합계 불일치 검사에만 쓴다.
    sums: dict[str, list[float | None]] | None = None
    # 계정 키 → 증감사유 텍스트. 사유 누락 검사에만 쓴다.
    reasons: dict[str, str] | None = None
    base_yyyymm: str | None = None
    base_version: str | None = None
    # 이번 검사에만 적용할 임시 기준("이번만 50%로 봐줘") — 저장하지 않는다.
    overrides: dict[str, dict] | None = None


class AnomalyScanResult(BaseModel):
    lab_pk: str
    findings: list[AnomalyFinding]
    # 기준별 건수 — 화면이 "급변 3 · 사유 누락 1"처럼 요약을 띄운다.
    counts: dict[str, int] = {}
    rules: list[AnomalyRuleOut] = []
    # 검사하지 못한 기준과 그 이유(비교할 전실행이 없을 때 등). 빈칸으로 두면 "이상 없음"으로
    # 읽히기 때문에 못 한 검사는 분명히 돌려준다.
    skipped: list[str] = []
