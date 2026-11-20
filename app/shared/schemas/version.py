from datetime import date, datetime

from pydantic import BaseModel


class VersionIn(BaseModel):
    year: int
    month: int
    kind: str  # forecast | actual


class VersionStatusUpdate(BaseModel):
    status: str  # drafting | done


class VersionOut(BaseModel):
    year: int
    month: int
    kind: str
    status: str
    confirmed_at: date | None = None


class VersionAccountStatusUpdate(BaseModel):
    """계정 확정 상태 변경. note는 "무엇을 알고도 확정했는지"를 남기는 칸이다
    (예: "미업로드 2개 포함 확정"). 작성중(drafting)으로 되돌리면 서버가 확정 일시와 함께 비운다."""

    status: str  # drafting | done
    note: str = ""


class VersionAccountStatusOut(BaseModel):
    account_code: str
    status: str
    # 완료로 바꾼 시각. 작성중이면 null — 화면은 "완료 · 11.15"처럼 상태 옆에 붙여 쓴다.
    confirmed_at: datetime | None = None
    note: str = ""
