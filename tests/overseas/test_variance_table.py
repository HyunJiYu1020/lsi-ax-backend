"""증감분석 표(compute_variance_table) — 행 구성 · 환차 분해 · 인건비 분해.

**2026-11-17: 프런트 대조(core.js ovAnalysis)를 걷어내고 백엔드 단독 검사로 바꿨다.**
증감분석 계정별 상세의 브라우저 계산(ovAnalysisLocal)을 지워서 이제 계산하는 쪽이 백엔드
하나뿐이다 — 대조할 상대가 없어졌다. 지운 테스트는 git 이력에 있다
(test_variance_table_crosscheck.py: test_amounts_match_front,
test_backend_table_matches_front_on_real_data, test_local_currency_lab_splits_fx_into_two_steps,
test_usd_only_has_no_local_fx_step, test_headcount_is_averaged_not_summed).

대조가 잡아 주던 건 대부분 **출력의 내부 정합성**이었다(증감 = 당월 − 기준, 환차 계 = 두 단계 합,
증감 = 환차 + 환차外, 인력은 평균). 그 성질은 계산자가 하나가 돼도 그대로 성립해야 하므로
백엔드 출력에 대고 그대로 검사한다 — 오히려 "실제로 계산하는 쪽"을 보게 됐다.

부수 효과로 이 파일은 node · frontend-html 유무와 무관해졌다. 전에는 프런트 소스가 없으면
(예: 백엔드만 clone한 CI) 이 파일 전체가 조용히 스킵돼서, 보호가 꺼진 걸 아무도 모르고
테스트는 녹색으로 남았다 — 같은 사고가 예전에 한 번 있었다(test_formula_crosscheck.py 주석).

대조가 과거에 잡은 것(기록): 백엔드 (%) 행의 분모가 개발비(A)였고 화면은 개발비(매출)(tot)이었다.
MARK UP이 0인 개발 데이터에서는 두 값이 같아서 드러나지 않았다 — 그래서 아래 pct 테스트는
MARK UP을 0이 아니게 두고, 두 분모가 실제로 다른 값을 내는지까지 같이 못 박는다.
"""

import pytest

from app.accounts.overseas.cost_groups import AGGREGATE_ROWS
from app.accounts.overseas.services.variance import variance_rows

# 금액 단위는 원이고 1원까지 허용한다 — 곱셈 순서 때문에 1e-9원짜리 부동소수점 노이즈가 남는다
# (0 vs -7.45e-09). 상대오차만 두면 0과 비교할 때 터진다.
MONEY = dict(rel=1e-6, abs=1.0)


def _empty_cells() -> dict[str, dict[str, float]]:
    return {
        k: {"cur": 0.0, "base": 0.0, "fx_local": 0.0, "fx_usd": 0.0, "fx_outside": 0.0}
        for k, _l, _u, _m in AGGREGATE_ROWS
    }


def _table(db, base_version="prev", lab_scope="ALL"):
    """증감분석 표 한 장. 비교할 데이터가 없으면 스킵한다."""
    from app.accounts.overseas.services.variance import compute_variance_table, current_basis_month
    from fastapi import HTTPException

    month = current_basis_month(db) or 1
    try:
        return compute_variance_table(db, 2026, month, "forecast", base_version, lab_scope, None)
    except HTTPException as e:
        pytest.skip(f"{base_version}/{lab_scope}: 비교할 데이터가 없다 — {e.detail}")


def _labs_by_currency(db) -> dict[str, list[str]]:
    """연구소 코드를 통화별로 묶는다 — 현지통화 연구소가 있는지로 환차 분해 테스트를 가른다."""
    from app.accounts.overseas.services.labs import lab_views

    from app.accounts.overseas.services.variance import current_basis_month

    month = current_basis_month(db) or 1
    out: dict[str, list[str]] = {}
    for lab in lab_views(db, f"2026-{month:02d}"):
        if not lab.is_active or lab.pk == "_ETC":
            continue
        cur = (lab.currency or "USD").upper()
        if lab.lab_code not in out.setdefault(cur, []):
            out[cur].append(lab.lab_code)
    return out


# ---------- (%) 행: 분모가 무엇인가 ----------


def test_pct_row_uses_sales_as_denominator():
    """(%) 행의 분모는 개발비(매출)(tot)이다 — 개발비(A)가 아니다.

    근거는 화면이 사용자에게 적어 주는 문장이다("합계 화면은 MARK UP ÷ 개발비(매출) 계산값").
    집계표(aggregate.pl_rows)도 tot을 쓴다. 2026-11-16까지 증감분석만 A를 써서, MARK UP이 0이
    아닌 데이터에서 챗봇이 화면과 다른 %를 말하게 되어 있었다(18.75% vs 23.08%).
    """
    cells = _empty_cells()
    cells["A"].update(cur=13.0, base=10.0)
    cells["B"].update(cur=3.0, base=2.0)
    cells["tot"].update(cur=16.0, base=12.0)

    pct = next(r for r in variance_rows(cells) if r.unit == "pct")

    assert pct.cur == pytest.approx(3 / 16 * 100), "분모가 개발비(매출)이 아니다"
    assert pct.base == pytest.approx(2 / 12 * 100)
    # 두 분모가 실제로 다른 값을 내는 입력인지 같이 못 박는다 — 안 그러면 A로 되돌려도 통과한다.
    assert pct.cur != pytest.approx(3 / 13 * 100), "A와 tot이 같은 입력이면 이 테스트는 아무것도 못 잡는다"


