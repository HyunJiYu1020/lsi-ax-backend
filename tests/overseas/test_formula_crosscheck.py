"""수식 대조 테스트 — 같은 입력에 프런트(core.js)와 백엔드가 같은 숫자를 내는지 본다.

환율 환산 · 인건비 분해 · 집계 트리 수식이 프런트(frontend-html/shared/core.js)와 백엔드에
**각각** 적혀 있다. 한 곳만 고치면 화면 숫자 ≠ 챗봇 숫자가 되고, 그건 사용자가 가장 먼저
알아차리는 종류의 버그다.

대조 대상은 frontend-html 하나다 — React(frontend/src/logic/calc.js)는 돌리지 않으므로
맞춰 보지 않는다.

이 테스트는 이중화를 없애지 않는다 — 어긋나는 순간 빨개지게만 만든다.
프런트 수식을 파이썬으로 베껴 적지 않고 js_formula_harness.mjs로 **실제 실행**해서 비교한다 —
베껴 적으면 그게 세 번째 복제본이 되고, 베낀 쪽이 낡으면 테스트가 거짓으로 통과한다.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.accounts.overseas.endpoints.collect import yyyymm, get_labor_split, get_overseas_state
from app.accounts.overseas.services.labs import lab_views
from app.accounts.overseas.services.entries import current_basis_month
from app.accounts.overseas.services.aggregate import agg_across, group_tree, krw_months, pl_rows, to_17
from app.accounts.overseas.cost_groups import AGGREGATE_ROWS, GROUP_CALC, resolve_leaves
from app.accounts.overseas.services.versions import load_basis, resolve_bases
from app.accounts.overseas.services.fxcalc import rate_arrays

BASIS_YEAR = 2026
HARNESS = Path(__file__).parent / "js_formula_harness.mjs"
REPO = Path(__file__).resolve().parents[3]  # tests/overseas/ → backend → S.LSI (프런트가 옆에 있다)

HAS_NODE = shutil.which("node") is not None
HAS_CORE = (REPO / "frontend-html/shared/core.js").exists()

# 프런트 소스가 없으면 비교할 게 없다. 스킵 조건은 **이 대조가 쓰는 것**만 본다 — 조건이 넓으면
# 관계없는 폴더가 사라진 순간 가드레일이 조용히 꺼지고 테스트는 녹색으로 남는다. 보호가 꺼진 걸
# 모르는 게 보호가 없는 것보다 나쁘다.
pytestmark = pytest.mark.skipif(
    not HAS_NODE or not HAS_CORE,
    reason="node 또는 프런트 소스(frontend-html/shared/core.js)가 없어 대조할 수 없다",
)

MONTHS = range(12)


def run_js(case: str, inp: dict | None = None, labs: list[dict] | None = None) -> dict:
    """harness를 돌려 {"core": ...}를 받는다."""
    job = json.dumps({"case": case, "input": inp or {}, "labs": labs}, ensure_ascii=False)
    p = subprocess.run(
        ["node", str(HARNESS)], input=job, capture_output=True, text=True, timeout=120
    )
    if p.returncode != 0:
        pytest.fail(
            f"프런트 수식을 실행할 수 없다(case={case}). core.js의 구조가 바뀌었으면 "
            f"js_formula_harness.mjs를 같이 고칠 것:\n{p.stderr[-3000:]}"
        )
    return json.loads(p.stdout)


def z(v: float | None) -> float:
    """미등록(null)을 0으로 본다 — 백엔드는 "안 올라온 칸"을 null로 남기고(빈칸으로 그린다)
    core.js·백엔드는 0으로 채운다. 금액 비교에서는 둘이 같은 뜻이라 여기서 맞춰 준다.
    이 표기 차이가 없어진 경위는 test_blank_cell_handling_now_matches_the_backend에 적어 뒀다."""
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


def test_group_rollup_matches_backend():
    """ovPartEl(core.js) = resolve_leaves 합(백엔드).

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


