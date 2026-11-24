"""기준정보 입출력 스키마.

행의 칼럼 이름은 **화면 키 그대로**다(gb · code · a1~a4 · name, team · t2 · t3 · fa · rep · state,
plm · prod · st …). DB 칼럼명(team_s · functional_area · is_rep …)으로 바꿔 받지 않는 이유는,
업로드 엑셀 머리글을 화면이 이미 이 키로 정규화해 두기 때문이다 — 서버가 다른 이름을 요구하면
변환이 한 겹 더 생기고, 그 변환이 틀리면 "올렸는데 빈칸"이 된다.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class SaveMeta(BaseModel):
    """저장 한 번의 단서 — 이력(ref_change_logs)에 그대로 들어간다.

    `how`를 화면이 보내는 이유: 서버는 PUT만 보고는 "표에서 고친 것"과 "엑셀을 올린 것"과
    "전월 복사"를 구분할 수 없다. 그 셋은 사고의 성격이 다르다(한 줄 오타 vs 표 통째로 갈림).
    `actor`는 로그인이 붙기 전까지 화면이 보내는 이름이고, 안 보내면 빈 문자열로 남는다 —
    서버가 가짜 이름을 박으면 감사 기록에 거짓 주체가 들어간다."""

    actor: str = ""
    how: str = "screen"  # screen | upload | copy | seed | chat
    note: str = ""  # 업로드면 파일명, 복사면 복사 출처


class CostElementRow(BaseModel):
    gb: str = ""
    code: str
    a1: str = ""
    a2: str = ""
    a3: str = ""
    a4: str = ""
    name: str = ""


class CostElementsOut(BaseModel):
    # 연도표를 읽었으면 그 연도, 버전 스냅샷을 읽었으면 그 버전 키가 채워진다.
    year: int | None = None
    version: str | None = None
    # 스냅샷을 어느 연도표에서 떠 왔는지. 화면이 "2026년 표 기준"을 안내문에 그대로 쓴다.
    source_year: int | None = None
    rows: list[CostElementRow]


class CostElementsIn(SaveMeta):
    year: int = Field(ge=2000, le=2099)
    rows: list[CostElementRow]


class AssetClassRow(BaseModel):
    cls: str
    text: str = ""
    code: str
    name: str = ""


class AssetClassesOut(BaseModel):
    rows: list[AssetClassRow]


class AssetClassesIn(SaveMeta):
    rows: list[AssetClassRow]


class DeptCodeRow(BaseModel):
    code: str
    job: str = ""
    name: str = ""
    bu: str = ""
    team: str = ""
    t2: str = ""
    t3: str = ""
    fa: str = ""
    fam3: str = ""
    upd: str = ""
    rep: str = "N"  # "Y" | "N" — 화면 select 값 그대로
    state: str = "사용"  # 사용 | 폐기


class PlmTaskRow(BaseModel):
    plm: str
    prod: str = ""
    name: str = ""
    nick: str = ""
    st: str = ""
    ps: str = ""
    pe: str = ""
    stg: str = ""
    ty: str = ""
    ty2: str = ""
    ss: str = ""
    se: str = ""
    pm: str = ""
    pl: str = ""
    own: str = ""
    cr: str = ""


class DeptCodesOut(BaseModel):
    version: str
    # 직전 버전에서 복사해 시작한 경우 그 버전 키. 화면이 "9.속보에서 복사" 배지를 띄운다.
    copied_from: str = ""
    rows: list[DeptCodeRow]


class DeptCodesIn(SaveMeta):
    version: str
    rows: list[DeptCodeRow]


class PlmTasksOut(BaseModel):
    version: str
    copied_from: str = ""
    rows: list[PlmTaskRow]


class PlmTasksIn(SaveMeta):
    version: str
    rows: list[PlmTaskRow]


class ProductRow(BaseModel):
    """제품정보(SC_FMMS) 한 줄. 칼럼 이름은 양식 머리글 그대로다 — 화면도 그 이름으로 파싱한다."""

    device: str
    yyyy: str = ""
    sel_code: str = ""
    section: str = ""
    sale_device: str = ""
    gr_device: str = ""
    fam1_code: str = ""
    fam2_code: str = ""
    fam3_code: str = ""
    fam4_code: str = ""


class ProductsOut(BaseModel):
    version: str
    copied_from: str = ""
    rows: list[ProductRow] = []


class ProductsIn(SaveMeta):
    version: str
    rows: list[ProductRow] = []


class ChangeRowOut(BaseModel):
    row_key: str
    change: str  # add | del | chg
    before: dict = {}
    after: dict = {}


class ChangeLogOut(BaseModel):
    id: int
    table_key: str
    table_label: str = ""  # 화면에서 부르는 이름("부서코드")
    scope: str = ""
    how: str = "screen"
    actor: str = ""
    note: str = ""
    rows_before: int = 0
    rows_after: int = 0
    added: int = 0
    removed: int = 0
    changed: int = 0
    # True면 바뀐 행이 많아 상세를 남기지 않았다는 뜻. 상세가 없는 것과 안 바뀐 것은 다르다.
    detail_omitted: bool = False
    summary: str = ""
    changed_at: datetime | None = None
    rows: list[ChangeRowOut] = []
