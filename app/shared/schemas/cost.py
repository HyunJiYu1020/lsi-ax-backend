from pydantic import BaseModel


class CostEntryValueIn(BaseModel):
    """cost_entries(헤더) 1칸의 디테일 값(표준 월 · 분기/연간 요약 · 커스텀 열) upsert 요청.
    헤더가 없으면 이 호출에서 같이 만든다(별도 "헤더 생성" 엔드포인트 없음). 세 가지 중 하나:
    - 표준 월 값: year>0 · month(1~12) · col_name=""
    - 분기/연간 요약 값: year>0 · month=0 · col_name이 "q1"~"q4"|"annual" 중 하나
      (표준 월 값을 쓸 때마다 서버가 그 해의 12개월로 자동 재계산해 같이 채운다 — 직접 보내면 그 값으로 덮어씀)
    - 커스텀 열 값: col_name!="" (q1~q4/annual 제외) · year=month=0
    """

    account_code: str
    scope_code: str
    category_code: str
    yyyymm: str  # 편성 기준 연월 "YYYYMM"
    version: str  # forecast | actual
    label: str | None = None  # 커스텀 category_code일 때만 채움(한글)
    label_en: str | None = None  # 커스텀 category_code일 때만 채움(영문)
    anchor_key: str | None = None  # 커스텀 행을 끼워 넣을 위치(고정 category_code). 없으면 맨 끝
    year: int = 0
    month: int = 0
    col_name: str = ""
    amount: float = 0


class CostEntryOut(BaseModel):
    account_code: str
    scope_code: str
    category_code: str
    yyyymm: str
    version: str
    label: str | None = None
    label_en: str | None = None
    anchor_key: str | None = None
    months: dict[str, float] = {}  # "YYYY-MM" -> amount (표준 월 값)
    summary: dict[str, float] = {}  # "q1"~"q4"|"annual" -> amount (분기·연간 합계, 월에서 자동 계산)
    cols: dict[str, float] = {}  # col_name -> amount (커스텀 열 값)
