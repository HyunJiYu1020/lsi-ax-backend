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
# 실제 호출부(anomaly_rules)가 넘기는 값을 그대로 쓴다 — 여기서 {"hc"}를 베껴 적으면
# 호출부가 안 넘기게 바뀌어도 이 파일만 통과한다.
from app.accounts.overseas.services.anomaly_rules import COUNT_KEYS

YM, BASE_YM, VER = 209901, 209812, "forecast"

# 저장·검사 단위는 $다(2026-11-18 단위 전환 — 그 전에는 K$였다). 이 파일의 금액은 "몇 K$인가"로
# 읽는 게 업무에 가까워서, 숫자는 K$로 적고 K를 곱해 $로 넘긴다. 인력(명)·비율(%)에는 곱하지 않는다.
K = 1000


def _detect(vals, base=None, sums=None, reasons=None, periods=None, outs_rows=None,
            analysis=None, basis_month=None, abases=None, **over):
    rules = anomaly.merged_rules([], over or None)
    return anomaly.detect(
        vals=vals, base_vals=base, sums=sums, reasons=reasons, rules=rules,
        group_calc=GROUP_CALC, label_of=category_label, leaves_of=resolve_leaves,
        periods=periods, outs_rows=outs_rows, analysis=analysis, basis_month=basis_month,
        analysis_bases=abases, count_keys=COUNT_KEYS,
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
    """전실행에 없던 항목은 비율을 낼 수 없다 — 0으로 나누지 말고 "전실행 0"으로 알린다.

    문구에 "신규"를 쓰지 않는다: 화면의 '신규'는 계정 자체가 없던 것(계정 목록에 없는 이름으로 온
    행 · 양식 밖 열)만 가리키고, 이쪽은 계정 행은 있었고 금액만 0이던 자리라 이상값으로 센다."""
    f = [x for x in _detect({"cons": [5 * K] * 12}, {"travel": [1 * K] * 12}) if x["rule"] == "jump"]
    assert len(f) == 1 and "전실행 0" in f[0]["message"] and "신규" not in f[0]["message"]
    assert f[0].get("rate") is None and f[0].get("base") == 0


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
    # new_category는 여기 없다: 급변이 같은 사실을 이미 올렸으면 빠진다
    # (test_new_value_in_an_old_account_is_reported_once_as_a_jump).
    new = _detect({"cons": [5 * K] * 12}, {"travel": [1 * K] * 12}, reasons={})
    assert {f["rule"] for f in new if f["month"] is None} == {"jump", "missing_reason"}


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


def test_scan_resolves_the_analysis_bases_itself(db):
    """증감분석을 같이 보내면 서버가 비교 기준을 **직접 푼다** — 전실행(직전 버전) · 경영계획
    (적용 차수). 화면이 "기준월 - 1"로 추측해 보내면 1월 속보(전년 12월 실적)에서 틀린다
    (API.md "비교 기준을 화면이 추측하면 안 된다").

    기준이 없는 DB에서는 지어내지 않고 skipped로 밝힌다 — 조용히 빠지면 "대조 이상 없음"이 된다.
    """
    r = _scan(db, analysis={"travel": {"y": {"plan": 1 * K, "prev": 1 * K, "cur": 1 * K}, "m": {}}},
              basis_month=11)
    # 비용 시트 값을 같이 안 보냈으니 당월 열 대조는 못 한다 — 그 사실이 문장으로 남는다.
    assert any("당월 대조" in s and "비용 시트" in s for s in r.skipped), r.skipped
    # 경영계획·전실행도 이 DB에 없으면 그 이유가 같이 남는다(있으면 대조가 돌아 findings로 나온다).
    assert any("경영계획 대조" in s for s in r.skipped) or any(
        f.rule == "analysis_plan" for f in r.findings
    ), r.skipped
    assert any("전실행 대조" in s for s in r.skipped) or any(
        f.rule == "analysis_prev" for f in r.findings
    ), r.skipped


def test_scan_compares_the_analysis_current_column_with_the_uploaded_cost_sheet(db):
    """당월 열은 같은 파일로 온 비용 시트(cost_vals)와 맞댄다 — 저장된 값이 아니다(반영 전이다)."""
    r = _scan(
        db,
        analysis={"travel": {"y": {"cur": 999 * K}, "m": {"cur": 1 * K}}},
        cost_vals={"travel": [1 * K] * 12},   # 연간 12K$ · 11월 1K$
        basis_month=11,
    )
    hit = [f for f in r.findings if f.rule == "analysis_cur"]
    assert [(f.block, f.fields) for f in hit] == [("y", ["cur"])], "연간만 어긋난 파일인데 짚은 칸이 다르다"
    assert not any("당월 대조" in s for s in r.skipped), "비용 값을 보냈는데 건너뛴 것으로 적었다"


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


def test_period_mismatch_year_cell_is_one_years_worth():
    """기간을 늘려 받은 파일(24·36개월)의 연간 칸은 **1년치**(1~12월)와 대조한다.

    양식의 1Q~4Q·연간 칸은 한 벌뿐이라 2년치 월 열이 와도 1년치를 가리킨다. 월 배열 전체를
    더해서 비교하면 분기 넷은 다 맞는데 연간 칸만 2배로 어긋나 보인다 — 월 500씩 24칸 ·
    분기 1500 · 연간 6000인 파일에서 연간만 이상값으로 칠해진다.
    """
    vals = {"sal": [500] * 24}
    periods = {"sal": [1500, 1500, 1500, 1500, 6000]}
    assert [f for f in _detect(vals, periods=periods) if f["rule"] == "period_mismatch"] == []

    # 1년치 합과 진짜로 어긋나면 그대로 걸린다(2년치 합과 우연히 같은 값도 걸러야 한다).
    hit = [f for f in _detect(vals, periods={"sal": [1500, 1500, 1500, 1500, 12000]})
           if f["rule"] == "period_mismatch"]
    assert len(hit) == 1 and hit[0]["month"] == 17


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
    비교 기준 자체가 다른 것이다.

    비교 상대는 **전실행(직전 버전)**이고 급변 검사가 쓰는 전월 속보(base_vals)와 다른 값이다 —
    그래서 analysis_bases로 따로 받는다(서버가 resolve_bases로 풀어 넣는다).
    """
    prev = {"sal": [10 * K] * 12}  # 본사 전실행 합계 120K$
    same = _detect({}, analysis={"sal": {"y": {"prev": 120 * K}, "m": {}}}, abases={"prev": prev})
    assert [f for f in same if f["rule"] == "analysis_prev"] == []

    diff = _detect({}, analysis={"sal": {"y": {"prev": 99 * K}, "m": {}}}, abases={"prev": prev})
    hit = [f for f in diff if f["rule"] == "analysis_prev"]
    assert len(hit) == 1 and "99" in hit[0]["message"] and "120" in hit[0]["message"]
    # 칸을 짚어야 그 칸만 칠린다(화면 ovModFlags의 ablk) — 블록 세 칸을 다 칠하면 고칠 자리가 가려진다.
    assert hit[0]["block"] == "y" and hit[0]["fields"] == ["prev"]

    # 연구소는 K$를 소수 한 자리까지 적어 보내고 우리 합계는 $ 원본이라, 제대로 옮겨 적어도
    # 수백 달러가 남는다. 그 반올림까지 걸리면 모든 계정이 "전실행 불일치"가 된다.
    rounded = {"sal": [10 * K + 37.31] * 12}          # 본사 합계 $120,447.72
    ok = _detect({}, analysis={"sal": {"y": {"prev": 120.4 * K}, "m": {}}}, abases={"prev": rounded})
    assert [f for f in ok if f["rule"] == "analysis_prev"] == []

    # 비교 상대를 안 주면 그 대조는 아예 안 돈다 — 서버가 skipped로 밝힌다(여기서 지어내지 않는다).
    assert [f for f in _detect({}, analysis={"sal": {"y": {"prev": 99 * K}, "m": {}}})
            if f["rule"] == "analysis_prev"] == []


def test_analysis_three_columns_are_checked_in_both_blocks():
    """증감분석 세 열을 각자의 상대와 맞대본다 — PLAN은 경영계획, 전실행 열은 직전 버전,
    당월 열은 **같은 파일로 온 비용 시트**다. 왼쪽 덩어리(연간)는 12개월 합, 오른쪽(당월)은
    기준월 한 칸이 기준이다.

    한 열·한 블록만 보면 PLAN을 지난 차수 값으로 적어 보내거나 당월 열이 비용 시트와 다른
    파일도 "이상 없음"으로 통과한다 — 같은 파일 안에서 두 시트가 서로 다른 금액을 말하는데
    어느 쪽을 저장해도 틀린 보고가 된다.
    """
    abases = {
        "plan": {"sal": [10 * K] * 12},   # 경영계획: 연간 120K$ · 11월 10K$
        "prev": {"sal": [20 * K] * 12},   # 전실행:   연간 240K$ · 11월 20K$
        "cur": {"sal": [30 * K] * 12},    # 비용 시트: 연간 360K$ · 11월 30K$
    }
    good = {"sal": {
        "y": {"plan": 120 * K, "prev": 240 * K, "cur": 360 * K},
        "m": {"plan": 10 * K, "prev": 20 * K, "cur": 30 * K},
    }}
    assert [f for f in _detect({}, analysis=good, basis_month=11, abases=abases)
            if f["rule"].startswith("analysis_") and f["rule"] != "analysis_missing"] == []

    bad = {"sal": {
        "y": {"plan": 111 * K, "prev": 240 * K, "cur": 999 * K},
        "m": {"plan": 10 * K, "prev": 77 * K, "cur": 30 * K},
    }}
    hit = {(f["rule"], f["block"]): f for f in _detect({}, analysis=bad, basis_month=11, abases=abases)
           if f["rule"] in ("analysis_plan", "analysis_prev", "analysis_cur")}
    assert set(hit) == {("analysis_plan", "y"), ("analysis_cur", "y"), ("analysis_prev", "m")}
    assert hit[("analysis_prev", "m")]["fields"] == ["prev"]
    # 당월 블록 지적은 몇 월인지를 문장에 적는다 — "당월"로만 적으면 지난 파일을 다시 볼 때 못 읽는다.
    assert "11월" in hit[("analysis_prev", "m")]["message"]


def test_analysis_subtotal_rows_compare_against_their_leaves():
    """소계·합계 행은 하위 계정 합과 맞대본다 — 본사는 소계를 저장하지 않는다(sum_mismatch와 같은
    사정). 그냥 찾으면 전부 "본사에 없음"이 되어, 소계 행을 적어 보낸 파일이 통째로 걸렸다."""
    leaves = {k: [1 * K] * 12 for k in resolve_leaves("lab")}
    n = len(resolve_leaves("lab"))
    ok = _detect({}, analysis={"lab": {"y": {"plan": n * 12 * K}, "m": {}}}, abases={"plan": leaves})
    assert [f for f in ok if f["rule"] == "analysis_plan"] == []

    bad = _detect({}, analysis={"lab": {"y": {"plan": 1 * K}, "m": {}}}, abases={"plan": leaves})
    assert [f["rule"] for f in bad if f["rule"] == "analysis_plan"] == ["analysis_plan"]


def test_analysis_plan_treats_a_missing_account_as_zero():
    """경영계획에 없는 계정은 "0으로 편성한 것"이다 — 거기에 금액을 적어 보냈으면 불일치다.
    전실행·비용 시트 열은 반대로 건너뛴다: 그 사실을 다른 기준이 이미 짚는다(전실행에 없던 값 ·
    비용 카드의 "파일에 이 행이 없습니다")."""
    a = {"travel": {"y": {"plan": 50 * K, "prev": 50 * K, "cur": 50 * K}, "m": {}}}
    got = {f["rule"] for f in _detect({}, analysis=a, abases={"plan": {}, "prev": {}, "cur": {}})}
    assert "analysis_plan" in got
    assert "analysis_prev" not in got and "analysis_cur" not in got


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


def test_headcount_period_cells_are_checked_in_people_not_dollars():
    """인력(명) 행의 분기 칸도 걸린다 — 금액 허용값($500)이 명수를 묻어 버리면 안 된다.

    왜 필요한가: 인력을 월 100명씩 적고 1Q~4Q 칸에 100을 적어 보낸 파일이 **연간만** 이상값으로
    뜨고 분기 넷은 조용히 통과했다(100 ≠ 300, 차이 200이 허용값 500 안). 같은 어긋남인데 칸에
    따라 걸리고 안 걸리면 담당자는 분기 칸을 "맞는 값"으로 읽는다. 값은 파일에 적혀 온 그대로
    보여 주고(화면이 그 값을 그린다) 어긋난 칸만 짚는 것이 이 검사의 몫이다.
    """
    hc = _detect({"hc": [100] * 12}, periods={"hc": [100, 100, 100, 100, 100]})
    pm = [f for f in hc if f["rule"] == "period_mismatch"]
    assert [f["month"] for f in pm] == [13, 14, 15, 16, 17], (
        "분기 1Q~4Q(13~16)와 연간(17)이 모두 걸려야 한다: " + str([(f["month"], f["message"]) for f in pm])
    )
    assert "100명" in pm[0]["message"] and "$" not in pm[0]["message"], (
        "명수를 금액으로 적으면(100명 → $0.1K) 경고를 읽을 수 없다: " + pm[0]["message"]
    )

    # 금액 행의 허용값은 그대로다 — 반올림 차이($0.4K)로 경고가 늘어나면 아무도 안 읽는다.
    sal = _detect({"sal": [100 * K] * 12}, periods={"sal": [300 * K + 400, 300 * K, 300 * K, 300 * K, 1200 * K]})
    assert not [f for f in sal if f["rule"] == "period_mismatch"]


def test_empty_subtotal_row_is_not_a_sum_mismatch():
    """연구소가 소계 행을 비워 보냈으면 합계 불일치가 아니다 — 빈칸은 0이 아니다.

    왜 필요한가: 소계 칸이 전부 빈 파일에서 "파일의 합계 $0.0K ≠ 하위 항목 합 $6,000K"가 소계
    행마다 떴다(개발비 · 인원성경비 · 자산성경비 … 9줄). 소계를 비워 보내는 건 정상이고 화면은
    하위 합으로 계산해 그린다 — 틀린 값을 보낸 게 아닌데 "이상값"으로 칠하면, 담당자는 진짜
    틀린 줄을 찾으려고 9줄을 하나씩 열어 보게 된다.
    """
    leaves = {"dep": [100 * K] * 12, "amort": [100 * K] * 12, "rent": [100 * K] * 12,
              "repair": [100 * K] * 12, "ins": [100 * K] * 12}
    blank = _detect(leaves, sums={"asset": [None] * 12})
    assert not [f for f in blank if f["rule"] == "sum_mismatch"], (
        "빈 소계 행을 0으로 읽고 합계 불일치를 올렸다: "
        + str([f["message"] for f in blank if f["rule"] == "sum_mismatch"])
    )

    # 한 달이라도 적어 왔으면 그 값이 연구소의 보고다 — 그때는 그대로 검사한다.
    partial = _detect(leaves, sums={"asset": [9 * K] + [None] * 11})
    assert [f for f in partial if f["rule"] == "sum_mismatch"], "적어 온 소계는 검사해야 한다"


def test_new_value_in_an_old_account_is_reported_once_as_a_jump():
    """전실행에 0이던 계정에 값이 생기면 **급변 한 줄**이다 — 같은 사실을 두 번 올리지 않는다.

    왜 필요한가: 두 지적은 등급이 달라서(급변=튀는 값 주황, 이쪽=빈칸 파랑) 한 행에 같이 붙으면
    미리보기에서 주황 줄 + 파란 칸이 되고, 파란 칸의 범례("… 새 행")만 본 담당자는 **계정 매칭이
    안 된 새 행**으로 읽는다 — 실제로는 그 계정에 제대로 붙은 행이다.
    """
    base = {"eda": [0] * 12}
    big = _detect({"eda": [100 * K] * 12}, base)
    rules = [f["rule"] for f in big]
    assert "jump" in rules, "전실행 0 → $1,200K는 급변이다"
    assert "new_category" not in rules, (
        "급변이 이미 말한 것을 전실행에 없던 값이 또 올렸다: "
        + str([f["message"] for f in big if f["rule"] == "new_category"])
    )

    # 급변의 금액 하한에 못 미치는 소액은 여전히 이쪽이 맡는다 — 숨기지는 않는다.
    small = _detect({"eda": [1] * 12}, base)
    assert [f["rule"] for f in small if f["rule"] in ("jump", "new_category")] == ["new_category"]