def test_pct_row_is_blank_safe_when_sales_is_zero():
    """개발비(매출)가 0이면 비율을 0%로 둔다.

    0%와 "낼 수 없음"을 가르는 건 집계표(pct_17은 null을 준다)와 다르다. 증감분석은 %p 차이를
    보여주는 표라 null을 넣으면 diff를 낼 수 없어서 0으로 둔다.
    """
    cells = _empty_cells()
    cells["B"].update(cur=3.0, base=2.0)   # tot은 0

    pct = next(r for r in variance_rows(cells) if r.unit == "pct")
    assert (pct.cur, pct.base, pct.diff) == (0.0, 0.0, 0.0)


# ---------- 출력의 내부 정합성 (17기간 × 전체 행) ----------


@pytest.mark.parametrize("base_version", ["prev", "prior"])
def test_diff_and_fx_split_are_consistent(db, base_version):
    """증감 = 당월 − 기준, 환차 계 = 현지→$ + $→원, 증감 = 환차 + 환차外.

    비교 기준을 둘 다 도는 이유: 전실행(prev)과 전년(prior)은 비교 버전의 연·월·속보실적이
    달라서 환율 기준도 달라진다 — 한쪽만 보면 기준 해석이 틀린 걸 놓친다.

    세 번째 식이 핵심이다. 환차 + 환차外가 증감과 안 맞으면 분해가 금액을 잃거나 중복 세고
    있다는 뜻이고, 사용자가 "왜 늘었나"를 검산하는 바로 그 자리가 틀어진다.
    """
    table = _table(db, base_version)

    checked = 0
    for period, rows in table.periods.items():
        for r in rows:
            if r.unit == "pct":
                continue  # 비율은 위 pct 테스트가 따로 본다
            where = f"{base_version}/{period}/{r.category_key}"
            assert r.diff == pytest.approx(r.cur - r.base, **MONEY), f"{where}: 증감 ≠ 당월 − 기준"
            if r.unit != "hc":
                assert r.fx == pytest.approx(r.fx_local + r.fx_usd, **MONEY), (
                    f"{where}: 환차 계 ≠ 현지→$ + $→원"
                )
                assert r.diff == pytest.approx(r.fx + r.fx_outside, **MONEY), (
                    f"{where}: 증감 ≠ 환차 + 환차外 — 분해가 금액을 잃거나 중복 세고 있다"
                )
            checked += 1
    assert checked > 100, f"검사한 칸이 {checked}개뿐이다 — 데이터가 비었는지 확인할 것"


def test_all_17_periods_are_computed(db):
    """17기간(12개월 · 4분기 · 연간)이 모두 나온다.

    기간을 다 내려줘야 하는 이유: 분기·연간을 화면이 월 값에서 접어 만들면 인력(평균)과
    비율(분자·분모를 각각 더한 뒤 나눔)이 틀어진다. 서버가 기간마다 따로 계산해서 준다.
    """
    table = _table(db)
    want = {"y", "q1", "q2", "q3", "q4"} | {f"m{m}" for m in range(1, 13)}
    assert set(table.periods) == want, "기간 묶음이 17개가 아니다"


def test_headcount_is_averaged_not_summed(db):
    """인력은 기간 평균이다 — 더하면 연간이 12배가 된다.

    집계표에서 실제로 틀렸던 자리와 같은 모양이라(to_17의 avg) 여기서도 못 박는다.
    인력은 명수라 환차를 가를 대상이 아니므로 환차 칸은 0이어야 한다.
    """
    table = _table(db)

    def hc(period):
        return next((r for r in table.periods[period] if r.unit == "hc"), None)

    y = hc("y")
    if y is None or not y.cur:
        pytest.skip("인력 데이터가 없다")

    months = [hc(f"m{m}").cur for m in range(1, 13)]
    assert y.cur == pytest.approx(sum(months) / 12, rel=1e-6), (
        "연간 인력이 월 평균이 아니다 — 합으로 접고 있으면 12배가 된다"
    )
    q1 = hc("q1")
    assert q1.cur == pytest.approx(sum(months[:3]) / 3, rel=1e-6), "분기 인력이 3개월 평균이 아니다"
    assert (y.fx, y.fx_outside) == (0.0, 0.0), "인력은 명수라 환차를 가를 대상이 아니다"


# ---------- 환차 2단 분해: 현지통화가 있을 때만 생기는 단계 ----------


