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


class FxAppliedRecomputeRequest(BaseModel):
    basis_year: int
    basis_month: int
    basis_kind: str = "forecast"  # forecast(속보) | actual(실적)
    rule: str = "avg"  # avg(평균) | last(말일) — 과거 대상월 기준
    dry_run: bool = False  # True면 계산만 하고 fx_applied_rates에 쓰지 않는다(저장 전 미리보기용)


class FxAppliedRow(BaseModel):
    target_month: int
    currency_code: str
    rate: float
    source: str  # actual | forecast | plan


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
