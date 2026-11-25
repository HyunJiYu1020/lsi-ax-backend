from datetime import datetime

from pydantic import BaseModel


class FileInfo(BaseModel):
    file: str
    at: str
    # 이 파일이 속한 기준연월 "YYYYMM". 화면이 자리(cur·prev…)별 묶음으로 보내더라도 달은 파일마다
    # 명시한다 — 자리는 보는 달에 따라 달라지는 값이라 저장 키로 쓸 수 없다(models.Upload 주석).
    # 비워 보내면 서버가 그 자리가 가리키는 달로 채운다.
    yyyymm: int = 0


class VersionData(BaseModel):
    """월 값은 float | None이다 — None은 "미등록/미업로드"(저장된 칸이 아예 없음)이고 0.0은
    "0으로 올라왔다"는 뜻이라 서로 다르다. 화면은 None을 빈칸으로 그리고, 합계는 있는 값만 더한다
    (공통 RULE: 빈칸 처리). PUT으로 None을 보내면 그 달은 저장하지 않고 미등록으로 둔다."""

    d: dict[str, dict[str, list[float | None]]]  # pk -> category_key -> [12 months]
    # 저장돼 있는 1Q~4Q · 연간 다섯 칸. pk -> category_key -> [q1,q2,q3,q4,year]. **읽기 전용**이다 —
    # 화면이 그대로 그리라고 내려주는 값이고, 여기로 다시 받지는 않는다(쓰기는 cost_periods).
    per: dict[str, dict[str, list[float | None]]] = {}


class ActualSnapshot(BaseModel):
    """'N월실적' 헤더로 업로드된, 확정된 실적 스냅샷. version='cur'에서만 쓰인다
    (예: 지금 기준월이 10월이고 9월 실적이 확정됐으면 thru_month=9)."""

    thru_month: int
    d: dict[str, dict[str, list[float | None]]]  # None = 미등록 (VersionData 주석 참고)


class ReasonRow(BaseModel):
    """증감사유 업로드 1행(연구소×버전×비용항목×구간). overseas_reason_rows와 1:1.
    lab_code는 파트 없는 연구소 코드(SARC는 CPU+GPU 합계 기준)."""

    lab_code: str
    version: str = "forecast"  # forecast(속보) | actual(실적) — cost_entries.version과 같은 값
    slot: str = ""  # 응답에만 담는 자리(cur · prev · prior · plan). 저장되는 값이 아니다
    yyyymm: int = 0
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


class VendorRow(BaseModel):
    """외주용역 상세 1행(연구소×버전×행). DB는 마스터(overseas_vendor_rows) + 월별 디테일
    (overseas_vendor_row_details)로 나눠 저장하지만, 화면은 한 행 = 12개월 배열로 다루므로
    API에서는 합쳐서 주고받는다. lab_code는 증감사유와 같이 파트 없는 연구소 코드다 —
    외주용역 시트도 SARC는 CPU+GPU를 합쳐 연구소 단위로만 올라온다."""

    lab_code: str
    version: str = "forecast"
    slot: str = ""
    yyyymm: int = 0
    row_no: int  # 업로드한 시트 안에서의 행 순서(1부터)
    category: str = ""  # Category
    project: str = ""  # Project name
    company: str = ""  # Company / Institution
    description: str = ""  # Description / Purpose
    m: list[float] = []  # 1~12월 금액
    source_file: str = ""


class VendorRowsIn(BaseModel):
    """외주용역 전용 저장 요청(PUT /overseas/vendor-rows). .

    rows에 들어온 (연구소, 버전) 조합만 통째로 바뀐다 — 보내지 않은 조합은 건드리지 않는다.
    빈 rows로 한 조합을 비울 수는 없다(무엇을 비울지 알 수 없으므로). 그건 행 삭제 API의 일이다.
    """

    basis_year: int  # 월 금액을 어느 해 디테일로 저장할지
    basis_month: int = 0  # 행에 yyyymm이 없을 때 쓸 기준월(ReasonRowsIn과 같다)
    rows: list[VendorRow]
    # 동시 편집 보호 — /overseas/state와 같은 지문을 쓴다(한 화면의 자료라 충돌 판정도 같아야 한다).
    # 빈 문자열이면 검사하지 않는다.
    state_token: str = ""


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


class ReasonRowsIn(BaseModel):
    """증감사유 전용 저장 요청(PUT /overseas/reason-rows). .

    외주용역·CP와 달리 **전체 교체가 아니라 보낸 것만 누적 반영**이다 — 연구소 회신을 항목별로
    쌓아 가는 자료라, 한 시트를 올렸을 때 다른 항목의 사유가 사라지면 안 된다.
    """

    rows: list[ReasonRow]
    # 행에 yyyymm이 없을 때 쓸 기준월. 둘 다 없으면 400이다 — 월을 모르는 채로는 넣지 않는다.
    basis_year: int = 0
    basis_month: int = 0
    state_token: str = ""  # /overseas/state와 같은 지문. 빈 문자열이면 검사하지 않는다


class CpRowsIn(BaseModel):
    """CP 계약 전용 저장 요청(PUT /overseas/cp-rows). .

    rows에 들어온 연도만 통째로 바뀐다 — 연 1회 올리는 파일이라 사라진 POC·직급은 지워야 한다.
    보내지 않은 연도는 건드리지 않는다.
    """

    rows: list[CpRow]
    state_token: str = ""  # /overseas/state와 같은 지문. 빈 문자열이면 검사하지 않는다
    # 올라와 있는 계약을 **줄이는** 교체(POC 수 · 직급 행 수 · 총 계약금 중 하나라도 감소)를
    # 허용할지. 기본은 거부(400)다 — CP는 연도 단위 전체 교체라, 파싱이 깨진 업로드 한 번이
    # 그 해 계약을 통째로 지운다.
    # 화면이 "POC 3개 → 1개로 바뀝니다. 계속할까요?"를 물어 "예"를 받았을 때만 true로 보낸다.
    confirm_shrink: bool = False
    # 이 계약이 적용되기 시작하는 편성 기준연월 "YYYY-MM". 연구소 기준정보(LabIn)와 같은 규칙이다 —
    # 이 달보다 이전 버전은 바뀌기 전 계약으로 분기 정산액이 계속 계산된다. 안 주면 지금 편성 중인
    # 기준월(= "이 버전부터"). 과거 계약을 고쳐야 하는 경우에만 직접 준다.
    effective_from: str | None = None


class OverseasState(BaseModel):
    step: int
    basis_year: int  # 편성 기준연도(프론트 S.basis 연도). cur/prev/plan은 이 연도, prior는 이 연도-1
    basis_month: int = 0  # 편성 기준월(1~12, 프론트 S.basis). cur의 "속보" 스냅샷을 이 달 기준으로 저장한다
    V: dict[str, VersionData]  # version -> VersionData (cur는 "속보" = 기준월까지 반영)
    actualV: dict[str, ActualSnapshot] = {}  # version -> 실적 스냅샷. 지금은 cur만 쓰임
    files: dict[str, dict[str, FileInfo]]  # version -> pk -> FileInfo
    note: dict[str, dict[str, str]]  # "base|per|alab" -> category_key -> text
    reason: list[ReasonRow] = []  # 증감사유 업로드 (연구소×버전×항목×구간 키로 upsert, cost처럼 누적)
    vendor: list[VendorRow] = []  # 외주용역 상세 (연구소×버전 단위로 전체 교체 — 업로드마다 행 수가 달라짐)
    cp: list[CpRow] = []  # CP 계약 업로드 (연도 단위로 전체 교체 — 연 1회 올리는 파일)
    # CpRowsIn.confirm_shrink와 같다 — 일괄 저장으로 CP를 보낼 때도 줄이는 교체는 확인이 필요하다.
    # 전용 API(PUT /overseas/cp-rows)를 쓰는 지금 화면은 cp를 빈 배열로 보내므로 해당되지 않는다.
    confirm_cp_shrink: bool = False
    # **이번 업로드로 들어온** 1Q~4Q · 연간. pk -> category_key -> [q1,q2,q3,q4,year] (당월 속보만).
    # 연구소가 파일에 적어 보낸 숫자를 월 합으로 고치지 않고 그대로 저장한다 — 연구소 보고서와
    # 글자 단위로 같아야 하고, 월 합과 다르면 그 차이는 "기간 합 불일치"로 따로 알린다.
    #
    # V["cur"].per(읽기)와 **일부러 다른 필드**다. GET이 준 상태를 그대로 PUT으로 돌려보내는 저장이
    # 많은데, 읽은 per를 그대로 쓰기로 받으면 월을 고쳐 저장해도 지난 분기 값이 그대로 다시 박혀서
    # 분기·연간이 월과 어긋난 채 멈춘다. 업로드를 반영하는 그 순간에만 이 필드가 채워져 온다.
    cost_periods: dict[str, dict[str, list[float | None]]] = {}
    # 경영계획 차수. GET에서는 V["plan"]이 **어느 차수인지**(적용 차수 — ★ → 확정 최신 → 최신),
    # PUT에서는 V["plan"]을 **어느 차수에 쓸지**다. PUT에서 비워 보내면 확정되지 않은 최신 차수에
    # 쓰고, 확정된 차수는 덮지 않는다(확정을 화면 저장이 덮으면 지난 보고가 소급해서 바뀐다).
    plan_no: str = ""
    # 각 자리가 가리키는 버전. "202608|actual"처럼 기준연월|종류이고, 경영계획은 "2026|P02"다.
    # 전실행이 어느 달인지는 서버만 아는 규칙이라(속보 M → 실적 M-1, 실적 M → 같은 달 속보 M)
    # 받는 쪽이 다시 계산하지 않게 답을 같이 내려준다.
    slots: dict[str, str] = {}
    # 늘 basis_month와 같은 값이다 — 응답에만 담고, 받는 쪽이 따로 쓸 일은 없다.
    monthless_month: int = 0
    # 동시 편집 보호. GET이 "지금 상태의 지문"을 담아 내려주고, PUT은 그걸 그대로
    # 돌려보낸다. 그 사이 다른 세션이 저장했으면 지문이 달라져 있어 409로 막는다.
    # 빈 문자열이면 검사하지 않는다.
    state_token: str = ""


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


class LabOneOut(LabOut):
    """연구소 하나를 저장했을 때의 응답(PUT /overseas/labs/one). 기준정보 값에 더해, 그 통화로
    환산할 환율이 실제로 있는지 확인한 결과를 같이 준다.

    왜 응답에 끼워 넣나: 환율이 없는 통화로 연구소를 잡아도 집계는 멈추지 않고 조용히 환산을
    건너뛴다(services/fxcalc.lab_currency_warning 주석). 따로 조회해야 알 수 있게 두면 호출하는
    쪽(챗봇·스크립트)이 빼먹고, 빼먹은 티도 안 난다 — 저장 응답에 같이 실어 보내면 안 볼 수 없다."""

    fx_warning: str | None = None