def test_local_currency_lab_splits_fx_into_two_steps(db):
    """현지통화 연구소는 환차가 현지→$와 $→원 두 단계로 갈린다.

    USD 연구소만 보면 현지→$ 단계가 없어 fx_local이 항상 0이고, 그러면 분해 코드가 틀려도
    테스트가 통과한다 — 그래서 현지통화 연구소를 따로 골라서 본다.
    """
    by_cur = _labs_by_currency(db)
    local = [c for cur, codes in by_cur.items() if cur != "USD" for c in codes]
    if not local:
        pytest.skip("현지통화 연구소가 없어 2단 분해 경로를 지나지 않는다")

    table = _table(db, lab_scope=local[0])
    tot = next(r for r in table.periods["y"] if r.category_key == "tot")
    if tot.fx == 0:
        pytest.skip(f"{local[0]}: 두 버전의 환율이 같아 환차가 0이다 — 분해를 가를 수 없다")

    assert tot.fx_local != 0, (
        f"{local[0]}은 현지통화 연구소인데 현지→$ 환차가 0이다 — 2단 분해가 동작하지 않는다"
    )


def test_usd_lab_has_no_local_fx_step(db):
    """USD 연구소는 현지→$ 환차가 0이다 — 그 단계가 존재하지 않는다."""
    by_cur = _labs_by_currency(db)
    if not by_cur.get("USD"):
        pytest.skip("USD 연구소가 없다")

    table = _table(db, lab_scope=by_cur["USD"][0])
    for period, rows in table.periods.items():
        for r in rows:
            if r.unit in ("pct", "hc"):
                continue
            assert r.fx_local == 0, (
                f"{period}/{r.category_key}: USD 연구소인데 현지→$ 환차가 0이 아니다"
            )


# ---------- 인건비 분해(인원 효과 · 단가 효과) ----------


def test_labor_split_comes_with_the_table(db):
    """증감분석 표에 17기간 인건비 분해가 같이 온다 — 따로 부르면 기간마다 왕복한다."""
    t = _table(db)
    assert sorted(t.labor_split) == sorted(t.periods), "분해가 표와 다른 기간 묶음이다"


def test_labor_split_sums_to_the_labor_row(db):
    """인원 효과 + 단가 효과 + 미분해 = 증감표 인건비 행의 환차外.

    이게 어긋나면 "왜 늘었나"를 나눠 보여준 값이 표의 숫자와 안 맞는다 — 사용자가 가장 먼저
    검산하는 자리이고, 분해가 금액을 잃거나 중복 세고 있다는 뜻이다.
    """
    t = _table(db)
    if not t.labor_split:
        pytest.skip("인건비 분해를 낼 수 없는 기준이다")

    for period, sp in t.labor_split.items():
        labor = next(r for r in t.periods[period] if r.category_key == "labor")
        assert sp.hc_effect + sp.rate_effect + sp.unsplit == pytest.approx(
            labor.fx_outside, **MONEY
        ), f"{period}: 분해 합이 인건비 환차外와 다르다"
        assert sp.fx_outside == pytest.approx(labor.fx_outside, **MONEY)


def test_plan_comparison_has_no_labor_split(db):
    """경영계획 대비는 분해가 빈 dict다 — 환율 기준이 없어 인원·단가로 나눌 수 없다.

    0으로 채워 내려보내면 "분해했는데 효과가 0"으로 읽힌다. 화면도 같은 제약이라 그 줄을 안 그린다.
    """
    t = _table(db, base_version="plan")
    assert t.labor_split == {}, "경영계획 대비인데 분해가 들어 있다"
    assert t.periods, "표 자체는 나와야 한다(환차만 못 가른다)"


def test_endpoint_and_table_use_the_same_split(db):
    """`/overseas/labor-split`(한 기간)과 증감분석 표(17기간)가 같은 값을 낸다.

    같은 함수를 부르므로 성립해야 하는 성질이다 — 깨지면 식이 두 벌로 갈린 것이고, 화면과 챗봇이
    다른 답을 하게 된다.
    """
    from app.accounts.overseas.endpoints.collect import get_labor_split
    from fastapi import HTTPException

    t = _table(db)
    if not t.labor_split:
        pytest.skip("인건비 분해를 낼 수 없는 기준이다")
    try:
        one = get_labor_split(basis_year=2026, base_version="prev", period="y", lab_scope="ALL", db=db)
    except HTTPException as e:
        pytest.skip(f"비교할 데이터가 없다 — {e.detail}")

    y = t.labor_split["y"]
    for field in ("hc_base", "hc_cur", "per_head_base", "per_head_cur",
                  "hc_effect", "rate_effect", "unsplit", "fx_outside"):
        assert getattr(y, field) == pytest.approx(getattr(one, field), rel=1e-9), (
            f"{field}: 엔드포인트와 표의 분해가 다르다"
        )
    # 표에는 연구소별 상세를 싣지 않는다(화면이 합계만 쓴다) — 상세는 엔드포인트 쪽에만 있다.
    assert not hasattr(y, "labs"), "표에 안 쓰는 연구소별 상세가 실려 있다"
    assert one.labs, "엔드포인트는 연구소별 상세를 줘야 한다"
