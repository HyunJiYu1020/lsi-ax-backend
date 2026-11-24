"""증감분석 화면 — 비교 기준 해석 · 환차 분해 · 증감사유 재확인 · 1차 고정 · 동인 표 유지.

여기서 검증하는 건 "화면이 서버 없이 추측하면 틀리는 것들"이다:
- 전실행이 무엇인지는 달·버전 종류·데이터 유무에 따라 달라진다(1월은 연도를 넘어가고, 실적의
  전실행은 같은 달 속보다). 화면이 "기준월 - 1"로 계산하면 둘 다 틀린다.
- 환차를 현지→$ / $→원으로 가른 세 칸의 합은 반드시 증감과 같아야 한다. 어긋나면 담당자가
  "증감 10억 중 환차가 얼마"를 설명할 수 없다.
- 사유를 저장한 뒤 금액이 바뀌면 사유는 남고 재확인만 켜져야 한다(지우면 매달 다시 쓴다).
- [1차 고정]은 보고까지 끝난 값을 지키는 장치라, 켜진 동안 쓰기가 실제로 막혀야 한다.

환차 분해는 DB에 "두 버전의 환율이 서로 다른" 상황을 만들기 어려워서(시드는 속보·실적 환율이
같다) 계산 함수(services의 variance_cells)에 환율 배열을 직접 넣어 검증한다 — 식 자체가 틀렸는지를 보는
테스트라 DB를 거칠 필요가 없다.
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints import variance as ov
from app.accounts.overseas.services import variance as varsvc
from app.accounts.overseas.endpoints.collect import LabView
from app.shared.models.close import CloseAccountStage
from app.accounts.overseas.services.entries import current_basis_month
from app.accounts.overseas.schemas import (
    ActualFirstIn,
    ActualStageIn,
    VarianceDriverFactor,
    VarianceDriverIn,
    VarianceReasonIn,
)


# ---------- 비교 기준 해석 ----------
def test_prev_is_previous_month_actual(db):
    """9월 속보의 전실행은 8월 실적이다 — 같은 달 속보가 아니다."""
    r = ov.get_variance_basis(2026, 9, "forecast", None, db)
    prev = next(b for b in r.bases if b.key == "prev")
    assert (prev.year, prev.month, prev.kind) == (2026, 8, "actual")
    assert prev.resolved_label == "26.08 실적"


def test_january_prev_crosses_into_last_year(db):
    """1월 속보의 전실행은 전년 12월 실적 — 연도를 넘어간다.

    화면이 "기준월 - 1"로 계산하면 "2026-00"이 되어 전실행 칸이 깨진다. 이 규칙은 사양에 명시된
    것이고(1월 속보 → 전년 12월 실적), 서버가 라벨까지 만들어 준다."""
    r = ov.get_variance_basis(2026, 1, "forecast", None, db)
    prev = next(b for b in r.bases if b.key == "prev")
    assert (prev.year, prev.month, prev.kind) == (2025, 12, "actual")


def test_actual_prev_is_same_month_forecast(db):
    """실적의 전실행은 같은 달 속보다 — 실적은 그 달 속보를 실행한 결과라서 그것과 맞대본다."""
    r = ov.get_variance_basis(2026, 9, "actual", None, db)
    prev = next(b for b in r.bases if b.key == "prev")
    assert (prev.year, prev.month, prev.kind) == (2026, 9, "forecast")


def test_prior_falls_back_and_says_so(db):
    """전년 = 전년 12월 실적. 그게 없으면 최근 실적/속보로 물러나고, 무엇을 기준으로 비교하는지
    문장으로 밝힌다 — 폴백을 조용히 하면 담당자는 전년 실적과 비교한 줄 안다."""
    r = ov.get_variance_basis(2026, 9, "forecast", None, db)
    prior = next(b for b in r.bases if b.key == "prior")
    if prior.fallback_note:
        assert "전년 12월 실적이 없어" in prior.fallback_note
        assert prior.resolved_label in prior.fallback_note
    else:
        assert (prior.year, prior.month, prior.kind) == (2025, 12, "actual")


def test_plan_default_is_starred_revision(db):
    """경영계획 기본 차수는 ★(is_final). ★가 없으면 가장 최근 차수를 쓴다 — 경영계획 대비 칸을
    빈 화면으로 두지 않는다."""
    r = ov.get_variance_basis(2026, 9, "forecast", None, db)
    if not r.plan_revisions:
        pytest.skip("2026년 경영계획 차수가 시드되지 않아 스킵")
    starred = [x.plan_no for x in r.plan_revisions if x.is_final]
    assert r.default_plan_no == (starred[0] if starred else r.plan_revisions[-1].plan_no)
    assert all(("★" in x.label) == x.is_final for x in r.plan_revisions)


def test_unknown_plan_no_is_rejected(db):
    with pytest.raises(HTTPException) as e:
        ov.get_variance_basis(2026, 9, "forecast", "P99", db)
    assert e.value.status_code == 404


# ---------- 환차 분해 ----------
def _lab(pk: str, currency: str) -> LabView:
    return LabView(
        pk=pk, lab_code=pk, part_code=None, name=pk, country="", sort_order=0,
        currency=currency, is_active=True,
    )


def _cells_with_rates(monkeypatch, labs, cur_d, base_d, rate_cur, rate_base, ix=(0,),
                      cur_extras=None, base_extras=None):
    """rate_arrays를 고정 환율로 바꿔치고 variance_cells만 돌린다 — 시드 환율은 속보·실적이
    같아서 DB로는 "환율이 움직인 상황"을 만들 수 없다."""
    calls = iter([rate_cur, rate_base])
    monkeypatch.setattr(varsvc, "rate_arrays", lambda *a, **k: next(calls))
    return varsvc.variance_cells(
        None, labs, list(labs), cur_d, base_d, ix, (2026, 9, "forecast"), (2026, 8, "actual"), True,
        cur_extras, base_extras,
    )


def test_extra_rows_count_in_variance_like_in_the_aggregate_table(monkeypatch):
    """추가 행(사용자가 [+ 행 추가]로 만든 행)은 집계표와 **똑같이** 증감분석에도 더해진다.

    고치기 전에는 집계표(agg_months)만 추가 행을 더하고 증감분석 서버 계산은 리프만 더해서, 같은
    화면에서 위(연구소별 요약, 브라우저 계산)와 아래(계정별 상세, 서버 계산)가 다른 금액을 보여줬다.
    실제 데이터에서 그 차이가 176억이었다 — 숫자가 틀린 게 아니라 두 표가 다른 걸 세고 있었다.

    자리(anchor_key)가 가리키는 집계와 그 상위 합계에만 올라가야 한다: 전산비에 얹은 행은
    전산비·개발비·개발비(매출)에 들어가고 인건비에는 안 들어간다.
    """
    labs = {"DSRA": _lab("DSRA", "USD")}
    rate = {"USD": [1000.0]}
    cells = _cells_with_rates(
        monkeypatch, labs,
        cur_d={"DSRA": {"sal": [100.0]}}, base_d={"DSRA": {"sal": [100.0]}},
        rate_cur=rate, rate_base=rate,
        cur_extras={"DSRA": {"it": [40.0]}},   # 전산비 아래 얹은 추가 행
        base_extras={"DSRA": {"it": [10.0]}},
    )
    # 전산비 칸: 추가 행만 있는 자리라 전액이 추가 행에서 온다(40 − 10 = 30 → ×환율)
    assert cells["it"]["cur"] == pytest.approx(40.0 * 1000)
    assert cells["it"]["base"] == pytest.approx(10.0 * 1000)
    # 개발비(매출)에는 급여 100 + 추가 행이 같이 들어간다
    assert cells["tot"]["cur"] == pytest.approx((100.0 + 40.0) * 1000)
    assert cells["tot"]["base"] == pytest.approx((100.0 + 10.0) * 1000)
    # 인건비에는 안 들어간다 — 자리가 전산비이기 때문이다
    assert cells["labor"]["cur"] == pytest.approx(100.0 * 1000)
    # 환율이 그대로면 증감 전부가 환차外다(환차 0) — 추가 행도 같은 분해를 지난다
    assert cells["tot"]["fx_local"] == 0 and cells["tot"]["fx_usd"] == 0
    assert cells["tot"]["fx_outside"] == pytest.approx(30.0 * 1000)


def test_extra_rows_do_not_touch_headcount(monkeypatch):
    """인력(hc)은 명이라 추가 행을 더하지 않는다 — 금액을 인원에 더하면 인당 금액이 전부 깨진다.
    집계표도 같은 규칙이다(anchor_key가 hc면 기타경비로 떨어뜨린다)."""
    labs = {"DSRA": _lab("DSRA", "USD")}
    rate = {"USD": [1000.0]}
    cells = _cells_with_rates(
        monkeypatch, labs,
        cur_d={"DSRA": {"hc": [10.0]}}, base_d={"DSRA": {"hc": [10.0]}},
        rate_cur=rate, rate_base=rate,
        cur_extras={"DSRA": {"misc": [40.0]}}, base_extras={"DSRA": {"misc": [40.0]}},
    )
    assert cells["hc"]["cur"] == pytest.approx(10.0) and cells["hc"]["base"] == pytest.approx(10.0)


def test_usd_lab_has_no_local_fx_step(monkeypatch):
    """USD 연구소는 현지→$ 단계가 없으므로 fx_local이 0이고, 환차는 $→원 하나뿐이다."""
    labs = {"DSRA": _lab("DSRA", "USD")}
    cells = _cells_with_rates(
        monkeypatch, labs,
        cur_d={"DSRA": {"sal": [100.0]}}, base_d={"DSRA": {"sal": [100.0]}},
        rate_cur={"USD": [1400.0]}, rate_base={"USD": [1300.0]},
    )
    sal = cells["sal"]
    assert sal["fx_local"] == 0
    # 금액은 안 변했고 환율만 100원 올랐다 → 전부 환차($→원)다.
    # 저장값이 $라 환차는 `$ × 환율차`다(전환 전에는 K$라 ×1000이 더 붙었다).
    assert sal["fx_usd"] == pytest.approx(100 * 100)
    assert sal["fx_outside"] == pytest.approx(0)


def test_local_currency_lab_splits_fx_into_two_steps(monkeypatch):
    """USD 외 통화 연구소는 현지→$와 $→원을 따로 낸다. 세 칸(현지→$ · $→원 · 환차外)의 합은
    반드시 증감과 같아야 한다 — 어긋나면 "증감 중 환차가 얼마"를 설명할 수 없다."""
    labs = {"SIRC": _lab("SIRC", "ILS")}
    cells = _cells_with_rates(
        monkeypatch, labs,
        cur_d={"SIRC": {"sal": [120.0]}}, base_d={"SIRC": {"sal": [100.0]}},
        rate_cur={"USD": [1400.0], "ILS": [3.6]},
        rate_base={"USD": [1300.0], "ILS": [3.0]},
    )
    sal = cells["sal"]
    assert sal["fx_local"] != 0  # 현지통화가 $ 대비 움직인 몫이 실제로 잡힌다
    assert sal["fx_usd"] != 0
    diff = sal["cur"] - sal["base"]
    assert diff == pytest.approx(sal["fx_local"] + sal["fx_usd"] + sal["fx_outside"])


def test_mixed_currencies_still_add_up(monkeypatch):
    """통화가 섞인 전체 합계에서도 두 환차 칸의 합이 환차 계와 맞는다 — 그래서 "전체 연구소"로
    봐도 환차 열을 믿을 수 있다."""
    labs = {"DSRA": _lab("DSRA", "USD"), "SIRC": _lab("SIRC", "ILS"), "SSIR": _lab("SSIR", "INR")}
    cells = _cells_with_rates(
        monkeypatch, labs,
        cur_d={pk: {"sal": [100.0 + i * 10]} for i, pk in enumerate(labs)},
        base_d={pk: {"sal": [90.0 + i * 10]} for i, pk in enumerate(labs)},
        rate_cur={"USD": [1400.0], "ILS": [3.6], "INR": [90.0]},
        rate_base={"USD": [1300.0], "ILS": [3.0], "INR": [85.0]},
    )
    sal = cells["sal"]
    diff = sal["cur"] - sal["base"]
    assert diff == pytest.approx(sal["fx_local"] + sal["fx_usd"] + sal["fx_outside"])


def test_headcount_is_averaged_not_rate_converted(monkeypatch):
    """인력은 금액이 아니라 명이다 — 환율을 곱하지 않고 기간 평균을 낸다(합을 내면 12배가 된다)."""
    labs = {"DSRA": _lab("DSRA", "USD")}
    cells = _cells_with_rates(
        monkeypatch, labs,
        cur_d={"DSRA": {"hc": [10.0, 20.0, 30.0]}}, base_d={"DSRA": {"hc": [10.0, 10.0, 10.0]}},
        rate_cur={"USD": [1400.0] * 3}, rate_base={"USD": [1300.0] * 3}, ix=(0, 1, 2),
    )
    assert cells["hc"]["cur"] == pytest.approx(20.0)  # 평균이지 합(60)이 아니다
    assert cells["hc"]["fx_usd"] == 0 and cells["hc"]["fx_outside"] == 0


def test_variance_rows_internally_consistent(db):
    """실제 DB 데이터로도 모든 금액 행에서 증감 = 환차 + 환차外가 성립하고, 소계가 하위 합과 맞는다."""
    month = current_basis_month(db) or 9
    try:
        r = ov.get_variance(2026, "prev", month, "forecast", "y", "ALL", None, "", db)
    except HTTPException as e:
        pytest.skip(f"비교 기준 데이터가 없어 스킵: {e.detail}")
    for row in r.rows:
        if row.unit == "krw":
            assert row.diff == pytest.approx(row.fx + row.fx_outside, abs=1.0), row.category_key
    by = {x.category_key: x for x in r.rows}
    assert by["labor"].diff == pytest.approx(sum(by[k].diff for k in ("sal", "bonus", "welf")), abs=1.0)
    assert by["tot"].diff == pytest.approx(by["A"].diff + by["B"].diff, abs=1.0)


def test_plan_comparison_cannot_split_fx(db):
    """경영계획은 속보/실적 환율 기준이 없어 환차를 가를 수 없다 — 환차 칸이 0이면 "환차가 없었다"로
    읽히므로, 응답 note에 그 사실을 적어 내려보낸다."""
    month = current_basis_month(db) or 9
    try:
        r = ov.get_variance(2026, "plan", month, "forecast", "y", "ALL", None, "", db)
    except HTTPException as e:
        pytest.skip(f"경영계획 데이터가 없어 스킵: {e.detail}")
    assert all(x.fx == 0 for x in r.rows)
    assert "환차를 가를 수 없다" in r.note


# ---------- 증감사유 ----------
def _reason_key(**over):
    base = dict(
        basis_year=2026, basis_month=9, kind="forecast", base_version="prev", period="y", lab_scope="ALL"
    )
    base.update(over)
    return base


def test_saved_reason_survives_amount_change_and_flags_recheck(db):
    """재업로드로 금액이 바뀌면 사유는 그대로 두고 재확인만 켠다 — 담당자가 쓴 문장을 서버가
    버리면 매달 처음부터 다시 써야 한다(사양: 사유 유지 + "재확인 필요")."""
    key = _reason_key()
    saved = ov.upsert_variance_reason(
        VarianceReasonIn(
            **key, category_key="travel", source_text="Increase due to 3 more business trips",
            reason_text="출장 3건 증가", current_diff=1_000_000_000, commit=True,
        ),
        db,
    )
    assert saved.saved_diff == pytest.approx(1_000_000_000)
    assert saved.needs_recheck is False and saved.saved_at

    after = ov.upsert_variance_reason(
        VarianceReasonIn(**key, category_key="travel", current_diff=1_900_000_000, commit=False), db
    )
    assert after.reason_text == "출장 3건 증가"  # 지워지지 않는다
    assert after.needs_recheck is True


def test_tiny_amount_drift_is_not_a_recheck(db):
    """환율 소수점 때문에 몇 원씩 흔들리는 건 변동으로 보지 않는다 — 그걸 변동으로 치면 모든 사유에
    재확인 딱지가 붙어 경고가 무의미해진다."""
    key = _reason_key()
    ov.upsert_variance_reason(
        VarianceReasonIn(**key, category_key="edu", reason_text="교육 증가", current_diff=1_000_000_000, commit=True),
        db,
    )
    after = ov.upsert_variance_reason(
        VarianceReasonIn(**key, category_key="edu", current_diff=1_000_000_500, commit=False), db
    )
    assert after.needs_recheck is False


def test_resaving_clears_recheck(db):
    """[저장]을 다시 누르면 기준점이 지금 금액으로 다시 잡히고 재확인 딱지가 지워진다."""
    key = _reason_key()
    ov.upsert_variance_reason(
        VarianceReasonIn(**key, category_key="rent", reason_text="임차료 인상", current_diff=1e9, commit=True), db
    )
    ov.upsert_variance_reason(VarianceReasonIn(**key, category_key="rent", current_diff=2e9, commit=False), db)
    again = ov.upsert_variance_reason(
        VarianceReasonIn(**key, category_key="rent", reason_text="임차료 인상(재확인)", current_diff=2e9, commit=True),
        db,
    )
    assert again.needs_recheck is False
    assert again.saved_diff == pytest.approx(2e9)


def test_stage_is_rejected_on_forecast(db):
    """1차/2차 구분은 실적에만 있다. 속보에 stage를 주면 같은 화면 값이 두 군데로 나뉘어 저장된다."""
    with pytest.raises(HTTPException) as e:
        ov.upsert_variance_reason(
            VarianceReasonIn(**_reason_key(stage="first"), category_key="travel", reason_text="x"), db
        )
    assert e.value.status_code == 400


def test_first_and_second_reasons_stored_separately(db):
    """1차 사유와 2차 사유는 따로 저장된다 — 1차는 보고까지 끝난 값이라 2차가 덮어쓰면 안 된다."""
    db.add(CloseAccountStage(account_key="overseas", basis_year=2026, basis_month=8, mapping_done=True))
    db.flush()
    first = ov.upsert_variance_reason(
        VarianceReasonIn(
            **_reason_key(basis_month=8, kind="actual", stage="first"), category_key="labor",
            reason_text="1차: ERP 약식 기준 감소", current_diff=-1e9,
        ),
        db,
    )
    second = ov.upsert_variance_reason(
        VarianceReasonIn(
            **_reason_key(basis_month=8, kind="actual", stage="second"), category_key="labor",
            reason_text="2차: PL 확정 후 감소폭 축소", current_diff=-5e8,
        ),
        db,
    )
    assert first.reason_text != second.reason_text
    rows = ov.list_variance_reasons(2026, "prev", "y", 8, "actual", "first", "ALL", None, db)
    assert [r.reason_text for r in rows] == ["1차: ERP 약식 기준 감소"]


def _check(db, month, **kw):
    """확정 경고 조회 — 반복되는 인자를 한곳에 모아 둔다."""
    return ov.check_variance_reasons(
        basis_year=2026, base_version="prev", period="y", basis_month=month,
        kind="forecast", stage="", lab_scope="ALL", plan_no=None, db=db, **kw,
    )


def test_reason_check_lists_only_big_moves_without_reason(db):
    """확정 경고는 증감률이 기준치를 넘었는데 사유가 없는 칸만 모은다. 집계 행(개발비(매출) 등)과
    인력·(%) 행은 빠진다 — 하위 계정의 합이라 같은 사유를 두 번 쓰게 된다."""
    month = current_basis_month(db) or 9
    # 인자를 키워드로 넘긴다 — 이 엔드포인트는 선택 파라미터가 계속 늘어서(threshold_amount 등)
    # 위치로 넘기면 새 파라미터가 끼어들 때 db가 그 자리로 밀려 들어간다(실제로 한 번 그랬다).
    try:
        chk = _check(db, month, threshold_pct=0.01)
    except HTTPException as e:
        pytest.skip(f"비교 기준 데이터가 없어 스킵: {e.detail}")
    assert chk.threshold_pct == 0.01
    keys = {g.category_key for g in chk.missing + chk.stale}
    assert not (keys & {"tot", "A", "hr", "labor", "asset", "hc", "pct"})
    assert all(abs(g.diff_pct) >= 0.01 for g in chk.missing)


def test_reason_check_amount_threshold_narrows(db):
    """"1억 이상 변동만 사유 써줘" — 금액 기준은 비율 기준과 **같이** 걸린다(둘 다 넘어야 한다).

    이상 감지의 급변 기준과 같은 결합이다(anomaly.py) — 비율만 보면 금액이 작은 항목이 전부
    걸려서 경고가 수십 개 뜨고, 그러면 아무도 안 읽는다."""
    month = current_basis_month(db) or 9
    try:
        loose = _check(db, month, threshold_pct=0.01)
    except HTTPException as e:
        pytest.skip(f"비교 기준 데이터가 없어 스킵: {e.detail}")

    tight = _check(db, month, threshold_pct=0.01, threshold_amount=1e8)
    assert tight.threshold_amount == 1e8
    assert all(g.diff is None or abs(g.diff) >= 1e8 for g in tight.missing)
    # 좁힌 결과는 넓은 결과의 부분집합이어야 한다 — 금액 기준이 OR로 붙으면 오히려 늘어난다.
    assert {g.category_key for g in tight.missing} <= {g.category_key for g in loose.missing}
    # 어떤 기준으로 걸러낸 결과인지 문장에 밝힌다 — 안 밝히면 "사유 다 썼다"를 전체 기준으로 읽는다.
    assert "1.0억원 이상" in tight.note


def test_reason_check_amount_only_when_pct_is_off(db):
    """threshold_pct=0이면 비율 조건이 꺼지고 금액만 본다 — 5%짜리 50억 변동도 잡으려면 이게 필요하다."""
    month = current_basis_month(db) or 9
    try:
        chk = _check(db, month, threshold_pct=0, threshold_amount=1e8)
    except HTTPException as e:
        pytest.skip(f"비교 기준 데이터가 없어 스킵: {e.detail}")
    assert "증감률" not in chk.note
    assert all(g.diff is None or abs(g.diff) >= 1e8 for g in chk.missing)


# ---------- 증감 동인 표 ----------
def _driver(**over):
    body = dict(
        basis_year=2026, basis_month=9, kind="forecast", lab_scope="ALL", driver_key="labor_hc_rate",
        title="인건비 동인 — 인력수 × 인당 단가", target_key="labor",
        factors=[
            VarianceDriverFactor(label="인력수", op="ref", operands=["hc"], unit="hc"),
            VarianceDriverFactor(label="인당 인건비", op="div_elementwise", operands=["labor", "hc"], unit="krw"),
        ],
        requested_text="인건비를 인력수 × 인당 단가로", carry_over=False,
    )
    body.update(over)
    return VarianceDriverIn(**body)


def test_driver_without_approval_does_not_follow_to_next_version(db):
    """승인(carry_over) 없이 붙인 동인 표는 그 버전에서만 보인다 — 그 달만 보려고 만든 분해가
    매달 따라붙으면 안 된다."""
    ov.upsert_variance_driver(_driver(), db)
    here = ov.list_variance_drivers(2026, 9, "forecast", "ALL", "prev", "y", None, False, db)
    assert [d.driver_key for d in here] == ["labor_hc_rate"]
    assert ov.list_variance_drivers(2026, 10, "forecast", "ALL", "prev", "y", None, False, db) == []


def test_approved_driver_follows_to_next_version_but_marked(db):
    """승인하면 다음 버전에도 따라오고, 그 버전에서 만든 게 아니라는 표시(created_here=False)와
    원래 버전(created_basis)이 같이 온다 — 화면이 "이전 버전에서 유지" 뱃지를 붙일 수 있게."""
    ov.upsert_variance_driver(_driver(carry_over=True), db)
    nxt = ov.list_variance_drivers(2026, 10, "forecast", "ALL", "prev", "y", None, False, db)
    assert len(nxt) == 1
    assert nxt[0].created_here is False
    assert nxt[0].created_basis == "26.09 속보"


def test_driver_in_this_version_wins_over_carried_one(db):
    """같은 키가 양쪽에 있으면 이 버전에 붙인 것을 쓴다 — 이번에 고쳐 둔 표가 지난달 설정에
    덮이면 안 된다."""
    ov.upsert_variance_driver(_driver(carry_over=True), db)
    ov.upsert_variance_driver(_driver(basis_month=10, title="10월에 고친 제목"), db)
    rows = ov.list_variance_drivers(2026, 10, "forecast", "ALL", "prev", "y", None, False, db)
    assert [r.title for r in rows] == ["10월에 고친 제목"]
    assert rows[0].created_here is True


def test_driver_values_are_computed_not_stored(db):
    """동인 금액은 저장하지 않고 조회할 때마다 계산한다 — 원본이 업로드로 바뀌면 같이 바뀌어야 한다.
    근거 문장(basis_text)도 같이 와서 "인건비 ÷ 인력"을 화면·챗봇이 그대로 보여줄 수 있다."""
    ov.upsert_variance_driver(_driver(), db)
    rows = ov.list_variance_drivers(2026, 9, "forecast", "ALL", "prev", "y", None, True, db)
    if not rows[0].rows:
        pytest.skip("비교 기준 데이터가 없어 동인 값을 계산할 수 없어 스킵")
    per_head = next(r for r in rows[0].rows if r.label == "인당 인건비")
    assert per_head.cur is not None and per_head.base is not None
    assert "인건비 ÷ 인력" in per_head.basis_text


def test_bad_driver_formula_is_rejected(db):
    with pytest.raises(HTTPException) as e:
        ov.upsert_variance_driver(
            _driver(factors=[VarianceDriverFactor(label="x", op="div_elementwise", operands=["labor"])]), db
        )
    assert e.value.status_code == 400
    with pytest.raises(HTTPException):
        ov.upsert_variance_driver(_driver(factors=[VarianceDriverFactor(label="x", op="멋대로", operands=["labor"])]), db)


# ---------- 실적 1차 ----------
def test_first_tab_is_closed_until_close_is_done(db):
    """실적결산 1차가 끝나지 않으면 가져올 ERP 값이 없다 — 화면이 1차 탭을 비활성으로 둘 수 있게
    close_first_done=False와 그 이유를 내려준다. 그 상태로는 고정도 할 수 없다."""
    st = ov.get_actual_stage(2026, 7, db)
    assert st.close_first_done is False
    assert "실적결산 1차" in st.note
    with pytest.raises(HTTPException) as e:
        ov.update_actual_stage(ActualStageIn(basis_year=2026, basis_month=7, first_locked=True), db)
    assert e.value.status_code == 400


def test_first_lock_blocks_writes_until_released(db):
    """[1차 고정]은 보고까지 끝난 값·사유를 지킨다 — 켜진 동안 값과 사유 쓰기가 모두 막히고,
    해제하면 다시 쓸 수 있다."""
    ov.update_actual_stage(ActualStageIn(basis_year=2026, basis_month=7, close_first_done=True), db)
    ov.upsert_actual_first(
        ActualFirstIn(basis_year=2026, basis_month=7, lab_code="SIRC", local_amount=12_000_000), db
    )
    locked = ov.update_actual_stage(ActualStageIn(basis_year=2026, basis_month=7, first_locked=True), db)
    assert locked.first_locked and locked.first_locked_at

    with pytest.raises(HTTPException) as e:
        ov.upsert_actual_first(ActualFirstIn(basis_year=2026, basis_month=7, lab_code="SIRC", local_amount=1), db)
    assert e.value.status_code == 409
    with pytest.raises(HTTPException) as e2:
        ov.upsert_variance_reason(
            VarianceReasonIn(
                **_reason_key(basis_month=7, kind="actual", stage="first"), category_key="tot", reason_text="x"
            ),
            db,
        )
    assert e2.value.status_code == 409

    ov.update_actual_stage(ActualStageIn(basis_year=2026, basis_month=7, first_locked=False), db)
    ov.upsert_actual_first(
        ActualFirstIn(basis_year=2026, basis_month=7, lab_code="SIRC", local_amount=13_000_000), db
    )


def test_first_lock_does_not_touch_version_status(db):
    """1차 고정은 버전 확정과 다른 층의 결정이다 — 2차 작업이 남아 있으므로 버전 status는 그대로여야
    한다(사양: status 영향 없음)."""
    from app.shared.models.version import Version

    before = [
        (v.year, v.month, v.kind, v.status)
        for v in db.scalars(__import__("sqlalchemy").select(Version))
    ]
    ov.update_actual_stage(ActualStageIn(basis_year=2026, basis_month=7, close_first_done=True), db)
    ov.update_actual_stage(ActualStageIn(basis_year=2026, basis_month=7, first_locked=True), db)
    after = [
        (v.year, v.month, v.kind, v.status)
        for v in db.scalars(__import__("sqlalchemy").select(Version))
    ]
    assert before == after


def test_first_converts_local_currency_to_dollar(db):
    """1차는 현지통화 총액을 현지/$ → 원/$ 순서로 환산한다. 비용 데이터와 같은 $ 단위로 맞춰야
    2차와 더하거나 비교할 수 있다."""
    ov.update_actual_stage(ActualStageIn(basis_year=2026, basis_month=7, close_first_done=True), db)
    ov.upsert_actual_first(
        ActualFirstIn(basis_year=2026, basis_month=7, lab_code="SIRC", local_amount=12_000_000), db
    )
    res = ov.get_actual_first(2026, 7, "prev", None, db)
    sirc = next(r for r in res.rows if r.lab_code == "SIRC")
    assert sirc.currency == "ILS"
    # ILS/$ 환율은 3~4 사이라 1,200만 ILS는 $3,000,000~4,500,000 범위다(= 3,000~4,500 K$).
    assert 2_000_000 < sirc.usd_amount < 6_000_000
    assert sirc.diff == pytest.approx(sirc.fx + sirc.fx_outside, abs=1.0)


def test_first_prefers_erp_supplied_dollar_amount(db):
    """ERP가 $ 환산액을 같이 줬으면 그 값을 이긴다 — 환율로 다시 계산하면 ERP 보고서와 어긋난다.
    그때는 현지→$ 단계를 ERP가 이미 밟았으므로 환차 현지→$를 가를 근거가 없다(0)."""
    ov.update_actual_stage(ActualStageIn(basis_year=2026, basis_month=7, close_first_done=True), db)
    ov.upsert_actual_first(
        ActualFirstIn(
            basis_year=2026, basis_month=7, lab_code="SIRC", local_amount=12_000_000, usd_amount=4_000.0
        ),
        db,
    )
    res = ov.get_actual_first(2026, 7, "prev", None, db)
    sirc = next(r for r in res.rows if r.lab_code == "SIRC")
    assert sirc.usd_from_source is True
    assert sirc.usd_amount == pytest.approx(4_000.0)
    assert sirc.fx_local == 0
    assert sirc.diff == pytest.approx(sirc.fx + sirc.fx_outside, abs=1.0)


def test_first_total_leaves_local_currency_blank(db):
    """합계의 현지통화 칸은 비워 둔다 — 통화가 서로 다른 금액을 더한 수는 뜻이 없다."""
    ov.update_actual_stage(ActualStageIn(basis_year=2026, basis_month=7, close_first_done=True), db)
    ov.upsert_actual_first(
        ActualFirstIn(basis_year=2026, basis_month=7, lab_code="SIRC", local_amount=12_000_000), db
    )
    ov.upsert_actual_first(
        ActualFirstIn(basis_year=2026, basis_month=7, lab_code="DSRA", local_amount=5_000_000), db
    )
    res = ov.get_actual_first(2026, 7, "prev", None, db)
    assert res.total.local_amount == 0
    assert res.total.currency == ""
    assert res.total.krw_amount == pytest.approx(sum(r.krw_amount for r in res.rows))


def test_stage_shift_reports_missing_first_instead_of_zero(db):
    """1차가 없으면 변동을 0으로 답하지 않고 "1차 데이터가 없다"고 밝힌다 — 0은 "안 바뀌었다"로
    읽힌다."""
    res = ov.get_actual_stage_shift(2026, 6, db)
    assert res.has_first is False
    assert "1차 데이터가 없어" in res.note


def test_stage_shift_uses_one_rate_so_only_amounts_differ(db):
    """1차 → 2차 변동은 양쪽을 같은(그 달 실적) 환율로 환산한다 — 환율 차이가 아니라 금액 차이만
    남겨야 "ERP 약식과 PL이 얼마나 다른지"를 볼 수 있다."""
    ov.update_actual_stage(ActualStageIn(basis_year=2026, basis_month=7, close_first_done=True), db)
    ov.upsert_actual_first(
        ActualFirstIn(
            basis_year=2026, basis_month=7, lab_code="SIRC", local_amount=12_000_000, reason_text="채용 지연"
        ),
        db,
    )
    res = ov.get_actual_stage_shift(2026, 7, db)
    assert res.has_first is True
    sirc = next(r for r in res.rows if r.lab_code == "SIRC")
    assert sirc.first_reason == "채용 지연"
    assert sirc.diff == pytest.approx(sirc.second_krw - sirc.first_krw)
    assert res.total.diff == pytest.approx(sum(r.second_krw - r.first_krw for r in res.rows))


# ---------- 연구소별 요약 표(화면 위)와 계정별 표(화면 아래)가 같은 숫자인가 ----------


def test_lab_table_total_matches_the_category_table(db):
    """연구소별 요약의 "전체 연구소" 줄 = 계정별 표의 개발비(매출) 줄.

    두 표는 같은 화면에 위·아래로 같이 떠 있다. 축만 반대(연구소 × 기간 / 계정 × 기간)지 같은
    금액을 다르게 자른 것이라 합이 어긋나면 둘 중 하나가 틀린 것이다. 2026-11-19 전에는 위 표만
    브라우저가 계산해서 실제로 연간 개발비가 25억 벌어져 있었다 — 그 재발을 막는 계약이다.
    """
    try:
        labs = ov.get_variance_labs(basis_year=2026, base_version="prev", basis_month=9, db=db)
        table = ov.get_variance_table(basis_year=2026, base_version="prev", basis_month=9, db=db)
    except HTTPException as e:  # 데이터가 없는 환경
        pytest.skip(f"비교할 데이터가 없다: {e.detail}")

    total = next(r for r in labs.labs if r.is_total)
    for period in ("y", "m9", "q3"):
        rows = {r.category_key: r for r in table.periods[period]}
        cell, tot = total.cells[period], rows["tot"]
        assert cell.diff == pytest.approx(tot.diff, abs=1.0), f"{period}: 증감"
        assert cell.fx_local == pytest.approx(tot.fx_local, abs=1.0), f"{period}: 환차 현지→$"
        assert cell.fx_usd == pytest.approx(tot.fx_usd, abs=1.0), f"{period}: 환차 $→원"
        # 환차外을 인건비/인건비外로 가른 두 칸의 합 = 계정별 표의 개발비 환차外
        assert cell.labor_outside + cell.non_labor_outside == pytest.approx(tot.fx_outside, abs=1.0)
        # 인건비 칸은 계정별 표의 인건비 줄과 같아야 한다
        assert cell.labor_outside == pytest.approx(rows["labor"].fx_outside, abs=1.0), f"{period}: 인건비"


def test_lab_rows_add_up_to_the_total_row(db):
    """연구소 줄을 전부 더하면 "전체 연구소" 줄이다 — 한 연구소가 빠지거나 두 번 세이면 어긋난다
    (파트가 있는 연구소(SARC)를 합치는 자리라 실제로 틀리기 쉽다)."""
    try:
        labs = ov.get_variance_labs(basis_year=2026, base_version="prev", basis_month=9, db=db)
    except HTTPException as e:
        pytest.skip(f"비교할 데이터가 없다: {e.detail}")

    total = next(r for r in labs.labs if r.is_total)
    each = [r for r in labs.labs if not r.is_total]
    assert each, "연구소 줄이 하나도 없다"
    for period in ("y", "m9"):
        for field in ("diff", "fx_local", "fx_usd", "labor_outside", "non_labor_outside"):
            got = sum(getattr(r.cells[period], field) for r in each)
            assert got == pytest.approx(getattr(total.cells[period], field), abs=1.0), f"{period}/{field}"
