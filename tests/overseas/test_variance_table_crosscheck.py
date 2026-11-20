"""증감분석 표 대조 — compute_variance_table(백엔드) = ovAnalysis(core.js), 17기간 × 계정.

PLAN.md Phase 6. 집계 화면(①~④)에 이어 증감분석도 서버 계산으로 넘기는 중이고, 이 테스트가
"넘겨도 숫자가 그대로"임을 보장한다.

**환차를 가르는 계산이라 두 버전의 환율을 다르게 줘야 한다.** 같은 환율을 주면 환차가 통째로
0이 되어 어떤 구현이든 통과한다(개발 DB가 실제로 그 상태다 — 속보와 전월 실적의 원/$가 같다).

이 테스트가 잡은 것: 백엔드 (%) 행의 분모가 개발비(A)였고 화면은 개발비(매출)(tot)이었다.
MARK UP이 0인 개발 데이터에서는 두 값이 같아서 드러나지 않았다.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.accounts.overseas.cost_groups import AGGREGATE_ROWS
from app.accounts.overseas.services.variance import variance_rows

HARNESS = Path(__file__).parent / "js_formula_harness.mjs"
REPO = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(
    not shutil.which("node") or not (REPO / "frontend-html/shared/core.js").exists(),
    reason="node 또는 프런트 소스(frontend-html/shared/core.js)가 없어 대조할 수 없다",
)

PERIODS = ["y", "q1", "q2", "q3", "q4"] + [f"m{m}" for m in range(1, 13)]

LABS_JS = [
    {"c": "SARC", "n": "SARC+ACL", "ctry": "미국", "cur": "USD", "parts": [["CPU", "CPU"]]},
    # 현지통화 연구소를 꼭 넣는다 — 환차가 현지→$와 $→원 두 단계로 갈리는 경로는 여기만 지난다.
    {"c": "SIRC", "n": "SIRC", "ctry": "이스라엘", "cur": "ILS"},
]
# 두 버전의 환율을 다르게 둔다(그래야 환차가 0이 아니다). 달마다도 다르게 — 한 값이면
# "월별 환율을 쓰는지 연평균을 쓰는지"가 안 갈린다.
RATE_CUR = [1.40 + 0.01 * j for j in range(12)]
RATE_BASE = [1.30 + 0.01 * j for j in range(12)]
LOCAL_CUR = [3.60 + 0.01 * j for j in range(12)]    # ILS/$
LOCAL_BASE = [3.50 + 0.01 * j for j in range(12)]

# MARK UP(B)을 0이 아니게 둔다 — 0이면 (%) 행의 분모가 A든 tot든 같은 값이 나와 버그가 숨는다.
D_CUR = {
    "SARC:CPU": {"sal": [10.0] * 12, "B": [3.0] * 12, "hc": [4.0] * 12},
    "SIRC": {"sal": [7.0] * 12, "B": [2.0] * 12, "hc": [3.0] * 12},
}
D_BASE = {
    "SARC:CPU": {"sal": [8.0] * 12, "B": [2.0] * 12, "hc": [3.0] * 12},
    "SIRC": {"sal": [6.0] * 12, "B": [2.0] * 12, "hc": [3.0] * 12},
}


def _run_js(local: bool) -> dict:
    rate_cur = dict(std=RATE_CUR, cp=RATE_CUR)
    rate_base = dict(std=RATE_BASE, cp=RATE_BASE)
    if local:
        rate_cur["ils"] = LOCAL_CUR
        rate_base["ils"] = LOCAL_BASE
    job = json.dumps({
        "case": "variance",
        "input": {
            "dCur": D_CUR, "dBase": D_BASE,
            "ratesCur": rate_cur, "ratesBase": rate_base,
            "periods": PERIODS,
        },
        "labs": LABS_JS if local else [dict(L, cur="USD") for L in LABS_JS],
    }, ensure_ascii=False)
    p = subprocess.run(["node", str(HARNESS)], input=job, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        pytest.fail(
            "core.js의 ovAnalysis를 실행할 수 없다 — 증감분석 계산이 바뀌었으면 "
            f"js_formula_harness.mjs의 variance 케이스를 같이 고칠 것:\n{p.stderr[-3000:]}"
        )
    return json.loads(p.stdout)["core"]


@pytest.fixture(scope="module")
def js() -> dict:
    return _run_js(local=True)


def test_pct_row_uses_sales_as_denominator():
    """(%) 행의 분모는 개발비(매출)(tot)이다 — 개발비(A)가 아니다.

    근거는 화면이 사용자에게 적어 주는 문장이다("합계 화면은 MARK UP ÷ 개발비(매출) 계산값").
    집계표(aggregate.pl_rows)도 tot을 쓴다. 2026-11-16까지 증감분석만 A를 써서, MARK UP이 0이
    아닌 데이터에서 챗봇이 화면과 다른 %를 말하게 되어 있었다(18.75% vs 23.08%).
    """
    cells = {
        k: {"cur": 0.0, "base": 0.0, "fx_local": 0.0, "fx_usd": 0.0, "fx_outside": 0.0}
        for k, _l, _u, _m in AGGREGATE_ROWS
    }
    cells["A"].update(cur=13.0, base=10.0)
    cells["B"].update(cur=3.0, base=2.0)
    cells["tot"].update(cur=16.0, base=12.0)

    pct = next(r for r in variance_rows(cells) if r.unit == "pct")

    assert pct.cur == pytest.approx(3 / 16 * 100), "분모가 개발비(매출)이 아니다"
    assert pct.base == pytest.approx(2 / 12 * 100)
    # 두 분모가 실제로 다른 값을 내는 입력인지 같이 못 박는다 — 안 그러면 A로 되돌려도 통과한다.
    assert pct.cur != pytest.approx(3 / 13 * 100), "A와 tot이 같은 입력이면 이 테스트는 아무것도 못 잡는다"


def test_pct_row_is_blank_safe_when_sales_is_zero():
    """개발비(매출)가 0이면 비율을 0%로 둔다 — 화면도 같다(ovAnalysis의 삼항 연산).

    0%와 "낼 수 없음"을 가르는 건 집계표(pct_17은 null을 준다)와 다르다. 증감분석은 %p 차이를
    보여주는 표라 null을 넣으면 diff를 낼 수 없어서, 화면이 이미 0으로 두고 있다.
    """
    cells = {
        k: {"cur": 0.0, "base": 0.0, "fx_local": 0.0, "fx_usd": 0.0, "fx_outside": 0.0}
        for k, _l, _u, _m in AGGREGATE_ROWS
    }
    cells["B"].update(cur=3.0, base=2.0)   # tot은 0

    pct = next(r for r in variance_rows(cells) if r.unit == "pct")
    assert (pct.cur, pct.base, pct.diff) == (0.0, 0.0, 0.0)


@pytest.mark.parametrize("period", PERIODS)
def test_amounts_match_front(period, js):
    """17기간 전부 — 계정별 기준·당월·증감·환차(현지→$ · $→원)·환차外가 화면과 같은지.

    기간을 다 도는 이유: 분기·연간을 월 값에서 접어 만들면 인력(평균)과 비율(분자·분모를 각각
    더한 뒤 나눔)이 틀어진다. 서버가 기간마다 따로 계산하는지 여기서 걸린다.
    """
    front = js[period]
    assert front, f"{period}: 프런트가 아무 값도 안 냈다"
    # 프런트는 계정 키 → {c,b,d,fx,fxL,fxU,ot} 맵을 준다.
    for key in ("tot", "A", "B", "labor", "sal", "hc"):
        assert key in front, f"{period}: 프런트에 {key} 행이 없다"
        x = front[key]
        assert x["d"] == pytest.approx(x["c"] - x["b"], rel=1e-9), f"{period}/{key}: 증감 ≠ 당월 − 기준"
        if key != "hc":
            assert x["fx"] == pytest.approx(x["fxL"] + x["fxU"], rel=1e-9), (
                f"{period}/{key}: 환차 계 ≠ 현지→$ + $→원"
            )
            assert x["d"] == pytest.approx(x["fx"] + x["ot"], rel=1e-6), (
                f"{period}/{key}: 증감 ≠ 환차 + 환차外 — 분해가 금액을 잃거나 중복 세고 있다"
            )


def test_local_currency_lab_splits_fx_into_two_steps(js):
    """현지통화 연구소가 있으면 환차가 현지→$와 $→원 두 단계로 갈린다.

    USD 연구소만 있으면 현지→$ 단계가 없어 fxL이 항상 0이고, 그러면 분해 코드가 틀려도 테스트가
    통과한다 — 그래서 ILS 연구소를 입력에 넣었다. 이게 0이면 입력이 잘못된 것이다.
    """
    y = js["y"]["tot"]
    assert y["fxL"] != 0, "현지통화 연구소를 넣었는데 현지→$ 환차가 0이다 — 입력이나 분해가 잘못됐다"
    assert y["fxU"] != 0, "$→원 환차가 0이다 — 두 버전의 원/$를 다르게 줬는지 확인할 것"


def test_usd_only_has_no_local_fx_step():
    """USD 연구소만 있으면 현지→$ 환차는 0이다 — 그 단계가 존재하지 않는다."""
    js_usd = _run_js(local=False)
    assert js_usd["y"]["tot"]["fxL"] == 0


def test_headcount_is_averaged_not_summed(js):
    """인력은 기간 평균이다 — 더하면 연간이 12배가 된다.

    집계표에서 실제로 틀렸던 자리와 같은 모양이라(ov17의 avg) 여기서도 못 박는다.
    """
    m1, q1, y = js["m1"]["hc"], js["q1"]["hc"], js["y"]["hc"]
    assert y["c"] == pytest.approx(m1["c"], rel=1e-9), "달마다 인원이 같은 입력이라 연간 평균도 같아야 한다"
    assert q1["c"] == pytest.approx(m1["c"], rel=1e-9)
    assert y["ot"] == 0 and y["fx"] == 0, "인력은 명수라 환차를 가를 대상이 아니다"


# ---------- 실제 DB 데이터로 금액 대조 ----------
# 위 테스트들은 합성 입력으로 "행을 만드는 규칙"을 본다. 여기는 **백엔드가 실제로 쓴 입력**
# (두 버전 금액 · 두 기준의 환율)을 그대로 꺼내 core.js에 먹이고 금액을 맞춰 본다 —
# 화면을 서버 계산으로 갈아탈 때 숫자가 그대로인지는 이것만이 보장한다.


def _feed_front(setup, ix_periods: dict[str, tuple[int, ...]], db) -> dict:
    """백엔드 준비물 → core.js 입력. rate_arrays를 백엔드와 같은 인자로 불러 환율까지 같게 맞춘다."""
    from app.accounts.overseas.services.fxcalc import rate_arrays

    local_curs = {
        (lab.currency or "USD").upper()
        for pk, lab in setup.labs.items()
        if pk in setup.pks and (lab.currency or "USD").upper() != "USD"
    }
    ix = tuple(range(12))
    rc = rate_arrays(db, *setup.cur_fx, local_curs, ix)
    rb = rate_arrays(db, *setup.base_fx, local_curs, ix) if setup.split_fx else rc

    def rates(arr):
        # core.js는 현지환율을 소문자 통화 키로 찾는다(ovLcOf). CP도 같은 원/$ 배열을 쓴다.
        return {"std": arr["USD"], "cp": arr["USD"], **{c.lower(): arr[c] for c in local_curs}}

    labs_js = []
    by_code: dict[str, list] = {}
    for pk in sorted(setup.pks, key=lambda pk: (setup.labs[pk].sort_order, pk)):
        by_code.setdefault(setup.labs[pk].lab_code, []).append(setup.labs[pk])
    for code, rows in by_code.items():
        first, multi = rows[0], len(rows) > 1
        L = {"c": code, "n": first.name.split(" · ")[0] if multi else first.name,
             "ctry": first.country or "", "cur": (first.currency or "USD").upper()}
        if multi:
            L["parts"] = [[r.part_code, r.name.split(" · ", 1)[1] if " · " in r.name else r.part_code]
                          for r in rows]
        labs_js.append(L)

    job = json.dumps({
        "case": "variance",
        "input": {
            "dCur": {pk: setup.cur_d.get(pk, {}) for pk in setup.pks},
            "dBase": {pk: setup.base_d.get(pk, {}) for pk in setup.pks},
            "ratesCur": rates(rc), "ratesBase": rates(rb),
            "periods": list(ix_periods),
        },
        "labs": labs_js,
    }, ensure_ascii=False)
    p = subprocess.run(["node", str(HARNESS)], input=job, capture_output=True, text=True, timeout=180)
    if p.returncode != 0:
        pytest.fail(f"core.js 실행 실패:\n{p.stderr[-3000:]}")
    return json.loads(p.stdout)["core"]


@pytest.mark.parametrize("base_version", ["prev", "prior"])
def test_backend_table_matches_front_on_real_data(db, base_version):
    """`/overseas/variance-table`의 금액 = core.js ovAnalysis (같은 입력·같은 환율).

    비교 기준을 둘 다 도는 이유: 전실행(prev)과 전년(prior)은 비교 버전의 연·월·속보실적이
    달라서 환율 기준도 달라진다 — 한쪽만 보면 기준 해석이 틀린 걸 놓친다.
    """
    from app.accounts.overseas.services.variance import (
        _variance_setup,
        compute_variance_table,
        current_basis_month,
    )
    from app.shared.core.periods import PERIOD_IX
    from fastapi import HTTPException

    month = current_basis_month(db) or 1
    try:
        setup = _variance_setup(db, 2026, month, "forecast", base_version, "ALL", None)
        table = compute_variance_table(db, 2026, month, "forecast", base_version, "ALL", None)
    except HTTPException as e:
        pytest.skip(f"{base_version}: 비교할 데이터가 없다 — {e.detail}")

    front = _feed_front(setup, PERIOD_IX, db)

    checked = 0
    for period, rows in table.periods.items():
        f = front[period]
        for r in rows:
            if r.category_key == "pct":
                continue  # 비율은 위 test_pct_row_uses_sales_as_denominator가 따로 본다
            x = f.get(r.category_key)
            assert x is not None, f"{period}: 프런트에 {r.category_key} 행이 없다"
            where = f"{base_version}/{period}/{r.category_key}"
            # 금액 단위는 원이고 1원까지 허용한다 — 두 구현의 곱셈 순서가 달라서 1e-9원짜리
            # 부동소수점 노이즈가 남는다(0 vs -7.45e-09). 상대오차만 두면 0과 비교할 때 터진다.
            assert x["c"] == pytest.approx(r.cur, rel=1e-6, abs=1.0), f"{where}: 당월 금액이 다르다"
            assert x["b"] == pytest.approx(r.base, rel=1e-6, abs=1.0), f"{where}: 기준 금액이 다르다"
            assert x["d"] == pytest.approx(r.diff, rel=1e-6, abs=1.0), f"{where}: 증감이 다르다"
            if r.unit != "hc":
                assert x["fxL"] == pytest.approx(r.fx_local, rel=1e-6, abs=1.0), f"{where}: 현지→$ 환차가 다르다"
                assert x["fxU"] == pytest.approx(r.fx_usd, rel=1e-6, abs=1.0), f"{where}: $→원 환차가 다르다"
                assert x["ot"] == pytest.approx(r.fx_outside, rel=1e-6, abs=1.0), f"{where}: 환차外가 다르다"
            checked += 1
    assert checked > 100, f"대조한 칸이 {checked}개뿐이다 — 데이터가 비었는지 확인할 것"


# ---------- 인건비 분해(인원 효과 · 단가 효과) ----------


def test_labor_split_comes_with_the_table(db):
    """증감분석 표에 17기간 인건비 분해가 같이 온다 — 따로 부르면 기간마다 왕복한다."""
    from app.accounts.overseas.services.variance import compute_variance_table, current_basis_month
    from fastapi import HTTPException

    month = current_basis_month(db) or 1
    try:
        t = compute_variance_table(db, 2026, month, "forecast", "prev", "ALL", None)
    except HTTPException as e:
        pytest.skip(f"비교할 데이터가 없다 — {e.detail}")

    assert sorted(t.labor_split) == sorted(t.periods), "분해가 표와 다른 기간 묶음이다"


def test_labor_split_sums_to_the_labor_row(db):
    """인원 효과 + 단가 효과 + 미분해 = 증감표 인건비 행의 환차外.

    이게 어긋나면 "왜 늘었나"를 나눠 보여준 값이 표의 숫자와 안 맞는다 — 사용자가 가장 먼저
    검산하는 자리이고, 분해가 금액을 잃거나 중복 세고 있다는 뜻이다.
    """
    from app.accounts.overseas.services.variance import compute_variance_table, current_basis_month
    from fastapi import HTTPException

    month = current_basis_month(db) or 1
    try:
        t = compute_variance_table(db, 2026, month, "forecast", "prev", "ALL", None)
    except HTTPException as e:
        pytest.skip(f"비교할 데이터가 없다 — {e.detail}")
    if not t.labor_split:
        pytest.skip("인건비 분해를 낼 수 없는 기준이다")

    for period, sp in t.labor_split.items():
        labor = next(r for r in t.periods[period] if r.category_key == "labor")
        assert sp.hc_effect + sp.rate_effect + sp.unsplit == pytest.approx(
            labor.fx_outside, rel=1e-6, abs=1.0
        ), f"{period}: 분해 합이 인건비 환차外와 다르다"
        assert sp.fx_outside == pytest.approx(labor.fx_outside, rel=1e-6, abs=1.0)


def test_plan_comparison_has_no_labor_split(db):
    """경영계획 대비는 분해가 빈 dict다 — 환율 기준이 없어 인원·단가로 나눌 수 없다.

    0으로 채워 내려보내면 "분해했는데 효과가 0"으로 읽힌다. 화면도 같은 제약이라 그 줄을 안 그린다.
    """
    from app.accounts.overseas.services.variance import compute_variance_table, current_basis_month
    from fastapi import HTTPException

    month = current_basis_month(db) or 1
    try:
        t = compute_variance_table(db, 2026, month, "forecast", "plan", "ALL", None)
    except HTTPException as e:
        pytest.skip(f"경영계획 데이터가 없다 — {e.detail}")

    assert t.labor_split == {}, "경영계획 대비인데 분해가 들어 있다"
    assert t.periods, "표 자체는 나와야 한다(환차만 못 가른다)"


def test_endpoint_and_table_use_the_same_split(db):
    """`/overseas/labor-split`(한 기간)과 증감분석 표(17기간)가 같은 값을 낸다.

    같은 함수를 부르므로 성립해야 하는 성질이다 — 깨지면 식이 두 벌로 갈린 것이고, 화면과 챗봇이
    다른 답을 하게 된다(P1-3의 그 문제).
    """
    from app.accounts.overseas.endpoints.collect import get_labor_split
    from app.accounts.overseas.services.variance import compute_variance_table, current_basis_month
    from fastapi import HTTPException

    month = current_basis_month(db) or 1
    try:
        t = compute_variance_table(db, 2026, month, "forecast", "prev", "ALL", None)
        one = get_labor_split(basis_year=2026, base_version="prev", period="y", lab_scope="ALL", db=db)
    except HTTPException as e:
        pytest.skip(f"비교할 데이터가 없다 — {e.detail}")
    if not t.labor_split:
        pytest.skip("인건비 분해를 낼 수 없는 기준이다")

    y = t.labor_split["y"]
    for field in ("hc_base", "hc_cur", "per_head_base", "per_head_cur",
                  "hc_effect", "rate_effect", "unsplit", "fx_outside"):
        assert getattr(y, field) == pytest.approx(getattr(one, field), rel=1e-9), (
            f"{field}: 엔드포인트와 표의 분해가 다르다"
        )
    # 표에는 연구소별 상세를 싣지 않는다(화면이 합계만 쓴다) — 상세는 엔드포인트 쪽에만 있다.
    assert not hasattr(y, "labs"), "표에 안 쓰는 연구소별 상세가 실려 있다"
    assert one.labs, "엔드포인트는 연구소별 상세를 줘야 한다"
