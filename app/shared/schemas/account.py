from datetime import datetime

from pydantic import BaseModel


class AccountStepOut(BaseModel):
    """계정 하나의 편성 프로세스 한 단계. 계정마다 단계 수·이름이 달라 행으로 관리된다
    (account_steps) — 그래서 화면은 이 배열 길이만큼 'N단계'를 그리면 된다."""

    step_order: int  # 1-based, 화면의 'N단계'와 같은 값
    tag: str  # 단계 태그 (취합/편성/검토/분석 등)
    title: str


class AccountOut(BaseModel):
    """대시보드의 계정 카드 하나. status·memo는 사람이 화면에서 고치는 값이고, steps·
    current_step_order는 그 계정의 편성 프로세스가 어디까지 왔는지다.

    전용 화면이 아직 없는 계정(= steps가 빈 배열)은 "준비 중" 스텁으로 보여주면 된다 —
    단계가 정의되지 않은 계정은 account_progress도 없어서 current_step_order가 None이다."""

    key: str
    name: str
    data_type: str  # matrix | line_items | monthly_total
    color: str | None = None
    status: str  # common_codes(ACCOUNT_STATUS)의 code
    status_label: str  # 그 code의 한글명 (화면에 그대로 쓸 수 있게 서버가 풀어준다)
    owner: str = ""  # 편성 주체(담당 조직·담당자). 빈 문자열이면 아직 안 정해진 것
    memo: str | None = None
    current_step_order: int | None = None  # 단계가 정의되지 않은 계정은 None
    steps: list[AccountStepOut] = []
    updated_at: datetime | None = None


class AccountUpdate(BaseModel):
    """계정 카드에서 사람이 고칠 수 있는 값. 보내지 않은 필드(None)는 그대로 둔다 —
    메모만 고치려고 status를 같이 보내지 않아도 기존 status가 지워지지 않게."""

    status: str | None = None
    owner: str | None = None
    memo: str | None = None


class AccountStepUpdate(BaseModel):
    """계정의 현재 진행 단계 이동. /overseas/step의 StepUpdate와 같은 모양으로 맞춘다."""

    step: int


class AccountSummaryIn(BaseModel):
    """계정 하나의 전체 현황판 숫자 — 그 버전의 월별 편성액과 전년 실적 총액.

    계정 전용 화면이 생기기 전에도 현황판은 숫자를 보여줘야 한다. 그 숫자가 브라우저에 있으면
    담당자마다 다른 총액을 보고 회의에 들어온다 — 그래서 전용 화면이 없는 계정도 이 값만은
    서버에 둔다. 저장되는 자리는 새 테이블이 아니라 공용 `cost_entries`다(scope="ALL",
    category="total") — 계정 전용 화면이 생기면 같은 자리를 더 잘게 쓰면 된다.
    """

    yyyymm: str  # 편성 기준 연월 "YYYYMM" (= 그 버전)
    version: str = "forecast"  # forecast | actual
    monthly: list[float] = []  # 1~12월 편성액. 12개가 아니면 모자란 달은 0으로 채운다
    prior_amount: float = 0  # 전년 실적 총액(비교 기준)


class AccountSummaryOut(BaseModel):
    key: str
    name: str = ""
    monthly: list[float] = []
    total: float = 0
    prior_amount: float = 0
