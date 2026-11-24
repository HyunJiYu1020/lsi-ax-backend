"""집계 화면 연구소별 표(탭 ①②③, 10장) — lab_tables. 행 구성 · 기간 접기 · CP 분기 정산.

**2026-11-18: 탭 구성이 바뀌었다.** ① 개발비(총계 t2u·t2k / 인건비 t5u·t5 / 인건비외 t6u·t6,
CP 분기 정산 반영) · ② 개발비(CP_PL) t7u·t7k · ③ 인건비(CP_PL) t3·t4다. 전에는 보정본을
개발비만, PL본을 인건비만 볼 수 있어서 짝이 안 맞았다. 표 키는 그대로 두고(화면·테스트가 그
키로 집는다) 새 표 넷(t5u · t6u · t7u · t7k)을 더했다.

**2026-11-17: 프런트 대조(core.js ovTables)를 걷어내고 백엔드 단독 검사로 바꿨다.**
집계 ②③④가 서버 값을 쓰게 된 뒤(11/16) 브라우저 계산 ovTables()를 지워서, 계산하는 쪽이
백엔드 하나뿐이다 — 대조할 상대가 없어졌다. 지운 테스트는 git 이력에 있다
(test_lab_tables_crosscheck.py: test_row_structure_matches_front, test_values_match_front,
test_caption_matches_front).

대조가 지켜 주던 "행 구성"은 아래 두 테스트로 옮겼다 — ③ 달러 표에만 "CP·SRCX 제외" 줄이
없고, ②·④에만 SARC 하위 파트 줄이 있다는 성질이다. 값 대조는 "기간 접기"(분기 = 3개월 합,
연간 = 12개월 합) 검사로 옮겼다: 두 구현을 맞춰 보는 게 아니라 **출력이 자기 정의와 맞는지**
보는 쪽이 계산자가 하나인 지금 상황에 맞다.

캡션 대조는 되살리지 않는다. 화면은 서버 caption을 쓰지 않고 자기 문구(ovTblMeta)를 쓴다 —
서버 문장은 챗봇 · API용 평서문이고 화면은 존댓말 안내를 보여준다. 대조 테스트는 "화면이 서버
캡션을 그대로 쓴다"고 적혀 있었지만 화면은 그런 적이 없다.

부수 효과로 이 파일은 node · frontend-html 유무와 무관해졌다. 전에는 프런트 소스가 없으면
(예: 백엔드만 clone한 CI) 파일 전체가 조용히 스킵됐다.

**CP 분기 정산**이 ②·④의 핵심이다 — CP는 분기 계약이라 올라온 월별 PL을 쓰지 않고 기준정보 ›
CP 계약 금액 ÷ 4를 3 · 6 · 9 · 12월에 넣는다. 이걸 놓치면 분기말마다 금액이 어긋난다.
"""

import pytest

from app.accounts.overseas.services.aggregate import LabGroup, lab_tables

TABLES = ("t2u", "t2k", "t5u", "t5", "t6u", "t6", "t7u", "t7k", "t3", "t4")
# 원화 표 — 맨 윗줄이 적용 환율이고, 환율이 없으면 아예 안 만들어진다.
KRW_TABLES = ("t2k", "t5", "t6", "t7k", "t4")
# CP 계약 금액으로 분기 정산한 표(① 개발비 탭) — 계약이 없으면 아예 안 만들어진다.
CP_TABLES = ("t2u", "t2k", "t5u", "t5", "t6u", "t6")

# 연구소 모양을 실제와 같게 둔다: 파트가 있는 연구소(SARC) · 현지통화 연구소(SIRC) ·
# 분기 정산 연구소(CP) · 표마다 이름이 달라지는 연구소(SRCX)를 모두 한 번에 지나간다.
GROUPS = [
    LabGroup("SARC", "SARC+ACL", "미국", ("SARC:CPU", "SARC:GPU"),
             (("SARC:CPU", "CPU"), ("SARC:GPU", "GPU (ACL)"))),
    LabGroup("SIRC", "SIRC", "이스라엘", ("SIRC",)),
    LabGroup("CP", "CP", "", ("CP",)),
    LabGroup("SRCX", "SRCX", "", ("SRCX",)),
]

