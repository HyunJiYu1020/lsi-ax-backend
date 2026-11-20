"""해외연구소 실적결산 전표 매칭 규칙.

이 로직의 계약은 "맞췄나"가 아니라 **언제 사람에게 넘기나**다. 전표에는 연구소가 적혀 오지 않아서
(비용이 과제코드 단위로 떨어진다) AI가 틀릴 수밖에 없고, 틀린 매핑은 연구소 실적을 둘 다 틀리게
만든다. 그래서 테스트는 셋을 본다: ① 단서가 모이면 자동(high)으로 올리는가, ② 단서가 애매하면
사람에게 넘기는가(low·미매핑), ③ 업무 규칙(NRE 공용 · 적요 CP)은 단정하는가.

DB가 필요 없는 순수 로직이라 conftest의 db 픽스처를 쓰지 않는다.
"""

from app.shared.core.voucher_match import (
    HIGH,
    LOW,
    MID,
    RULE_MIN_HITS,
    has_cp_mark,
    match_rule,
    suggest_lab,
    suggest_rules,
)

LABS = [
    {"pk": "SARC:CPU", "lab_code": "SARC", "name": "SARC+ACL"},
    {"pk": "DSRA", "lab_code": "DSRA", "name": "DSRA"},
    {"pk": "CP", "lab_code": "CP", "name": "CP"},
]
SHARED = {"shared_centers": {"C10I8000"}}


def _vou(**kw):
    base = {"vendor": "", "text": "", "cost_center": "", "project_code": ""}
    return {**base, **kw}


# ---------- NRE 공용 부서코드 · 적요 CP (업무 규칙) ----------
def test_shared_nre_center_with_cp_goes_to_cp_lab():
    """NRE 공용 부서코드 전표 중 적요에 CP가 있으면 CP(미청구 NRE) 연구소 몫이다 — 업무 규칙이라
    추측이 아니고, 그래서 신뢰도도 high다."""
    got = suggest_lab(_vou(cost_center="C10I8000", text="9월 CP 정산분"), LABS, SHARED)

    assert got["scope_key"] == "CP"
    assert got["confidence"] == HIGH
    assert got["excluded"] is False
    assert "CP" in got["reason"]


def test_shared_nre_center_without_cp_is_excluded():
    """같은 공용 부서코드인데 적요에 CP가 없으면 해외연구소 실적이 아니다(국내가 부담한 공용 비용).
    지우지 않고 제외로 남기는 이유는 제외분 합계가 전표 총액과 맞는지 확인하는 자리이기 때문이다."""
    got = suggest_lab(_vou(cost_center="C10I8000", text="9월 공용 설비 사용분"), LABS, SHARED)

    assert got["excluded"] is True
    assert got["scope_key"] == ""
    assert got["confidence"] == HIGH
    assert "CP 없음" in got["exclude_reason"]


def test_cp_mark_does_not_match_cpu_or_capex():
    """'CPU'·'CAPEX'를 CP로 읽으면 SARC:CPU 전표가 전부 CP 연구소로 간다 — 낱말 경계를 봐야 한다."""
    assert has_cp_mark("CP 정산") is True
    assert has_cp_mark("9월분 (CP)") is True
    assert has_cp_mark("CPU 검증 용역") is False
    assert has_cp_mark("CAPEX 집행분") is False


def test_shared_center_cp_missing_from_master_asks_person():
    """적요에 CP가 있는데 CP 연구소가 기준정보에 없으면(사용 중지) 다른 연구소에 임의로 붙이지 않고
    사람에게 넘긴다 — 제외로 처리해서도 안 된다(CP 몫은 분명히 있다)."""
    got = suggest_lab(_vou(cost_center="C10I8000", text="CP 정산분"), [LABS[0], LABS[1]], SHARED)

    assert got["scope_key"] == "" and got["excluded"] is False
    assert got["confidence"] == LOW