class LabIn(BaseModel):
    """연구소 기준정보 편집 화면(ovLabPage) 저장 payload. lab_code 단위 1행 = 화면의 1행이고,
    SARC처럼 lab_code 하나에 파트(pk)가 여러 개면 currency·is_active·sort_order는 그 파트들에
    동일하게 적용된다 (화면에 파트별로 나눠서 고치는 입력란이 없음)."""

    lab_code: str
    name: str
    currency: str
    is_active: bool = True
    # 이 변경이 적용되기 시작하는 편성 기준연월 "YYYY-MM". 공통 RULE("기준정보 변경은 변경한
    # 버전부터 적용, 지난 버전 영향 없음")대로, 이 달보다 이전 버전은 바뀌기 전 값으로 계속 계산된다.
    # 안 주면 지금 편성 중인 기준월(= "이 버전부터")로 본다. 과거를 고쳐야 하는 경우에만 직접 준다.
    effective_from: str | None = None


class LabSheetOut(BaseModel):
    lab_code: str
    sheet_key: str
    name: str
    sheet_order: int


class LabSheetIn(BaseModel):
    """연구소(lab_code)에 시트(sheet_key)를 배정한다. sheet_key는 미리 common_codes
    (group_code='OVERSEAS_SHEET_TYPE')에 등록돼 있어야 한다."""

    lab_code: str
    sheet_key: str
    sheet_order: int = 0


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
    version: str  # forecast(속보) | actual(실적)
    category_key: str
    yyyymm: int  # 편성 기준 연월 YYYYMM 6자리 정수 (cost_entries.yyyymm)
    month: int  # 이 금액이 속한 달. 기본 1~12는 yyyymm 연도 그대로, 13~24는 그다음 연도 1~12월(기간을
    # 12개월 너머로 늘려 받을 때 씀), 25~36은 그다음다음 연도… 식으로 12개씩 다음 해로 넘어간다.
    amount: float


class CostEntryOut(BaseModel):
    lab_pk: str
    version: str
    category_key: str
    yyyymm: int
    year: int
    month: int
    # None = 그 달 칸이 아예 등록되지 않았다는 뜻(미업로드). 0.0("0으로 올라옴")과 구분해야 하므로
    # 0으로 치환하지 않는다 — 챗봇도 이 값을 "없음"으로 읽고 "0원"이라고 답하면 안 된다.
    amount: float | None


class UploadIn(BaseModel):
    lab_pk: str
    # 그 기준월의 차수(v1 · v2 …). 비우면 서버가 그 달의 다음 차수를 매긴다 —
    # 올리는 쪽은 "몇 번째인지"를 알 수 없으므로 세는 일은 서버가 한다.
    version: str = ""
    # 이 파일이 속한 기준연월 YYYYMM 6자리 정수. 경영계획처럼 달이 없는 자료는 None이고, 그때는
    # version이 그 해의 차수(P02)를 가리킨다(models.Upload.yyyymm 주석).
    yyyymm: int | None = None
    filename: str


class UploadOut(UploadIn):
    uploaded_at: datetime
    s3_key: str | None = None
    # 원본 보관도 uploads 행을 추가해 지문을 움직인다 — 화면이 보낸 지문이 보관 직전과 같았을 때만
    # 새 지문을 채워 준다(아니면 None). 409는 내지 않는다: 보관은 행 추가뿐이라 덮어쓸 것이 없고,
    # 여기서 거부하면 "파싱 실패 파일도 보관한다"는 약속이 지문 때문에 깨진다.
    state_token: str | None = None


class NoteIn(BaseModel):
    base_version: str
    period: str
    lab_scope: str
    category_key: str
    note_text: str


class NoteOut(NoteIn):
    pass


class ExtraRowIn(BaseModel):
    lab_pk: str
    version: str  # forecast(속보) | actual(실적)
    row_key: str
    label: str  # 구분(한글)
    label_en: str = ""  # Cost Element(영문)
    yyyymm: int  # v가 속한 편성 기준 연월 YYYYMM 6자리 정수 (cost_entries.yyyymm)
    anchor_key: str | None = None  # 표에서 이 계정(core.js OV_EL의 k) 바로 뒤에 끼워 넣는다. None이면 맨 끝
    # 원시 행(is_derived=False)의 월별 값. 12개월(기본) 또는 12의 배수(24·36…, 기간을 12개월 너머로
    # 늘려 받을 때). 파생 행은 값을 저장하지 않고 formula로 매번 계산하므로 비워 보내면 된다.
    # None인 달은 미등록(빈칸)이라 저장하지 않는다 — 0으로 올라온 달과 구분된다.
    # None은 "금액은 건드리지 마라"는 뜻이다(이미 있는 행의 이름·위치만 고칠 때) — []와 구분한다.
    # 금액을 되보내게 하면 그 왕복에서 값이 상할 수 있다: 화면이 K$ 단위면 서버가 받은 금액을
    # 1000배 하는데(chat.py _to_stored_dollar), 조회해서 받은 $ 값을 그대로 되보내면 1000배 커진다.
    v: list[float | None] | None = None
    # 파일에 적혀 온 1Q~4Q·연간 칸 [1Q,2Q,3Q,4Q,연간] — 고정 계정(state의 cost_periods)과 같은
    # 규칙으로, 추가 행도 연구소가 보고한 공식 분기·연간을 월 합으로 덮지
    # 않고 그대로 보여주기 위해 받는다. None이면 "건드리지 않는다"(이름·자리만 고치는 저장),
    # 리스트면 그 행의 분기·연간 전체 교체다 — []는 지운다는 뜻이다(분기 칸 없이 다시 올라온
    # 파일이 지난 파일의 분기값을 남기면 안 된다. cost_headers가 전체 교체인 것과 같은 이유).
    per: list[float | None] | None = None
    # 동시 편집 지문(services/state.state_token) — vendor-rows·cp-rows와 같은 규칙. 보내면 낡았을 때
    # 409로 거부하고, 응답(ExtraRowOut)에 저장 뒤의 새 지문을 실어 준다. 전용 저장도 지문을
    # 움직이므로 화면이 새 지문으로 갈아끼우지 않으면 **다음 전체 저장이 자기 저장과 충돌**한다.
    # 안 보내면(챗봇) 검사하지 않는다.
    state_token: str | None = None
    # 누가 부르는지. 화면(screen)과 챗봇(chat)에 서로 다른 검사를 걸기 위해 받는다 —
    # anomaly_rule_logs.source · guidelines.source와 같은 값 체계다.
    #
    # 왜 나눠야 하나: 화면의 '+행 추가'는 사람이 표를 보면서 누르는 거라 같은 이름이 이미 있으면
    # 눈에 보이고, 게다가 화면은 이 PUT의 실패를 무시한다(core.js의 .catch(=>{})) — 여기서 400을
    # 주면 화면에는 행이 보이는데 서버에는 없는 상태가 조용히 생긴다. 말로 시켜서 표를 못 보는
    # 챗봇 경로에서만 "이미 있는 행 아닌가요?"를 막는다(anchor_key 가드와 같은 이유).
    source: str = "screen"
    # True면 실제로 수집한 비용이 아니라 다른 계정들을 조합(합산·비율 등)해 계산한 파생 행이라는 뜻.
    # 취합에서 추가한 원시 행과 집계에서 추가한 파생 행을 저장 후에도 구분하기 위해 DB에 남긴다 —
    # 삭제 권한(원시=취합에서만, 파생=집계에서만)과 표시 규칙(원시=양쪽, 파생=집계만), 그리고
    # '개발비(A)' 합계에서 파생값을 빼는 판단이 모두 이 플래그에서 나온다.
    is_derived: bool = False
    # 파생 행의 계산식. 계정을 세로로 묶는다 — {"op":"div_elementwise","operands":["labor","hc"]}는
    # 인건비 ÷ 인력(= 인당인건비). op: sum_elementwise | div_elementwise. 원시 행은 None.
    formula: dict | None = None


class ExtraRowOut(ExtraRowIn):
    # 파생 행일 때만 채워지는 계산 근거 — "인건비 1,000 ÷ 인력 10 = 100"을 화면·챗봇이 그대로
    # 보여줄 수 있도록 재료별 월 값과 월별/연간 계산 결과를 서버가 풀어서 내려준다.
    basis: dict | None = None


class ExtraColIn(BaseModel):
    """편성 그리드에 추가한 열. label 자체가 열 식별자라 이름 변경은 지원하지 않는다
    (바꾸려면 옛 이름을 지우고 새 이름으로 다시 만들면 된다)."""

    lab_pk: str
    version: str  # forecast(속보) | actual(실적)
    yyyymm: int
    label: str
    # True면 월을 가로로 묶어 계산한 파생 열(집계 전용). 칸 값을 저장하지 않고 formula로 매번 계산한다.
    is_derived: bool = False
    # 파생 열의 계산식. 월을 가로로 묶는다 — {"op":"sum_months","months":[7,8,9]}는 3분기 합계.
    # op: sum_months | avg_months. 원시 열은 None.
    formula: dict | None = None
    # 칸을 그리는 형식(값은 원문 그대로 두고 표시만 바꾼다). raw(그대로) |
    # pct_ratio(0.825 → 82.5%) | pct_plain(82.5 → 82.5%). 글자 값은 형식과 무관하게 원문 그대로다.
    fmt: str = "raw"


class ExtraColOut(ExtraColIn):
    pass


class ExtraValueIn(BaseModel):
    """추가 열의 칸 하나(행×열) 값. row_key는 cost_entries.category_code(고정 계정 또는 사용자 추가 행의 키),
    yyyymm은 그 행이 속한 편성 기준 연월(다른 모든 행과 마찬가지로 항상 있어야 함). label 자체가 열
    식별자라 열 이름을 바꾸는 건 지원하지 않는다 — 바꾸려면 옛 이름을 지우고 새 이름으로 다시 넣으면 된다.

    value는 숫자와 글자를 다 받는다 — 양식 밖 열이라 금액·비율·메모가 섞여 온다. 어느 쪽으로
    저장할지는 서버가 한 곳에서 판정한다(services/extra_rows.classify_extra_value): "1,200"처럼
    숫자로 읽히는 글자는 숫자, 나머지는 원문 그대로 text. JSON 응답의 타입이 곧 저장된 타입이다
    (숫자면 number, 문자열이면 text) — 받는 쪽은 typeof 분기만 하면 된다."""

    lab_pk: str
    version: str
    row_key: str
    yyyymm: int
    label: str
    value: float | str = 0
    # 동시 편집 지문 — ExtraRowIn.state_token과 같은 규칙(낡았으면 409, 응답에 새 지문).
    state_token: str | None = None


class ExtraValueOut(ExtraValueIn):
    # 파생 열의 칸일 때만 채워지는 계산 근거 — "7월 100 + 8월 110 + 9월 120 = 330".
    basis: dict | None = None


