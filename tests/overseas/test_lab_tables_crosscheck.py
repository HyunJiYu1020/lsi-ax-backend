"""집계 ②③④ 탭(연구소별 표) 대조 — lab_tables(백엔드) = ovTables(core.js).

PLAN.md Phase 6. ① 전체 계정은 test_formula_crosscheck.py가 45행 × 17칸으로 보고, 여기는
나머지 6개 표(② 개발비 달러·원화, ③ 인건비 달러·원화, ④ CP 보정 인건비·인건비외)를 본다.

**행 구성까지 비교하는 이유**: ①은 계정 45행이 고정이라 숫자만 맞으면 됐지만, ②③④는 행이
연구소별이고 표마다 조합이 다르다 — ③ 달러 표만 "CP·SRCX 제외" 줄이 없고(프런트가 그 줄을
빼고 그린다), ②·④만 SARC 하위 줄을 그리고, 연구소 줄 이름도 표마다 꼬리말이 다르다
("CP (미청구) NRE · 분기 정산", "SRCX(APJT포함)", "SIRC (이스라엘)"). 숫자만 맞춰 두면
행이 하나 어긋난 표를 통과시킨다.

**CP 분기 정산**이 ②·④의 핵심이다 — CP는 분기 계약이라 올라온 월별 PL을 쓰지 않고 기준정보 ›
CP 계약 금액 ÷ 4를 3 · 6 · 9 · 12월에 넣는다. 이걸 놓치면 분기말마다 금액이 어긋난다.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.accounts.overseas.services.aggregate import LabGroup, lab_tables

HARNESS = Path(__file__).parent / "js_formula_harness.mjs"
REPO = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(
    not shutil.which("node") or not (REPO / "frontend-html/shared/core.js").exists(),
    reason="node 또는 프런트 소스(frontend-html/shared/core.js)가 없어 대조할 수 없다",
)

TABLES = ("t2u", "t2k", "t3", "t4", "t5", "t6")

# 연구소 모양을 실제와 같게 둔다: 파트가 있는 연구소(SARC) · 현지통화 연구소(SIRC) ·
# 분기 정산 연구소(CP) · 표마다 이름이 달라지는 연구소(SRCX)를 모두 한 번에 지나간다.
LABS_JS = [
    {"c": "SARC", "n": "SARC+ACL", "ctry": "미국", "cur": "USD",
     "parts": [["CPU", "CPU"], ["GPU", "GPU (ACL)"]]},
    {"c": "SIRC", "n": "SIRC", "ctry": "이스라엘", "cur": "USD"},
    {"c": "CP", "n": "CP", "ctry": "", "cur": "USD"},
    {"c": "SRCX", "n": "SRCX", "ctry": "", "cur": "USD"},
]
GROUPS = [
    LabGroup("SARC", "SARC+ACL", "미국", ("SARC:CPU", "SARC:GPU"),
             (("SARC:CPU", "CPU"), ("SARC:GPU", "GPU (ACL)"))),
    LabGroup("SIRC", "SIRC", "이스라엘", ("SIRC",)),
    LabGroup("CP", "CP", "", ("CP",)),
    LabGroup("SRCX", "SRCX", "", ("SRCX",)),
]

# 환율을 달마다 다르게 둔다 — 한 값으로 고정하면 "월별 환율을 쓰는지 연평균을 쓰는지"가 안 갈린다.
RATE = [1.30, 1.31, 1.32, 1.33, 1.34, 1.35, 1.36, 1.37, 1.38, 1.39, 1.40, 1.41]
CP_POCS = [{"total": 400_000, "costSum": 240_000}]   # 저장 단위($) — ÷4 ÷1000 = 분기 100 · 60 K$
CP_QUARTER = (100.0, 60.0)

# 미등록 칸을 일부러 섞는다(SIRC 2월 인건비, SRCX 인력 일부) — 빈칸·0 구분이 표에서도 지켜지는지.
D = {
    "SARC:CPU": {"sal": [10.0] * 12, "B": [2.0] * 12, "hc": [4.0] * 12},
    "SARC:GPU": {"sal": [5.0] * 12, "B": [1.0] * 12, "hc": [2.0] * 12},
    "SIRC": {"sal": [7.0, None] + [7.0] * 10, "B": [3.0] * 12, "hc": [3.0] * 12},
    "CP": {"sal": [1.0] * 12, "B": [1.0] * 12},
    "SRCX": {"sal": [2.0] * 12, "B": [1.0] * 12, "hc": [1.0, 1.0, None] + [1.0] * 9},
}


@pytest.fixture(scope="module")
def js() -> dict:
    """core.js의 ovTables()를 실제로 실행한 결과. 수식을 파이썬으로 베껴 적지 않는다."""
    job = json.dumps(
        {"case": "lab_tables", "input": {"d": D, "rate": RATE, "cpPocs": CP_POCS}, "labs": LABS_JS},
        ensure_ascii=False,
    )
    p = subprocess.run(["node", str(HARNESS)], input=job, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        pytest.fail(
            "core.js의 ovTables()를 실행할 수 없다 — 표 구성이 바뀌었으면 "
            f"js_formula_harness.mjs의 lab_tables 케이스를 같이 고칠 것:\n{p.stderr[-3000:]}"
        )
    return json.loads(p.stdout)["core"]


@pytest.fixture(scope="module")
def backend() -> dict:
    return lab_tables(GROUPS, D, {}, RATE, CP_QUARTER)


def test_all_six_tables_exist(backend):
    """표 6개를 다 만든다 — 하나라도 빠지면 그 탭이 서버 값을 못 받아 조용히 로컬 계산으로 돌아간다."""
    assert sorted(backend) == sorted(TABLES)


@pytest.mark.parametrize("key", TABLES)
def test_row_structure_matches_front(key, js, backend):
    """행 개수 · 라벨 · 들여쓰기 · 표시 형식 · 굵기가 같다.

    숫자가 맞아도 행이 어긋나면 화면은 다른 표를 그린다 — ③ 달러 표에만 없는 "CP·SRCX 제외" 줄,
    ②·④에만 있는 SARC 하위 줄, 표마다 다른 연구소 줄 이름이 전부 여기서 걸린다.
    """
    front, back = js[key]["rows"], backend[key]["rows"]
    assert [r["label"] for r in front] == [r["label"] for r in back], f"{key}: 행 구성이 다르다"
    for i, (f, b) in enumerate(zip(front, back)):
        where = f"{key}[{i}] {f['label']}"
        assert f["level"] == b["level"], f"{where}: 들여쓰기가 다르다"
        assert f["format"] == b["format"], f"{where}: 표시 형식이 다르다"
        assert (f["total"], f["bold"], f["muted"]) == (b["total"], b["bold"], b["muted"]), (
            f"{where}: 합계·굵기·참고 표시가 다르다"
        )


@pytest.mark.parametrize("key", TABLES)
def test_values_match_front(key, js, backend):
    """17칸 전부 같은 숫자. 프런트는 미등록을 0으로 채우므로(설계 차이) 값이 있는 칸만 비교한다."""
    for f, b in zip(js[key]["rows"], backend[key]["rows"]):
        for j in range(17):
            where = f"{key}/{f['label']}[{j}]" + (" 월" if j < 12 else " 분기" if j < 16 else " 연간")
            fv, bv = f["v"][j], b["values"][j]
            if bv is None:
                assert fv in (0, 0.0, None), f"백엔드는 빈칸인데 프런트에 값이 있다: {where} = {fv!r}"
            else:
                assert fv == pytest.approx(bv, rel=1e-9), f"값이 다르다: {where}"


@pytest.mark.parametrize("key", TABLES)
def test_caption_matches_front(key, js, backend):
    """표 제목도 같아야 한다 — 화면이 서버 캡션을 그대로 쓰므로 다르면 탭 제목이 바뀐다."""
    assert js[key]["caption"] == backend[key]["caption"]


def test_cp_is_quarterly_only_in_tabs_2_and_4(backend):
    """CP 줄은 ②·④에서만 분기 금액이고, ③은 올라온 PL 그대로다.

    이게 뒤집히면 ③과 ④가 같은 표가 되어 "CP 보정" 탭의 존재 이유가 사라지고, 반대로 ②에
    보정이 안 들어가면 분기말 정산과 어긋난 개발비가 보고된다.
    """
    def cp_row(key, label):
        return next(r for r in backend[key]["rows"] if r["label"] == label)

    # ② 달러: 분기달(3 · 6 · 9 · 12월)에만 계약금, 나머지 달은 빈칸
    v = cp_row("t2u", "CP (미청구) NRE · 분기 정산")["values"]
    assert [v[j] for j in (2, 5, 8, 11)] == [100.0] * 4, "CP 계약금 ÷ 4가 분기달에 안 들어갔다"
    assert all(v[j] is None for j in (0, 1, 3, 4, 6, 7, 9, 10)), "분기 정산인데 다른 달에 금액이 있다"
    assert v[16] == pytest.approx(400.0), "연간은 분기 4개 합이다"

    # ③ 인건비(달러): 올라온 PL 그대로 — 매달 1.0씩이라 12달 다 값이 있다
    v3 = cp_row("t3", "CP")["values"]
    assert all(v3[j] == pytest.approx(1.0) for j in range(12)), "③은 보정 없이 PL 그대로여야 한다"

    # ④ 인건비(원화): 인건비 계약금(60)이 분기달에만, 그 달 환율로 환산
    v5 = cp_row("t5", "CP")["values"]
    assert v5[2] == pytest.approx(60.0 * RATE[2] * 1000), "④는 인건비 계약금 × 그 달 환율이다"
    assert v5[0] is None


def test_quarterly_amount_is_not_the_annual_contract(backend):
    """분기 금액은 연 계약금의 1/4이다 — ÷4를 빼먹으면 금액이 정확히 네 배가 된다."""
    # 라벨을 정확히 집는다 — startswith("CP")는 SARC 하위 줄 "CPU"를 먼저 잡는다.
    v = next(r for r in backend["t2u"]["rows"] if r["label"] == "CP (미청구) NRE · 분기 정산")["values"]
    assert v[2] == pytest.approx(CP_POCS[0]["total"] / 4 / 1000)


def test_per_capita_uses_period_totals_not_monthly_average(backend):
    """인당 개발비의 분기·연간은 "구간 개발비 합 ÷ 구간 평균 인력"이다.

    월별 비율을 그대로 평균 내면 인력이 적은 달의 비율이 왜곡되게 끼어든다. 두 방식이 다른 값을
    낸다는 걸 같이 고정해 둔다 — 안 그러면 평균으로 바꿔 놓아도 테스트가 통과한다.
    """
    row = next(r for r in backend["t2u"]["rows"] if "인당 개발비" in r["label"])
    monthly = [v for v in row["values"][:12] if v is not None]
    naive_annual = sum(monthly) / len(monthly)

    assert row["values"][16] != pytest.approx(naive_annual, rel=1e-6), (
        "연간 인당 개발비가 월별 비율의 평균과 같다 — 구간 합 ÷ 구간 평균 인력으로 내야 한다"
    )


def test_krw_tables_need_a_rate(backend):
    """환율이 없으면 원화 표를 만들지 않는다 — 환율 없이 원화를 내려보내면 0원 표가 된다."""
    only_usd = lab_tables(GROUPS, D, {}, None, CP_QUARTER)
    assert sorted(only_usd) == ["t2u"]


def test_cp_tabs_are_dropped_without_a_contract(backend):
    """CP 계약이 없으면 ④(CP 보정)를 만들지 않는다 — 보정할 재료가 없는데 표를 내면
    "보정했다"고 읽히는 빈 표가 나간다. ②·③은 계약 없이도 만들 수 있다."""
    no_cp = lab_tables(GROUPS, D, {}, RATE, None)
    assert sorted(no_cp) == ["t2k", "t2u", "t3", "t4"]