# ---------- 단서 세 개로 연구소 추정 ----------
def test_two_strong_hints_are_high_confidence():
    """거래처 이력 + 적요 낱말이 같은 곳을 가리키면 자동(high)으로 올린다."""
    hints = {"vendor": {"ADTECHNOLOGY": "DSRA"}}
    got = suggest_lab(_vou(vendor="ADTechnology", text="DSRA 9월 용역"), LABS, hints)

    assert got["scope_key"] == "DSRA"
    assert got["confidence"] == HIGH


def test_single_weak_hint_needs_review():
    """적요 낱말만 하나 겹치는 전표는 '확인 필요'(mid 이하)로 남긴다 — 자동으로 넘기면 "거래처만
    맞는" 전표가 조용히 틀린 연구소에 붙는다."""
    got = suggest_lab(_vou(text="SARC+ACL 임차료"), LABS, {})

    assert got["scope_key"] == "SARC:CPU"
    assert got["confidence"] in (MID, LOW)


def test_no_hint_is_unmapped_not_a_guess():
    """단서가 하나도 없으면 아무 연구소에도 붙이지 않는다. 추측해서 붙이면 담당자가 그걸 검증할
    방법이 없다."""
    got = suggest_lab(_vou(vendor="처음 보는 업체", text="9월 대금"), LABS, {})

    assert got["scope_key"] == ""
    assert got["confidence"] == LOW
    assert "찾지 못했다" in got["reason"]


def test_conflicting_hints_go_to_person():
    """단서가 서로 다른 연구소를 가리키면(과제 이력은 SARC, 적요에는 DSRA) 점수 높은 쪽을 자동으로
    고르면 모순을 아무도 못 본 채 반은 틀린다 — 사람이 골라야 한다."""
    hints = {"project": {"PJT001": "SARC:CPU"}}
    got = suggest_lab(_vou(project_code="PJT001", text="DSRA 분담금"), LABS, hints)

    assert got["scope_key"] == ""
    assert got["confidence"] == LOW
    assert "갈린다" in got["reason"]


def test_weak_noise_does_not_block_a_strong_match():
    """2위가 적요 낱말 하나만 걸친 정도면(ACL이 SARC+ACL의 낱말과 겹친다) 모순이 아니라 잡음이다 —
    그걸로 1위를 막으면 멀쩡한 전표까지 전부 '확인 필요'로 쌓여서 담당자가 아무것도 자동으로 못
    넘긴다."""
    hints = {"vendor": {"ADTECHNOLOGY": "DSRA"}, "project": {"PJT001": "DSRA"}}
    got = suggest_lab(_vou(vendor="ADTechnology", project_code="PJT001", text="ACL 서버 9월 용역"), LABS, hints)

    assert got["scope_key"] == "DSRA"
    assert got["confidence"] == HIGH


def test_ambiguous_hint_is_dropped_before_scoring():
    """같은 거래처가 두 연구소에 걸린 이력은 단서표를 만드는 쪽(_hints)에서 빼므로, 여기서는 그
    거래처가 아무 점수도 못 준다."""
    got = suggest_lab(_vou(vendor="공용업체"), LABS, {"vendor": {}})

    assert got["scope_key"] == ""


# ---------- 승인된 규칙이 AI를 이긴다 ----------
def test_approved_rule_wins_over_ai():
    """사람이 "앞으로 이렇게"라고 답한 규칙은 AI 추천을 이긴다 — 기계가 다시 뒤집으면 같은 수정을
    매달 반복하게 된다."""
    rules = [{"match_field": "vendor", "match_value": "(주)한빛상사", "scope_key": "DSRA", "excluded": False}]

    assert match_rule(_vou(vendor="한빛상사(주)"), rules) is None  # 글자 순서가 다르면 같은 거래처가 아니다
    assert match_rule(_vou(vendor="(주) 한빛상사"), rules)["scope_key"] == "DSRA"  # 공백·기호만 다르면 같다