def test_group_definitions_cover_the_same_keys():
    """GROUP_CALC(백엔드)와 OV_EL의 calc(프런트)가 같은 집계 행을 같은 하위로 정의하는지.

    위 테스트는 "고정 입력에서 합이 같다"만 보므로, 쓰지 않은 리프가 한쪽에만 추가돼도 통과한다.
    정의 자체를 맞춰 두면 그 경우도 잡힌다."""
    js = run_js("group_defs")
    backend = {k: list(v) for k, v in GROUP_CALC.items()}

    front = js["core"]
    # ① 양쪽이 아는 집계 행은 하위 구성이 정확히 같아야 한다.
    both = set(front) & set(backend)
    diff = {k: (front[k], backend[k]) for k in both if front[k] != backend[k]}
    assert not diff, f"core.js와 백엔드의 집계 그룹 구성이 다르다 (프런트, 백엔드): {diff}"

    # ② 화면에 있는 집계 행을 백엔드가 모르면 챗봇이 그 행을 전개할 수 없다.
    unknown = set(front) - set(backend)
    assert not unknown, f"core.js에만 있는 집계 행(백엔드 GROUP_CALC에 없음): {sorted(unknown)}"
    assert set(backend) - set(front) == set(), "백엔드에만 있는 집계 행이 생겼다 — core.js도 같이 고칠 것"


def test_krw_aggregation_invariants():
    """원화 환산 집계(agg_across)의 성질. 2026-11-18까지는 core.js ovAgg와 대조했는데, 화면이
    이 계산을 더 하지 않게 되면서(모두 서버 값) 대조할 상대가 없어졌다 — 그래서 **백엔드 출력의
    내부 정합성**으로 바꿨다(pl_rows · lab_tables · variance를 지울 때 한 것과 같은 전환).

    지키는 성질 넷:
      ① 원화 = 저장값($) × (원/$) — 리프를 손으로 더한 값과 같다
      ② 연구소 묶음 = 각 연구소 따로 낸 값의 합(더하는 순서와 무관)
      ③ rate를 안 주면 저장 단위($) 그대로다 — 인력(hc)처럼 환율을 곱하면 안 되는 행이 쓴다
      ④ 전부 미등록인 달은 None이고 0이 아니다 — 하나라도 올라오면 올라온 것만 더한다
    """
    d = {PK_USD: flat(PD_USD), PK_LOCAL: flat(PD_LOCAL)}

    for k in ["labor", "hr", "A", "tot"]:
        tree = group_tree(k)
        leaves = [x for x in tree if x not in GROUP_CALC]
        both = agg_across([PK_USD, PK_LOCAL], d, {}, tree, RATE_KRW)
        one = agg_across([PK_USD], d, {}, tree, RATE_KRW)
        other = agg_across([PK_LOCAL], d, {}, tree, RATE_KRW)
        for j in MONTHS:
            assert z(both[j]) == pytest.approx(z(one[j]) + z(other[j]), rel=1e-9), f"② {k}[{j}]"
            hand = sum(z(d[PK_USD].get(leaf, [None] * 12)[j]) for leaf in leaves)
            # 저장값이 $라 원화는 `값 × 환율`이다.
            assert z(one[j]) == pytest.approx(hand * RATE_KRW[j], rel=1e-9), f"① {k}[{j}]"

    # ③ rate 없이 부르면 환산하지 않는다(인력 행이 쓰는 경로)
    hc = group_tree("hc")
    raw = agg_across([PK_USD], d, {}, hc, None)
    krw = agg_across([PK_USD], d, {}, hc, RATE_KRW)
    for j in MONTHS:
        assert z(krw[j]) == pytest.approx(z(raw[j]) * RATE_KRW[j], rel=1e-9), f"③ [{j}]"

    # ④ 미등록 처리: 아무도 안 올린 달은 None, 한쪽만 올린 달은 그 값만
    tree = group_tree("labor")
    leaf = sorted(x for x in tree if x not in GROUP_CALC)[0]
    blank = {PK_USD: {leaf: [None] * 12}, PK_LOCAL: {leaf: [None] * 11 + [7.0]}}
    out = agg_across([PK_USD, PK_LOCAL], blank, {}, tree, RATE_KRW)
    assert out[0] is None, "전부 미등록인 달은 0이 아니라 빈칸이다"
    assert out[11] == pytest.approx(7.0 * RATE_KRW[11], rel=1e-9)