# 환율을 달마다 다르게 둔다 — 한 값으로 고정하면 "월별 환율을 쓰는지 연평균을 쓰는지"가 안 갈린다.
RATE = [1.30, 1.31, 1.32, 1.33, 1.34, 1.35, 1.36, 1.37, 1.38, 1.39, 1.40, 1.41]
CP_POCS = [{"total": 400_000, "costSum": 240_000}]   # 저장 단위($) — ÷4 = 분기 100,000 · 60,000 $
CP_QUARTER = (100_000.0, 60_000.0)

# 미등록 칸을 일부러 섞는다(SIRC 2월 인건비, SRCX 인력 일부) — 빈칸·0 구분이 표에서도 지켜지는지.
D = {
    "SARC:CPU": {"sal": [10.0] * 12, "B": [2.0] * 12, "hc": [4.0] * 12},
    "SARC:GPU": {"sal": [5.0] * 12, "B": [1.0] * 12, "hc": [2.0] * 12},
    "SIRC": {"sal": [7.0, None] + [7.0] * 10, "B": [3.0] * 12, "hc": [3.0] * 12},
    "CP": {"sal": [1.0] * 12, "B": [1.0] * 12},
    "SRCX": {"sal": [2.0] * 12, "B": [1.0] * 12, "hc": [1.0, 1.0, None] + [1.0] * 9},
}

QUARTERS = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (9, 10, 11))


@pytest.fixture(scope="module")
def backend() -> dict:
    return lab_tables(GROUPS, D, {}, RATE, CP_QUARTER)


def test_all_tables_exist(backend):
    """표 10개를 다 만든다 — 하나라도 빠지면 그 탭이 빈 표로 그려진다(폴백을 지웠다)."""
    assert sorted(backend) == sorted(TABLES)


def test_each_item_has_a_dollar_and_a_won_table(backend):
    """항목마다 달러 표와 원화 표가 짝으로 있다 — 화면의 단위 전환이 같은 항목을 그려야 한다.

    2026-11-18 개편의 핵심이다. 전에는 CP 보정 인건비 · 인건비외가 원화밖에 없어서, 그 탭에서는
    단위를 고를 수 없었다(달러로 보려면 보정이 안 걸린 ③ 탭으로 가야 했다)."""
    for usd, krw in (("t2u", "t2k"), ("t5u", "t5"), ("t6u", "t6"), ("t7u", "t7k"), ("t3", "t4")):
        assert usd in backend and krw in backend, f"{usd} · {krw} 짝이 안 맞는다"
        money = {r["format"] for t in (usd, krw) for r in backend[t]["rows"]} - {"rate"}
        assert money == {"usd", "krw"}, f"{usd} · {krw}: 한쪽 단위만 있다 ({money})"


# ---------- 행 구성: 표마다 다른 조합 ----------


def test_every_row_has_17_periods(backend):
    """모든 행이 17칸이다(12개월 · 4분기 · 연간) — 한 칸이라도 모자라면 화면이 열을 밀어 그린다."""
    for key in TABLES:
        for r in backend[key]["rows"]:
            assert len(r["values"]) == 17, f"{key}/{r['label']}: 칸이 {len(r['values'])}개다"


