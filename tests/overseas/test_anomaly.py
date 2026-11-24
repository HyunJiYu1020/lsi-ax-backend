"""업로드 값 이상 감지 — 기준 4개와 "기준을 바꾸면 경고가 바뀐다"는 계약.

이 기능의 핵심은 두 가지다: ① 경고가 과하지 않아야 한다(금액 작은 항목이 비율만으로 걸리면
담당자가 목록을 안 읽는다), ② 기준은 저장돼서 창을 새로 열어도 유지돼야 한다. 그래서 테스트도
"걸리는가"만 보지 않고 "안 걸려야 할 것이 안 걸리는가"와 "바꾼 기준이 남는가"를 같이 본다.
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints import collect as ov
from app.shared.core import anomaly
from app.accounts.overseas.cost_groups import GROUP_CALC, category_label, resolve_leaves
from app.accounts.overseas.schemas import AnomalyRuleIn, AnomalyScanIn

YM, BASE_YM, VER = "209901", "209812", "forecast"

# 저장·검사 단위는 $다(2026-11-18 단위 전환 — 그 전에는 K$였다). 이 파일의 금액은 "몇 K$인가"로
# 읽는 게 업무에 가까워서, 숫자는 K$로 적고 K를 곱해 $로 넘긴다. 인력(명)·비율(%)에는 곱하지 않는다.
K = 1000


def _detect(vals, base=None, sums=None, reasons=None, periods=None, outs_rows=None,
            analysis=None, basis_month=None, **over):
    rules = anomaly.merged_rules([], over or None)
    return anomaly.detect(
        vals=vals, base_vals=base, sums=sums, reasons=reasons, rules=rules,
        group_calc=GROUP_CALC, label_of=category_label, leaves_of=resolve_leaves,
        periods=periods, outs_rows=outs_rows, analysis=analysis, basis_month=basis_month,
    )


def _rules(db):
    return {r.rule_key: r for r in ov.list_anomaly_rules(db=db)}


# ---------- 판정 규칙 (DB 없이) ----------


def test_jump_needs_both_pct_and_amount():
    """급변은 비율과 금액을 **둘 다** 넘어야 걸린다 — 잡비 $1K → $2K(+100%)까지 걸리면
    연구소 12개에서 경고가 수십 개 떠서 아무도 안 읽는다."""
    small = _detect({"oth": [1 * K] * 12}, {"oth": [0.5 * K] * 12})  # +100%지만 변동액 $6K
    assert [f["rule"] for f in small] == []

    big = _detect({"travel": [12 * K] * 12}, {"travel": [10 * K] * 12})  # +20%, 변동액 $24K
    jump = [f for f in big if f["rule"] == "jump"]
    assert len(jump) == 1 and "+20.0%" in jump[0]["message"]

    flat = _detect({"sal": [1000 * K] * 12}, {"sal": [1000 * K] * 12})  # 금액은 크지만 변동 없음
    assert [f for f in flat if f["rule"] == "jump"] == []


def test_jump_reports_the_month_that_drove_it():
    """어느 달이 변동을 끌었는지 집어 준다 — 화면이 그 칸부터 표시하게 하려는 값이다."""
    vals = {"travel": [10 * K] * 11 + [60 * K]}
    jump = [f for f in _detect(vals, {"travel": [10 * K] * 12}) if f["rule"] == "jump"][0]
    assert jump["month"] == 12


def test_new_item_without_base_is_reported_as_new():
    """전실행에 없던 항목은 비율을 낼 수 없다 — 0으로 나누지 말고 "신규"로 알린다."""
    f = [x for x in _detect({"cons": [5 * K] * 12}, {"travel": [1 * K] * 12}) if x["rule"] == "jump"]
    assert len(f) == 1 and "신규" in f[0]["message"] and f[0].get("rate") is None


def test_sum_mismatch_allows_rounding():
    """소계와 하위 합의 차이가 허용 오차 안이면 안 걸린다(연구소가 반올림해서 보낸다)."""
    leaves = {"sal": [100 * K] * 12, "bonus": [10 * K] * 12, "welf": [5 * K] * 12}  # 합 1,380K$

    ok = _detect(leaves, sums={"labor": [115 * K] * 12})  # 1,380K$ — 정확히 일치
    assert [f for f in ok if f["rule"] == "sum_mismatch"] == []

    rounding = _detect(leaves, sums={"labor": [115.02 * K] * 12})  # 차이 0.24K$ → 오차 안
    assert [f for f in rounding if f["rule"] == "sum_mismatch"] == []

    bad = _detect(leaves, sums={"labor": [200 * K] * 12})
    f = [x for x in bad if x["rule"] == "sum_mismatch"]
    assert len(f) == 1 and f[0]["category_key"] == "labor"
    # 비교한 값이 12개월 합이니 연간 칸(17)을 가리킨다. month가 없으면 화면이 행 전체를
    # 테두리로 그리는데, 그 표시는 "행이 통째로 새것"(신규)일 때만 쓰기로 한 자리다.
    assert f[0]["month"] == 17


def test_missing_reason_follows_the_jump_rule():
    """사유 누락은 급변에 종속이다 — 급변으로 안 걸린 항목은 사유가 없어도 묻지 않는다."""
    vals, base = {"travel": [12 * K] * 12}, {"travel": [10 * K] * 12}

    assert any(f["rule"] == "missing_reason" for f in _detect(vals, base, reasons={}))
    assert not any(f["rule"] == "missing_reason" for f in _detect(vals, base, reasons={"travel": "채용 지연"}))
    # 급변 기준을 올려서 그 항목이 안 걸리면 사유 누락도 같이 사라진다.
    assert not any(f["rule"] == "missing_reason" for f in _detect(vals, base, reasons={}, jump={"pct": 50}))


def test_missing_reason_marks_the_same_cell_as_the_jump():
    """사유 누락은 급변이 찍은 **그 칸**에 붙는다 — 행 전체가 아니다.

    화면은 month도 months도 없는 지적을 행 테두리로 그린다(core.js ovModFlags). 그 표시는
    "행이 통째로 새것"(전실행에 없던 항목)이라는 뜻으로 쓰기로 한 자리라, 칸을
    짚을 수 있는 지적까지 행으로 그리면 담당자가 어디를 고쳐야 하는지 알 수 없다.
    빈 양식을 그대로 올렸을 때 사유 누락 29건이 행을 통째로 칠해서 신규 행처럼 보인 적이 있다.
    """
    vals, base = {"travel": [10 * K] * 11 + [60 * K]}, {"travel": [10 * K] * 12}
    out = _detect(vals, base, reasons={})
    jump = next(f for f in out if f["rule"] == "jump")
    miss = next(f for f in out if f["rule"] == "missing_reason")
    assert miss["month"] == jump["month"] == 12

    # 신규(전실행에 값이 없던 항목)만 행 전체로 남는다 — 그 행은 실제로 통째로 새 행이다.
    new = _detect({"cons": [5 * K] * 12}, {"travel": [1 * K] * 12}, reasons={})
    assert {f["rule"] for f in new if f["month"] is None} == {"jump", "missing_reason", "new_category"}


def test_only_new_rows_are_marked_row_wide():
    """빈 양식을 그대로 올린 파일 — 행 전체 표시가 하나도 나오면 안 된다.

    모든 계정이 빈칸이면 빈칸(칸 단위)과 급변(그 칸)만 떠야 한다. 실제로 이 파일에서 사유 누락
    29건이 행 전체로 그려져 "새로 추가된 행이 29개"처럼 보였다.
    """
    leaves = ["sal", "bonus", "welf", "travel", "misc", "tax", "nonop", "B", "swl", "outs"]
    blank = {k: [None] * 12 for k in leaves}
    base = {k: [10 * K] * 12 for k in leaves}

    out = _detect(blank, base, reasons={})
    assert out, "빈 양식인데 아무것도 안 걸렸다"
    row_wide = [f for f in out if f["month"] is None and not f["months"]]
    assert row_wide == [], [f["rule"] for f in row_wide]


def test_unit_suspect_catches_won_sized_values_and_1000x():
    """단위를 틀려 적어 온 값(절대값이 너무 큼)과 전 버전 대비 1000배를 잡는다.

    연구소 양식은 K$인데 거기에 $ 원본이나 원 단위를 적어 보내면 정확히 1000배로 들어온다 —
    업로드가 그 셀을 또 ×1000해서 저장하므로 반영 전에 잡아야 한다(화면 업로드 미리보기)."""
    assert any(f["rule"] == "unit_suspect" for f in _detect({"sal": [2_000_000 * K] * 12}))
    # K$ 칸에 90,000(= 정상 최대의 90배)이 들어온 경우 — 담당자가 실제로 겪는 모양이다.
    assert any(f["rule"] == "unit_suspect" for f in _detect({"sal": [90_000 * K] * 12}))
    assert any(f["rule"] == "unit_suspect" for f in _detect({"sal": [100_000 * K] * 12}, {"sal": [100 * K] * 12}))
    # 정상 범위는 안 걸린다.
    assert not any(f["rule"] == "unit_suspect" for f in _detect({"sal": [120 * K] * 12}, {"sal": [100 * K] * 12}))
    # 한 칸 실측 최대(1,000K$)의 몇 배까지는 봐준다 — 연구소가 커지는 것만으로 경고가 뜨면 안 된다.
    assert not any(f["rule"] == "unit_suspect" for f in _detect({"sal": [5_000 * K] * 12}))


def test_disabled_rule_is_not_checked():
    """꺼 둔 기준("사유 누락은 안 알려줘")은 검사하지 않는다."""
    vals, base = {"travel": [12 * K] * 12}, {"travel": [10 * K] * 12}
    out = _detect(vals, base, reasons={}, missing_reason={"enabled": False})
    assert not any(f["rule"] == "missing_reason" for f in out)
    assert any(f["rule"] == "jump" for f in out)  # 다른 기준은 그대로 돈다


# ---------- 기준 저장 (DB) ----------


def test_rules_have_defaults_and_readable_desc(db):
    """기준 4개가 기본값으로 나오고, 지금 값이 들어간 설명 문장을 같이 준다
    (화면·챗봇이 숫자를 따로 조립하면 두 곳이 다르게 적는다)."""
    r = _rules(db)
    assert set(r) == set(anomaly.RULE_KEYS)
    assert r["jump"].pct == 10 and r["jump"].amount == 10 * K   # $10,000 = 10K$
    # 설명 문장은 화면과 같은 K$로 적는다 — 저장값($)을 그대로 적으면 표의 숫자와 달라 보인다.
    assert "10%" in r["jump"].desc and "$10K" in r["jump"].desc


def test_chat_change_is_saved_and_only_touches_what_was_sent(db):
    """"50% 넘는것만 알려줘"는 비율만 바꾼다 — 금액 하한까지 초기화되면 경고 범위가 엉뚱해진다.
    저장되므로 다시 조회해도(= 창을 새로 열어도) 50%가 그대로 있다."""
    ov.upsert_anomaly_rule(AnomalyRuleIn(rule_key="급변", pct=50, source="chat", note="50% 넘는것만 알려줘"), db=db)

    r = _rules(db)["jump"]
    assert r.pct == 50 and r.amount == 10 * K  # 금액 하한은 건드리지 않았다
    assert "50%" in r.desc

    hist = ov.list_anomaly_rule_history(db=db)
    assert hist[0].rule_key == "jump" and hist[0].source == "chat"
    assert hist[0].before["pct"] == 10 and hist[0].after["pct"] == 50
    assert "50%" in hist[0].note


def test_reset_restores_defaults(db):
    """"기준 원래대로"는 기본값으로 되돌린다."""
    ov.upsert_anomaly_rule(AnomalyRuleIn(rule_key="jump", pct=50, amount=100 * K), db=db)
    ov.upsert_anomaly_rule(AnomalyRuleIn(rule_key="jump", reset=True), db=db)

    r = _rules(db)["jump"]
    assert (r.pct, r.amount) == (10, 10 * K)
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
    base = dict(lab_pk="DSRA", version=VER, yyyymm=YM, vals={"travel": [12 * K] * 12}, reasons={"travel": "사유 있음"})
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


# ---------- 2026-11 추가: 빈칸 · 기간 합 · 신규 계정 · 증감분석 대조 ----------


def test_blank_cell_is_not_zero():
    """빈칸(null)과 0은 다른 상태다 — 0은 "그 달에 안 썼다"는 보고이고 빈칸은 "아직 안 냈다"다.
    0을 빈칸으로 잡으면 연구소가 0으로 보고한 달마다 경고가 떠서 목록이 무용해진다."""
    blank = _detect({"sal": [10 * K, None] + [10 * K] * 10})
    hit = [f for f in blank if f["rule"] == "missing_cell"]
    assert len(hit) == 1 and hit[0]["months"] == [2] and "2월" in hit[0]["message"]

    zeros = _detect({"sal": [0] * 12})
    assert [f for f in zeros if f["rule"] == "missing_cell"] == []


def test_blank_cells_are_one_finding_per_category():
    """칸이 여러 개 비어도 계정당 한 줄로 묶는다(칸은 months로 다 넘긴다) — 36계정 × 12달을
    건별로 올리면 목록이 수백 줄이 되어 아무도 안 읽는다."""
    hit = [f for f in _detect({"sal": [None] * 12}) if f["rule"] == "missing_cell"]
    assert len(hit) == 1 and hit[0]["months"] == list(range(1, 13))
    assert "외 6칸" in hit[0]["message"]  # 메시지는 앞 6칸만 적고 나머지는 개수로 줄인다


def test_period_mismatch_checks_quarters_and_year():
    """월 합과 파일의 1Q~4Q·연간 칸을 대조한다. 칸 번호는 13~16=1Q~4Q, 17=연간 —
    화면 17칸 표(월 12 + 분기 4 + 연간)의 위치를 그대로 쓴다."""
    vals = {"sal": [10 * K] * 12}  # 분기 30 · 연간 120이 맞다(K$)
    ok = _detect(vals, periods={"sal": [30 * K, 30 * K, 30 * K, 30 * K, 120 * K]})
    assert [f for f in ok if f["rule"] == "period_mismatch"] == []

    bad = _detect(vals, periods={"sal": [30 * K, 30 * K, 30 * K, 30 * K, 999 * K]})
    hit = [f for f in bad if f["rule"] == "period_mismatch"]
    assert len(hit) == 1 and hit[0]["month"] == 17 and "연간" in hit[0]["message"]

    rounding = _detect(vals, periods={"sal": [30.3 * K, None, None, None, None]})  # 반올림 차이는 통과
    assert [f for f in rounding if f["rule"] == "period_mismatch"] == []


def test_period_mismatch_skips_blank_period_cells():
    """연구소가 분기 칸을 아예 안 적었으면 기간 불일치로 올리지 않는다 — 그건 빈칸(missing_cell)이
    잡을 일이고, 여기서 또 올리면 같은 칸이 두 번 걸린다."""
    hit = _detect({"sal": [10 * K] * 12}, periods={"sal": [None, None, None, None, None]})
    assert [f for f in hit if f["rule"] == "period_mismatch"] == []


def test_period_mismatch_covers_outsourcing_rows():
    """외주용역은 계정이 아니라 업체 행이라, 행 이름으로 가리킨다."""
    rows = [{"label": "ACME Design", "months": [1 * K] * 12, "periods": [3 * K, 3 * K, 3 * K, 3 * K, 13 * K]}]
    hit = [f for f in _detect({}, outs_rows=rows) if f["rule"] == "period_mismatch"]
    assert len(hit) == 1
    assert hit[0]["row_label"] == "ACME Design" and hit[0]["category_key"] == "outs"


def test_new_category_has_no_amount_floor():
    """급변의 "신규"와 달리 금액 하한이 없다 — 금액이 작아도 계정이 하나 는 건 양식이 바뀌었다는
    신호라 담당자가 알아야 한다. 전실행이 없으면(비교 불가) 올리지 않는다."""
    tiny = _detect({"oth": [0.1 * K] * 12}, {"sal": [10 * K] * 12})
    assert [f["rule"] for f in tiny if f["rule"] == "jump"] == []  # 금액이 작아 급변은 안 걸린다
    hit = [f for f in tiny if f["rule"] == "new_category"]
    assert len(hit) == 1 and hit[0]["month"] is None  # 행 전체 표시

    no_base = _detect({"oth": [0.1 * K] * 12})
    assert [f for f in no_base if f["rule"] == "new_category"] == []


def test_analysis_prev_column_must_match_our_base():
    """증감분석의 전실행 열은 연구소가 전월 파일을 보고 옮겨 적는 칸이라, 본사 값과 어긋나면
    비교 기준 자체가 다른 것이다."""
    base = {"sal": [10 * K] * 12}  # 본사 전실행 합계 120K$
    same = _detect({}, base, analysis={"sal": {"y": {"prev": 120 * K}, "m": {}}})
    assert [f for f in same if f["rule"] == "analysis_prev"] == []

    diff = _detect({}, base, analysis={"sal": {"y": {"prev": 99 * K}, "m": {}}})
    hit = [f for f in diff if f["rule"] == "analysis_prev"]
    assert len(hit) == 1 and "99" in hit[0]["message"] and "120" in hit[0]["message"]

    # 연구소는 K$를 소수 한 자리까지 적어 보내고 우리 합계는 $ 원본이라, 제대로 옮겨 적어도
    # 수백 달러가 남는다. 그 반올림까지 걸리면 모든 계정이 "전실행 불일치"가 된다.
    rounded = {"sal": [10 * K + 37.31] * 12}          # 본사 합계 $120,447.72
    ok = _detect({}, rounded, analysis={"sal": {"y": {"prev": 120.4 * K}, "m": {}}})
    assert [f for f in ok if f["rule"] == "analysis_prev"] == []


def test_analysis_current_block_blanks_are_reported():
    """당월 블록은 그 달 보고의 본문이라 비면 읽을 게 없다. 연간 블록 빈칸은 여기서 안 본다."""
    hit = [
        f for f in _detect({}, analysis={"sal": {"y": {"prev": None}, "m": {"plan": 10 * K, "prev": None, "cur": 12 * K}}})
        if f["rule"] == "analysis_missing"
    ]
    assert len(hit) == 1 and "prev" in hit[0]["message"] and hit[0]["block"] == "m"


def test_new_rules_can_be_turned_off_like_the_old_ones():
    """새 기준도 기존 기준과 같은 스위치를 탄다 — 챗봇에서 끄면 그 검사만 빠진다."""
    off = _detect(
        {"sal": [None] * 12}, {"oth": [1 * K] * 12},
        missing_cell={"enabled": False}, new_category={"enabled": False},
    )
    assert [f["rule"] for f in off if f["rule"] in ("missing_cell", "new_category")] == []


def test_scan_reports_checks_it_skipped_for_missing_input(db):
    """입력이 없어서 못 한 검사는 "이상 없음"과 구분되게 밝힌다."""
    r = _scan(db)
    assert any("기간 합" in s for s in r.skipped)
    assert any("증감분석" in s for s in r.skipped)
    # "신규 계정"이 아니라 "전실행에 없던 값"이다 — 신규는 공통코드(사용 중)에 없는 이름으로 온
    # 행의 몫이고(화면 ovRowKey · row_alias), 이 기준은 계정은 있고 값이 새로 생긴 경우다.
    assert any("전실행에 없던 값" in s for s in r.skipped)