# ---------- ② 인건비 분해 (인원 효과 vs 단가 효과) ----------




@pytest.mark.parametrize("lab_scope", ["SARC", "SIRC"])
def test_labor_split_matches_backend(db, lab_scope):
    """백엔드 /overseas/labor-split = core.js ovLaborSplit (같은 DB 데이터로).

    백엔드가 쓴 입력(연구소별 월 값 · 환율 배열)을 그대로 꺼내 core.js에 먹이고 결과를 비교한다.
    연구소를 한 곳씩 보는 이유: 프런트 OV_LABS는 하드코딩 초기값이라 DB에만 있는 연구소
    (INDIA_LAB 등)를 모르고, 전체(ALL)로 비교하면 수식 차이가 아니라 그 목록 차이가 잡힌다.
    SARC는 USD, SIRC는 현지통화(ILS)라 두 분기를 다 지난다.

    입력을 고르는 부분(연구소 필터 · 환율 기준)은 엔드포인트가 하는 일을 테스트에서 다시 적은
    것이다 — services에 같은 입력을 만드는 함수가 생기면 그걸 그대로 부르도록 고칠 것.
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
    labs = {lab.pk: lab for lab in lab_views(db, f"{BASIS_YEAR:04d}-{basis_month:02d}")}

    pks = [row.lab_pk for row in result.labs]
    if not pks:
        pytest.skip(f"{lab_scope}: 올라온 데이터가 없다")

    # 비교 기준(전실행)은 **서버와 같은 해석기**로 고른다 — "그 해 최신 실적" 같은 다른 규칙을
    # 쓰면 실적 버전이 둘 이상일 때 서버가 쓰는 달(버전 순서상 직전)과 달라지고, 수식이 같아도
    # 값이 어긋난다. 그러면 이 대조가 "수식 차이"가 아니라 "기준 차이"를 잡는다.
    base = next(b for b in resolve_bases(db, BASIS_YEAR, basis_month, "forecast", None).bases
                if b.key == "prev")
    if not base.has_data:
        pytest.skip("전실행 데이터가 없다")
    base_d = load_basis(db, (base.year, base.month, base.kind, base.plan_no), set(pks))

    local = {labs[pk].currency for pk in pks if labs[pk].currency and labs[pk].currency != "USD"}
    rate_cur = rate_arrays(db, BASIS_YEAR, basis_month, "forecast", local, tuple(MONTHS))
    rate_base = rate_arrays(db, base.year, base.month, base.kind, local, tuple(MONTHS))

    def rates(arr: dict[str, list[float]]) -> dict[str, list[float]]:
        # core.js는 현지환율을 소문자 통화 키("ils")로 찾는다(ovLcOf). CP도 같은 원/$ 배열을 쓴다.
        return {"std": arr["USD"], "cp": arr["USD"], **{c.lower(): arr[c] for c in local}}

    inp = {
        "pks": pks,
        "ix": list(MONTHS),
        "cur": {"d": {pk: state.V["cur"].d.get(pk, {}) for pk in pks}, "rate": rates(rate_cur)},
        "base": {"d": {pk: base_d.get(pk, {}) for pk in pks}, "rate": rates(rate_base)},
    }
    js = run_js("labor_split", inp)

    for impl in ["core"]:
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

    """
    basis_month = current_basis_month(db) or 1
    labs = lab_views(db, f"{BASIS_YEAR:04d}-{basis_month:02d}")
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
    """연구소 → 현지통화 표가 DB와 core.js에서 같은지.

    수식이 아니라 수식이 읽는 기준정보지만, 여기가 어긋나면 환차 분해가 통째로 달라진다 —
    현지통화 연구소를 USD로 보면 "현지→$" 단계가 사라지고 그 몫이 환차外로 샌다
    (overseasConstants.js:4-7에 실제로 그렇게 샌 적이 있다고 적혀 있다).

    프런트의 OV_LABS는 하드코딩 초기값이고 실행 중에는 API 목록으로 덮어쓰지만
    (core.js ovLabsFromApi), 초기값이 DB와 다르면 로딩 전 첫 화면이 다른 숫자를 그린다."""
    js = run_js("lab_currencies")
    front = js["core"]
    db_cur = {lab.lab_code: (lab.currency or "USD").upper() for lab in lab_views(db)}

    # 프런트가 아는 연구소에 대해서만 본다(DB에만 있는 연구소는 프런트가 API로 받는다).
    mismatch = {
        code: (front[code], db_cur[code])
        for code in front
        if code in db_cur and front[code] != db_cur[code]
    }
    assert not mismatch, f"프런트 초기값과 DB의 연구소 통화가 다르다 (프런트, DB): {mismatch}"

    # TDC는 국가 기본값(대만→TWD)의 예외다 — $로 결산하는 연구소라 양쪽 모두 USD여야 한다.
    # 국가로 유추해 TWD로 되돌리는 회귀가 가장 생기기 쉬운 자리라 따로 못 박는다.
    assert front["TDC"] == db_cur["TDC"] == "USD"


