"""실적결산 · 전표매핑 테이블 — ERP 전표를 받아(1단계 취합) 계정별 편성 양식에 붙이는(2단계 가공)
과정의 저장소.

왜 cost_entries로 안 되는가: cost_entries는 "어느 연구소 어느 달이 얼마"라는 **결과**만 담는다.
결산은 그 결과에 이르는 **근거**가 자료다 — 어떤 전표가 어느 연구소에 붙었고, 누가 AI 추천을
뒤집었고, 무엇을 왜 제외했는지가 남아야 "이 숫자가 왜 이렇게 됐나"를 되짚을 수 있다. 그래서 전표
원본(close_vouchers)과 매핑 판단(close_voucher_maps)을 따로 둔다. 매핑이 끝나면 합계만
cost_entries로 넘어가고, 근거는 여기 남는다.

전표는 **다시 온다**. ERP가 같은 달 전표를 재전송하면(전기 취소·추가 전표) 담당자가 지난번에
손으로 고친 매핑이 날아가면 안 된다 — 그래서 매핑을 전표 행과 같은 테이블에 두지 않고
(전표번호·라인) 키로 가리키는 별도 테이블에 둔다. 전표를 지우고 다시 넣어도 매핑은 그대로
붙는다(close_voucher_maps.voucher_no/line_no는 FK가 아니라 논리 키다).

해외연구소가 특별한 점: 다른 계정은 전표의 코스트센터가 곧 편성 단위(부서)지만, 해외연구소 전표는
**과제코드 단위**로 떨어진다. 과제는 연구소가 여러 개 걸릴 수도 있고 연구소 이름이 적요에만 있을
수도 있어서, 매핑 단위(scope_key)가 연구소(overseas_labs.pk)가 된다.
"""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CloseVoucher(Base):
    """ERP 전표 한 줄(전표번호×라인). 받은 모양 그대로 담고 고치지 않는다.

    금액을 여기서 조정하지 않는 게 중요하다 — 화면에서 "저장"하는 건 매핑(어디에 붙일지)이지
    금액이 아니다. 엑셀 다운로드가 원래 금액을 내려야 ERP와 맞춰볼 수 있다(공통 RULE: 원본 보관).

    account_key는 분류 **결과**다(부서코드 + 비용계정 → 19개 계정 중 하나). 빈 문자열이면 미분류이고,
    그건 오류가 아니라 "기준정보에 아직 없는 조합"이라는 뜻이다 — 화면이 알림으로 띄우고 담당자가
    기준정보(close_dept_rules)를 고치거나 직접 지정한다.
    """

    __tablename__ = "close_vouchers"
    __table_args__ = (
        UniqueConstraint("basis_year", "basis_month", "voucher_no", "line_no", name="uq_close_vouchers_key"),
        Index("ix_close_vouchers_period_account", "basis_year", "basis_month", "account_key"),
        CheckConstraint("basis_month BETWEEN 1 AND 12", name="ck_close_vouchers_basis_month"),
        {
            "comment": "실적결산 ERP 전표 라인(연월×전표번호×라인). 받은 값은 고치지 않고, 분류 결과"
            "(account_key)만 채운다. 매핑 판단은 close_voucher_maps에 따로 둔다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    basis_year: Mapped[int] = mapped_column(SmallInteger, comment="결산 대상 연도(전기일이 아니라 결산 귀속 연도)")
    basis_month: Mapped[int] = mapped_column(SmallInteger, comment="결산 대상 월(1~12)")
    voucher_no: Mapped[str] = mapped_column(String(20), comment="ERP 전표번호")
    line_no: Mapped[int] = mapped_column(SmallInteger, default=1, comment="전표 안의 라인 번호(한 전표가 여러 줄로 온다)")
    posted_on: Mapped[date | None] = mapped_column(Date, comment="전기일. 결산 귀속월과 다를 수 있어 따로 담는다")
    account_code: Mapped[str] = mapped_column(String(20), default="", comment="ERP 비용계정 코드(53840100 등)")
    account_name: Mapped[str] = mapped_column(String(100), default="", comment="ERP 비용계정명")
    cost_center: Mapped[str] = mapped_column(String(20), default="", comment="코스트센터(부서코드)")
    cost_center_name: Mapped[str] = mapped_column(String(100), default="", comment="부서명")
    project_code: Mapped[str] = mapped_column(
        String(40), default="", comment="과제코드(WBS). 해외연구소 전표는 이 단위로 떨어져서 연구소 매핑의 주 단서다"
    )
    vendor: Mapped[str] = mapped_column(String(100), default="", comment="거래처명. AI 매칭의 가장 강한 단서")
    text: Mapped[str] = mapped_column(Text, default="", comment="적요. 연구소 이름·CP 표시가 여기만 있는 전표가 있다")
    currency: Mapped[str] = mapped_column(String(3), default="KRW", comment="거래 통화")
    fc_amount: Mapped[float] = mapped_column(Numeric(18, 2), default=0, comment="거래통화 금액(원본)")
    krw_amount: Mapped[float] = mapped_column(Numeric(18, 0), default=0, comment="원화 금액(원본). 집계·다운로드가 쓰는 값")
    ref_no: Mapped[str] = mapped_column(String(40), default="", comment="품의번호")
    account_key: Mapped[str] = mapped_column(
        String(32), default="", index=True, comment="자동 분류된 19개 계정 key(overseas 등). 빈 문자열이면 미분류"
    )
    classify_by: Mapped[str] = mapped_column(
        String(10), default="", comment="분류 근거: rule(부서코드+비용계정 기준정보) | account(비용계정만) | user(직접 지정)"
    )
    classify_note: Mapped[str] = mapped_column(String(200), default="", comment="분류 근거 설명(화면에 그대로 보여준다)")
    source_file: Mapped[str] = mapped_column(String(255), default="", comment="업로드 파일명. ERP I/F로 받으면 'ERP I/F'")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="이 전표를 받은 시각(재수신하면 갱신된다)"
    )


class CloseVoucherMap(Base):
    """전표 한 줄의 매핑 판단 — 어디에 붙일지, 제외할지, 다른 계정으로 이관할지.

    전표와 테이블을 가른 이유는 **재수신**이다. ERP가 같은 달을 다시 보내면 close_vouchers는 지우고
    다시 넣지만, 담당자가 고친 매핑은 그대로 남아야 한다(공통 RULE: 전표 재수신 시 기존 매핑 유지,
    신규만 매칭). 그래서 이 테이블은 전표 id가 아니라 (연월·전표번호·라인)을 키로 쓴다.

    decided_by가 셋인 이유: ai는 AI 1차 추천 그대로, rule은 담당자가 승인한 규칙(close_match_rules)이
    자동 적용된 것, user는 담당자가 직접 고른 것이다. AI 다시 매칭은 ai만 덮어쓰고 user는 건드리지
    않는다 — 사람이 판단한 걸 기계가 되돌리면 같은 수정을 매달 다시 해야 한다.

    excluded와 scope_key는 서로 배타적이다. 제외는 "이 전표는 해외연구소 실적이 아니다"라는 판단이라
    (NRE 공용 부서코드 중 적요에 CP가 없는 전표) 어느 연구소에도 붙지 않는다. 다만 지우지는 않는다 —
    제외분 합계를 보여줘야 전표 총액과 집계가 맞는지 확인할 수 있다.

    transferred_to는 "이 전표는 다른 계정 것"이라는 판단이다. 옮긴 쪽에는 '이관 보냄', 받은 쪽에는
    '이관 받음'으로 보여줘야 양쪽 담당자가 같은 전표를 두 번 세지 않는다.
    """

    __tablename__ = "close_voucher_maps"
    __table_args__ = (
        UniqueConstraint("basis_year", "basis_month", "voucher_no", "line_no", name="uq_close_voucher_maps_key"),
        CheckConstraint(
            "decided_by IN ('ai', 'rule', 'user')", name="ck_close_voucher_maps_decided_by"
        ),
        CheckConstraint(
            "confidence IN ('high', 'mid', 'low', '')", name="ck_close_voucher_maps_confidence"
        ),
        CheckConstraint("NOT (excluded AND scope_key <> '')", name="ck_close_voucher_maps_excluded_no_scope"),
        {
            "comment": "전표 한 줄의 매핑 판단(붙일 곳·제외·이관). 전표와 테이블을 갈라 ERP 재수신에도 "
            "담당자 수정이 살아남게 한다 — 키가 전표 id가 아니라 (연월·전표번호·라인)이다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    basis_year: Mapped[int] = mapped_column(SmallInteger, comment="결산 대상 연도")
    basis_month: Mapped[int] = mapped_column(SmallInteger, comment="결산 대상 월(1~12)")
    voucher_no: Mapped[str] = mapped_column(String(20), comment="ERP 전표번호(close_vouchers와 같은 값, FK는 아니다)")
    line_no: Mapped[int] = mapped_column(SmallInteger, default=1, comment="전표 라인 번호")
    account_key: Mapped[str] = mapped_column(String(32), default="", comment="이 매핑이 속한 계정 key(이관 후에는 받은 계정)")
    scope_key: Mapped[str] = mapped_column(
        String(50),
        default="",
        comment="붙일 곳. 해외연구소는 overseas_labs.pk(\"SARC:CPU\"), 부서별 계정은 코스트센터. 빈 문자열이면 미매핑",
    )
    decided_by: Mapped[str] = mapped_column(
        String(4), default="ai", comment="ai(AI 1차) | rule(승인된 규칙 자동 적용) | user(담당자 직접)"
    )
    confidence: Mapped[str] = mapped_column(
        String(4), default="", comment="AI 신뢰도 high|mid|low. 담당자가 고른 뒤에는 빈 문자열(신뢰도 개념이 없다)"
    )
    reason: Mapped[str] = mapped_column(String(200), default="", comment="판단 근거 한 줄(화면 'AI 추천 · 근거' 칸)")
    excluded: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="제외 여부. True면 어느 연구소에도 붙지 않지만 제외분 합계로는 보여준다"
    )
    exclude_reason: Mapped[str] = mapped_column(String(200), default="", comment="제외 사유(NRE 공용 · 적요에 CP 없음 등)")
    transferred_to: Mapped[str] = mapped_column(
        String(32), default="", comment="이관 보낸 대상 계정 key. 채워져 있으면 이 계정 집계에서 빠진다"
    )
    transferred_from: Mapped[str] = mapped_column(
        String(32), default="", comment="이관 받은 원래 계정 key. 채워져 있으면 '이관 받음'으로 표시한다"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막으로 고친 시각"
    )


class CloseMatchRule(Base):
    """담당자가 승인한 매칭 규칙 — "앞으로도 이렇게" 한 번 답하면 다음 달부터 자동 적용된다.

    왜 필요한가: 같은 거래처·같은 과제가 매달 똑같이 온다. AI가 매번 '중간' 신뢰도로 추천하고
    담당자가 매번 같은 연구소로 고치면, 그 수정은 담당자의 머릿속에만 남는다. 두 번 이상 같은 수정을
    하면 화면이 "앞으로 이렇게 할까요?"를 묻고, 승인한 것만 여기 남겨 다음부터는 rule로 자동 적용한다
    (overseas_carry_defs가 파생 행 유지를 한 번 묻고 남기는 것과 같은 방식).

    자동으로 규칙을 만들지 않는 이유: 한 달만 예외로 옮긴 전표(그 달에만 다른 연구소가 부담한 비용)도
    두 번 고치면 반복으로 보인다. 규칙이 되면 다음 달에 조용히 틀린 곳으로 붙어서, 틀렸다는 걸
    아무도 모른다.

    match_field가 여럿인 이유: 단서가 전표마다 다르다. 거래처가 분명한 전표는 vendor로, 연구소
    이름이 적요에만 있는 전표는 text로, 과제 단위로 딱 나뉘는 전표는 project로 걸어야 한다.
    """

    __tablename__ = "close_match_rules"
    __table_args__ = (
        UniqueConstraint("account_key", "match_field", "match_value", name="uq_close_match_rules_key"),
        CheckConstraint(
            "match_field IN ('vendor', 'text', 'cost_center', 'project')", name="ck_close_match_rules_match_field"
        ),
        {
            "comment": "담당자가 승인한 전표→편성항목 매칭 규칙. 반복 수정을 감지해 물어보고, 승인한 "
            "것만 남겨 다음 달부터 자동 적용한다(승인 없이 자동 생성하지 않는다)."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(String(32), comment="이 규칙이 적용되는 계정 key(overseas 등)")
    match_field: Mapped[str] = mapped_column(
        String(12), comment="무엇으로 거는지: vendor(거래처) | text(적요 포함) | cost_center(코스트센터) | project(과제코드)"
    )
    match_value: Mapped[str] = mapped_column(String(100), comment="비교할 값. text는 '포함'으로, 나머지는 '같음'으로 본다")
    scope_key: Mapped[str] = mapped_column(
        String(50), default="", comment="붙일 곳(연구소 pk 등). excluded가 True면 빈 문자열"
    )
    excluded: Mapped[bool] = mapped_column(Boolean, default=False, comment="True면 '이 조건은 제외한다'는 규칙")
    hit_count: Mapped[int] = mapped_column(
        SmallInteger, default=0, comment="규칙을 만들게 한 반복 수정 횟수(화면에 '3번 같은 수정'으로 보여준다)"
    )
    note: Mapped[str] = mapped_column(String(200), default="", comment="규칙 설명. 나중에 왜 만든 규칙인지 알아보려면 필요하다")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="승인한 시각")


class CloseDeptRule(Base):
    """계정 자동 분류 기준정보 — (부서코드 × 비용계정) → 19개 계정 중 하나.

    비용계정만으로는 안 갈라진다. 같은 ERP 계정코드가 국내 부서에서 나오면 그 계정 몫이고 해외연구소
    부서에서 나오면 해외연구소 몫인 비용이 있다. 그래서 부서코드를 같이 본다.

    cost_center를 빈 문자열로 두면 "그 비용계정 전체"를 뜻한다(부서 구분 없이 한 계정으로 가는 비용).
    둘 다 채운 행이 빈 행보다 먼저 이긴다 — 더 구체적인 기준이 이겨야 예외를 한 줄로 적을 수 있다.

    is_shared는 "공용 부서코드"다. NRE 공용처럼 여러 연구소가 같이 쓰는 코드는 전표만 보고 연구소를
    가를 수 없어서, 2단계에서 적요까지 봐야 한다 — 화면이 그 전표를 '확인 필요'로 먼저 올리게
    표시해 둔다.
    """

    __tablename__ = "close_dept_rules"
    __table_args__ = (
        UniqueConstraint("cost_center", "account_code", name="uq_close_dept_rules_key"),
        {
            "comment": "전표 계정 자동 분류 기준정보(부서코드×비용계정 → 계정 key). cost_center가 빈 "
            "문자열이면 그 비용계정 전체를 뜻하고, 구체적인 행이 먼저 이긴다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    cost_center: Mapped[str] = mapped_column(
        String(20), default="", comment="코스트센터(부서코드). 빈 문자열이면 비용계정만으로 분류한다"
    )
    account_code: Mapped[str] = mapped_column(String(20), comment="ERP 비용계정 코드")
    account_key: Mapped[str] = mapped_column(String(32), comment="이 조합이 가는 19개 계정 key")
    is_shared: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        comment="공용 부서코드 여부(NRE 공용 등). True면 전표만으로 연구소를 가를 수 없어 2단계에서 적요까지 본다",
    )
    note: Mapped[str] = mapped_column(String(200), default="", comment="기준 설명(화면 분류 근거에 그대로 쓰인다)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막으로 고친 시각"
    )


class CloseAccountStage(Base):
    """계정 하나의 그 달 결산 상태 — 매핑을 끝냈는지, 언제 끝냈는지.

    versions / version_account_status와 다른 층이다. 그쪽은 "편성 버전의 계정이 확정됐는지"를 담고,
    이쪽은 "결산 전표 매핑이 끝났는지"를 담는다. 대부분의 계정은 둘이 같이 끝나지만 **해외연구소는
    다르다** — 전표 매핑이 끝나도 그건 1차(ERP 약식)일 뿐이고, 계정 완료는 PL이 나온 뒤 실적
    시뮬레이션 화면의 [확정]으로만 된다. 그래서 여기서 '1차 완료'까지만 표시하고 계정 완료는
    건드리지 않는다(first_done_at이 그 1차 완료 시각이다).

    해외연구소의 first_done_at은 overseas_actual_stages.close_first_done과 짝이다 — 여기서 1차를
    끝내면 증감분석 화면의 [1차 분석] 탭이 열린다.
    """

    __tablename__ = "close_account_stages"
    __table_args__ = (
        UniqueConstraint("basis_year", "basis_month", "account_key", name="uq_close_account_stages_key"),
        {
            "comment": "계정별 결산 전표매핑 상태. 해외연구소는 여기서 '1차 완료'까지만 하고 계정 완료는 "
            "실적 시뮬레이션 [확정]으로만 된다(versions 쪽과 다른 층)."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    basis_year: Mapped[int] = mapped_column(SmallInteger, comment="결산 대상 연도")
    basis_month: Mapped[int] = mapped_column(SmallInteger, comment="결산 대상 월(1~12)")
    account_key: Mapped[str] = mapped_column(String(32), comment="계정 key")
    mapping_done: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="전표 매핑을 끝냈는지. 해외연구소는 이게 곧 '1차 완료'다"
    )
    first_done_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="매핑 완료(해외연구소는 1차 완료) 시각. 되돌리면 NULL로 지운다"
    )
    note: Mapped[str] = mapped_column(String(200), default="", comment="완료 당시 메모(미매핑 n건 남김 등)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막으로 고친 시각"
    )
