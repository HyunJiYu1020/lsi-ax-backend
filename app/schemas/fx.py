from pydantic import BaseModel


class FxActualUpload(BaseModel):
    """한 연월(year_month)의 실적 환율. avg(월평균 실적 환율 파일)와 eom(말일 환율 파일)는 서로
    다른 시점에 따로 올라올 수 있어 통화별로 독립적으로 채워진다."""

    avg: dict[str, float] = {}  # 통화 -> 월평균 실적값
    avg_file: str = ""
    avg_at: str = ""
    eom: dict[str, float] = {}  # 통화 -> 말일값
    eom_file: str = ""
    eom_at: str = ""


class FxForecastUpload(BaseModel):
    """한 연월(year_month)의 속보(=실행, forecast) 환율 업로드. rows[통화] = 속보 환율 값 1개."""

    rows: dict[str, float] = {}
    file: str = ""
    at: str = ""


class FxPlanRow(BaseModel):
    c: str
    m: list[float]  # 1~12월


class FxPlanUpload(BaseModel):
    rows: list[FxPlanRow]
    file: str
    at: str


class FxState(BaseModel):
    actual: dict[str, FxActualUpload] = {}  # year_month -> 실적(월평균 + 말일)
    forecast: dict[str, FxForecastUpload] = {}  # year_month -> 속보(=실행) 업로드
    plan: dict[str, FxPlanUpload] = {}  # year -> 경영환율 업로드


class FxAppliedRecomputeRequest(BaseModel):
    basis_year: int
    basis_month: int
    basis_kind: str = "sok"  # sok(속보) | act(실적)
    rule: str = "avg"  # avg(평균) | last(말일) — 과거 대상월 기준


class FxAppliedRow(BaseModel):
    target_month: int
    currency_code: str
    rate: float
    source: str  # actual | forecast | plan