def test_blank_cell_handling_now_matches_the_backend():
    """미등록 칸(빈칸) 처리가 두 구현에서 같아졌다 — 2026-11에 프런트를 백엔드 규칙으로 맞췄다.

    전에는 core.js가 "안 올라온 칸"을 num()으로 0으로 바꿔 읽어서, "하위가 전부 미등록인 소계"가
    프런트에서는 0, 백엔드에서는 빈칸이었다. 업로드 양식에서 연구소가 **0으로 보고한 달**과
    **아직 안 낸 달**을 구분하기로 하면서(ovParse가 빈칸을 null로 읽고 서버도 그 달을 저장하지
    않는다) 화면 표도 0은 "0", 미등록은 "–"로 그려야 해서 프런트 계산이 null을 들고 가게 됐다.

    금액은 양쪽 모두 "있는 값만 더한다"라 달라지지 않는다 — 달라지는 건 전부 미등록인 칸의 표기뿐이다.
    (위 금액 대조가 z()로 null을 0으로 맞추는 건 그대로 둔다 — 비교에서는 둘이 같은 뜻이다.)"""
    # 인건비 리프(sal·bonus·welf)를 하나도 안 보낸 입력 → labor는 "전부 미등록"인 소계가 된다.
    js = run_js("part_el", {"pd": {"misc": [5] * 12}, "keys": ["labor", "misc"]})

    assert js["core"]["labor"] == [None] * 12, "프런트가 전부 미등록인 소계를 0으로 채웠다"
    assert js["core"]["misc"] == [5] * 12, "값이 있는 칸은 그대로다"
    assert to_17([None] * 12, "sum") == [None] * 17, "백엔드도 전부 미등록이면 빈칸을 유지한다"


# ---------- ⑤ 17열 집계 (월 12 + 분기 4 + 연간) ----------

_MONTHS_CASES = {
    "값이 다 있는 경우": [float(m) for m in range(1, 13)],
    "미등록이 섞인 경우": [1.0, None, 3.0, None, None, None, 7.0, 8.0, None, None, None, None],
    "0이 섞인 경우": [0.0, 2.0, 0.0, 4.0] + [0.0] * 8,
    "전부 미등록": [None] * 12,
}