class CostHeaderIn(BaseModel):
    """연구소 비용 시트의 기간 열 머리글 원문 한 벌(그 시트 전체). 업로드를 반영할 때 한 번 보낸다.

    labels는 열 자리 → 머리글 글자다: "m1".."m12"는 월(기간을 12개월 너머로 받으면 "m13"…),
    "q1".."q4"는 분기, "y"는 연간. 파일에 없던 열은 키 자체를 빼고 보내면 된다 — 그 열은 화면이
    종전처럼 코드 머리글("1월")을 쓴다.

    보낸 한 벌이 그 (연구소 · 버전 · 기준월)의 머리글 **전체를 교체**한다. 파일마다 열 구성이
    달라질 수 있어서 열별 덮어쓰기로는 지난 업로드의 열이 남는다(외주용역 행과 같은 방식).
    빈 labels를 보내면 그 시트의 머리글을 지운다(= 코드 머리글로 되돌린다)."""

    lab_pk: str
    version: str  # forecast(속보) | actual(실적)
    yyyymm: int
    labels: dict[str, str] = {}
    source_file: str = ""


class CostHeaderOut(CostHeaderIn):
    pass


class LaborSplitLab(BaseModel):
    """인건비 분해의 연구소 한 줄. amount 단위는 전부 원이고 인원만 명이다."""

    lab_pk: str
    lab_name: str
    currency: str
    hc_base: float
    hc_cur: float
    per_head_base: float  # 원/명 — 비교 버전 환율로 환산한 인건비 ÷ 평균 인원
    per_head_cur: float
    hc_effect: float
    rate_effect: float
    # 인력(hc)이 없어서 인원·단가로 나눌 수 없는 연구소의 환차外 몫. 나눌 수 있으면 0이다.
    unsplit: float
    fx_outside: float  # = hc_effect + rate_effect + unsplit


class LaborSplitResult(BaseModel):
    """'인건비가 왜 늘었나'를 인원 효과와 단가 효과로 나눈 결과.

    화면(분석 › 증감분석 › 계정별 증감분석 › 계정별 상세)의 인건비 행 아래 줄들과 같은 계산이다.
    환율이 움직여서 변한 몫(환차)은 빼고, 환차外만 나눈다:
      환차外 = (당월인원 − 비교인원) × 비교단가 + 당월인원 × (당월단가 − 비교단가)
    두 효과의 합(+ 인력 없는 연구소 몫)은 fx_outside와 정확히 일치한다."""

    basis_year: int
    basis_month: int = 0  # 어느 버전의 분해인가(0 = 응답을 만든 서버가 최신 속보로 떨어뜨렸다)
    kind: str = "forecast"  # forecast(속보) | actual(실적) — 집계표·증감 표와 같은 뜻
    base_label: str = ""  # 비교 대상 라벨("26.08 실적") — 무엇과 비교한 분해인지
    period: str  # y(연간) | q1~q4 | m1~m12
    base_version: str  # prev(전실행) | prior(전년 실적) | plan(경영계획)
    lab_scope: str  # "ALL" 또는 연구소 코드/pk
    hc_base: float
    hc_cur: float
    per_head_base: float
    per_head_cur: float
    hc_effect: float
    rate_effect: float
    unsplit: float
    fx_outside: float
    # 화면의 "환차外" 칸과 맞춰볼 수 있게 같이 돌려준다 — 이 값이 화면과 다르면 분해값도 다르다.
    labor_cur: float  # 당월 인건비 (원, 비교 버전 환율로 환산)
    labor_base: float  # 비교 버전 인건비 (원)
    labs: list[LaborSplitLab]
    note: str = ""


class VendorProjectRow(BaseModel):
    """외주용역비를 과제(project) 단위로 묶은 한 줄. 금액 단위는 백만원(화면 외주용역 표와 같다)."""

    project: str
    amount: float
    item_count: int  # 그 과제에 달린 외주용역 건수
    labs: list[str]
    company_count: int
    # 아래 셋은 비교 버전을 지정했고 그 데이터가 실제로 있을 때만 값이 들어온다. 없으면 0이 아니라
    # null이다 — 0으로 내려보내면 "증감이 없다"로 읽혀서, 못 구한 걸 구한 것처럼 답하게 된다
    # (실제로 비교 데이터가 없는데 모든 과제 diff가 0.0으로 나가고 있었다).
    base_amount: float | None = None
    diff: float | None = None
    status: str | None = None  # 신규 | 종료 | 변동 | 동일


class VendorByProjectResult(BaseModel):
    """외주용역비 과제별 집계. 비용 본체(cost_entries)에는 과제 칸이 없어서 과제 기준 분석은
    외주용역 시트에서만 된다 — 그래서 이 결과는 개발비 전체가 아니라 외주용역비만 다룬다는 걸
    scope_note로 분명히 밝힌다(챗봇이 '개발비를 과제별로 쪼갰다'고 오해해서 답하면 안 된다)."""

    version: str
    base_version: str | None = None
    period: str
    lab_code: str | None = None
    unit: str = "백만원"
    total: float
    # 비교하지 못했으면 null (위 base_amount 주석과 같은 이유)
    base_total: float | None = None
    diff: float | None = None
    # 비교 버전을 요청했고 그 데이터가 실제로 있어서 증감을 낸 경우에만 True.
    compared: bool = False
    projects: list[VendorProjectRow]
    scope_note: str = "외주용역비만. 개발비 전체가 아니다(비용 데이터에는 과제 칸이 없다)."
    note: str = ""


class OverseasSummaryLab(BaseModel):
    """히어로 카드의 "업로드 n/12" 진척을 연구소 단위로 푼 것. 파트가 여러 개인 연구소(SARC)는
    파트가 모두 올라와야 uploaded=True다 — 화면 ovHas 판정과 같은 규칙."""

    lab_code: str
    name: str
    # 그 연구소(파트 전부)에 등록된 칸이 하나라도 있으면 True. "값이 0이 아닌 칸"이 아니라
    # "칸이 존재하는가"로 본다 — 전부 0으로 올라온 연구소도 업로드는 된 것이다.
    uploaded: bool


class OverseasSummary(BaseModel):
    """해외연구소 화면 맨 위 히어로 카드 한 장에 들어가는 값 전부.

    화면(frontend-html/shared/core.js)이 그대로 그리는 숫자다 — 화면이
    이 응답을 그대로 그린다 — 히어로 카드와 ③집계 KPI 카드가 모두 이 값을 쓴다."""

    basis_year: int
    basis_month: int
    basis_label: str  # "26.09" — 카드 제목의 "(26.09 속보 기준)"
    period: str  # y(연간) | q1~q4 | m1~m12
    version: str  # cur | prev | prior | plan
    # 집계표와 같은 "어느 버전을 읽었는가". kind는 **읽은** 버전의 종류라 version=prev면 요청한
    # kind가 아니라 전실행의 종류가 온다(forecast|actual|plan). version_label은 "26.08 실적"처럼
    # 화면·챗봇이 그대로 쓰는 문장이다 — 양쪽이 따로 만들면 같은 버전을 다르게 부른다.
    kind: str = "forecast"
    plan_no: str = ""
    version_label: str = ""
    step: int  # 0-based 화면 단계 인덱스 (0 취합 · 1 집계 · 2 증감분석)
    step_count: int
    total: float  # 개발비(매출) = 연구소 합계 (원)
    labor: float  # 인건비 (원, CP 분기 정산 반영)
    non_labor: float  # 인건비외 (원, CP 분기 정산 반영)
    labor_pct: float
    non_labor_pct: float
    uploaded: int  # 데이터가 올라온 연구소 수
    lab_count: int  # 전체 연구소 수
    labs: list[OverseasSummaryLab]
    # 위 금액이 "연구소 전체 합계"가 아니라 "올라온 것만 더한 합계"라는 걸 말로 적은 것. 화면
    # 히어로 카드의 "12개 중 n개 기준" 문구와 같은 내용이다 — 챗봇이 이 총액을 전체 합계라고
    # 단정해서 답하면 안 되므로, 세는 쪽이 아니라 답하는 쪽이 읽을 문장으로 같이 내려준다.
    coverage_note: str = ""
    note: str = ""


class ReasonTranslateIn(BaseModel):
    """증감사유 번역 요청. texts는 연구소 회신 원문(영문)이고, 같은 문장이 여러 번 들어와도 된다 —
    서버가 정규화·중복 제거해서 LLM에는 한 번만 묻는다."""

    texts: list[str]
    provider: str | None = None  # "anthropic" | "ollama" | "gemini" — 생략 시 settings.chat_provider
    # True면 캐시(overseas_reason_translations)를 무시하고 다시 번역해서 덮어쓴다 — 모델을 바꿨거나
    # 번역이 이상해서 다시 돌릴 때만 쓴다(기본은 캐시 우선: 같은 원문은 항상 같은 번역이어야 한다).
    refresh: bool = False


class ReasonTranslateItem(BaseModel):
    """원문 1건의 번역 결과. ko가 null이면 번역하지 못한 것이고, 그때 화면은 원문을 그대로 보여준다
    (번역 실패를 빈칸으로 두면 연구소가 사유를 안 쓴 것처럼 보인다)."""

    source: str  # 정규화한 원문 (요청에 넣은 문장과 공백만 다를 수 있다)
    ko: str | None = None
    engine: str = ""
    cached: bool = False  # True면 이번에 LLM을 부르지 않고 저장된 번역을 그대로 돌려준 것


class ReasonTranslateResult(BaseModel):
    """번역 결과 묶음. LLM 호출이 실패해도 200으로 돌려주고 error에 이유를 담는다 — 증감분석 화면은
    번역 없이도 원문으로 그려져야 하는데, 여기서 4xx/5xx를 던지면 화면 쪽에서 구분하기 어렵다."""

    items: list[ReasonTranslateItem]
    provider: str
    translated: int = 0  # 이번에 LLM을 불러 새로 번역한 건수
    cached: int = 0  # 저장된 번역을 그대로 쓴 건수
    failed: int = 0  # 번역하지 못한 건수(ko=null)
    error: str = ""


class ReasonSummaryItem(BaseModel):
    """요약에 넣을 회신 한 줄. label은 그 사유가 달린 계정 이름(인건비 등)이다 — 원문만 모아 보내면
    모델이 "무엇이 늘었는지"를 본문에서 추측하게 되고, 계정 이름이 원문에 없으면 지어낸다."""

    label: str
    text: str


class ReasonSummaryIn(BaseModel):
    """지금 화면에 보이는 회신 묶음을 요약해 달라는 요청. 무엇을 보내느냐는 화면이 정한다 —
    연구소·기간·비교 기준으로 이미 걸러 놓은 그 목록이 담당자가 읽으려던 범위다."""

    items: list[ReasonSummaryItem]
    scope_label: str = ""  # "SARC · 전실행 대비 · 연간" — 요약문이 무엇을 본 것인지 모델에게 알려준다
    provider: str | None = None


