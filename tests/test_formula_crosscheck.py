"""수식 대조 테스트 — 같은 입력에 core.js · calc.js · 백엔드가 같은 숫자를 내는지 본다.

PLAN.md P1-4: 환율 환산 · 인건비 분해 · 집계 트리 수식이 세 곳(frontend-html/shared/core.js,
frontend/src/logic/calc.js, 백엔드)에 복제돼 있다. 지금까지 유일한 안전장치는 overseas.py:912-915의
"식을 바꿀 때 세 곳을 같이 고칠 것" 주석뿐이었다. 한 곳만 고치면 화면 숫자 ≠ 챗봇 숫자가 되고,
그건 사용자가 가장 먼저 알아차리는 종류의 버그다.

이 테스트는 삼중화를 없애지 않는다(그건 Phase 6). 어긋나는 순간 빨개지게만 만든다.

프런트 수식을 파이썬으로 베껴 적지 않고 js_formula_harness.mjs로 **실제 실행**해서 비교한다 —
베껴 적으면 그게 네 번째 복제본이 되고, 베낀 쪽이 낡으면 테스트가 거짓으로 통과한다.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.accounts.overseas.endpoints.collect import yyyymm, get_labor_split, get_overseas_state
from app.accounts.overseas.services.labs import lab_views
from app.accounts.overseas.services.entries import current_basis_month
from app.accounts.overseas.services.aggregate import group_tree, krw_months
from app.accounts.overseas.cost_groups import GROUP_CALC, resolve_leaves
from app.accounts.overseas.services.fxcalc import rate_arrays

BASIS_YEAR = 2026
HARNESS = Path(__file__).parent / "js_formula_harness.mjs"
REPO = Path(__file__).resolve().parents[2]

# 프런트 소스나 node가 없는 환경(CI 컨테이너에 백엔드만 올린 경우)에서는 대조할 상대가 없으므로 스킵한다.
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None
    or not (REPO / "frontend-html/shared/core.js").exists()
    or not (REPO / "frontend/src/logic/calc.js").exists(),
    reason="node 또는 프런트 소스(core.js · calc.js)가 없어 대조할 수 없다",
)

MONTHS = range(12)


def run_js(case: str, inp: dict | None = None, labs: list[dict] | None = None) -> dict:
    """harness를 돌려 {"core": ..., "calc": ...}를 받는다."""
    job = json.dumps({"case": case, "input": inp or {}, "labs": labs}, ensure_ascii=False)
    p = subprocess.run(
        ["node", str(HARNESS)], input=job, capture_output=True, text=True, timeout=120
    )
    if p.returncode != 0:
        pytest.fail(
            f"프런트 수식을 실행할 수 없다(case={case}). core.js/calc.js의 구조가 바뀌었으면 "
            f"js_formula_harness.mjs를 같이 고칠 것:\n{p.stderr[-3000:]}"
        )
    return json.loads(p.stdout)


def z(v: float | None) -> float:
    """미등록(null)을 0으로 본다 — calc.js는 "안 올라온 칸"을 null로 남기고(빈칸으로 그린다)
    core.js·백엔드는 0으로 채운다. 금액 비교에서는 둘이 같은 뜻이라 여기서 맞춰 준다.
    이 차이 자체는 test_blank_cell_handling_differs_by_design이 따로 기록한다."""
    return 0.0 if v is None else float(v)


def flat(pd: dict[str, list[float]]) -> dict[str, list[float | None]]:
    """고정 입력(리프별 12개월) → get_overseas_state의 d[pk] 모양."""
    return {k: list(v) for k, v in pd.items()}


# ---------- 고정 입력 ----------
# 저장되는 리프 계정 전체. 테스트가 직접 적어 둔다 — 백엔드의 resolve_leaves로 만들면 백엔드가
# 리프를 빠뜨렸을 때 입력에서도 같이 빠져서 대조가 눈을 감는다(아래 test_leaf_set_is_frozen이
# 이 목록과 백엔드를 맞춰 본다).
LEAVES = (
    "sal", "bonus", "welf", "edu", "travel",
    "dep", "amort", "rent", "repair", "ins",
    "supp", "test", "ord",
    "comm", "it", "cons", "util", "car", "tel", "pub", "oth",
    "conv", "event", "social",
    "misc", "tax", "nonop", "B",
)


def pd_fixed(seed: int) -> dict[str, list[float]]:
    """리프마다 서로 다른 값을 12개월 전부 채운 고정 입력.

    리프를 하나도 비우지 않는 게 중요하다 — 안 채운 리프가 있으면 그 리프를 집계 그룹에
    더하거나 빼도 합이 안 변해서 대조가 어긋남을 놓친다(실제로 처음 작성했을 때 edu를
    labor에 추가한 변이를 못 잡았다). 값도 리프마다 다르게 둬야 리프가 서로 자리를 바꾼
    경우까지 잡힌다. 달마다도 값이 달라야 "월별로 따로 계산하는가"를 본다."""
    return {
        **{k: [round(seed + i * 1.7 + j * 0.3, 2) for j in MONTHS] for i, k in enumerate(LEAVES)},
        "hc": [seed + 5 + (j // 3) for j in MONTHS],
    }


PD_USD = pd_fixed(10)
PD_LOCAL = pd_fixed(31)
# 환율은 달마다 다르게 둔다 — 12개월 모두 같은 값이면 "월별로 다른 환율을 쓰는가"를 못 잡는다.
RATE_KRW = [1300, 1310, 1320, 1330, 1340, 1350, 1360, 1370, 1380, 1390, 1400, 1410]
RATE_BASE_KRW = [1250, 1255, 1260, 1265, 1270, 1275, 1280, 1285, 1290, 1295, 1300, 1305]
RATE_ILS = [3.60, 3.61, 3.62, 3.63, 3.64, 3.65, 3.66, 3.67, 3.68, 3.69, 3.70, 3.71]
RATE_BASE_ILS = [3.50, 3.51, 3.52, 3.53, 3.54, 3.55, 3.56, 3.57, 3.58, 3.59, 3.60, 3.61]

# 세 구현이 모두 아는 연구소만 쓴다 — 프런트 OV_LABS에 없는 연구소(DB에만 있는 INDIA_LAB 등)를
# 넣으면 수식이 아니라 "기준정보 목록이 낡았다"는 다른 문제를 측정하게 된다.
PK_USD = "SARC:CPU"
PK_LOCAL = "SIRC"


# ---------- ① 집계 트리 (손익계산서 계층 소계) ----------


def test_leaf_set_is_frozen():
    """위 LEAVES가 백엔드가 아는 리프 전체와 같은지. 계정을 추가하면 여기도 같이 고쳐야 하고,
    그래야 윗 테스트의 고정 입력이 계속 "모든 리프를 채운" 상태로 남는다."""
    assert set(LEAVES) == set(resolve_leaves("tot")), (
        "저장 리프 목록이 바뀌었다 — LEAVES를 고쳐서 고정 입력이 모든 리프를 채우게 할 것"
    )
    # 그룹 키가 리프 목록에 섞여 들어오면 이중 계산이 된다.
    assert not (set(LEAVES) & set(GROUP_CALC))


def test_group_rollup_matches_three_implementations():
    """ovPartEl(core.js · calc.js) = resolve_leaves 합(백엔드).

    집계 행(개발비(A) 등)은 저장된 값이 아니라 리프 여러 개를 더한 값이고, 그 "어느 리프를 더하는가"가
    프런트는 OV_EL의 calc 필드, 백엔드는 cost_groups.GROUP_CALC에 따로 적혀 있다. 한쪽에만 리프를
    추가하면 같은 행의 숫자가 갈린다(실제로 LLM이 리프를 빠뜨려 1,934.5 대신 1,607.7을 답한 사고가
    cost_groups.py:47에 기록돼 있다)."""
    keys = sorted(GROUP_CALC) + ["hc", "B", "misc"]
    js = run_js("part_el", {"pd": flat(PD_USD), "keys": keys})

    for k in keys:
        leaves = resolve_leaves(k)
        for j in MONTHS:
            backend = sum(PD_USD.get(leaf, [0] * 12)[j] for leaf in leaves)
            assert z(js["core"][k][j]) == pytest.approx(backend), f"core.js ≠ 백엔드: {k}[{j}]"
            assert z(js["calc"][k][j]) == pytest.approx(backend), f"calc.js ≠ 백엔드: {k}[{j}]"


def test_group_definitions_cover_the_same_keys():
    """GROUP_CALC(백엔드)와 OV_EL의 calc(프런트)가 같은 집계 행을 같은 하위로 정의하는지.

    위 테스트는 "고정 입력에서 합이 같다"만 보므로, 쓰지 않은 리프가 한쪽에만 추가돼도 통과한다.
    정의 자체를 맞춰 두면 그 경우도 잡힌다."""
    js = run_js("group_defs")
    backend = {k: list(v) for k, v in GROUP_CALC.items()}

    for impl in ("core", "calc"):
        front = js[impl]
        # ① 양쪽이 아는 집계 행은 하위 구성이 정확히 같아야 한다.
        both = set(front) & set(backend)
        diff = {k: (front[k], backend[k]) for k in both if front[k] != backend[k]}
        assert not diff, f"{impl}.js와 백엔드의 집계 그룹 구성이 다르다 (프런트, 백엔드): {diff}"

        # ② 화면에 있는 집계 행을 백엔드가 모르면 챗봇이 그 행을 전개할 수 없다.
        unknown = set(front) - set(backend)
        assert not unknown, f"{impl}.js에만 있는 집계 행(백엔드 GROUP_CALC에 없음): {sorted(unknown)}"

    # calc.js에는 itx(IT 관련 비용)가 없다 — IT 계정 행은 React로 아직 이식되지 않았고, 해외연구소
    # 화면에는 쓰이지 않는다. 백엔드·core.js에만 있는 건 결함이 아니라 이식 범위 차이라 기록만 한다.
    assert "itx" in js["core"] and "itx" not in js["calc"]
    assert set(backend) - set(js["core"]) == set(), "백엔드에만 있는 집계 행이 생겼다 — core.js도 같이 고칠 것"


def test_krw_aggregation_matches_three_implementations():
    """ovAgg(mode="krw") = krw_months. 저장값 K$ × 1000 × (원/$)로 원화를 만드는 경로다.

    연구소 2곳(USD · 현지통화)을 묶어서 집계한다 — pk 하나만 넣으면 "여러 연구소를 더하는 순서"에서
    나는 차이를 못 잡는다."""
    d = {PK_USD: flat(PD_USD), PK_LOCAL: flat(PD_LOCAL)}
    keys = ["labor", "hr", "A", "tot", "hc"]
    js = run_js(
        "agg_krw", {"d": d, "pks": [PK_USD, PK_LOCAL], "keys": keys, "rate": RATE_KRW}
    )

    for k in keys:
        tree = group_tree(k)
        backend = [0.0] * 12
        for pk in (PK_USD, PK_LOCAL):
            # 인력(hc)은 금액이 아니라 명수라 환율을 곱하지 않는다 — 프런트 ovAgg도 k==="hc"를 건너뛴다.
            rate = [1.0] * 12 if k == "hc" else RATE_KRW
            months = krw_months(d[pk], {}, tree, rate)
            for j in MONTHS:
                backend[j] += months[j] if k != "hc" else months[j] / 1000
        for j in MONTHS:
            assert z(js["core"][k][j]) == pytest.approx(backend[j], rel=1e-9), f"core.js ≠ 백엔드: {k}[{j}]"
            assert z(js["calc"][k][j]) == pytest.approx(backend[j], rel=1e-9), f"calc.js ≠ 백엔드: {k}[{j}]"


# ---------- ② 인건비 분해 (인원 효과 vs 단가 효과) ----------


def test_labor_split_matches_between_frontends():
    """core.js ovLaborSplit = calc.js ovLaborSplit (고정 입력).

    두 구현 모두 캐시(Cc/Cb)를 인자로 받는 순수 함수라 DB 없이 바로 비교된다. 백엔드 쪽 식은
    get_labor_split 안에 박혀 있어 순수 함수로 부를 수 없으므로(PLAN Phase 1에서 services/로
    추출할 대상이다) 실제 DB로 비교하는 건 아래 test_labor_split_matches_backend가 맡는다.

    현지통화 연구소를 꼭 포함한다 — 환차外 기준("당월 현지금액 → 비교 현지환율 → 비교 원/$")은
    USD 연구소만 보면 분기가 아예 안 타진다."""
    inp = {
        "pks": [PK_USD, PK_LOCAL],
        "ix": list(MONTHS),
        "cur": {
            "d": {PK_USD: flat(PD_USD), PK_LOCAL: flat(PD_LOCAL)},
            "rate": {"std": RATE_KRW, "cp": RATE_KRW, "ils": RATE_ILS},
        },
        "base": {
            # 비교 버전은 인원·금액을 한 단계 낮춰서 인원 효과·단가 효과가 둘 다 0이 아니게 만든다.
            "d": {
                PK_USD: flat({**PD_USD, "sal": [v - 1 for v in PD_USD["sal"]], "hc": [7] * 12}),
                PK_LOCAL: flat({**PD_LOCAL, "sal": [v - 2 for v in PD_LOCAL["sal"]], "hc": [9] * 12}),
            },
            "rate": {"std": RATE_BASE_KRW, "cp": RATE_BASE_KRW, "ils": RATE_BASE_ILS},
        },
    }
    js = run_js("labor_split", inp)

    for field in ("hcEff", "rateEff", "rest", "hcC", "hcB", "pB", "pC", "ot"):
        assert js["core"][field] == pytest.approx(js["calc"][field], rel=1e-9), (
            f"core.js ≠ calc.js: {field}"
        )
    # 분해가 실제로 일어났는지(두 효과가 0이면 위 비교는 0 == 0으로 통과한다).
    assert js["calc"]["hcEff"] != 0 and js["calc"]["rateEff"] != 0
    # 두 효과 + 분해 못 한 몫 = 환차外. 식의 정의 자체(overseas.py:1086)라 세 구현 공통으로 성립해야 한다.
    assert js["calc"]["hcEff"] + js["calc"]["rateEff"] + js["calc"]["rest"] == pytest.approx(
        js["calc"]["ot"], rel=1e-9
    )


@pytest.mark.parametrize("lab_scope", ["SARC", "SIRC"])
def test_labor_split_matches_backend(db, lab_scope):
    """백엔드 /overseas/labor-split = calc.js ovLaborSplit (같은 DB 데이터로).

    백엔드가 쓴 입력(연구소별 월 값 · 환율 배열)을 그대로 꺼내 calc.js에 먹이고 결과를 비교한다.
    연구소를 한 곳씩 보는 이유: 프런트 OV_LABS는 하드코딩 초기값이라 DB에만 있는 연구소
    (INDIA_LAB 등)를 모르고, 전체(ALL)로 비교하면 수식 차이가 아니라 그 목록 차이가 잡힌다.
    SARC는 USD, SIRC는 현지통화(ILS)라 두 분기를 다 지난다.

    입력을 고르는 부분(연구소 필터 · 환율 기준)은 엔드포인트가 하는 일을 테스트에서 다시 적은
    것이다 — Phase 1에서 services/overseas/로 추출되면 그 함수를 그대로 부르도록 고칠 것.
    """
    try:
        result = get_labor_split(
            basis_year=BASIS_YEAR, base_version="prev", period="y", lab_scope=lab_scope, db=db
        )
    except Exception as e:  # 데이터(전실행 스냅샷 · 환율)가 없으면 비교할 게 없다
        pytest.skip(f"{lab_scope}: 백엔드가 인건비 분해를 낼 수 없다 — {e}")

    state = get_overseas_state(basis_year=BASIS_YEAR, db=db)
    snap = state.actualV.get("cur")
    assert snap is not None  # get_labor_split이 성공했으면 반드시 있다
    basis_month = state.basis_month or 1
    labs = {lab.pk: lab for lab in lab_views(db, yyyymm(BASIS_YEAR, basis_month))}

    pks = [row.lab_pk for row in result.labs]
    if not pks:
        pytest.skip(f"{lab_scope}: 올라온 데이터가 없다")

    local = {labs[pk].currency for pk in pks if labs[pk].currency and labs[pk].currency != "USD"}
    rate_cur = rate_arrays(db, BASIS_YEAR, basis_month, "forecast", local, tuple(MONTHS))
    rate_base = rate_arrays(db, BASIS_YEAR, snap.thru_month, "actual", local, tuple(MONTHS))

    def rates(arr: dict[str, list[float]]) -> dict[str, list[float]]:
        # calc.js는 현지환율을 소문자 통화 키("ils")로 찾는다(ovLcOf). CP도 같은 원/$ 배열을 쓴다.
        return {"std": arr["USD"], "cp": arr["USD"], **{c.lower(): arr[c] for c in local}}

    inp = {
        "pks": pks,
        "ix": list(MONTHS),
        "cur": {"d": {pk: state.V["cur"].d.get(pk, {}) for pk in pks}, "rate": rates(rate_cur)},
        "base": {"d": {pk: snap.d.get(pk, {}) for pk in pks}, "rate": rates(rate_base)},
    }
    js = run_js("labor_split", inp)

    for impl in ("core", "calc"):
        got = js[impl]
        assert got["hcC"] == pytest.approx(result.hc_cur, rel=1e-9), f"{impl}: 당월 인원"
        assert got["hcB"] == pytest.approx(result.hc_base, rel=1e-9), f"{impl}: 비교 인원"
        assert got["pB"] == pytest.approx(result.per_head_base, rel=1e-6), f"{impl}: 비교 단가"
        assert got["pC"] == pytest.approx(result.per_head_cur, rel=1e-6), f"{impl}: 당월 단가"
        assert got["hcEff"] == pytest.approx(result.hc_effect, rel=1e-6), f"{impl}: 인원 효과"
        assert got["rateEff"] == pytest.approx(result.rate_effect, rel=1e-6), f"{impl}: 단가 효과"
        assert got["rest"] == pytest.approx(result.unsplit, rel=1e-6), f"{impl}: 분해 못 한 몫"
        assert got["ot"] == pytest.approx(result.fx_outside, rel=1e-6), f"{impl}: 환차外"


# ---------- ③ 환율 ----------


def test_local_currency_rate_formula_matches_core_js(db):
    """백엔드 _rate_arrays의 현지통화 배열 = 현지/$ = (원/$) ÷ (원/현지), 월평균 기준.

    core.js ovLRate와 같은 정의다(overseas.py:928이 그렇게 적고 있다). core.js 쪽은 전역 환율
    상태(S.fxm)를 거쳐야 해서 harness로 바로 부를 수 없으므로, 여기서는 백엔드가 그 **정의**를
    지키는지를 본다 — 즉 돌려준 현지통화 배열이 USD 배열과 원/현지 환율의 비율인지.

    calc.js의 ovLRate는 이 정의와 다르다 — 그쪽은 (원/$)÷(원/현지)를 계산하지 않고 환율 표에 따로
    저장된 "현지/$" 행(ilsA · ilsE)을 읽고, 실적 구간은 실적환율 · 이후는 월말환율로 바꿔 쓴다
    (calc.js:93-101). 즉 core.js·백엔드는 "항상 월평균", calc.js는 "구간별 전환"이다. 수치 대조가
    아니라 설계가 갈린 지점이라 Phase 6(수식 백엔드 단일화)에서 맞춰야 한다 — 여기 기록만 남긴다.
    """
    basis_month = current_basis_month(db) or 1
    labs = lab_views(db, yyyymm(BASIS_YEAR, basis_month))
    local = {lab.currency for lab in labs if lab.currency and lab.currency != "USD"}
    if not local:
        pytest.skip("현지통화 연구소가 없다")

    ix = tuple(MONTHS)
    try:
        arr = rate_arrays(db, BASIS_YEAR, basis_month, "forecast", local, ix)
    except Exception as e:
        pytest.skip(f"환율 데이터가 없다 — {e}")

    assert set(arr) == {"USD"} | local
    for cur in local:
        assert len(arr[cur]) == 12
        for j in MONTHS:
            # 환율이 없는 달은 0으로 둔다(호출부가 "현지통화 분리 없음"으로 처리한다 — overseas.py:958).
            # 있는 달은 반드시 원/$보다 작거나 커도 "0이 아닌 비율"이어야 한다.
            assert arr[cur][j] >= 0
            if arr[cur][j]:
                assert arr["USD"][j] > 0, f"{cur}[{j}]: 현지환율이 있는데 원/$가 없다"


# ---------- ④ 수식이 쓰는 기준정보 ----------


def test_lab_currency_tables_agree(db):
    """연구소 → 현지통화 표가 DB · core.js · calc.js에서 같은지.

    수식이 아니라 수식이 읽는 기준정보지만, 여기가 어긋나면 환차 분해가 통째로 달라진다 —
    현지통화 연구소를 USD로 보면 "현지→$" 단계가 사라지고 그 몫이 환차外로 샌다
    (overseasConstants.js:4-7에 실제로 그렇게 샌 적이 있다고 적혀 있다).

    프런트의 OV_LABS는 둘 다 하드코딩 초기값이고 실행 중에는 API 목록으로 덮어쓰지만
    (core.js ovLabsFromApi), 초기값이 DB와 다르면 로딩 전 첫 화면이 다른 숫자를 그린다."""
    js = run_js("lab_currencies")
    front_core, front_calc = js["core"], js["calc"]
    db_cur = {lab.lab_code: (lab.currency or "USD").upper() for lab in lab_views(db)}

    diff = {
        code: (front_core.get(code), front_calc.get(code))
        for code in set(front_core) | set(front_calc)
        if front_core.get(code) != front_calc.get(code)
    }
    assert not diff, f"core.js와 calc.js의 연구소 통화 표가 다르다 (core, calc): {diff}"

    # DB와도 맞는지 — 프런트가 아는 연구소에 대해서만 본다(DB에만 있는 연구소는 프런트가 API로 받는다).
    mismatch = {
        code: (front_calc[code], db_cur[code])
        for code in front_calc
        if code in db_cur and front_calc[code] != db_cur[code]
    }
    assert not mismatch, f"프런트 초기값과 DB의 연구소 통화가 다르다 (프런트, DB): {mismatch}"

    # TDC는 국가 기본값(대만→TWD)의 예외다 — $로 결산하는 연구소라 세 곳 모두 USD여야 한다.
    # 국가로 유추해 TWD로 되돌리는 회귀가 가장 생기기 쉬운 자리라 따로 못 박는다.
    assert front_core["TDC"] == front_calc["TDC"] == db_cur["TDC"] == "USD"


def test_blank_cell_handling_differs_by_design():
    """미등록 칸(빈칸) 처리가 세 구현에서 다르다는 사실을 고정해 둔다.

    calc.js는 "안 올라온 칸"을 null로 남겨 빈칸으로 그리고(공통 RULE: 빈칸 처리 — 0으로 올라온 것과
    구분해야 한다), core.js와 백엔드는 0으로 채운다. 금액 합계는 어느 쪽이든 같지만, "하위가 전부
    미등록인 소계"에서만 null vs 0으로 갈린다.

    이 테스트는 그 차이를 없애라는 뜻이 아니라, 위의 금액 대조가 z()로 null을 0으로 맞추고 있는
    근거를 남기는 것이다 — Phase 6에서 백엔드가 유일한 계산자가 되면 백엔드도 null을 내려야 한다."""
    # 인건비 리프(sal·bonus·welf)를 하나도 안 보낸 입력 → labor는 "전부 미등록"인 소계가 된다.
    js = run_js("part_el", {"pd": {"misc": [5] * 12}, "keys": ["labor", "misc"]})

    assert js["calc"]["labor"] == [None] * 12, "calc.js는 전부 미등록인 소계를 빈칸으로 둔다"
    assert js["core"]["labor"] == [0] * 12, "core.js는 0으로 채운다"
    # 값이 있는 쪽은 둘이 같다.
    assert js["calc"]["misc"] == js["core"]["misc"] == [5] * 12
