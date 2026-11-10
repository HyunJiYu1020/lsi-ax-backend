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


class FxVersion(BaseModel):
    end: list[float]
    act: list[float]
    cp: list[float]
    ilsE: list[float]
    ilsA: list[float]
    inrE: list[float]
    inrA: list[float]


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
    fx: dict[str, FxVersion]  # version -> FxVersion
    reason: list[ReasonRow] = []  # 증감사유 업로드 (연구소×버전×항목×구간 키로 upsert, cost처럼 누적)
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
    version: str  # cur(당월 속보) | prev(전실행) | prior(전년 실적) | plan(경영계획)
    category_key: str
    year: int  # 이 금액이 속한 연도 (overseas_cost_entries.year)
    month: int
    amount: float
    actual_thru_month: int = 0  # cur에서 실적/속보 스냅샷을 구분. 그 외 버전은 0


class CostEntryOut(CostEntryIn):
    pass


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


class FxRateIn(BaseModel):
    version: str
    currency: str
    rate_type: str
    month: int
    value: float


class FxRateOut(FxRateIn):
    pass


class CustomRowIn(BaseModel):
    lab_pk: str
    version: str  # cur(당월 속보) | prev(전실행) | prior(전년 실적) | plan(경영계획)
    row_key: str
    label: str  # 구분(한글)
    label_en: str = ""  # Cost Element(영문)
    year: int  # v가 속한 연도 (overseas_cost_entries.year)
    actual_thru_month: int = 0  # cur에서 실적(N월)과 속보(N+1월)를 구분. 그 외 버전은 0
    anchor_key: str | None = None  # 표에서 이 계정(core.js OV_EL의 k) 바로 뒤에 끼워 넣는다. None이면 맨 끝
    v: list[float]  # 12개월


class CustomRowOut(CustomRowIn):
    pass


class ExtraColOut(BaseModel):
    """overseas_cost_entries 행에 추가된 열(label) — 각 행의 custom_fields에서 distinct label로
    뽑아낸 것. label 자체가 열 식별자라 등록 전용 테이블이 따로 없다."""

    lab_pk: str
    version: str
    label: str


class ExtraValueIn(BaseModel):
    """추가 열의 칸 하나(행×열) 값. row_key는 overseas_cost_entries.category_key(고정 계정 또는 custom row_key),
    year은 그 행이 속한 연도(다른 모든 행과 마찬가지로 항상 있어야 함). label 자체가 열 식별자라 열 이름을
    바꾸는 건 지원하지 않는다 — 바꾸려면 옛 이름을 지우고 새 이름으로 다시 넣으면 된다."""

    lab_pk: str
    version: str
    row_key: str
    year: int
    label: str
    actual_thru_month: int = 0  # row_key가 가리키는 행의 실적/속보 구분과 맞춰야 함
    value: float = 0


class ExtraValueOut(ExtraValueIn):
    pass