class ReasonSummaryResult(BaseModel):
    """요약 결과. 번역과 같은 약속으로 실패해도 200에 error를 담는다 — 요약이 안 된다고 표가
    사라지면 안 되고, 원문·번역은 표에 그대로 남아 있다."""

    lines: list[str] = []
    count: int = 0  # 요약에 들어간 회신 건수(화면이 "15건을 요약했습니다"로 밝힌다)
    engine: str = ""
    error: str = ""


class RowMatchSuggestion(BaseModel):
    """"이 행과 같은 행인가요?"로 보여줄 후보 하나. score는 후보를 고르는 기준이 아니라 정렬용이다 —
    합칠지 말지는 항상 사람이 답한다(자동 병합은 Travel Exp / Test Exp처럼 다른 행을 합칠 수 있다)."""

    kind: str  # "category"(고정 계정) | "extra_row"(이미 있는 추가 행)
    key: str  # category면 비용항목 키(travel), extra_row면 정식 이름의 정규화값
    label: str  # 화면에 보여줄 정식 이름(한글)
    label_en: str = ""
    matched_on: str = ""  # 후보의 어느 이름에 걸렸는지(한글명 / 영문명) — 사람이 판단할 근거
    score: float = 0
    # 그 추가 행을 이미 쓰고 있는 연구소(고정 계정이면 빈 목록) — "SARC·DSRA가 쓰는 행"이라는 걸
    # 알면 같은 행인지 판단하기 쉽다.
    labs: list[str] = []


class RowMatchItem(BaseModel):
    """업로드 파일의 행 이름 1건에 대한 판정 결과.

    resolved가 있으면 이미 사람이 답해 둔 이름이라 묻지 않고 그대로 반영한다(별칭 재사용).
    resolved가 없고 suggestions가 있으면 [반영] 전에 물어본다. 둘 다 없으면 그냥 새 행이다."""

    label: str
    resolved: RowMatchSuggestion | None = None
    # resolved.kind가 "distinct"면 "별개 행"이라고 이미 답한 이름이다 — 묻지 않고 새 행으로 둔다.
    suggestions: list[RowMatchSuggestion] = []


class RowMatchIn(BaseModel):
    """labels: 업로드 파일에서 계정 목록에 없던 행 이름들. version·yyyymm은 후보로 쓸 "이미 있는
    추가 행"을 어느 버전에서 찾을지 정한다(그 달에 실제로 쓰이는 행만 후보로 올린다)."""

    labels: list[str]
    yyyymm: int
    version: str | None = None


class RowMatchResult(BaseModel):
    items: list[RowMatchItem]


class RowAliasIn(BaseModel):
    """"같은 행" 또는 "별개 행"이라고 답한 결과를 저장한다. target_kind="distinct"면 key·label은 비운다."""

    alias_label: str
    target_kind: str  # "category" | "extra_row" | "distinct"
    target_key: str = ""
    target_label: str = ""


class RowAliasOut(BaseModel):
    alias_norm: str
    alias_label: str
    target_kind: str
    target_key: str = ""
    target_label: str = ""


class RowMergeIn(BaseModel):
    """이미 적재된 추가 행의 금액을 다른 행으로 합쳐 옮긴다.

    별칭(RowAliasIn)과 하는 일이 다르다 — 별칭은 "앞으로 올라올 파일"의 이름을 맞추고, 이건 "이미
    들어간 값"을 옮긴다. 담당자가 "Other랑 Misc 기타경비로 합쳐줘" 한 마디로 둘 다 원하므로 보통
    같이 호출되지만, 별칭은 지울 수 있고 합치기는 되돌릴 수 없어서 따로 둔다."""

    yyyymm: int # 합칠 칸의 편성 기준 연월 YYYYMM 6자리 정수
    version: str # forecast(속보) | actual(실적)
    source_labels: list[str] # 합쳐 없앨 추가 행 이름들(정규화해서 찾는다 — 'Other'/'other' 같다)
    target_kind: str # "category"(고정 계정으로) | "extra_row"(이미 있는 추가 행으로)
    target_key: str = "" # category면 비용항목 키(misc 등), extra_row면 정식 이름의 정규화값
    target_label: str = "" # extra_row면 합쳐 넣을 정식 이름
    lab_pk: str = "" # 비우면 전체 연구소. 특정 연구소만 합치려면 그 pk
    # True면 저장하지 않고 "이렇게 바뀐다"만 돌려준다. 합치기는 되돌릴 수 없어서(원본 행이 지워진다)
    # 호출부가 먼저 이걸로 보여주고 사람 확인을 받아야 한다.
    dry_run: bool = True


class RowMergePreview(BaseModel):
    """한 연구소에서 합쳐질 내용 한 건 — 무엇이 얼마로 바뀌는지."""

    lab_pk: str
    source_label: str
    source_total: float # 합쳐 넣을 금액(연간 합, $)
    target_before: float # 합치기 전 대상 행 금액(연간 합, $)
    target_after: float


class RowMergeResult(BaseModel):
    """합치기 결과(또는 dry_run 미리보기)."""

    dry_run: bool
    yyyymm: int
    version: str
    target_label: str
    merged: list[RowMergePreview] = []
    # 합쳐 없앤(또는 없앨) 행 수. merged와 다를 수 있다 — 한 연구소가 'Other'와 'Misc'를 둘 다
    # 쓰면 merged는 2건이지만 대상 행은 하나다.
    removed_rows: int = 0
    # 확정을 작성 중으로 되돌렸는지(D7). dry_run이면 항상 False.
    unconfirmed: bool = False
    note: str = ""


class CarryDefIn(BaseModel):
    """집계 파생 행·열을 "다음 버전에도 유지"할지 정한다. carry=False면 저장해 둔 승인을 지운다."""

    kind: str  # "row"(파생 행) | "col"(파생 열)
    def_key: str  # 파생 행이면 row_key, 파생 열이면 label
    label: str = ""
    carry: bool = True


class CarryDefOut(BaseModel):
    kind: str
    def_key: str
    label: str = ""


class CarryForwardIn(BaseModel):
    """새 버전을 열 때, 유지 승인한 파생 행·열의 정의(계산식)만 그 버전으로 복제한다.
    값은 복제하지 않는다 — 재료가 새 버전 업로드 값으로 바뀌면 계산 결과도 그 버전 숫자로 나와야 한다."""

    from_yyyymm: int
    from_version: str
    to_yyyymm: int
    to_version: str


class CarryForwardResult(BaseModel):
    rows: int = 0  # 복제한 파생 행 수
    cols: int = 0  # 복제한 파생 열 수
    skipped: list[str] = []  # 이미 그 버전에 있어서 건너뛴 것(이름)


# ---------- 비교 기준 (전실행 · 전년 · 경영계획) ----------
class PlanRevisionOut(BaseModel):
    """경영계획 차수 하나. 증감분석 화면의 "경영계획 대비"가 어느 차수를 보는지 고르는 선택지다."""

    plan_no: str  # "P00" | "P01" ...
    label: str  # 화면에 보일 이름. 공식 비교 기준(is_final)이면 "P01 ★"
    is_final: bool  # 그 연도의 공식 비교 기준(★). 화면 기본 선택이 이 차수다
    is_confirmed: bool
    finalized_at: str = ""


class VarianceBasisOut(BaseModel):
    """비교 기준 한 칸이 "실제로 무엇과 비교하는지"를 풀어 놓은 것.

    화면 버튼에 "전실행"이라고만 쓰면 담당자는 그게 8월 실적인지 9월 속보인지 알 수 없다. 1월 속보의
    전실행은 전년 12월 실적이라 연도까지 넘어가므로 더더욱 그렇다. 그래서 라벨을 서버가 만들어 준다 —
    화면·챗봇·엑셀이 같은 문장을 쓰게 하려는 것이다."""

    key: str  # prev | prior | plan
    label: str  # "전실행" | "전년" | "경영계획"
    resolved_label: str  # 실제 비교 대상 "25.12 실적" | "P01 ★"
    year: int = 0
    month: int = 0
    kind: str = ""  # forecast | actual | plan
    plan_no: str = ""  # key=plan일 때의 차수
    has_data: bool = True  # 그 버전에 저장된 데이터가 있는지. False면 증감을 낼 수 없다
    # 원래 기준(전년 12월 실적 등)이 없어서 다른 버전으로 대체한 경우의 안내 문구.
    # 예: "전년 12월 실적이 없어 26.11 속보 기준으로 비교합니다." 화면은 이 문장을 뱃지로 띄운다.
    fallback_note: str = ""


class VarianceBasisResult(BaseModel):
    basis_year: int
    basis_month: int
    kind: str  # forecast | actual | plan
    basis_label: str  # "26.09 속보"
    bases: list[VarianceBasisOut]
    plan_revisions: list[PlanRevisionOut] = []
    default_plan_no: str = ""  # ★ 차수(없으면 가장 최근 차수)


# ---------- 증감분석 표 (근거 데이터) ----------
class VarianceRow(BaseModel):
    """계정 한 줄의 증감 분해. 금액은 모두 원(KRW)이고, 화면이 억원/백만원으로 줄여 보여준다
    (공통 RULE: 저장·전송은 원금액, 표시만 줄인다).

    환차를 두 칸으로 가르는 이유: USD가 아닌 통화를 쓰는 연구소는 "현지통화가 $ 대비 움직인 몫"과
    "$가 원 대비 움직인 몫"이 섞여 있어서, 합쳐 놓으면 어느 환율 때문에 변했는지 알 수 없다.
    USD 연구소는 현지→$ 단계가 없으니 fx_local이 항상 0이다."""

    category_key: str
    label: str
    label_en: str = ""
    level: int = 0  # 손익계산서형 들여쓰기 단계
    unit: str = "krw"  # krw(금액) | hc(명) | pct(%)
    memo: bool = False  # 참고 행(IT 관련 비용·외주용역비) — 개발비 합계에 들어가지 않는다
    base: float = 0  # 비교 기준 금액
    cur: float = 0  # 지금 보고 있는 버전 금액
    diff: float = 0  # cur - base
    diff_pct: float | None = None  # 증감률 %. 기준이 0이면 낼 수 없어 null
    fx: float = 0  # 환차 계 = fx_local + fx_usd
    fx_local: float = 0  # 환차 현지→$ (USD 연구소는 0)
    fx_usd: float = 0  # 환차 $→원
    fx_outside: float = 0  # 환차外 = 금액 자체의 증감


