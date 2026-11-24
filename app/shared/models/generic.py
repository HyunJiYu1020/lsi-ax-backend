"""전 계정 공용 테이블 — 파일을 받고 증감을 분석하는 계정이면 모두 쓰는 기능들.

왜 계정별 접두사를 안 쓰나: 행 이름 매칭·번역 캐시·파생 정의 유지·이상 감지 기준·증감사유는
"엑셀을 받아 증감을 분석하는 계정"이면 똑같이 필요하다. 계정마다 테이블을 만들면
dep_row_aliases · outsource_anomaly_rules처럼 계정당 7~8개씩 복제된다.

이미 cost_entries · notes · uploads가 account_key 컬럼 패턴으로 전 계정을 받고 있으므로 같은
패턴에 맞췄다 — 테이블은 하나고 계정은 행으로 구분한다.

account_key는 기본값이 없는 NOT NULL이다(accounts.key FK). 계정을 명시하지 않은 삽입을 DB가
바로 막게 하려는 것이다 — 기본값을 'overseas'로 두면 새 계정 코드가 그 값을 조용히 물려받는다.
단 text_translations만 account_key가 없다: 원문 해시로 찾는 전역 캐시라 계정을 구분할 이유가
없다(같은 영어 문장은 어느 계정에서 올라와도 같은 한국어다).
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

class TextTranslation(Base):
    """증감사유 영문 원문 → 한국어 번역 캐시(원문 1건당 1행).

    왜 저장하는가: 증감분석 화면은 연구소 12개 × 계정 20여 개의 사유를 한 번에 그려서, 번역을
    매번 LLM에 물으면 화면이 뜨는 데만 수십 초가 걸리고 호출 비용도 그만큼 든다. 더 중요한 건
    일관성이다 — 같은 회신 문장이 어제와 오늘 다른 한국어로 보이면 보고서 설명이 흔들린다.
    한 번 번역한 원문은 이 테이블의 값을 계속 쓰고, 다시 번역해야 할 때만(모델 교체·오역 수정)
    같은 행을 덮어쓴다.

    원문(source_text)을 번역문과 같은 행에 남기는 건 공통 RULE(원문 보관) 때문이다 — 번역이
    이상할 때 연구소가 실제로 뭐라고 썼는지 되짚을 수 있어야 한다. 식별은 정규화한 원문의
    해시로 한다(엑셀에서 긁은 원문은 줄바꿈·연속 공백이 제각각이라 원문 그대로는 같은 문장이
    여러 행으로 갈라진다 — app/core/reason_translate.py의 normalize 참고)."""

    __tablename__ = "text_translations"
    __table_args__ = {
        "comment": "해외연구소 증감사유 영문 원문 → 한국어 번역 캐시(정규화한 원문 해시로 식별, 원문 같이 보관)."
    }

    id: Mapped[int] = mapped_column(primary_key=True)
    source_hash: Mapped[str] = mapped_column(
        String(64), unique=True, comment="정규화(연속 공백 1칸)한 원문의 SHA-256 16진 문자열 — 같은 문장을 두 번 번역하지 않게 하는 키"
    )
    source_text: Mapped[str] = mapped_column(String(600), comment="정규화한 영문 원문(보관용). 번역이 이상할 때 되짚는 근거")
    ko_text: Mapped[str] = mapped_column(String(600), comment="한국어 번역문(보고용 명사형 간결체)")
    engine: Mapped[str] = mapped_column(
        String(40), default="", comment='번역한 모델("gemini:gemini-3.5-flash-lite" 등). 모델을 바꾼 뒤 옛 번역을 구분·재번역할 때 쓴다'
        )
    guide_hash: Mapped[str] = mapped_column(
        String(16), default="",
        comment="이 번역에 적용된 번역 지침(guidelines, applies_to='translate')의 지문. 빈 문자열이면 "
        "지침 없이 번역한 것이다.\n"
        "왜 필요한가: 캐시는 원문 해시로만 찾으므로 담당자가 번역 지침을 바꿔도(\"Headcount는 인원으로 "
        "써\") 이미 캐시된 번역은 옛 규칙 그대로 계속 나온다 — 지침을 등록했는데 번역이 안 바뀌는 걸 "
        "담당자는 기능이 안 되는 것으로 읽는다. 조회할 때 이 값이 지금 지침과 다르면 캐시를 못 쓴 것으로 "
        "보고 다시 번역해 덮어쓴다(원문 1건 = 번역 1개는 그대로 유지한다)",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="처음 번역한 시각")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="다시 번역해서 덮어쓴 시각"
    )


class RowAlias(Base):
    """"이 행 이름은 저 행과 같은 행"이라고 사람이 한 번 답한 결과(원문 이름 1개당 1행).

    연구소마다 같은 비용을 다른 이름으로 적어 보낸다 — 고정 계정 출장비의 영문명은 'Travel Exp'인데
    'Travel Expense'로 적어 보내는 연구소가 있다. 업로드할 때 비슷한 이름을 찾아 "같은 행인가요?"를
    묻고(app/core/row_match.py), 그 대답을 여기 남긴다. 안 남기면 다음 달에 12개 연구소에 같은
    질문을 처음부터 다시 하게 된다.

    "아니다(별개 행)"도 같이 저장한다(target_kind='distinct') — 그것도 사람이 판단한 결과라서,
    안 남기면 매달 "이거 같은 행 아니에요?"를 또 묻는다. 묻지 않기 위해 저장하는 테이블이다.

    alias_norm은 정규화한(공백·기호 뺀 소문자) 이름이라 'Travel Expense'와 'travel  expense'가
    한 행으로 합쳐진다. 한 이름이 두 행을 가리킬 수는 없어서 unique다 — 바꾸려면 덮어쓴다."""

    __tablename__ = "row_aliases"
    __table_args__ = (
        # account_key를 키에 넣어야 계정마다 같은 이름을 따로 답할 수 있다 — 'Travel Expense'를
        # 해외연구소는 출장비로, 다른 계정은 다른 행으로 볼 수 있기 때문이다.
        UniqueConstraint("account_key", "alias_norm", name="uq_row_aliases_norm"),
        CheckConstraint("target_kind IN ('category', 'extra_row', 'distinct')", name="ck_overseas_row_aliases_target_kind"),
        {
            "comment": "업로드 파일의 행 이름 → 이미 있는 행 매핑(사람이 확인한 결과). "
            "target_kind=category(고정 계정) | extra_row(추가 행) | distinct(별개 행이라고 답함)."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(
        String(30), ForeignKey("accounts.key"), index=True,
        comment="비용계정 키(accounts.key). 이 테이블은 전 계정 공용이라 계정을 명시해야 한다 — "
        "기본값을 두지 않는 이유는 새 계정이 'overseas'를 조용히 물려받는 것을 막기 위함이다",
    )
    alias_norm: Mapped[str] = mapped_column(
        String(100), comment="파일에 적혀 온 이름을 정규화한 값(공백·기호 제거 + 소문자). 계정과 묶여 매칭 키가 된다"
    )
    alias_label: Mapped[str] = mapped_column(String(100), comment="파일에 적혀 온 이름 원문(보관용 — 어떤 표기에 답한 건지 되짚을 때)")
    target_kind: Mapped[str] = mapped_column(
        String(12), comment="category(고정 계정과 같은 행) | extra_row(이미 있는 추가 행과 같은 행) | distinct(별개 행)"
    )
    target_key: Mapped[str] = mapped_column(
        String(100), default="", comment="category면 비용항목 키(travel 등), extra_row면 정식 이름의 정규화값. distinct면 빈 값"
    )
    target_label: Mapped[str] = mapped_column(
        String(100), default="", comment="합쳐 넣을 정식 이름(화면·집계에서 쓰는 이름). distinct면 빈 값"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="처음 답한 시각")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="답을 바꿔서 덮어쓴 시각"
    )


class CarryDef(Base):
    """집계 화면에서 만든 파생 행·열 중 "다음 버전에도 유지"를 승인한 것(1건당 1행).

    파생 행·열은 버전×기준연월 단위로 저장돼서, 새 버전을 열면 지난 버전에서 만든 계산 행이
    사라진다 — 매달 보는 지표(인당 인건비 등)는 매번 다시 만들어야 했다. 그렇다고 전부 자동으로
    따라가게 하면, 그 달만 보려고 만든 일회성 지표까지 계속 끌려다닌다. 그래서 만들 때 한 번 묻고
    (승인한 것만) 여기 남겨서, 새 버전을 열 때 그것만 복제한다.

    값이 아니라 정의(계산식)만 따라간다 — 재료(업로드 값)가 새 버전 것으로 바뀌면 계산 결과도
    그 버전 숫자로 다시 나온다. def_key는 파생 행이면 row_key, 파생 열이면 label이다(각자 그
    종류 안에서 식별자 역할을 하는 값)."""

    __tablename__ = "carry_defs"
    __table_args__ = (
        UniqueConstraint("account_key", "kind", "def_key", name="uq_carry_defs_key"),
        CheckConstraint("kind IN ('row', 'col')", name="ck_overseas_carry_defs_kind"),
        {"comment": "다음 버전에도 유지하기로 승인한 집계 파생 행·열. 새 버전을 열 때 이 정의만 복제한다."},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(
        String(30), ForeignKey("accounts.key"), index=True,
        comment="비용계정 키(accounts.key). 이 테이블은 전 계정 공용이라 계정을 명시해야 한다 — "
        "기본값을 두지 않는 이유는 새 계정이 'overseas'를 조용히 물려받는 것을 막기 위함이다",
    )
    kind: Mapped[str] = mapped_column(String(3), comment="row(파생 행) | col(파생 열)")
    def_key: Mapped[str] = mapped_column(String(100), comment="파생 행이면 row_key, 파생 열이면 label")
    label: Mapped[str] = mapped_column(String(100), default="", comment="사람이 읽을 이름(어떤 지표를 유지 중인지 보여줄 때)")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="승인한 시각")


class VarianceReason(Base):
    """증감분석 화면의 증감사유 한 칸 — "회신 원문 → 번역 + AI 초안 → 담당자 수정 → [저장]"의 상태 전부.

    왜 notes 테이블로 안 되는가: notes는 (계정·비교기준·기간·행·항목)만 키로 잡고 텍스트 한 줄을
    담는다. 증감사유는 그 한 줄에 이르는 과정 자체가 보고 자료다 — 연구소가 영문으로 써 보낸 원문,
    그 번역, AI가 뽑은 보고용 초안, 담당자가 고쳐서 확정한 문장이 다 남아야 "이 숫자를 왜 이렇게
    설명했나"를 되짚을 수 있다(공통 RULE: 원문 보관). 게다가 notes에는 기준연월이 없어서 9월 속보에
    쓴 사유가 10월 속보에도 그대로 붙는다 — 금액이 바뀌었는데 사유가 따라오면 틀린 설명이 된다.

    saved_diff가 핵심이다: [저장]을 누른 순간의 증감액을 같이 박아 둔다. 그 뒤 연구소가 파일을 다시
    올려서 금액이 바뀌면 사유를 지우지 않고(담당자가 쓴 글을 서버가 버리면 안 된다) needs_recheck만
    켜서 화면에 "재확인 필요"로 띄운다 — 사유는 유지, 확인은 사람이 다시 한다.

    stage는 실적 버전의 1차/2차를 가른다. 1차(ERP 약식)는 연구소 총액만 보고 쓰는 사유이고 2차(PL)는
    계정까지 보고 쓰는 사유라서, 같은 연구소·같은 달이어도 서로 덮어쓰면 안 된다(1차는 보고까지 끝난
    값이다). 속보 버전은 1차/2차 구분이 없어 빈 문자열을 쓴다."""

    __tablename__ = "variance_reasons"
    __table_args__ = (
        UniqueConstraint(
            "account_key", "basis_year", "basis_month", "kind", "stage", "base_version", "period",
            "lab_scope", "category_key",
            name="uq_variance_reasons_key",
        ),
        CheckConstraint("kind IN ('forecast', 'actual')", name="ck_overseas_variance_reasons_kind"),
        CheckConstraint("stage IN ('', 'first', 'second')", name="ck_overseas_variance_reasons_stage"),
        CheckConstraint("base_version IN ('prev', 'prior', 'plan')", name="ck_overseas_variance_reasons_base_version"),
        {
            "comment": "증감분석 증감사유 한 칸의 전체 상태(회신 원문·번역·AI 초안·확정 문장). "
            "저장 시점 증감액(saved_diff)을 같이 남겨 재업로드로 금액이 바뀌면 needs_recheck로 '재확인 필요'를 띄운다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(
        String(30), ForeignKey("accounts.key"), index=True,
        comment="비용계정 키(accounts.key). 이 테이블은 전 계정 공용이라 계정을 명시해야 한다 — "
        "기본값을 두지 않는 이유는 새 계정이 'overseas'를 조용히 물려받는 것을 막기 위함이다",
    )
    basis_year: Mapped[int] = mapped_column(SmallInteger, comment="편성 기준연도")
    basis_month: Mapped[int] = mapped_column(SmallInteger, comment="편성 기준월(1~12)")
    kind: Mapped[str] = mapped_column(String(10), comment="forecast(속보) | actual(실적) — versions.kind와 같은 값")
    stage: Mapped[str] = mapped_column(
        String(8), default="", comment="실적 분석 단계: first(1차 ERP 약식) | second(2차 PL). 속보는 빈 문자열"
    )
    base_version: Mapped[str] = mapped_column(String(10), comment="비교 기준: prev(전실행) | prior(전년) | plan(경영계획)")
    period: Mapped[str] = mapped_column(String(4), comment="기간: y(연간) | q1~q4 | m1~m12")
    lab_scope: Mapped[str] = mapped_column(
        String(20), comment='화면에서 고른 연구소 범위. "ALL"(전체) | 연구소 코드 | 연구소 pk("SARC:CPU")'
    )
    category_key: Mapped[str] = mapped_column(String(40), comment="비용항목 키(labor · travel · tot ...)")
    source_text: Mapped[str] = mapped_column(
        Text, default="", comment="연구소가 써 보낸 회신 원문(보통 영문). 번역·초안의 근거라 지우지 않고 같이 남긴다"
    )
    source_ko: Mapped[str] = mapped_column(Text, default="", comment="회신 원문의 한국어 번역(overseas_reason_translations와 같은 규칙)")
    ai_draft: Mapped[str] = mapped_column(Text, default="", comment="번역 + 증감 숫자로 AI가 만든 보고용 사유 초안")
    reason_text: Mapped[str] = mapped_column(
        Text, default="", comment="담당자가 고쳐서 [저장]한 확정 사유. 보고서에 실리는 문장이다"
    )
    saved_diff: Mapped[float | None] = mapped_column(
        Numeric(18, 2),
        comment="[저장] 시점의 증감액(원). 지금 증감액과 다르면 재업로드로 금액이 바뀐 것이라 needs_recheck를 켠다",
    )
    needs_recheck: Mapped[bool] = mapped_column(
        Boolean, default=False, comment='True면 저장 뒤 금액이 바뀌었다는 뜻 — 화면에 "재확인 필요"로 표시한다(사유는 유지)'
    )
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="[저장]을 누른 시각. 초안만 있으면 NULL")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="이 칸이 처음 생긴 시각")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )


class VarianceDriver(Base):
    """증감 동인 표 — "인건비를 인력수 × 인당 단가로 쪼개 보여줘" 같은 요청으로 증감분석 화면에
    덧붙인 분해 표 하나.

    왜 추가 행(cost_entries + formula)이 아닌가: 추가 행은 편성 그리드의 한 줄이고, 이건 증감분석
    화면에만 붙는 별도 표다(기준·당월·증감을 동인별로 가른 작은 표). 무엇보다 추가 행에는 "다음
    버전에도 유지할까"라는 개념이 없다 — 여기서는 그게 핵심 상태다.

    carry_over가 그 상태다. AI가 표를 붙일 때는 지금 보고 있는 버전에만 붙이고(carry_over=False),
    담당자가 "다음 버전에도?"에 승인하면 켠다. 조회할 때 "이 버전에 붙인 표 + 이전 버전에서
    carry_over로 켜 둔 표"를 합쳐 주므로, 승인한 표는 다음 달에도 저절로 따라온다. 승인 안 한 표는
    그 버전에서만 보이고 사라진다 — 한 번 물어본 분해를 매달 영구 고정하지 않으려는 것이다.

    factors에는 숫자가 아니라 계산식만 담는다(파생 행이 formula만 저장하는 것과 같은 이유) —
    연구소가 파일을 다시 올리면 동인 금액도 따라 바뀌어야 한다."""

    __tablename__ = "variance_drivers"
    __table_args__ = (
        UniqueConstraint(
            "account_key", "basis_year", "basis_month", "kind", "lab_scope", "driver_key",
            name="uq_variance_drivers_key",
        ),
        CheckConstraint("kind IN ('forecast', 'actual')", name="ck_overseas_variance_drivers_kind"),
        {
            "comment": "증감분석 화면에 AI가 덧붙인 증감 동인 분해 표. carry_over=True면 다음 버전에도 따라온다. "
            "금액이 아니라 계산식(factors)만 담아 원본이 바뀌면 같이 바뀐다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(
        String(30), ForeignKey("accounts.key"), index=True,
        comment="비용계정 키(accounts.key). 이 테이블은 전 계정 공용이라 계정을 명시해야 한다 — "
        "기본값을 두지 않는 이유는 새 계정이 'overseas'를 조용히 물려받는 것을 막기 위함이다",
    )
    basis_year: Mapped[int] = mapped_column(SmallInteger, comment="이 표를 붙인 편성 기준연도")
    basis_month: Mapped[int] = mapped_column(SmallInteger, comment="이 표를 붙인 편성 기준월(1~12)")
    kind: Mapped[str] = mapped_column(String(10), comment="forecast(속보) | actual(실적)")
    lab_scope: Mapped[str] = mapped_column(String(20), default="ALL", comment='연구소 범위("ALL" | 연구소 코드 | pk)')
    driver_key: Mapped[str] = mapped_column(String(40), comment='표 식별 키(같은 버전 안에서 유일). 예: "labor_hc_rate"')
    title: Mapped[str] = mapped_column(String(100), comment='표 제목("인건비 동인 — 인력수 × 인당 단가")')
    target_key: Mapped[str] = mapped_column(String(40), comment="분해 대상 비용항목 키(labor 등). 이 행 아래에 표를 붙인다")
    factors: Mapped[list] = mapped_column(
        JSONB,
        comment='동인 행 목록. [{"label":"인력수","op":"ref","operands":["hc"],"unit":"hc"}, '
        '{"label":"인당 단가","op":"div_elementwise","operands":["labor","hc"],"unit":"krw"}]',
    )
    requested_text: Mapped[str] = mapped_column(
        String(500), default="", comment="담당자가 요청한 원문 문장. 나중에 이 표가 왜 생겼는지 되짚는 근거"
    )
    carry_over: Mapped[bool] = mapped_column(
        Boolean, default=False, comment='True면 "다음 버전에도?"에 승인한 표 — 이후 버전 조회에도 따라온다'
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="표를 붙인 시각")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막 갱신 시각"
    )


class AnomalyRule(Base):
    """이상 감지 기준(기준 1건당 1행). 급변 몇 % · 합계 오차 얼마 같은 값을 담는다.

    왜 테이블인가: 코드에 박아두면 못 바꾸고, 브라우저에 두면 창을 닫으면 날아가고 사람마다 다른
    기준으로 보게 된다. 챗봇에서 "이제 50% 넘는것만 알려줘"를 승인하면 이 행이 바뀌고, 그 뒤로는
    창을 새로 열어도·다른 담당자가 봐도 같은 기준이 적용된다(해외연구소 공통).

    쓰는 파라미터만 채우고 나머지는 NULL이다 — 급변은 pct+amount, 합계 불일치는 amount(오차),
    사유 누락은 켜고/끄기만, 단위 의심은 amount(절대값)+ratio(배수). 기본값과 각 기준의 뜻은
    app/core/anomaly.py의 DEFAULTS에 있다(행이 없으면 그 값으로 동작한다)."""

    __tablename__ = "anomaly_rules"
    __table_args__ = (
        # 계정마다 급변 기준이 다를 수 있다(해외연구소는 50%, 다른 계정은 30% 등).
        UniqueConstraint("account_key", "rule_key", name="uq_anomaly_rules_key"),
        {
        "comment": "이상 감지 기준(급변·합계 불일치·사유 누락·단위 의심). 금액 단위는 $(저장 단위와 같다). "
            "행이 없으면 app/core/anomaly.py의 기본값으로 동작한다."
        },
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(
        String(30), ForeignKey("accounts.key"), index=True,
        comment="비용계정 키(accounts.key). 이 테이블은 전 계정 공용이라 계정을 명시해야 한다 — "
        "기본값을 두지 않는 이유는 새 계정이 'overseas'를 조용히 물려받는 것을 막기 위함이다",
    )
    rule_key: Mapped[str] = mapped_column(
        String(20), comment="jump(급변) | sum_mismatch(합계 불일치) | missing_reason(사유 누락) | unit_suspect(단위 의심)"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, comment="사용 여부. false면 그 기준은 검사하지 않는다")
    pct: Mapped[float | None] = mapped_column(Numeric(6, 2), comment="비율 기준(%). 급변만 쓴다")
    amount: Mapped[float | None] = mapped_column(
        Numeric(14, 2), comment="금액 기준($). 급변=변동액 하한, 합계 불일치=허용 오차, 단위 의심=절대값 상한"
    )
    ratio: Mapped[float | None] = mapped_column(Numeric(10, 2), comment="배수 기준. 단위 의심만 쓴다(전 버전 대비 N배·1/N)")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="마지막으로 바꾼 시각"
    )


class AnomalyRuleLog(Base):
    """이상 감지 기준 변경 이력(변경 1건당 1행).

    기준이 바뀌면 경고 개수가 달라지고, 그러면 "지난달엔 왜 이게 안 걸렸지"라는 질문이 나온다.
    그때 "9월에 급변 기준을 10% → 50%로 올렸다"를 댈 수 있어야 한다. 챗봇으로 바꾸는 경로가
    열려 있어서(말 한마디로 바뀐다) 더 필요하다 — 무엇이 어떻게 바뀌었는지 before/after로 남긴다."""

    __tablename__ = "anomaly_rule_logs"
    __table_args__ = {"comment": "이상 감지 기준 변경 이력(before/after + 누가 어떤 경로로 바꿨는지)."}

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(
        String(30), ForeignKey("accounts.key"), index=True,
        comment="비용계정 키(accounts.key). 이 테이블은 전 계정 공용이라 계정을 명시해야 한다 — "
        "기본값을 두지 않는 이유는 새 계정이 'overseas'를 조용히 물려받는 것을 막기 위함이다",
    )
    rule_key: Mapped[str] = mapped_column(String(20), comment="바뀐 기준 키(anomaly_rules.rule_key)")
    before: Mapped[dict | None] = mapped_column(JSONB, comment="바뀌기 전 값(enabled·pct·amount·ratio)")
    after: Mapped[dict | None] = mapped_column(JSONB, comment="바뀐 뒤 값")
    source: Mapped[str] = mapped_column(String(10), default="screen", comment="chat(챗봇) | screen(화면) | reset(기본값 복구)")
    note: Mapped[str] = mapped_column(String(200), default="", comment='무슨 말로 바꿨는지(예: 챗봇 요청 "50% 넘는것만 알려줘")')
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="바꾼 시각")


class Guideline(Base):
    """담당자가 챗봇에 말로 등록한 업무 지침 — "사유는 3줄 이내", "금액은 억원으로 써"(AI-18).

    왜 테이블이 필요한가: 지침을 대화 안에서만 말하면 그 대화창을 닫는 순간 사라진다. 다음 달에
    같은 담당자가 같은 말을 또 해야 하고, 사람이 바뀌면 "우리 팀은 금액을 억원으로 쓴다"는 약속
    자체가 전해지지 않는다. 지침은 한 번 말하면 계속 적용돼야 하는 값이라 브라우저에도 둘 수
    없다 — 여러 담당자가 같은 보고서를 만든다.

    왜 프롬프트 파일(chat_prompts.py)에 적지 않는가: 거기 있는 규칙은 "숫자를 지어내지 마라"처럼
    틀리면 사고가 나는 불변 규칙이고, 이 테이블은 "억원으로 써"처럼 팀의 취향이라 담당자가 말로
    바꿔야 하는 값이다. 코드에 적으면 바꿀 때마다 배포가 필요하다.

    topic이 핵심이다: 지침의 **주제**를 키로 잡아서 같은 주제를 다시 등록하면 덮어쓴다. 금액
    단위 지침이 "억원"과 "백만원" 두 줄로 동시에 살아 있으면 모델이 둘 중 아무거나 고르고, 그게
    매번 달라진다. 주제를 키로 두면 "금액은 백만원으로 써"가 들어올 때 기존 "억원" 지침이 바로
    잡혀서 "바꿀까요?"를 물을 수 있다(TC-091 지침 충돌).

    account_key에 FK를 걸지 않았다. 빈 문자열이 "전 계정 공통"이라는 뜻을 가져야 하는데(금액
        단위는 해외연구소만의 약속이 아니다) FK로는 그 값을 표현할 수 없고, NULL로 두면 Postgres가
    UNIQUE·ON CONFLICT에서 NULL을 서로 다른 값으로 취급해 같은 전 계정 지침이 여러 줄로 쌓인다
    (cost_entry_details.col_name에 같은 함정이 적혀 있다). 그래서 빈 문자열로 두고, 비어 있지
    않을 때 accounts.key에 있는지는 엔드포인트가 확인한다."""

    __tablename__ = "guidelines"
    __table_args__ = (
        UniqueConstraint("account_key", "applies_to", "topic", name="uq_guidelines_topic"),
        CheckConstraint(
        "applies_to IN ('all', 'chat', 'variance_reason', 'translate')", name="ck_guidelines_applies_to"
        ),
        CheckConstraint("topic <> ''", name="ck_guidelines_topic_not_empty"),
        CheckConstraint("text <> ''", name="ck_guidelines_text_not_empty"),
        {
        "comment": "담당자가 말로 등록한 업무 지침(AI-18). 주제(topic)를 키로 잡아 같은 주제를 "
        "다시 등록하면 덮어쓴다. account_key=''는 전 계정 공통, applies_to로 적용 범위를 가른다."
        },
        )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_key: Mapped[str] = mapped_column(
        String(30), default="", index=True,
        comment="비용계정 키(accounts.key). 빈 문자열이면 전 계정 공통 지침 — 그 값을 FK로 "
        "표현할 수 없어 FK를 걸지 않고 엔드포인트가 확인한다",
        )
    applies_to: Mapped[str] = mapped_column(
        String(20), default="all",
        comment="적용 범위: all(어디서나) | chat(대화 답변) | variance_reason(증감사유 초안) | "
        "translate(회신 번역). 번역 지침이 대화 답변까지 바꾸면 엉뚱한 곳에 적용된다",
        )
    topic: Mapped[str] = mapped_column(
        String(40),
        comment='지침의 주제("금액 단위" · "사유 길이"). 같은 주제를 다시 등록하면 덮어쓰는 키다 — '
        "같은 주제 지침이 둘 이상 살아 있으면 모델이 매번 다른 걸 고른다",
        )
    text: Mapped[str] = mapped_column(
        String(300), comment='지침 문장 그대로("금액은 억원 단위로 쓴다"). 프롬프트에 이 문장이 그대로 들어간다'
        )
    source: Mapped[str] = mapped_column(
        String(10), default="chat", comment="chat(챗봇으로 등록) | screen(화면에서 등록)"
        )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="처음 등록한 시각"
        )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), comment="같은 주제로 덮어쓴 시각"
        )
