"""실적결산 · 전표매핑 스키마.

화면이 쓰는 단위로 묶었다 — 전표 목록(1단계), 연구소별 매핑 작업대(2단계), 계정별 결산 현황(3단계).
특히 2단계는 "연구소별 표 + 전표별 추천"을 한 번에 내려준다: 화면이 두 번 호출해서 합치면 전표
목록과 연구소 합계가 서로 다른 시점의 값이 되어, 합계는 맞는데 전표를 세면 안 맞는 표가 나온다.
"""

from pydantic import BaseModel


class VoucherLine(BaseModel):
    """ERP 전표 한 줄. 받은 모양 그대로 — krw_amount는 원본 금액이고 매핑으로 바뀌지 않는다."""

    voucher_no: str
    line_no: int = 1
    posted_on: str = ""  # "YYYY-MM-DD", ERP가 안 주면 빈 문자열
    account_code: str = ""
    account_name: str = ""
    cost_center: str = ""
    cost_center_name: str = ""
    project_code: str = ""
    vendor: str = ""
    text: str = ""
    currency: str = "KRW"
    fc_amount: float = 0
    krw_amount: float = 0
    ref_no: str = ""


class VoucherOut(VoucherLine):
    """전표 + 분류 결과. account_key가 빈 문자열이면 미분류(기준정보에 없는 조합)다."""

    account_key: str = ""
    classify_by: str = ""
    classify_note: str = ""
    source_file: str = ""


class VoucherIngestIn(BaseModel):
    """전표 적재. 같은 (연월·전표번호·라인)이 다시 오면 전표는 덮어쓰고 매핑은 남긴다
    (공통 RULE: 전표 재수신 시 기존 매핑 유지, 신규만 매칭)."""

    basis_year: int
    basis_month: int
    source_file: str = ""
    # True면 이 연월의 기존 전표 중 이번에 안 온 것을 지운다(전기 취소분 반영). 매핑은 지우지 않는다.
    replace: bool = True
    rows: list[VoucherLine]


class AccountSummaryRow(BaseModel):
    """1단계 계정별 요약 한 줄. 미분류는 account_key가 빈 문자열인 행으로 내려간다."""

    account_key: str
    account_name: str = ""
    account_codes: list[str] = []
    voucher_count: int = 0
    krw_total: float = 0
    # 분류 근거가 '비용계정만'인 건수 — 부서코드 기준이 없어 확인이 필요한 전표다.
    weak_count: int = 0


class VoucherIngestResult(BaseModel):
    basis_year: int
    basis_month: int
    received: int = 0
    deleted: int = 0
    # 기존 매핑이 그대로 살아남은 전표 수 / 새로 AI 매칭한 전표 수.
    kept_maps: int = 0
    new_maps: int = 0
    unclassified: int = 0
    by_account: list[AccountSummaryRow] = []
    note: str = ""


class DeptRule(BaseModel):
    cost_center: str = ""
    account_code: str
    account_key: str
    is_shared: bool = False
    note: str = ""


class MatchRuleOut(BaseModel):
    id: int
    account_key: str
    match_field: str
    match_value: str
    scope_key: str = ""
    excluded: bool = False
    hit_count: int = 0
    note: str = ""
    created_at: str = ""


class MatchRuleIn(BaseModel):
    """규칙 등록 = "앞으로 이렇게" 승인. 승인 없이 서버가 규칙을 만들지 않는다."""

    account_key: str = "overseas"
    match_field: str  # vendor | text | cost_center | project
    match_value: str
    scope_key: str = ""
    excluded: bool = False
    hit_count: int = 0
    note: str = ""
    # True면 등록과 동시에 이 연월의 미매핑·AI 추천 전표에 규칙을 적용한다(담당자 수정은 건드리지 않는다).
    apply_basis_year: int | None = None
    apply_basis_month: int | None = None


class RuleSuggestion(BaseModel):
    """"3번 같은 수정을 하셨어요 — 앞으로 이렇게 할까요?" 한 건."""

    match_field: str
    match_value: str
    scope_key: str = ""
    scope_label: str = ""
    excluded: bool = False
    hit_count: int = 0
    question: str = ""