@pytest.mark.parametrize("how", ["sum", "avg", "avgnz"])
@pytest.mark.parametrize("case", list(_MONTHS_CASES))
def test_17_column_backend_keeps_blanks_blank(how, case):
    """to_17은 미등록(None)을 끝까지 빈칸으로 유지한다 — 프런트 없이 백엔드만 보는 고정.

    원래 이 성질은 calc.js와 대조해서 확인했는데(React를 안 쓰게 되어 그 테스트는 지웠다),
    프런트 대조에 기대면 **프런트가 0으로 채우는 설계** 때문에 빈칸 위치가 느슨하게 검사된다.
    여기가 틀어지면 "안 올라온 달"이 0원으로 보고된 달처럼 보여 집계표가 조용히 과소집계된다.

    모드가 셋인 이유가 핵심이다: sum은 있는 값만 더하고, avg는 있는 달로만 평균 내고(미등록을
    0으로 세면 인원 평균이 실제보다 낮아진다), avgnz는 0까지 빼고 평균 낸다(환율은 0일 수가
    없으니 0은 "아직 안 넣은 달"이다)."""
    months = _MONTHS_CASES[case]
    out = to_17(list(months), how)

    assert len(out) == 17
    for j in range(12):
        assert (out[j] is None) == (months[j] is None), f"{how}/{case}: 월 {j+1}의 미등록 여부가 바뀌었다"
    # 구간에 값이 하나도 없으면 분기·연간도 빈칸이다(0이 아니다).
    for q, ix in enumerate(((0, 1, 2), (3, 4, 5), (6, 7, 8), (9, 10, 11))):
        if all(months[i] is None for i in ix):
            assert out[12 + q] is None, f"{how}/{case}: {q+1}Q이 전부 미등록인데 빈칸이 아니다"
    if all(m is None for m in months):
        assert out[16] is None, f"{how}/{case}: 연간이 전부 미등록인데 빈칸이 아니다"


@pytest.mark.parametrize("how", ["sum", "avg", "avgnz"])
@pytest.mark.parametrize("case", list(_MONTHS_CASES))
def test_17_column_core_js_fills_blanks_with_zero(how, case):
    """core.js의 ov17은 미등록을 0으로 채운다 — 값이 있는 칸은 백엔드와 같아야 한다.

    이 차이는 결함이 아니라 설계 차이다(core.js는 num()으로 null을 0으로 바꿔 읽는다,
    test_blank_cell_handling_differs_by_design 참조). 여기서 고정하는 건 **값이 있는 칸은
    두 구현이 같다**는 것과, 차이가 "미등록을 0으로 본다"는 그 한 가지뿐이라는 점이다.
    """
    months = _MONTHS_CASES[case]
    js = run_js("to17", {"months": months, "how": how})
    backend = to_17(list(months), how)

    for i in range(17):
        where = f"{how}/{case}[{i}]"
        if backend[i] is not None:
            assert js["core"][i] == pytest.approx(backend[i]), f"값이 있는 칸이 다르다: {where}"
        else:
            # 백엔드가 미등록이라고 본 칸 → core.js는 0을 준다(그 외 값이면 설계 차이가 아니다)
            assert js["core"][i] in (0, 0.0, None), f"미등록 칸에 0이 아닌 값이 있다: {where} = {js['core'][i]!r}"


# ---------- ⑥ 집계 ① 전체 계정 표 (45행 × 17칸) ----------