class VarianceResult(BaseModel):
    """증감분석 표 한 장 전체. 챗봇이 "SARC 인건비 왜 늘었어?"에 답할 때 근거로 쓰는 데이터이기도
    해서, 화면이 보여주는 것과 같은 값을 같은 식으로 낸다(식을 바꿀 때는 core.js도 같이 고칠 것)."""

    basis_year: int
    basis_month: int
    kind: str
    stage: str = ""  # 실적의 first(1차) | second(2차). 속보는 빈 문자열
    base_version: str
    base_label: str  # "25.12 실적" — 표 머리글에 그대로 쓴다
    period: str
    lab_scope: str
    plan_no: str = ""
    rows: list[VarianceRow]
    fallback_note: str = ""
    note: str = ""


class LaborSplitTotals(BaseModel):
    """인건비 분해의 합계만. 증감분석 표에 실어 보내는 모양이다.

    왜 연구소별 상세(LaborSplitResult.labs)를 빼는가: 화면은 합계 줄만 그리는데, 17기간 × 연구소
    20여 곳이면 한 응답에 안 쓰는 줄 340개가 실린다(260KB → 60KB). 엑셀 다운로드는 연구소마다
    따로 받으니 그 차이가 수 MB가 된다. 연구소별 상세가 필요하면 `/overseas/labor-split`을 쓴다.
    """

    hc_base: float = 0
    hc_cur: float = 0
    per_head_base: float = 0  # 원/명
    per_head_cur: float = 0
    hc_effect: float = 0  # 인원이 늘어서 생긴 몫
    rate_effect: float = 0  # 1인당 인건비가 올라서 생긴 몫
    unsplit: float = 0  # 인력을 안 낸 연구소 몫(나눌 수 없어 따로 뺀다)
    fx_outside: float = 0  # = hc_effect + rate_effect + unsplit


class VarianceLabCell(BaseModel):
    """연구소 한 곳의 한 기간 증감. 금액은 전부 원이고 인원만 명이다.

    화면(분석 › 증감분석 › 연구소별 증감 요약)의 한 칸이다. 환차外를 인건비 / 인건비外로 한 번 더
    가르는 이유: 그 표가 "인건비가 늘어서인가, 그 외가 늘어서인가"를 묻는 표이기 때문이다.
    인건비 분해(인원 효과 · 단가 효과)는 그 칸을 우클릭해 펼칠 때 쓰고, 인력이 없는 연구소는
    나눌 수 없어 unsplit에 남는다(services/labor_split.py)."""

    base: float = 0 # 비교 기준 금액
    cur: float = 0 # 지금 보고 있는 버전 금액
    diff: float = 0 # cur - base
    fx: float = 0 # 환차 계 = fx_local + fx_usd
    fx_local: float = 0 # 환차 현지→$ (USD 연구소는 0)
    fx_usd: float = 0 # 환차 $→원
    fx_outside: float = 0 # 환차外 계 = labor_outside + non_labor_outside
    labor_outside: float = 0 # 환차外 · 인건비
    non_labor_outside: float = 0 # 환차外 · 인건비外
    # 인건비 분해 — 경영계획 대비처럼 환율 기준이 없어 분해할 수 없으면 전부 0이고 splittable=false다
    # (0을 "분해했더니 0"으로 읽지 않도록 플래그를 같이 준다).
    splittable: bool = False
    hc_base: float = 0
    hc_cur: float = 0
    per_head_base: float = 0
    per_head_cur: float = 0
    hc_effect: float = 0
    rate_effect: float = 0
    unsplit: float = 0


class VarianceLabRow(BaseModel):
    """연구소 한 줄(= 표의 한 행). cells의 키는 기간 코드(y · q1~q4 · m1~m12)다."""

    lab_code: str
    lab_name: str
    is_total: bool = False # 맨 윗줄 "전체 연구소" 합계행
    cells: dict[str, VarianceLabCell]
    # 증감사유 칸에 처음 들어가 있는 [분석 초안] 블록(연간 기준). 위 cells를 만든 값 그대로 쓴
    # 문장이라 표 숫자와 어긋나지 않는다 — 문구 규칙은 services/reason_draft.py에 있다.
    # 합계행(is_total)은 빈 문자열이다: 화면의 그 줄에는 사유 칸이 없다.
    reason_draft: str = ""


class VarianceLabsResult(BaseModel):
    """증감분석 **연구소별** 요약 표 — 17기간 × 연구소. 계정별 표(VarianceTable)의 짝이다.

    왜 따로 두나: 계정별 표는 "한 연구소(또는 전체)의 계정들"이고 이 표는 "한 계정 묶음(개발비
    매출)의 연구소들"이라, 축이 반대다. 화면이 두 축을 모두 서버에서 받아야 위·아래 표가 같은
    숫자를 보여준다 — 이 표만 브라우저가 계산하던 동안 두 표가 25억까지 벌어졌다.
    """

    basis_year: int
    basis_month: int
    kind: str
    stage: str = ""
    base_version: str
    base_label: str
    plan_no: str = ""
    labs: list[VarianceLabRow]
    fallback_note: str = ""
    note: str = ""


class VarianceExtraCell(BaseModel):
    """추가 행 한 칸(기간 하나)의 증감. 뜻과 계산식은 고정 계정 행(VarianceRow)의 금액 칸과 같다 —
    추가 행은 인력·비율 같은 단위 구분이 없어 항상 금액(원)이다."""

    base: float = 0
    cur: float = 0
    diff: float = 0
    fx: float = 0
    fx_local: float = 0
    fx_usd: float = 0
    fx_outside: float = 0


class VarianceExtraRow(BaseModel):
    """연구소가 직접 추가한 행 한 줄의 증감(17기간).

    고정 45행에는 이 행의 이름이 없다 — 금액은 anchor_key로 지정한 계정 합계에 녹아 들어간다.
    그래서 당월에만 생긴 행이 있어도 표에서는 "기타경비가 늘었다"까지만 보이고 **무엇이 늘었는지**가
    안 보인다. 이 목록이 그 줄을 이름째로 준다: 비교 기준에 없던 행은 base가 0이고 증감이 전액이다.

    **줄은 (얹힌 계정, 이름)으로 묶는다.** 집계표 ① 전체 계정 탭과 같은 규칙이다(core.js의
    byLabel) — 한 연구소가 '클라우드 사용료'를 추가하면 연구소마다 자기 줄이 따로 서는 게 아니라
    어느 범위로 보든 같은 한 줄이고, 그 줄에 더해지는 금액만 범위에 따라 달라진다. 그래서 행 목록은
    **전체 연구소**에서 모으고(그 연구소에 없으면 0원 줄로 선다), 금액만 조회 범위로 더한다.
    이름이 같아도 얹힌 계정이 다르면 다른 줄이다 — 그래야 각 줄이 자기 계정 합계와 맞는다.

    기간별로 쪼개지 않고 한 줄이 17칸을 들고 있는 이유: 줄의 정체(이름 · 얹힌 계정)는 기간과
    무관한데, 기간으로 쪼개면 같은 정체를 17번 되풀이해 보내게 된다.
    """

    label: str
    label_en: str = ""
    # 얹힌 계정 키와 그 이름. 합산 제외 행("__none__")은 어느 계정 합계에도 안 들어간다.
    anchor_key: str = ""
    anchor_label: str = ""
    # 얹힌 계정 합계(= 고정 45행)에 이 금액이 들어가 있는가. False면 표의 어느 줄에도 없는 행이라
    # 받는 쪽이 "이 줄을 더하면 합계가 두 번 세어지는가"를 판단할 수 있다.
    in_total: bool = True
    # 이 줄에 실제로 값이 있는 연구소 pk들(조회 범위 안에서). 빈 목록이면 이 범위에서는 0원 줄이다 —
    # 다른 연구소가 추가해서 목록에만 서 있는 줄인지, 이 연구소가 추가한 줄인지 가를 수 있다.
    labs: list[str] = []
    # 합쳐진 원본 행 키들(연구소마다 row_key가 다르다 — 'ux_cloudfee' · 'cloud_fee_dsra').
    row_keys: list[str] = []
    cells: dict[str, VarianceExtraCell] = {}


class VarianceTable(BaseModel):
    """증감분석 화면 표 한 장 전체 — 17기간 × 계정. periods의 키는 조회 파라미터와 같은 기간 코드
    (y · q1~q4 · m1~m12)이고, 값은 그 기간의 계정 행 목록이다.

    기간별 행을 따로 주는 이유: 월 금액을 더해 분기를 만들 수 없는 행이 있다 — 인력은 평균이고
    비율은 분자·분모를 각각 더한 뒤 나눠야 한다. 화면이 월 값을 접어 분기를 만들면 그 두 행이
    틀어진다(집계표에서 실제로 그랬다, to_17 참조).
    """

    basis_year: int
    basis_month: int
    kind: str
    stage: str = ""
    base_version: str
    base_label: str
    lab_scope: str
    plan_no: str = ""
    periods: dict[str, list[VarianceRow]]
    # 기간별 인건비 분해(인원 효과 · 단가 효과). 화면이 인건비 행의 ▸를 누르면 보여주는 줄들이다.
    # 같은 응답에 담는 이유: 기간마다 따로 부르면 17번, 연구소까지 더하면 수십 번이 된다.
    # 경영계획 대비는 환율 기준이 없어 분해할 수 없으므로 빈 dict가 온다(화면도 같은 제약).
    labor_split: dict[str, LaborSplitTotals] = {}
    # 연구소가 직접 추가한 행들(이름 있는 행). 고정 45행에는 이름이 없고 얹힌 계정에 녹아 있어서,
    # 그 줄을 따로 보여줘야 "당월에만 생긴 행 때문에 늘었다"가 드러난다.
    extras: list[VarianceExtraRow] = []
    fallback_note: str = ""
    note: str = ""


# ---------- 증감사유 (회신 원문 → 번역 + AI 초안 → 수정 후 저장) ----------
class VarianceReasonKey(BaseModel):
    """사유 한 칸의 자리. 화면의 "비교 기준 | 기간 | 연구소" 선택 + 계정 한 줄에 대응한다."""

    basis_year: int
    basis_month: int
    kind: str = "forecast"  # forecast | actual
    stage: str = ""  # 실적일 때 first(1차) | second(2차). 속보는 빈 문자열
    base_version: str  # prev | prior | plan
    period: str  # y | q1~q4 | m1~m12
    lab_scope: str = "ALL"
    category_key: str


class VarianceReasonOut(VarianceReasonKey):
    """사유 한 칸의 상태 전부. 화면은 source_text(회신 원문)와 source_ko(번역)를 읽기 전용으로 보여주고,
    ai_draft를 [AI 초안 넣기]로 편집칸에 복사하고, 담당자가 고친 결과를 reason_text로 저장한다."""

    source_text: str = ""
    source_ko: str = ""
    ai_draft: str = ""
    reason_text: str = ""
    saved_diff: float | None = None
    needs_recheck: bool = False
    saved_at: str = ""
    # 지금 증감액(원). saved_diff와 다르면 재업로드로 금액이 바뀐 것이다 — 화면이 두 값을 같이 보여
    # 준다("저장 시점 ▲12억 → 지금 ▲19억").
    current_diff: float | None = None