class VoucherMapOut(BaseModel):
    """전표 한 줄의 매핑 상태 — 화면 '최종 매칭' 칸과 상태 칩이 이걸 그대로 쓴다."""

    voucher_no: str
    line_no: int = 1
    scope_key: str = ""
    scope_label: str = ""
    decided_by: str = "ai"
    confidence: str = ""
    reason: str = ""
    excluded: bool = False
    exclude_reason: str = ""
    transferred_to: str = ""
    transferred_from: str = ""
    # 화면 상태 칩: ai(AI 자동) | rule(규칙 자동) | user(사람 수정) | check(확인 필요) |
    # unmapped(미매핑) | excluded(제외) | sent(이관 보냄) | received(이관 받음)
    status: str = "ai"


class LabMappingRow(BaseModel):
    """2단계 상단 '매핑 결과' 표 한 줄 = 연구소 하나. prev_amount는 전실행(당월 속보) 금액이다."""

    scope_key: str
    lab_code: str = ""
    label: str
    prev_amount: float = 0
    actual_amount: float = 0
    diff: float = 0
    voucher_count: int = 0


class OverseasMappingResult(BaseModel):
    """2단계 가공 · 해외연구소 작업대 한 벌. 상단 표(labs) · 전표별 추천(vouchers/maps) ·
    제외·이관 집계 · 규칙 제안까지 한 번에 내려 화면이 합산을 다시 하지 않게 한다."""

    basis_year: int
    basis_month: int
    prev_label: str = ""  # "2026년 9월 속보" 같은 전실행 라벨 — 비교 기준 이름은 서버가 만든다
    labs: list[LabMappingRow] = []
    vouchers: list[VoucherOut] = []
    maps: list[VoucherMapOut] = []
    prev_total: float = 0
    actual_total: float = 0
    unmapped_count: int = 0
    unmapped_total: float = 0
    check_count: int = 0
    excluded_count: int = 0
    excluded_total: float = 0
    transfer_in_total: float = 0
    transfer_out_total: float = 0
    suggestions: list[RuleSuggestion] = []
    mapping_done: bool = False
    first_done_at: str = ""
    # 계정 완료 상태(실적 시뮬레이션 [확정]). 해외연구소는 1차 완료와 따로 간다.
    account_done: bool = False
    note: str = ""


class MapEdit(BaseModel):
    """매핑 한 건 수정. scope_key·excluded·transferred_to 중 하나만 뜻이 있다 —
    제외는 어느 연구소에도 붙지 않고, 이관은 이 계정에서 빠진다."""

    voucher_no: str
    line_no: int = 1
    scope_key: str = ""
    excluded: bool = False
    exclude_reason: str = ""
    transferred_to: str = ""


class MapEditIn(BaseModel):
    basis_year: int
    basis_month: int
    account_key: str = "overseas"
    edits: list[MapEdit]


class MapEditResult(BaseModel):
    saved: int = 0
    # 이 저장으로 새로 생긴 규칙 제안 — 저장 직후 "앞으로 이렇게 할까요?"를 띄우려고 같이 내려준다.
    suggestions: list[RuleSuggestion] = []
    note: str = ""


class StageOut(BaseModel):
    """계정 하나의 결산 상태. 해외연구소는 mapping_done이 '1차 완료'이고 account_done(실적
    시뮬레이션 [확정])과 따로 간다 — 1차 없이도 2차를 진행할 수 있다."""

    basis_year: int
    basis_month: int
    account_key: str
    account_name: str = ""
    mapping_done: bool = False
    first_done_at: str = ""
    account_done: bool = False
    # 화면 표시 문구: "미착수" | "작성중" | "1차 완료" | "완료"
    display_status: str = "미착수"
    voucher_count: int = 0
    krw_total: float = 0
    unmapped_count: int = 0
    note: str = ""


class StageIn(BaseModel):
    basis_year: int
    basis_month: int
    account_key: str = "overseas"
    mapping_done: bool
    note: str = ""
