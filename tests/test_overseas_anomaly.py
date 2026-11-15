"""업로드 값 이상 감지 — 기준 4개와 "기준을 바꾸면 경고가 바뀐다"는 계약.

이 기능의 핵심은 두 가지다: ① 경고가 과하지 않아야 한다(금액 작은 항목이 비율만으로 걸리면
담당자가 목록을 안 읽는다), ② 기준은 저장돼서 창을 새로 열어도 유지돼야 한다. 그래서 테스트도
"걸리는가"만 보지 않고 "안 걸려야 할 것이 안 걸리는가"와 "바꾼 기준이 남는가"를 같이 본다.
"""

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import overseas as ov
from app.core import anomaly
from app.core.cost_groups import GROUP_CALC, category_label, resolve_leaves
from app.schemas.overseas import AnomalyRuleIn, AnomalyScanIn

YM, BASE_YM, VER = "209901", "209812", "forecast"


def _detect(vals, base=None, sums=None, reasons=None, **over):
    rules = anomaly.merged_rules([], over or None)
    return anomaly.detect(
        vals=vals, base_vals=base, sums=sums, reasons=reasons, rules=rules,
        group_calc=GROUP_CALC, label_of=category_label, leaves_of=resolve_leaves,
    )


def _rules(db):
    return {r.rule_key: r for r in ov.list_anomaly_rules(db=db)}


# ---------- 판정 규칙 (DB 없이) ----------


def test_jump_needs_both_pct_and_amount():
    """급변은 비율과 금액을 **둘 다** 넘어야 걸린다 — 잡비 $1K → $2K(+100%)까지 걸리면
    연구소 12개에서 경고가 수십 개 떠서 아무도 안 읽는다."""
    small = _detect({"oth": [1] * 12}, {"oth": [0.5] * 12})  # +100%지만 변동액 $6K
    assert [f["rule"] for f in small] == []

    big = _detect({"travel": [12] * 12}, {"travel": [10] * 12})  # +20%, 변동액 $24K
    jump = [f for f in big if f["rule"] == "jump"]
    assert len(jump) == 1 and "+20.0%" in jump[0]["message"]

    flat = _detect({"sal": [1000] * 12}, {"sal": [1000] * 12})  # 금액은 크지만 변동 없음
    assert [f for f in flat if f["rule"] == "jump"] == []


def test_jump_reports_the_month_that_drove_it():
    """어느 달이 변동을 끌었는지 집어 준다 — 화면이 그 칸부터 표시하게 하려는 값이다."""
    vals = {"travel": [10] * 11 + [60]}
    jump = [f for f in _detect(vals, {"travel": [10] * 12}) if f["rule"] == "jump"][0]
    assert jump["month"] == 12


def test_new_item_without_base_is_reported_as_new():
    """전실행에 없던 항목은 비율을 낼 수 없다 — 0으로 나누지 말고 "신규"로 알린다."""
    f = [x for x in _detect({"cons": [5] * 12}, {"travel": [1] * 12}) if x["rule"] == "jump"]
    assert len(f) == 1 and "신규" in f[0]["message"] and f[0].get("rate") is None


def test_sum_mismatch_allows_rounding():
    """소계와 하위 합의 차이가 허용 오차 안이면 안 걸린다(연구소가 반올림해서 보낸다)."""
    leaves = {"sal": [100] * 12, "bonus": [10] * 12, "welf": [5] * 12}  # 합 1,380

    ok = _detect(leaves, sums={"labor": [115] * 12})  # 1,380 — 정확히 일치
    assert [f for f in ok if f["rule"] == "sum_mismatch"] == []

    rounding = _detect(leaves, sums={"labor": [115.02] * 12})  # 차이 0.24 → 오차 안
    assert [f for f in rounding if f["rule"] == "sum_mismatch"] == []

    bad = _detect(leaves, sums={"labor": [200] * 12})
    f = [x for x in bad if x["rule"] == "sum_mismatch"]
    assert len(f) == 1 and f[0]["category_key"] == "labor"


def test_missing_reason_follows_the_jump_rule():
    """사유 누락은 급변에 종속이다 — 급변으로 안 걸린 항목은 사유가 없어도 묻지 않는다."""
    vals, base = {"travel": [12] * 12}, {"travel": [10] * 12}

    assert any(f["rule"] == "missing_reason" for f in _detect(vals, base, reasons={}))
    assert not any(f["rule"] == "missing_reason" for f in _detect(vals, base, reasons={"travel": "채용 지연"}))
    # 급변 기준을 올려서 그 항목이 안 걸리면 사유 누락도 같이 사라진다.
    assert not any(f["rule"] == "missing_reason" for f in _detect(vals, base, reasons={}, jump={"pct": 50}))