def test_usd_labor_table_has_no_excluded_row(backend):
    """③ 달러 표(t3)에만 "CP·SRCX 제외" 줄이 없다.

    표마다 행 조합이 다른 자리라 대조 테스트가 지켜 주던 성질이다. 화면이 그 줄을 빼고 그리므로
    서버가 넣어 보내면 화면과 행이 하나 어긋난다.
    """
    def has_excluded(key):
        return any("제외" in r["label"] for r in backend[key]["rows"])

    assert not has_excluded("t3"), "③ 달러 표에 제외 줄이 들어 있다"
    for key in ("t2u", "t2k", "t4", "t5", "t5u", "t6", "t6u", "t7u", "t7k"):
        assert has_excluded(key), f"{key}: 제외 줄이 없다"


def test_only_some_tables_show_lab_parts(backend):
    """개발비 달러 표와 ① 인건비 · 인건비외 표만 SARC 하위 파트(CPU · GPU) 줄을 그린다 —
    ③ 인건비(CP_PL)는 연구소 합계까지만 보여준다(화면이 그렇게 그려 온 구성이다)."""
    def has_parts(key):
        return any(r["label"] in ("CPU", "GPU (ACL)") for r in backend[key]["rows"])

    for key in ("t2u", "t7u", "t5", "t5u", "t6", "t6u"):
        assert has_parts(key), f"{key}: 파트 줄이 없다"
    assert not has_parts("t3"), "③ 달러 표에 파트 줄이 들어 있다"
    assert not has_parts("t4"), "③ 원화 표에 파트 줄이 들어 있다"


def test_krw_tables_start_with_the_applied_rate(backend):
    """원화 표는 맨 윗줄이 "적용 환율 (원/$)"이다 — 금액 계층 밖이라 들여쓰기가 없고 참고줄이다.

    화면 ①의 환율 줄도 이 줄을 가져다 쓴다(core.js ovFxTopRow) — 프런트가 환율을 다시 분기 ·
    연간으로 평균 내면 그게 또 하나의 복제 수식이 되기 때문이다. 그래서 자리와 형식을 못 박는다.
    """
    for key in KRW_TABLES:
        head = backend[key]["rows"][0]
        assert head["format"] == "rate", f"{key}: 첫 줄이 환율 행이 아니다"
        assert head["label"] == "적용 환율 (원/$)", f"{key}: 환율 줄 이름이 다르다"
        assert head["level"] == 0 and head["muted"], f"{key}: 환율 줄은 들여쓰기 없는 참고줄이다"
    for key in ("t2u", "t5u", "t6u", "t7u", "t3"):
        assert backend[key]["rows"][0]["format"] != "rate", f"{key}: 달러 표에 환율 줄이 있다"


# ---------- 기간 접기: 분기 = 3개월 합, 연간 = 12개월 합 ----------


def test_money_rows_fold_months_into_quarters_and_year(backend):
    """금액 행은 분기 = 그 3개월 합, 연간 = 12개월 합이다.

    대조 테스트가 17칸 값을 전부 맞춰 보던 자리를 대신한다 — 두 구현을 비교하는 대신 출력이
    자기 정의와 맞는지 본다. 미등록(None)이 섞인 행이 입력에 있어서(SIRC 2월) "있는 값만
    더한다"는 규칙도 같이 지나간다.
    """
    checked = 0
    for key in TABLES:
        for r in backend[key]["rows"]:
            # 환율·비율·인당 값은 합이 아니라 평균/나눗셈이라 이 규칙의 대상이 아니다.
            if r["format"] not in ("krw", "usd") or "인당" in r["label"]:
                continue
            v = r["values"]
            months = v[:12]
            for qi, ix in enumerate(QUARTERS):
                part = [months[j] for j in ix if months[j] is not None]
                want = sum(part) if part else None
                got = v[12 + qi]
                where = f"{key}/{r['label']}/{qi + 1}Q"
                if want is None:
                    assert got is None, f"{where}: 월이 다 빈칸인데 분기에 값이 있다"
                else:
                    assert got == pytest.approx(want, rel=1e-9), f"{where}: 분기 ≠ 3개월 합"
            have = [m for m in months if m is not None]
            if have:
                assert v[16] == pytest.approx(sum(have), rel=1e-9), (
                    f"{key}/{r['label']}: 연간 ≠ 12개월 합"
                )
            checked += 1
    assert checked > 20, f"검사한 행이 {checked}개뿐이다 — 표가 비었는지 확인할 것"


