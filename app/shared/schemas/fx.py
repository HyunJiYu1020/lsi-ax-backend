from datetime import datetime

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


class FxCurrencyCoverage(BaseModel):
    """그 해에 그 통화의 환율 원본(fx_rates)이 어느 달까지 들어와 있는지. 연구소 통화를 새로 잡거나
    바꿀 때 "환산할 환율이 실제로 있나"를 확인하는 데 쓴다 — 없으면 금액이 조용히 환산 없이
    계산되기 때문에(현지통화 분리가 그냥 빠진다), 사람에게 입력을 요청할 근거가 필요하다."""

    currency_code: str
    year: int
    # 이 통화의 rate 칸에 넣어야 하는 값의 단위 — 원/통화가 아니다. 업로드 엑셀과 같은 형태로
    # "INR/$"(달러당 루피)처럼 적는다. 사람에게 환율을 물을 때 반드시 이 단위로 물어야 한다.
    rate_unit: str
    registered: bool  # 그 해에 실적·속보 중 하나라도 들어와 있나
    forecast_months: list[int] = []
    actual_avg_months: list[int] = []
    actual_eom_months: list[int] = []
    plan_months: list[int] = []
    note: str = ""  # 사람이 그대로 읽어도 되는 한 줄 요약


class FxRateIn(BaseModel):
    """환율 원본 한 칸(연월 × 통화 × 종류)을 직접 넣을 때. 파일 업로드(PUT /fx/state)가 정상 경로고,
    이건 "연구소를 새로 만들었는데 그 통화 환율이 아직 없다" 같은 빈칸 하나를 메우는 용도다."""

    year_month: str  # "YYYY-MM"
    currency_code: str
    kind: str = "forecast"  # actual_avg | actual_eom | forecast | plan
    # 업로드 엑셀과 같은 형태의 값이다(원/통화가 아니다) — USD는 원/$, EUR·GBP·AUD·NZD는 $/통화,
    # 나머지는 통화/$. FxCurrencyCoverage.rate_unit이 그 통화의 단위를 알려준다.
    rate: float
    source_file: str = "직접 입력"


class FxAppliedRecomputeRequest(BaseModel):
    basis_year: int
    basis_month: int
    basis_kind: str = "forecast"  # forecast(속보) | actual(실적)
    rule: str = "avg"  # avg(평균) | last(말일) — 과거 대상월 기준
    dry_run: bool = False  # True면 계산만 하고 fx_applied_rates에 쓰지 않는다(저장 전 미리보기용)


class FxAppliedRow(BaseModel):
    target_month: int
    currency_code: str
    # None은 "그 달 원본 환율이 없다"는 뜻이다(0으로 내리면 받는 쪽이 "환율 0"으로 읽어
    # 금액을 0으로 환산해 버린다 — 실제로 그렇게 전부 0이 된 적이 있다).
    rate: float | None
    source: str  # actual | forecast


class OverseasFxRuleOverrideRow(BaseModel):
    year_month: str  # "YYYY-MM" — 이 달만 기본값 대신 rule을 쓴다
    rule: str  # avg(월평균) | last(말일)
    created_at: datetime


class OverseasFxRuleState(BaseModel):
    """해외연구소 환산 환율 기준의 현재 상태 전체. 화면은 이걸 한 번 받아서 "어떤 달이든
    예외가 있으면 그 rule, 없으면 default"로 바로 판정한다."""

    default: str  # 예외가 없는 모든 달에 적용되는 기본값 (avg | last)
    overrides: list[OverseasFxRuleOverrideRow] = []


class OverseasFxRuleOverrideIn(BaseModel):
    """특정 달만 기본값과 다르게 쓸 때(= "이번 한 번만")."""

    year_month: str  # "YYYY-MM"
    rule: str  # avg(월평균) | last(말일)


class OverseasFxRuleDefaultIn(BaseModel):
    """기본값 자체를 바꿀 때(= "앞으로 계속"). effective_from 이전 달들은 지금 보이는 값 그대로
    예외로 고정해서, 기본값 변경이 과거로 소급되지 않게 한다."""

    rule: str  # avg(월평균) | last(말일)
    effective_from: str  # "YYYY-MM" — 이 달부터 새 기본값. 이전 달은 현재 값으로 고정(freeze)된다