def test_unit_suspect_catches_won_sized_values_and_1000x():
    """K$ 칸에 원 단위로 올라온 값과 전 버전 대비 1000배를 잡는다."""
    assert any(f["rule"] == "unit_suspect" for f in _detect({"sal": [2_000_000] * 12}))
    assert any(f["rule"] == "unit_suspect" for f in _detect({"sal": [100_000] * 12}, {"sal": [100] * 12}))
    # 정상 범위는 안 걸린다.
    assert not any(f["rule"] == "unit_suspect" for f in _detect({"sal": [120] * 12}, {"sal": [100] * 12}))


def test_disabled_rule_is_not_checked():
    """꺼 둔 기준("사유 누락은 안 알려줘")은 검사하지 않는다."""
    vals, base = {"travel": [12] * 12}, {"travel": [10] * 12}
    out = _detect(vals, base, reasons={}, missing_reason={"enabled": False})
    assert not any(f["rule"] == "missing_reason" for f in out)
    assert any(f["rule"] == "jump" for f in out)  # 다른 기준은 그대로 돈다


# ---------- 기준 저장 (DB) ----------


def test_rules_have_defaults_and_readable_desc(db):
    """기준 4개가 기본값으로 나오고, 지금 값이 들어간 설명 문장을 같이 준다
    (화면·챗봇이 숫자를 따로 조립하면 두 곳이 다르게 적는다)."""
    r = _rules(db)
    assert set(r) == set(anomaly.RULE_KEYS)
    assert r["jump"].pct == 10 and r["jump"].amount == 10
    assert "10%" in r["jump"].desc and "$10K" in r["jump"].desc


def test_chat_change_is_saved_and_only_touches_what_was_sent(db):
    """"50% 넘는것만 알려줘"는 비율만 바꾼다 — 금액 하한까지 초기화되면 경고 범위가 엉뚱해진다.
    저장되므로 다시 조회해도(= 창을 새로 열어도) 50%가 그대로 있다."""
    ov.upsert_anomaly_rule(AnomalyRuleIn(rule_key="급변", pct=50, source="chat", note="50% 넘는것만 알려줘"), db=db)

    r = _rules(db)["jump"]
    assert r.pct == 50 and r.amount == 10  # 금액 하한은 건드리지 않았다
    assert "50%" in r.desc

    hist = ov.list_anomaly_rule_history(db=db)
    assert hist[0].rule_key == "jump" and hist[0].source == "chat"
    assert hist[0].before["pct"] == 10 and hist[0].after["pct"] == 50
    assert "50%" in hist[0].note


def test_reset_restores_defaults(db):
    """"기준 원래대로"는 기본값으로 되돌린다."""
    ov.upsert_anomaly_rule(AnomalyRuleIn(rule_key="jump", pct=50, amount=100), db=db)
    ov.upsert_anomaly_rule(AnomalyRuleIn(rule_key="jump", reset=True), db=db)

    r = _rules(db)["jump"]
    assert (r.pct, r.amount) == (10, 10)
    assert ov.list_anomaly_rule_history(db=db)[0].source == "reset"


def test_bad_rule_changes_are_rejected(db):
    """없는 기준, 음수 금액, 1 이하 배수는 막는다."""
    for body in [
        AnomalyRuleIn(rule_key="없는기준", pct=50),
        AnomalyRuleIn(rule_key="jump", pct=0),
        AnomalyRuleIn(rule_key="jump", amount=-1),
        AnomalyRuleIn(rule_key="unit_suspect", ratio=1),
    ]:
        with pytest.raises(HTTPException) as e:
            ov.upsert_anomaly_rule(body, db=db)
        assert e.value.status_code == 400


# ---------- 검사 엔드포인트 ----------


def _scan(db, **kw):
    base = dict(lab_pk="DSRA", version=VER, yyyymm=YM, vals={"travel": [12] * 12}, reasons={"travel": "사유 있음"})
    return ov.scan_anomalies(AnomalyScanIn(**(base | kw)), db=db)


def test_scan_says_what_it_could_not_check(db):
    """비교할 전실행이 없으면 급변을 검사할 수 없다 — 조용히 빠지면 "이상 없음"으로 읽히므로
    skipped로 분명히 돌려준다."""
    r = _scan(db)
    assert r.findings == [] and r.counts == {}
    assert any("급변" in s for s in r.skipped)
    assert any("합계 불일치" in s for s in r.skipped)  # 파일에 소계 행이 없을 때도 알린다


def test_scan_overrides_do_not_save(db):
    """"이번만 50%로 봐줘"는 그 조회에만 적용되고 저장되지 않는다."""
    before = _rules(db)["jump"].pct
    r = _scan(db, overrides={"jump": {"pct": 50}})

    assert [x.pct for x in r.rules if x.rule_key == "jump"] == [50]  # 이번 결과엔 50이 적용됐다
    assert _rules(db)["jump"].pct == before  # 저장된 기준은 그대로


def test_scan_rejects_unknown_lab_or_version(db):
    """없는 연구소·버전으로는 검사하지 않는다(조용히 빈 결과를 주면 이상 없음으로 읽힌다)."""
    with pytest.raises(HTTPException):
        _scan(db, lab_pk="NOPE")
    with pytest.raises(HTTPException):
        _scan(db, version="made_up")
