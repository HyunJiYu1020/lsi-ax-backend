"""증감사유 입출력(전 계정 공용). `variance_reasons` 테이블을 계정 구분 없이 쓴다.

해외연구소 전용 경로(`/overseas/variance-reasons`)와 같은 테이블이지만 담는 것이 한 겹 적다:
그쪽은 회신 원문·번역·AI 초안·**서버가 계산한 지금 증감액**까지 묶어 내려준다(연구소 PL 수식이
서버에 있어서 가능하다). 다른 계정은 증감액 수식이 아직 화면에만 있어서, 저장 시점 증감액을
화면이 보내 주고 서버는 그걸 그대로 박아 둔다 — 서버가 모르는 숫자를 추측해서 채우면 "재확인
필요" 표시가 아무 근거 없이 켜진다.
"""

from pydantic import BaseModel, Field


class ReasonKey(BaseModel):
    """사유 한 칸의 자리. 화면의 "어느 계정 · 어느 버전 · 어떤 비교 기준 · 어느 기간 · 어느 묶음"이다."""

    account_key: str
    basis_year: int = Field(ge=2000, le=2099)
    basis_month: int = Field(ge=1, le=12)
    kind: str = "forecast"  # forecast(속보) | actual(실적)
    stage: str = ""  # 실적 1차/2차 구분. 속보는 빈 문자열
    base_version: str = "prev"  # prev(전실행) | prior(전년) | plan(경영계획)
    period: str = "y"  # y(연간) | q1~q4 | m1~m12
    scope: str = "ALL"  # 계정 안의 범위(해외연구소는 연구소, 다른 계정은 "ALL" 또는 부서·계약 키)


class ReasonRow(BaseModel):
    """사유 한 칸의 내용. `category_key`가 그 칸의 이름이다 — 감가상각비는 "up"·"dn"(증가·감소
    요약)과 표의 행 키를, 위탁용역비·생소비는 계약 키를 쓴다."""

    category_key: str
    reason_text: str = ""
    source_text: str = ""
    ai_draft: str = ""
    # [저장] 시점의 증감액(원). 화면이 보내 준 값을 그대로 박아 둔다 — 다음에 금액이 달라지면
    # 화면이 "재확인 필요"를 띄울 근거가 된다.
    diff: float | None = None


class ReasonOut(ReasonRow):
    saved_diff: float | None = None
    needs_recheck: bool = False
    saved_at: str = ""


class ReasonsOut(ReasonKey):
    rows: list[ReasonOut] = []


class ReasonsIn(ReasonKey):
    """그 자리의 사유 칸들을 한 번에 저장한다(화면이 사유 여러 칸을 같이 고친다).

    비운 칸(`reason_text=""`)은 **지운다** — 담당자가 지운 문장을 서버가 들고 있으면 다음 조회에
    되살아난다. 그래서 "안 보낸 칸은 그대로, 빈 문자열로 보낸 칸은 삭제"로 가른다.
    """

    rows: list[ReasonRow] = []