# ---------- CP 분기 정산 ----------


def test_cp_is_quarterly_only_in_the_corrected_tab(backend):
    """CP 줄은 ① 개발비 탭에서만 분기 정산이다 — 3 · 6 · 9 · 12월에만 금액이 있고 나머지는 빈칸.

    ②·③(CP_PL)은 올라온 PL 그대로(보정 없음)여야 한다. 두 표가 같은 값을 내면 보정이 안
    걸린 것이다 — 그러면 ①과 ②를 따로 둔 이유가 없어진다.
    """
    # 라벨을 정확히 집는다 — startswith("CP")는 SARC 하위 줄 "CPU"를 먼저 잡는다.
    def cp_row(key, label):
        return next(r for r in backend[key]["rows"] if r["label"] == label)

    # ② 달러: 분기달(3 · 6 · 9 · 12월)에만 계약금, 나머지 달은 빈칸
    v = cp_row("t2u", "CP (미청구) NRE · 분기 정산")["values"]
    assert [v[j] for j in (2, 5, 8, 11)] == [100_000.0] * 4, "CP 계약금 ÷ 4가 분기달에 안 들어갔다"
    assert all(v[j] is None for j in (0, 1, 3, 4, 6, 7, 9, 10)), "분기 정산인데 다른 달에 금액이 있다"
    assert v[16] == pytest.approx(400_000.0), "연간은 분기 4개 합이다"

    # ③ 인건비(달러): 올라온 PL 그대로 — 매달 1.0씩이라 12달 다 값이 있다
    v3 = cp_row("t3", "CP")["values"]
    assert all(v3[j] == pytest.approx(1.0) for j in range(12)), "③은 보정 없이 PL 그대로여야 한다"

    # ② 개발비(CP_PL): 같은 개발비를 보정 없이 — CP 줄에 12달 다 값이 있고 분기 정산 꼬리말도 없다
    v7 = cp_row("t7u", "CP")["values"]
    assert all(v7[j] is not None for j in range(12)), "②는 CP도 PL 그대로여야 한다"
    assert v7[2] != pytest.approx(100_000.0), "②에 분기 정산액이 들어갔다"

    # ④ 인건비(원화): 인건비 계약금(60)이 분기달에만, 그 달 환율로 환산
    v5 = cp_row("t5", "CP")["values"]
    assert v5[2] == pytest.approx(60_000.0 * RATE[2]), "④는 인건비 계약금 × 그 달 환율이다"
    assert v5[0] is None


def test_quarterly_amount_is_not_the_annual_contract(backend):
    """분기 칸에 들어가는 건 총 계약금이 아니라 ÷ 4다 — 통째로 넣으면 연간이 4배가 된다."""
    # 라벨을 정확히 집는다 — startswith("CP")는 SARC 하위 줄 "CPU"를 먼저 잡는다.
    v = next(r for r in backend["t2u"]["rows"] if r["label"] == "CP (미청구) NRE · 분기 정산")["values"]
    # 계약도 비용도 $ 단위라 분기 분할(÷4)만 한다(전환 전에는 집계가 K$라 ÷1000이 더 붙었다).
    assert v[2] == pytest.approx(CP_POCS[0]["total"] / 4)