class VarianceReasonIn(VarianceReasonKey):
    """[저장] 또는 초안 보관. reason_text를 보내면 그 문장으로 저장하고, 저장 시점 증감액(current_diff)을
    같이 받아 스냅샷으로 남긴다 — 그래야 다음 업로드에서 금액이 바뀐 걸 서버가 알아챈다.

    current_diff를 생략하면 서버가 직접 계산한다(화면이 안 보냈을 때의 안전장치)."""

    source_text: str | None = None
    source_ko: str | None = None
    ai_draft: str | None = None
    reason_text: str | None = None
    current_diff: float | None = None
    # True면 [저장]을 누른 것(saved_at·saved_diff를 갱신하고 재확인 표시를 지운다).
    # False면 초안만 보관한다(원문·번역·AI 초안만 채울 때).
    commit: bool = True


class VarianceReasonDraftIn(VarianceReasonKey):
    """회신 원문을 번역하고 보고용 사유 초안까지 만들어 달라는 요청.

    증감 금액을 같이 받는 이유: "채용 증가"라는 원문만으로는 보고용 문장이 안 된다 — "신규 채용 3명에
    따른 인건비 1.2억원 증가"처럼 숫자가 붙어야 보고서에 실린다. 그 숫자는 화면이 이미 계산해서
    보여주고 있으니 그대로 받아 쓴다(서버가 다시 계산하면 화면과 어긋날 수 있다)."""

    source_text: str
    diff: float | None = None  # 증감액(원)
    diff_pct: float | None = None  # 증감률 %
    provider: str | None = None
    save: bool = True  # True면 번역·초안을 그 칸에 보관한다(확정 사유는 건드리지 않는다)


class VarianceReasonDraftResult(BaseModel):
    source_text: str
    source_ko: str = ""
    ai_draft: str = ""
    engine: str = ""
    error: str = ""  # 번역·초안 생성이 실패해도 200으로 돌려주고 이유를 담는다(화면은 원문으로 그려진다)


class VarianceReasonGap(BaseModel):
    """"증감률이 큰데 사유가 없는 칸" 하나. 확정 직전 경고에 쓴다."""

    category_key: str
    label: str
    diff: float
    diff_pct: float
    reason_text: str = ""
    needs_recheck: bool = False


class VarianceReasonCheck(BaseModel):
    """확정 경고 — 증감률이 기준치를 넘었는데 사유가 비어 있거나 재확인이 필요한 칸 목록.

    확정을 서버가 막지는 않는다(공통 RULE: 미업로드가 있어도 이동·확정을 막지 않고 밝힌다). 대신
    무엇을 알고도 확정하는지 화면이 보여줄 수 있게 목록과 문장을 돌려준다."""

    threshold_pct: float
    # 적용한 금액 기준(원). 0이면 금액으로는 걸러내지 않았다는 뜻 — 화면·챗봇이 어떤 기준의
    # 결과인지 밝힐 수 있어야 한다("사유 다 썼다"를 전체 기준으로 읽으면 안 된다).
    threshold_amount: float = 0.0
    missing: list[VarianceReasonGap] = []  # 사유가 비어 있는 칸
    stale: list[VarianceReasonGap] = []  # 사유는 있지만 금액이 바뀌어 재확인이 필요한 칸
    note: str = ""


# ---------- 증감 동인 표 (AI) ----------
class VarianceDriverFactor(BaseModel):
    """동인 한 줄. 금액이 아니라 계산식을 담는다 — 원본이 업로드로 바뀌면 같이 바뀌어야 한다."""

    label: str
    op: str = "ref"  # ref(그 항목 그대로) | div_elementwise(월별 나누기) | sub(차) | sum(합)
    operands: list[str] = []  # 비용항목 키(labor · hc ...)
    unit: str = "krw"  # krw | usd | hc | pct


class VarianceDriverIn(BaseModel):
    basis_year: int
    basis_month: int
    kind: str = "forecast"
    lab_scope: str = "ALL"
    driver_key: str
    title: str
    target_key: str
    factors: list[VarianceDriverFactor]
    requested_text: str = ""
    # "다음 버전에도?"의 답. True로 저장하면 이후 버전 조회에도 이 표가 따라온다.
    carry_over: bool = False


class VarianceDriverRow(BaseModel):
    """동인 한 줄의 계산 결과(기준 · 당월 · 증감). 근거를 같이 보여주려고 재료 값도 담는다."""

    label: str
    unit: str = "krw"
    base: float | None = None
    cur: float | None = None
    diff: float | None = None
    diff_pct: float | None = None
    basis_text: str = ""  # "인건비 1,000 ÷ 인력 10 = 100" — 챗봇·화면이 근거로 그대로 보여준다


class VarianceDriverOut(VarianceDriverIn):
    rows: list[VarianceDriverRow] = []
    # 이 표가 지금 보고 있는 버전에서 만들어진 것인지(False면 이전 버전에서 carry_over로 따라온 것).
    # 화면은 따라온 표에 "이전 버전에서 유지" 뱃지를 붙이고, 지울 때 원래 버전을 알려 준다.
    created_here: bool = True
    created_basis: str = ""  # 이 표를 처음 붙인 버전 "26.09 속보"
    created_at: str = ""


# ---------- 실적 1차/2차 분석 ----------
class ActualFirstRow(BaseModel):
    """1차(ERP 약식) 연구소 한 줄. 현지통화 총액 → $ → 원화로 환산한 값과 환차 분해까지 담는다.

    1차에는 계정 상세도 외주용역도 없다 — ERP 약식이 연구소 총액만 주기 때문이다. 그래서 이 한 줄이
    1차 분석의 전부다."""

    lab_code: str
    lab_name: str = ""
    currency: str = "USD"
    local_amount: float = 0  # 현지통화 총액 (그 달)
    usd_amount: float = 0 # $ 환산($ — 저장 단위와 같다)
    krw_amount: float = 0  # 원화 환산 (원)
    base_krw: float = 0  # 비교 기준 금액 (원)
    diff: float = 0
    diff_pct: float | None = None
    fx: float = 0
    fx_local: float = 0  # 현지→$ (USD 연구소는 0)
    fx_usd: float = 0  # $→원
    fx_outside: float = 0
    reason_text: str = ""
    # ERP가 $ 환산액을 같이 줬는지. False면 usd_amount는 서버가 환율로 계산한 값이다.
    usd_from_source: bool = False


class ActualFirstIn(BaseModel):
    """1차 값·사유 저장. [1차 고정] 상태면 서버가 409로 거부한다 — 보고까지 끝난 값이라서다."""

    basis_year: int
    basis_month: int
    lab_code: str
    local_amount: float | None = None
    usd_amount: float | None = None
    reason_text: str | None = None
    source_file: str = ""


class ActualStageOut(BaseModel):
    """실적 한 달의 1차/2차 상태. 화면이 1차 탭을 열 수 있는지, 수정할 수 있는지를 이걸로 판단한다."""

    basis_year: int
    basis_month: int
    # 실적결산 1차가 끝났는지. False면 화면의 [1차 분석] 탭을 비활성으로 둔다(가져올 ERP 값이 없다).
    close_first_done: bool = False
    first_locked: bool = False
    first_locked_at: str = ""
    # 1차에 저장된 연구소 수 / 전체 연구소 수 — "12개 중 9개 입력" 안내용.
    first_filled: int = 0
    lab_count: int = 0
    note: str = ""


class ActualStageIn(BaseModel):
    basis_year: int
    basis_month: int
    close_first_done: bool | None = None
    first_locked: bool | None = None


class ActualFirstResult(BaseModel):
    """1차 분석 표 한 장. 합계는 연구소 줄을 더한 값이다(1차에는 '원화 기타'가 없다 — 그건 2차에서
    더하는 값이라 1차 보고 범위에 들어가지 않는다)."""

    basis_year: int
    basis_month: int
    base_version: str
    base_label: str
    stage: ActualStageOut
    rows: list[ActualFirstRow]
    total: ActualFirstRow
    fallback_note: str = ""
    note: str = ""


class ActualStageShift(BaseModel):
    """2차 화면 맨 위의 "1차 → 2차 변동" 한 줄(연구소별 + 합계).

    1차는 ERP 약식, 2차는 PL이라 금액이 달라지는 게 정상이다. 그 차이를 2차 화면에서 먼저 보여주지
    않으면, 1차로 이미 보고한 숫자와 2차 숫자가 왜 다른지 설명할 수 없다."""

    lab_code: str
    lab_name: str = ""
    first_krw: float = 0  # 1차 원화 환산
    second_krw: float = 0  # 2차(PL) 원화 환산
    diff: float = 0
    diff_pct: float | None = None
    first_reason: str = ""


class ActualStageShiftResult(BaseModel):
    basis_year: int
    basis_month: int
    rows: list[ActualStageShift]
    total: ActualStageShift
    # 1차가 없으면(실적결산 1차 미완료) 비교할 대상이 없다 — 화면은 이 경우 변동 줄을 숨긴다.
    has_first: bool = True
    note: str = ""


# ---------- 집계 화면: CP 계약 · 확정 사전점검 · 인건비율 ----------
class AggregateRow(BaseModel):
    """집계표 한 줄 × 17칸(월 12 + 분기 4 + 연간).

    values의 None은 미등록(빈칸)이고 0.0은 "0원으로 올라왔다"는 뜻이다 — 화면이 둘을 다르게
    그려야 한다(공통 RULE: 빈칸 처리).
    """

    key: str
    label: str
    label_en: str = ""
    level: int = 0  # 손익계산서형 들여쓰기 단계
    format: str = "krw" # krw(금액) | usd($ — 화면이 K$로 나눠 보여준다) | hc(명) | pct(%)
    memo: bool = False  # 참고 행(IT 관련 비용·외주용역비) — 개발비 합계에 들어가지 않는다
    values: list[float | None]


class AggregateDerivedCol(BaseModel):
    """집계 ① 표의 파생 열(계산 열) 하나. 화면에서 [+ 열 추가]로 만든 "3분기 합계" 같은 열이다.

    칸 값은 저장하지 않는다 — 계산식만 저장하고 볼 때마다 계산한다. 그 계산을
    서버가 한다. values의 키는 행을 가리키고 세 종류가 있다:
    - "k:<계정키>" 고정 45행 (예: "k:sal")
    - "l:<라벨>" 취합에서 연구소가 추가한 원시 행 — 집계표에서는 **라벨로 합쳐진** 한 줄이다
    - "d:<행키>" 집계에서 추가한 파생 행
    값이 null인 칸은 "그 행은 이 열로 묶을 수 없다"는 뜻이다(비율·환율 행) — 0이 아니다.
    """

    label: str
    op: str # sum_months | avg_months
    months: list[int] # 1~12
    values: dict[str, float | None]


