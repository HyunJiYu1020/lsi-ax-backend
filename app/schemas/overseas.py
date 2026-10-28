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


class OverseasState(BaseModel):
    step: int
    basis_month: int = 0  # 편성 기준월(1~12, 프론트 S.basis). cur의 "속보" 스냅샷을 이 달 기준으로 저장한다
    V: dict[str, VersionData]  # version -> VersionData (cur는 "속보" = 기준월까지 반영)
    actualV: dict[str, ActualSnapshot] = {}  # version -> 실적 스냅샷. 지금은 cur만 쓰임
    files: dict[str, dict[str, FileInfo]]  # version -> pk -> FileInfo
    note: dict[str, dict[str, str]]  # "base|per|alab" -> category_key -> text
    fx: dict[str, FxVersion]  # version -> FxVersion


class StepUpdate(BaseModel):
    step: int


class LabOut(BaseModel):
    pk: str
    lab_code: str
    part_code: str | None
    name: str
    country: str


class CostEntryIn(BaseModel):
    lab_pk: str
    version: str
    category_key: str
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
    version: str
    row_key: str
    label: str
    v: list[float]  # 12개월


class CustomRowOut(CustomRowIn):
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