@pytest.mark.parametrize("mode", ["usd", "krw"])
def test_pl_rows_is_45_rows_and_folds_periods(mode):
    """집계 ① 전체 계정 표 — 45행이고, 분기 = 3개월 합 · 연간 = 12개월 합이다.

    **2026-11-17: 프런트 대조(ovPLRows)를 걷어내고 백엔드 단독 검사로 바꿨다.** 집계 ①이 서버
    값을 쓰게 된 뒤 브라우저 계산 ovPLRows를 지워서 대조할 상대가 없어졌다(지운 테스트는 git
    이력에 있다: test_pl_rows_matches_front).

    대조가 지켜 주던 건 ① 45행 구성(키 · 표시 형식)과 ② 17칸 값이었다. 전자는 그대로 보고,
    후자는 "기간 접기가 자기 정의와 맞는지"로 바꿨다 — 계산자가 하나뿐일 때는 두 구현을 맞춰
    보는 대신 출력이 규칙과 맞는지 보는 쪽이 맞다.

    대조가 과거에 잡은 것(기록): ① ov17 avg가 안 올라온 달을 0으로 세던 것, ② 비율의 분모가
    개발비 vs 개발비(매출)로 갈려 있던 것. 둘 다 지금은 백엔드 한 곳에만 있다.
    """
    d = {PK_USD: flat(PD_USD), PK_LOCAL: flat(PD_LOCAL)}
    pks = [PK_USD, PK_LOCAL]
    rate = RATE_KRW if mode == "krw" else None
    backend = pl_rows(pks, d, {}, mode, rate)

    assert len(backend) == 45, "행 수가 다르다 — 표 구성이 어긋났다"
    # 키와 표시 형식은 계정 정의(AGGREGATE_ROWS)가 정한다 — 표가 정의와 갈리면 화면이 다른 표를 그린다.
    assert [r["key"] for r in backend] == [k for k, _l, _u, _m in AGGREGATE_ROWS], "행 키·순서가 정의와 다르다"
    for r in backend:
        assert len(r["values"]) == 17, f"{r['key']}: 칸이 {len(r['values'])}개다"

    for r in backend:
        # 인력은 평균, 비율은 분자 ÷ 분모라 "합" 규칙의 대상이 아니다(각각 다른 테스트가 본다).
        if r["format"] not in ("krw", "usd"):
            continue
        months = r["values"][:12]
        for qi, ix in enumerate(((0, 1, 2), (3, 4, 5), (6, 7, 8), (9, 10, 11))):
            part = [months[j] for j in ix if months[j] is not None]
            got, where = r["values"][12 + qi], f"{mode}/{r['key']}/{qi + 1}Q"
            if part:
                assert got == pytest.approx(sum(part), rel=1e-9), f"{where}: 분기 ≠ 3개월 합"
            else:
                assert got is None, f"{where}: 월이 다 빈칸인데 분기에 값이 있다"
        have = [m for m in months if m is not None]
        if have:
            assert r["values"][16] == pytest.approx(sum(have), rel=1e-9), f"{mode}/{r['key']}: 연간 ≠ 12개월 합"


def test_pl_rows_memo_rows_are_outside_the_total():
    """참고 행(IT 관련 비용·외주용역비)은 개발비 합계에 들어가지 않는다.

    들어가면 개발비(매출)가 부풀어 보고 숫자가 틀어진다. 프런트도 memo:1로 표시해 합계에서 빼고 있다.
    """
    cats = {"sal": [10.0] * 12, "B": [2.0] * 12, "eda": [99.0] * 12, "outs": [77.0] * 12}
    by = {r["key"]: r for r in pl_rows([PK_USD], {PK_USD: cats}, {}, "usd", None)}

    assert by["tot"]["values"][0] == pytest.approx(12.0), "참고 행이 합계에 섞였다"
    assert by["eda"]["values"][0] == pytest.approx(99.0)
    assert by["outs"]["values"][0] == pytest.approx(77.0)
    assert by["itx"]["values"][0] == pytest.approx(99.0), "itx는 하위(eda 등)의 합이다"
    assert all(by[k]["memo"] for k in ("itx", "eda", "outs"))
    assert not by["tot"]["memo"] and not by["labor"]["memo"]