class AggregateTable(BaseModel):
    """집계 화면 표 한 장. 캡션·안내 문장까지 서버가 만들어 준다 — 화면과 챗봇이 같은 설명을
    쓰게 하려는 것이다(/overseas/summary의 coverage_note와 같은 방식)."""

    basis_year: int
    basis_month: int
    version: str
    # 어느 버전의 표인가 — 요청한 기준연월·종류를 되돌려준다. 화면이 과거 버전을 띄울 때
    # "지금 받은 게 그 버전 맞나"를 확인할 자리가 필요하다(연도만으로는 구분이 안 된다).
    kind: str = "forecast"  # forecast(속보) | actual(실적) | plan(경영계획)
    plan_no: str = ""  # kind=plan 또는 version=plan일 때의 차수("P02")
    version_label: str = ""  # "26.08 실적" · "2026년 경영계획 P02"
    mode: str  # usd | krw
    caption: str
    note: str
    rows: list[AggregateRow]
    # 화면에서 [+ 열 추가]로 만든 파생 열의 값. 열이 없으면 빈 목록이다.
    derived_cols: list[AggregateDerivedCol] = []
    # 화면에서 [+ 행 추가]로 만든 파생 행(계산 행). **이 표의 단위로 계산해서** 준다 — 원화 표면
    # 재료를 환산한 뒤 계산식을 평가한 값이라 금액 행은 원화고 비율 행은 그대로다. 45행 사이
    # 어디에 끼워 넣을지(anchor_key)는 화면이 정하므로 rows와 따로 보낸다.
    derived_rows: list[ExtraRowOut] = []


class LabTableRow(BaseModel):
    """집계 ②③④ 탭의 한 줄 × 17칸. ①(AggregateRow)과 달리 계정이 아니라 **연구소** 줄이라
    category key가 없고, 대신 표시 규칙(합계줄·굵게·참고줄)을 서버가 같이 내려준다 — 행 구성이
    표마다 다르기 때문이다(③ 달러 표만 "CP·SRCX 제외" 줄이 없고, ②·④만 SARC 하위 줄이 있다).

    values의 None은 미등록(빈칸), 0.0은 "0으로 올라왔다"다(공통 RULE: 빈칸 처리).
    """

    label: str
    # 연구소 줄의 식별자(연구소 코드, 하위 파트 줄은 pk). 합계·소계·환율·인당 줄은 빈 문자열이다.
    # 화면이 같은 표를 두 버전(당월 · 전실행) 받아 짝지을 때 쓴다 — 라벨은 표마다 달라서
    # 짝짓기에 쓸 수 없다(② 달러 표 "SRCX(APJT포함)" vs 원화 표 "SRCX").
    key: str = ""
    level: int = 0  # 들여쓰기 단계(0 = 합계·소계, 1 = 연구소, 2 = 연구소 하위 파트)
    format: str = "krw" # krw(원) | usd($ — 화면이 K$로 나눠 보여준다) | rate(원/$)
    total: bool = False  # 표 맨 위 합계줄
    bold: bool = False  # 소계줄(예: 개발비(A,CP제외))
    muted: bool = False  # 참고줄(하위 파트 · 적용 환율)
    values: list[float | None]


class LabTable(BaseModel):
    """집계 화면 연구소별 표 한 장(탭 ①②③)."""

    caption: str
    note: str
    rows: list[LabTableRow]


class LabTablesOut(BaseModel):
    """집계 화면 탭 ①②③의 표를 한 번에. 탭을 누를 때마다 따로 받지 않는 이유: 비싼 건 표 조립이
    아니라 화면 상태를 읽는 일이고 그건 모든 표가 같이 쓴다 — 열 번 부르면 같은 읽기를 열 번 한다.

    tables의 키(화면도 같은 키로 표를 집는다):
    ① 개발비 — t2u · t2k(총계 달러·원화) · t5u · t5(인건비) · t6u · t6(인건비외) · CP 분기 정산
    ② 개발비(CP_PL) — t7u · t7k
    ③ 인건비(CP_PL) — t3 · t4
    환율이 없으면 원화 표가, CP 계약이 없으면 ① 전체가 빠진다 — 재료 없이 만든 0원 표를
    내려보내지 않으려는 것이다(④ 전체 계정은 이 응답이 아니라 AggregateTable이다).
    """

    basis_year: int
    basis_month: int
    version: str
    kind: str = "forecast"  # ①(AggregateTable)과 같은 뜻 — 어느 버전의 표인가
    plan_no: str = ""
    version_label: str = ""
    tables: dict[str, LabTable]


class CpContractPoc(BaseModel):
    """CP 계약의 POC 한 건(직급 행을 접은 값). 금액 단위는 저장값과 같은 $다."""

    poc_type: str = ""
    poc_proj: str = ""
    total: float = 0  # POC Total (연 계약 총액)
    cost_sum: float = 0  # Cost Sum POC (그중 인건비)


class CpContractOut(BaseModel):
    """그 해 CP 계약(기준정보 › CP 계약) 요약 — 집계 ④ CP 보정 탭이 이 금액으로 분기 정산한다.

    registered=False는 "계약 금액이 0원"이 아니라 "아직 안 올라왔다"는 뜻이다. 화면은 그때 CP 정산
    칸을 0이 아니라 빈칸으로 두고 알린다(공통 RULE: CP 계약 미등록이면 CP 정산 칸 빈칸 + 알림) —
    0으로 채우면 "CP는 계약이 없다"가 아니라 "CP 비용이 0원"으로 읽혀서 총액을 잘못 말하게 된다.

    quarter_*는 연 금액 ÷ 4다(분기 정산 계약이라 3 · 6 · 9 · 12월에 한 번씩 발생한다)."""

    year: int
    registered: bool
    poc_count: int = 0
    year_total: float = 0  # 연 계약 총액($)
    year_labor: float = 0  # 그중 인건비($)
    quarter_total: float = 0  # 분기 하나치($)
    quarter_labor: float = 0
    source_file: str = ""
    uploaded_at: str = ""
    pocs: list[CpContractPoc] = []
    note: str = ""


class CpContractAmountIn(BaseModel):
    """CP 계약 "금액만" 바꾸는 요청(PUT /overseas/cp-contract-amount).

    업로드(PUT /overseas/cp-rows)는 파일 하나로 표 전체를 갈아끼우는 경로고, 이건 "2026 계약액을
    5.2M$로 바꿔줘"처럼 **금액 하나만** 고치는 경로다. 직급 행·인원·맨먼스는 그대로 두고 금액
    항목만 같은 비율로 움직인다(services/cp.rescale_rows).

    금액 단위는 저장값과 같은 **$**다 — 5.2M$는 5200000이다. 집계 표가 K$로 보여주는 건 화면이
    1000으로 나누기 때문이고, 여기 들어오는 값은 계약서 금액 그대로다.

    두 가지 중 하나만 쓴다:
    - year_total: 그 해 연 계약 총액을 이 값으로($). POC가 여러 개면 **지금 비중대로** 나눠 가진다.
    - poc_proj(+poc_type) + poc_total: POC 하나의 계약액만 이 값으로. 나머지 POC는 그대로.

    둘 다 주거나 둘 다 안 주면 거부한다 — "연 합계를 바꾸려던 건지 POC 하나를 바꾸려던 건지"는
    금액이 몇 배씩 달라지는 차이라, 서버가 짐작하면 안 된다.
    """

    year: int
    year_total: float | None = None
    poc_type: str | None = None
    poc_proj: str | None = None
    poc_total: float | None = None
    # 연구소 기준정보·CP 업로드와 같은 규칙 — 안 주면 지금 편성 중인 기준월부터 적용되고,
    # 그 달보다 이전 버전은 바뀌기 전 계약으로 분기 정산액이 계속 계산된다.
    effective_from: str | None = None
    state_token: str = ""


class CpContractChangeOut(BaseModel):
    """계약액 변경 결과 — 전·후를 같이 돌려준다.

    왜 after만 주지 않나: 이 변경의 핵심은 "얼마가 얼마로 바뀌고, 분기 정산액이 어떻게 되는가"다.
    after만 주면 부르는 쪽(챗봇·화면)이 before를 따로 조회해 둬야 하고, 그걸 빼먹으면 사용자는
    바뀐 숫자만 보게 된다."""

    before: CpContractOut
    after: CpContractOut
    effective_from: str  # 이 버전부터 새 계약이 적용된다
    note: str  # 사람이 그대로 읽어도 되는 전후 요약


class OverseasConfirmCheck(BaseModel):
    """[계정 확정]을 눌러도 되는지 — 서버가 판정한 결과.

    blocking이 비어 있으면 확정할 수 있다. 지금 막는 것은 "환율 미등록 월"뿐이다 — 환율이 없는 달은
    원화 환산 자체가 안 돼서 확정해도 그 달 숫자가 없다(공통 RULE: 환율 미등록 월 있으면 확정 불가).

    warnings는 "알고도 확정할 수 있는" 것들이다(미업로드 연구소 · 증감사유 미작성 · 금액이 바뀌어
    재확인이 필요한 사유 · CP 계약 미등록). 서버가 막지 않고 밝히기만 하는 이유는 공통 RULE이
    그렇게 정하기 때문이다 — 대신 확정할 때 note("미업로드 2개 포함 확정")를 같이 남겨서, 나중에
    그 총액을 연구소 전체 합계로 오해하지 않게 한다."""

    basis_year: int
    basis_month: int
    kind: str  # forecast(속보 편성) | actual(실적 시뮬레이션)
    can_confirm: bool
    blocking: list[str] = []
    warnings: list[str] = []
    # 확정 시 version_account_status.note에 그대로 넣을 문장. 경고가 없으면 빈 문자열이다.
    note: str = ""
    fx_missing_months: list[int] = []  # 적용 환율(원/$)이 없는 달
    missing_labs: list[str] = []  # 아직 안 올라온 연구소 이름
    lab_count: int = 0
    uploaded: int = 0
    reason_missing: int = 0  # 증감률이 기준치를 넘었는데 사유가 빈 항목 수
    reason_stale: int = 0  # 사유 저장 뒤 금액이 바뀌어 재확인이 필요한 항목 수
    cp_contract_registered: bool = True


class OverseasLaborRatioRow(BaseModel):
    """인건비율 한 줄(전체 또는 연구소). ratio가 null이면 분모(개발비)가 0이거나 아직 안 올라와서
    비율을 낼 수 없다는 뜻이다 — 화면은 0%가 아니라 '—'로 적는다."""

    lab_code: str  # "ALL"이면 전체 연구소 합계
    name: str
    total: float  # 개발비(매출), 원
    labor: float  # 인건비, 원
    ratio: float | None = None  # 인건비 ÷ 개발비 × 100