def test_per_capita_uses_period_totals_not_monthly_average(backend):
    """인당 개발비의 분기·연간은 "구간 개발비 합 ÷ 구간 평균 인력"이다 — 월별 비율의 평균이 아니다.

    월별 비율을 그대로 평균 내면 인력이 적은 달의 비율이 왜곡되게 끼어든다. 인력이 미등록인 달이
    섞인 입력(SRCX 3월)에서 두 방식의 값이 갈린다.
    """
    row = next(r for r in backend["t2u"]["rows"] if "인당" in r["label"])
    monthly = [v for v in row["values"][:12] if v is not None]
    naive_annual = sum(monthly) / len(monthly)
    assert row["values"][16] != pytest.approx(naive_annual, rel=1e-6), (
        "연간 인당 개발비가 월별 비율의 평균과 같다 — 구간 합계로 계산하지 않고 있다"
    )


# ---------- 재료가 없으면 표를 뺀다 ----------


def test_krw_tables_need_a_rate(backend):
    """환율이 없으면 원화 표를 아예 안 준다 — 0원 표를 내려보내면 "보정했다"로 읽힌다."""
    only_usd = lab_tables(GROUPS, D, {}, None, CP_QUARTER)
    assert sorted(only_usd) == ["t2u", "t3", "t5u", "t6u", "t7u"]


def test_corrected_tab_is_dropped_without_a_contract(backend):
    """CP 계약이 없으면 ① 개발비 탭(보정본)을 통째로 빼고 준다 — 분기 정산액이 없으면 그 표는
    "보정했다"고 읽히는 PL 복사본이 된다. 화면은 cpNone 안내로 그 이유를 적고 ②를 가리킨다.

    ②③(CP_PL)은 계약과 무관하게 그대로 나온다 — 올라온 PL만 쓰기 때문이다."""
    no_cp = lab_tables(GROUPS, D, {}, RATE, None)
    assert sorted(no_cp) == ["t3", "t4", "t7k", "t7u"]
    assert not any(k in no_cp for k in CP_TABLES), "계약 없이 보정 표가 나왔다"


def test_lab_rows_carry_a_key(backend):
    """연구소 줄에는 key(연구소 코드)가 붙고, 합계·소계·환율·인당 줄에는 없다.

    화면의 "○월 실적 vs 전실행" 표가 이 표를 **두 버전** 받아 같은 연구소끼리 짝짓는다.
    라벨로 짝지으면 틀린다 — 같은 연구소인데 표마다 라벨이 다르다(② 달러 표 "SRCX(APJT포함)"
    vs 원화 표 "SRCX"). 그래서 짝짓기용 키를 서버가 준다.
    """
    for name in TABLES:
        rows = backend[name]["rows"]
        labs = [r for r in rows if r["level"] == 1 and r["key"]]
        assert labs, f"{name}에 연구소 줄이 없다"
        # 합계줄·환율줄은 연구소가 아니라 키가 없다
        assert all(not r["key"] for r in rows if r["total"] or r["format"] == "rate")
        # "인당 개발비"는 level 1인데 연구소가 아니다 — 키가 없어야 걸러낼 수 있다
        assert all(not r["key"] for r in rows if "인당" in r["label"])
        assert len({r["key"] for r in labs}) == len(labs), f"{name}에 키가 겹친다"

    # 라벨은 표마다 달라도 키는 같다 — 그게 짝짓기를 라벨이 아니라 키로 하는 이유다
    u = {r["key"]: r["label"] for r in backend["t2u"]["rows"] if r["level"] == 1 and r["key"]}
    k = {r["key"]: r["label"] for r in backend["t2k"]["rows"] if r["level"] == 1 and r["key"]}
    assert set(u) == set(k)
    assert any(u[code] != k[code] for code in u), "라벨이 전부 같으면 이 테스트가 지키는 게 없다"


def test_lab_part_rows_are_keyed_by_pk(backend):
    """SARC의 CPU · GPU 같은 하위 줄은 연구소 코드가 아니라 pk로 식별한다(한 연구소에 여러 줄)."""
    parts = [r for r in backend["t2u"]["rows"] if r["level"] == 2]
    assert parts, "하위 파트 줄이 없다(② 표에는 있어야 한다)"
    assert all(":" in r["key"] for r in parts), [r["key"] for r in parts]