def test_text_rule_matches_by_containment():
    """적요는 '포함'으로 본다 — 같은 내용을 매달 조금씩 다르게 적어 보낸다."""
    rules = [{"match_field": "text", "match_value": "클라우드", "scope_key": "SARC:CPU", "excluded": False}]

    assert match_rule(_vou(text="9월 클라우드 사용분 정산"), rules)["scope_key"] == "SARC:CPU"
    assert match_rule(_vou(text="9월 서버 임차료"), rules) is None


# ---------- 반복 수정 → 규칙 제안 ----------
def test_repeated_correction_becomes_a_suggestion():
    """같은 수정을 RULE_MIN_HITS번 하면 "앞으로 이렇게 할까요?"를 묻는다."""
    corrections = [
        {"vendor": "이노와이어리스", "cost_center": "", "project_code": "", "scope_key": "DSRA", "excluded": False}
    ] * RULE_MIN_HITS

    got = suggest_rules(corrections, [])

    assert len(got) == 1
    assert (got[0]["match_field"], got[0]["scope_key"]) == ("vendor", "DSRA")
    assert got[0]["hit_count"] == RULE_MIN_HITS


def test_two_corrections_are_not_enough():
    """두 번은 우연일 수 있다 — 그 달만 예외로 옮긴 전표까지 규칙이 되면 다음 달에 조용히 틀린 곳에
    붙는다."""
    corrections = [
        {"vendor": "이노와이어리스", "cost_center": "", "project_code": "", "scope_key": "DSRA", "excluded": False}
    ] * (RULE_MIN_HITS - 1)

    assert suggest_rules(corrections, []) == []


def test_hint_that_went_two_ways_is_never_suggested():
    """같은 거래처를 어떤 달은 SARC로, 어떤 달은 DSRA로 고쳤으면 규칙으로 만들면 안 된다 — 규칙이
    되면 다음 달에 절반이 틀린 채 자동 적용된다."""
    corrections = [
        {"vendor": "공용업체", "cost_center": "", "project_code": "", "scope_key": "SARC:CPU", "excluded": False}
    ] * RULE_MIN_HITS + [
        {"vendor": "공용업체", "cost_center": "", "project_code": "", "scope_key": "DSRA", "excluded": False}
    ] * RULE_MIN_HITS

    assert [s for s in suggest_rules(corrections, []) if s["match_field"] == "vendor"] == []


def test_already_registered_rule_is_not_asked_again():
    """이미 등록한 규칙은 다시 묻지 않는다."""
    corrections = [
        {"vendor": "이노와이어리스", "cost_center": "", "project_code": "", "scope_key": "DSRA", "excluded": False}
    ] * RULE_MIN_HITS
    existing = [{"match_field": "vendor", "match_value": "이노와이어리스", "scope_key": "DSRA", "excluded": False}]

    assert suggest_rules(corrections, existing) == []


def test_exclusion_can_become_a_rule():
    """제외도 반복되면 규칙이 된다(공용 비용을 매달 제외하는 경우) — scope_key 없이 excluded만 켠다."""
    corrections = [
        {"vendor": "", "cost_center": "C10I8000", "project_code": "", "scope_key": "", "excluded": True}
    ] * RULE_MIN_HITS

    got = suggest_rules(corrections, [])

    assert len(got) == 1
    assert got[0]["excluded"] is True and got[0]["scope_key"] == ""


def test_unmapped_correction_is_not_a_rule_candidate():
    """미매핑으로 되돌린 수정은 "어디에 붙일지"를 정하지 않은 것이라 규칙이 될 수 없다."""
    corrections = [
        {"vendor": "이노와이어리스", "cost_center": "", "project_code": "", "scope_key": "", "excluded": False}
    ] * RULE_MIN_HITS

    assert suggest_rules(corrections, []) == []