class OverseasLaborRatio(BaseModel):
    """인건비율 = 인건비 ÷ 개발비(매출). 집계 화면의 인건비율 팝업과 같은 값이다.

    분기 · 연간은 월별 비율을 더하거나 평균 내지 않고 "합계끼리" 나눈다(공통 RULE) — 달마다 금액이
    다르면 월별 비율의 평균은 실제 비율과 어긋난다. 그래서 period를 받아 그 기간 합계로 계산한다.
    기준은 원화 · CP 분기 정산 반영(히어로 카드 구성 비율과 같은 기준)이다."""

    basis_year: int
    basis_month: int
    period: str  # y(연간) | q1~q4 | m1~m12
    version: str
    kind: str = "forecast"  # 집계표(AggregateTable)와 같은 뜻 — 읽은 버전의 종류
    plan_no: str = ""
    version_label: str = ""  # "26.08 실적"
    rows: list[OverseasLaborRatioRow]
    note: str = ""


class AnomalyRuleOut(BaseModel):
    """이상 감지 기준 1건. 쓰지 않는 파라미터는 null이다(급변은 pct+amount, 합계 불일치는 amount …).
    desc는 지금 값이 들어간 설명 문장이라 화면·챗봇이 그대로 읽어 쓸 수 있다."""

    rule_key: str
    label: str
    enabled: bool = True
    pct: float | None = None
    amount: float | None = None # $(저장 단위)
    ratio: float | None = None
    desc: str = ""


class AnomalyRuleIn(BaseModel):
    """기준 변경. 보낸 항목만 바뀐다(pct만 보내면 amount는 그대로) — "50% 넘는것만 알려줘"는
    비율만 바꾸는 말이고, 그때 금액 하한까지 초기화되면 경고 범위가 엉뚱하게 달라진다.
    reset=true면 그 기준을 기본값으로 되돌린다("기준 원래대로")."""

    rule_key: str
    enabled: bool | None = None
    pct: float | None = None
    amount: float | None = None
    ratio: float | None = None
    reset: bool = False
    source: str = "screen"  # chat | screen | reset — 이력에 남는다
    note: str = ""  # 무슨 말로 바꿨는지(챗봇 요청 문장)


class AnomalyRuleLogOut(BaseModel):
    rule_key: str
    before: dict | None = None
    after: dict | None = None
    source: str = ""
    note: str = ""
    changed_at: datetime


class OutsRowIn(BaseModel):
    """외주용역 목록 한 줄 — 기간 합 대조에만 쓴다(저장 경로와 무관)."""

    label: str = ""
    months: list[float | None] = []
    periods: list[float | None] = []


class AnomalyFinding(BaseModel):
    """이상 감지 1건. category_key·month로 화면이 어느 칸을 표시할지 정한다(month=null이면 행 전체)."""

    rule: str
    rule_label: str
    category_key: str
    category_label: str
    month: int | None = None
    # 칸이 여럿인 기준(빈칸 등)은 months로 묶어서 돌려준다 — 계정당 한 줄로 보여주고 칸은 전부
    # 표시한다. month·months 모두 1~12는 달, 13~16은 1Q~4Q, 17은 연간이다(화면 17칸 표와 같은 순서).
    months: list[int] = []
    message: str
    value: float | None = None
    base: float | None = None
    rate: float | None = None
    # 외주용역처럼 계정이 아니라 행 이름으로 가리키는 경우의 그 행 이름.
    row_label: str = ""
    # 증감분석에서 어느 블록인지("y"=연간 · "m"=당월). 화면이 그 블록 칸만 표시한다.
    block: str = ""
    # 그 블록에서 문제가 있는 칸 이름("plan" · "prev" · "cur"). 블록 전체를 칠하지 않고 이 칸만
    # 표시한다 — 세 칸 중 하나가 비었는데 셋을 다 칠하면 어디를 고쳐야 하는지가 가려진다.
    fields: list[str] = []


class AnomalyScanIn(BaseModel):
    """반영 전 업로드 값을 검사한다. vals를 주면 그 값으로(=대기 중인 파일), 안 주면 이미 저장된
    값으로 검사한다([계정 확정] 전에 "남아 있는 이상"을 세는 쪽이 이 경로를 쓴다).

    base_yyyymm·base_version은 급변을 비교할 전실행이다 — 역할(전실행/전년)과 실제 버전의 매핑은
    화면이 갖고 있어서 서버가 추측하지 않고 받는다. 없으면 급변·단위(배수) 검사를 건너뛴다."""

    lab_pk: str
    version: str
    yyyymm: int
    vals: dict[str, list[float | None]] | None = None
    # 파일에 적혀 온 소계·합계 행(계정 키 → 12개월). 합계 불일치 검사에만 쓴다.
    sums: dict[str, list[float | None]] | None = None
    # 계정 키 → 증감사유 텍스트. 사유 누락 검사에만 쓴다.
    reasons: dict[str, str] | None = None
    base_yyyymm: int | None = None
    base_version: str | None = None
    # 전실행을 **연구소 단위**(같은 lab_code의 파트 합)로 읽는다. 증감분석·외주용역은 연구소가
    # 연구소 전체로 보내는 시트라(SARC = CPU+GPU 합), 파트 하나의 전실행과 비교하면 SARC는
    # 항상 어긋나 보인다. 비용 시트는 파트별로 오므로 이 값을 켜지 않는다.
    base_lab_wide: bool = False
    # 파일에 적혀 온 분기·연간 칸(계정 키 → [1Q,2Q,3Q,4Q,연간]). 월 합과 대조한다.
    # 빈칸은 null로 보낸다 — 0으로 바꿔 보내면 "0이라고 적어 보냈다"가 되어 전부 불일치로 걸린다.
    periods: dict[str, list[float | None]] | None = None
    # 외주용역 목록의 업체 행(월 12칸 + 분기·연간 칸). 계정이 아니라 행 이름으로 가리킨다.
    outs_rows: list[OutsRowIn] | None = None
    # 증감분석 시트 값(계정 키 → {"y": 연간 블록, "m": 당월 블록} → {plan, prev, cur}).
    # 업로드 파싱 결과(core.js ovParseRsn)와 같은 모양으로 받는다 — 옮겨 적으면 어긋난다.
    analysis: dict[str, dict[str, dict[str, float | None]]] | None = None
    # **같은 파일로 온 비용 시트**의 월별 값(계정 키 → 12개월). 증감분석 당월 열 대조에만 쓴다 —
    # 반영 전이라 비용 값은 아직 DB에 없고, 보려는 것도 "한 파일 안에서 두 시트가 같은 말을 하는가"다.
    # 파트가 나뉜 연구소(SARC)는 증감분석이 연구소 전체 기준이라 파트를 합쳐서 보낸다(TOTAL 제외).
    # 안 보내면 그 대조를 건너뛴다.
    cost_vals: dict[str, list[float | None]] | None = None
    # 파일에 **실제로 있던** 기간 열의 자리 키("m1".."m12" · "q1".."q4" · "y").
    # 열 누락 검사가 이걸 양식과 대조한다 — overseas_cost_headers.col_key와 같은 키 규칙이고,
    # 화면은 업로드 파싱이 읽어 둔 머리글(core.js ovParse의 hdr)의 키를 그대로 보낸다.
    # 안 보내면 그 검사를 건너뛴다(열 목록을 모르는 호출에서 "열이 다 없다"가 되면 안 된다).
    cols: list[str] | None = None
    # 당월이 몇 월인지(메시지에만 쓴다). 안 주면 "당월"로 적는다.
    basis_month: int | None = None
    # 이번 검사에만 적용할 임시 기준("이번만 50%로 봐줘") — 저장하지 않는다.
    overrides: dict[str, dict] | None = None


class AnomalyScanResult(BaseModel):
    lab_pk: str
    findings: list[AnomalyFinding]
    # 기준별 건수 — 화면이 "급변 3 · 사유 누락 1"처럼 요약을 띄운다.
    counts: dict[str, int] = {}
    rules: list[AnomalyRuleOut] = []
    # 검사하지 못한 기준과 그 이유(비교할 전실행이 없을 때 등). 빈칸으로 두면 "이상 없음"으로
    # 읽히기 때문에 못 한 검사는 분명히 돌려준다.
    skipped: list[str] = []


class LaborRatioBlock(BaseModel):
    """인건비율 팝업의 한 블록(전체 · 연구소 · 파트). 세 배열 모두 17칸이다(월 12 + 분기 4 + 연간).

    `/overseas/labor-ratio`와 **다른 표**다 — 그쪽은 원화 · CP 분기 정산 · 한 기간 한 줄이고,
    이 팝업은 달러($ 저장 단위, 화면이 K$로 나눠 보여준다) · CP는 올라온 PL 그대로 · 17칸 · 파트까지 내려간다. 팝업이 "환율과 상관
    없이 비율만 본다"는 화면이라 단위가 다르다.

    ratio는 기간 **합계끼리** 나눈 값이다 — 월별 비율의 평균으로 내면 금액이 작은 달의 비율이
    왜곡되게 끼어든다. 분모가 0이거나 미등록이면 null이고, 그때 화면은 "–"를 그린다(0%가 아니다).
    """

    key: str # ALL | 연구소 코드 | 파트 pk
    label: str
    level: int # 0 전체 · 1 연구소 · 2 파트
    labor: list[float | None]
    dev: list[float | None]
    ratio: list[float | None]


class LaborRatioTable(BaseModel):
    """집계 화면 인건비율 팝업 전체. 블록 순서가 그대로 화면 줄 순서다."""

    basis_year: int
    basis_month: int
    version: str
    kind: str = "forecast"
    plan_no: str = ""
    version_label: str = ""
    unit: str = "K$" # 화면에 쓰는 표시 단위 이름. 값 자체는 $다(화면이 1000으로 나눈다)
    blocks: list[LaborRatioBlock]
    note: str = ""


class LabMonthlyRow(BaseModel):
    """연구소 **파트** 한 줄의 월별 원화. 마감 전표 매핑 단위가 파트라서 연구소 묶음이 아니다."""

    pk: str
    lab_code: str
    label: str
    months: list[float | None] # 12칸, 원


class LabMonthlyOut(BaseModel):
    """마감 2단계가 쓰는 연구소별 월별 원화 금액(개발비(매출) · CP 분기 보정 반영).

    왜 따로 있나: 마감 화면은 "그 달 전표를 어느 연구소에 붙일까"의 타깃 금액과, 비교 기준이 되는
    12개월 금액이 필요하다. 집계표(②)는 연구소 묶음 단위라 파트가 없고, 히어로(summary)는 합계
    하나뿐이다. 둘 다 이 화면이 쓸 모양이 아니라서 pk 단위로 따로 내려준다.
    """

    basis_year: int
    basis_month: int
    version: str
    kind: str = "forecast"
    plan_no: str = ""
    version_label: str = ""
    unit: str = "원"
    rows: list[LabMonthlyRow]
    total: list[float | None] # 12칸, 원 — 연구소 전체 합계
    note: str = ""
