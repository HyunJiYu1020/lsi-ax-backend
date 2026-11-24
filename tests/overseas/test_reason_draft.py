"""증감사유 [분석 초안] — 문구 규칙과 엔드포인트 계약.

이 초안은 목업 시절부터 브라우저(core.js ovRsnDraft)가 만들고 있었다. 서버로 옮긴 이유는
services/reason_draft.py 머리말에 있고, 여기서는 **옮긴 뒤에도 같은 규칙으로 쓰는지**를 고정한다.

규칙을 테스트로 박아 두는 이유: 이 문장은 보고서에 그대로 실린다. 임계치 하나가 흔들리면 같은
데이터에서 어제 있던 줄이 오늘 사라지고, 담당자는 그걸 "데이터가 바뀌었다"로 읽는다.
"""

import pytest

from app.accounts.overseas.services import reason_draft as rd
from app.accounts.overseas.services.variance import compute_variance_labs, _non_labor_keys

BASIS_YEAR, BASIS_MONTH = 2026, 10
# 억 단위 금액을 손으로 적지 않게 — 아래 입력들은 "몇 억"으로 읽혀야 한다.
EOK = 1e8


def inputs(**over) -> rd.DraftInputs:
    """기본값은 "인당 단가가 올라서 인건비가 늘고, 환차도 조금 있는 연구소" 한 곳이다.
    테스트마다 보려는 값만 덮어쓴다."""
    base = dict(
        cur_total=100 * EOK, base_total=90 * EOK, diff=10 * EOK,
        fx=1 * EOK, fx_local=0.0, fx_outside=9 * EOK,
        labor_outside=6 * EOK, non_labor_outside=3 * EOK,
        hc_base=40.0, hc_cur=44.0, splittable=True,
        hc_effect=2 * EOK, rate_effect=4 * EOK,
        per_head_base=120_000.0, per_head_cur=132_000.0,
        currency="$", local_currency="",
        categories=(("임차료", 2 * EOK), ("감가상각비", 1 * EOK), ("(급여)", 9 * EOK)),
        rate_base=tuple([1300.0] * 12), rate_cur=tuple([1350.0] * 12),
    )
    return rd.DraftInputs(**{**base, **over})


# ---------- 블록 모양 ----------

def test_block_starts_with_tag_and_bullets():
    """화면은 [분석 초안] 머리말로 이 블록을 찾는다 — 담당자가 고친 사유에서도 "어디까지가 자동
    생성분인지"를 그 표시로 가른다(core.js ovRsnAiStrip과 같은 방식)."""
    out = rd.build(inputs())

    assert out.startswith(rd.DRAFT_TAG + "\n")
    body = out.split("\n")[1:]
    assert body and all(line.startswith("· ") for line in body)


def test_empty_when_nothing_to_say():
    """쓸 줄이 없으면 빈 문자열이다 — 화면이 그 블록을 아예 안 붙인다. 빈 머리말만 남기면
    담당자는 "초안을 만들려다 실패했다"로 읽는다."""
    assert rd.build(inputs(cur_total=0.0)) != ""  # 데이터 없음은 할 말이 있는 경우다
    assert rd.draft_lines(inputs(cur_total=0.0)) == ["선택 버전에 이 연구소 데이터가 없습니다"]


# ---------- 먼저 걸러내는 경우 ----------

def test_missing_data_is_not_zero_change():
    """"데이터가 없다"와 "변동이 없다"를 가른다 — 같이 "변동 없음"으로 적으면 업로드가 안 된 걸
    그대로인 것으로 읽는다. 어느 쪽 버전이 비었는지도 구분해서 알려준다."""
    assert rd.draft_lines(inputs(cur_total=0.0)) == ["선택 버전에 이 연구소 데이터가 없습니다"]
    assert rd.draft_lines(inputs(base_total=0.0)) == ["비교 버전에 이 연구소 데이터가 없습니다"]


def test_no_change_when_both_diff_and_fx_are_small():
    """증감도 환차도 임계치 미만이면 한 줄로 끝낸다. 환차만 있는 경우(금액은 그대로인데 환율이
    움직인 연구소)는 "변동 없음"이 아니다 — 보고에는 그 환차가 올라가야 한다."""
    small = rd.MIN_AMOUNT - 1
    assert rd.draft_lines(inputs(diff=small, fx=small)) == ["변동 없음"]
    assert rd.draft_lines(inputs(diff=small, fx=2 * EOK))[0].startswith("전체 ")


# ---------- ① 전체 ----------

def test_total_line_splits_fx_and_outside():
    """첫 줄은 항상 "전체 = 환차 + 환차外"다. 증가는 ↑, 감소는 △ — 화면 표기(triS)와 같아야
    담당자가 표와 문장을 같은 기호로 읽는다."""
    line = rd.draft_lines(inputs())[0]
    assert line == "전체 10.0억↑ = 환차 1.0억↑ + 환차外 9.0억↑"

    down = rd.draft_lines(inputs(diff=-10 * EOK, fx_outside=-9 * EOK))[0]
    assert down == "전체 △10.0억 = 환차 1.0억↑ + 환차外 △9.0억"


def test_amounts_below_threshold_print_as_zero():
    """임계치 미만은 "0억"으로 눌러 쓴다 — 500만원을 "0.1억"으로 적으면 읽는 사람이 의미 있는
    변동으로 본다."""
    assert rd.eok(rd.MIN_AMOUNT - 1) == "0억"
    assert rd.eok(-(rd.MIN_AMOUNT - 1)) == "0억"
    assert rd.eok(rd.MIN_AMOUNT) == "0.1억↑"
    assert rd.eok(-rd.MIN_AMOUNT) == "△0.1억"


# ---------- ② 인건비 ----------

def test_labor_line_names_the_bigger_effect():
    """인원 효과와 단가 효과를 같이 적고, 큰 쪽을 결론으로 붙인다."""
    line = next(x for x in rd.draft_lines(inputs()) if x.startswith("인건비 "))

    assert "인원 40.0→44.0명(4.0명↑)" in line
    assert "인당 인건비 $120K→$132K(10.0%↑)" in line
    assert line.endswith("주로 인당 단가 상승 영향")

    hc_driven = next(
        x for x in rd.draft_lines(inputs(hc_effect=5 * EOK, rate_effect=1 * EOK))
        if x.startswith("인건비 ")
    )
    assert hc_driven.endswith("주로 인원 증가 영향")


def test_labor_line_skips_conclusion_when_both_effects_are_small():
    """두 효과가 다 작으면 "주로 ~ 영향"을 붙이지 않는다 — 1천만원 차이로 주된 원인을 단정하면
    보고 문장이 과해진다."""
    line = next(
        x for x in rd.draft_lines(inputs(hc_effect=EOK / 100, rate_effect=EOK / 100))
        if x.startswith("인건비 ")
    )
    assert "주로" not in line


def test_labor_line_needs_headcount_on_both_sides():
    """인력을 안 낸 연구소(CP 등)는 단가를 낼 수 없어 이 줄을 쓰지 않는다 — 단가를 만들어 적으면
    없는 근거를 대는 셈이다."""
    assert not any(x.startswith("인건비 ") for x in rd.draft_lines(inputs(hc_cur=0.0)))
    assert not any(x.startswith("인건비 ") for x in rd.draft_lines(inputs(hc_base=0.0)))


def test_labor_line_says_so_when_split_is_impossible():
    """경영계획 대비처럼 환율 기준이 없으면 인원 · 단가로 가를 수 없다(labor_split이 거부한다).
    표의 분해 줄도 그때는 비어 있으니 문장도 분해를 말하지 않는다 — 인원 변동까지만 적는다."""
    line = next(x for x in rd.draft_lines(inputs(splittable=False)) if x.startswith("인건비 "))

    assert "인원 40.0→44.0명(4.0명↑)" in line
    assert "환율 기준이 없어 인원 · 단가 분해 불가" in line
    assert "인당 인건비" not in line


def test_tiny_headcount_move_reads_as_unchanged():
    """인원은 월평균이라 0.04명 같은 값이 나온다 — "0.0명 증가"로 적으면 변동으로 읽힌다."""
    line = next(
        x for x in rd.draft_lines(inputs(hc_base=40.0, hc_cur=40.04)) if x.startswith("인건비 ")
    )
    assert "인원 40.0→40.0명(변동 없음)" in line


def test_per_head_prints_local_currency_in_thousands():
    """인당 인건비는 연구소 담당자가 읽는 값이라 현지통화 K로 쓴다(양식 단위가 K$다).

    저장값은 $라 1000으로 나눠야 K다 — 화면 ovRsnDraft의 mo()에는 그 나눗셈이 빠져서 $132K를
    "$132,000K"로 적고 있었다. 옮기면서 고친 자리이므로 숫자로 박아 둔다.
    """
    assert rd.per_head_text(132_000.0, "$") == "$132K"
    assert rd.per_head_text(12_906_000.0, "INR") == "INR 12,906K"

    line = next(
        x for x in rd.draft_lines(inputs(currency="INR", local_currency="INR",
                                         per_head_base=12_866_000.0, per_head_cur=12_906_000.0))
        if x.startswith("인건비 ")
    )
    assert "인당 인건비 INR 12,866K→INR 12,906K" in line
    assert line.endswith("(현지통화 INR 기준)")


# ---------- ③ 인건비外 ----------

def test_non_labor_line_lists_top_categories_only():
    """변동이 큰 계정만, 금액순으로 상위 몇 개. 전부 적으면 한 문장이 20개 계정으로 늘어난다."""
    line = next(x for x in rd.draft_lines(inputs(
        categories=(("임차료", 2 * EOK), ("감가상각비", 1 * EOK), ("전산비", 3 * EOK),
                    ("보험료", 0.5 * EOK), ("출장비", rd.MIN_AMOUNT - 1)),
    )) if x.startswith("인건비外 "))

    assert line == "인건비外 3.0억↑: 전산비 3.0억↑, 임차료 2.0억↑, 감가상각비 1.0억↑"
    assert "출장비" not in line  # 임계치 미만은 빠진다


def test_non_labor_line_strips_parentheses_from_labels():
    """표에서는 들여쓰기를 괄호로 표시하지만("(급여)"), 문장에 그대로 넣으면 "(교육훈련비)
    1.2억↑"처럼 읽힌다."""
    line = next(x for x in rd.draft_lines(inputs(categories=(("(복리후생)", 2 * EOK),)))
                if x.startswith("인건비外 "))
    assert "복리후생 2.0억↑" in line and "(" not in line


def test_non_labor_line_without_big_categories_still_shows_total():
    """계정별로는 작아도 합이 크면 그 합은 적는다 — 줄을 빼면 환차外가 어디서 왔는지 안 보인다."""
    line = next(x for x in rd.draft_lines(inputs(categories=())) if x.startswith("인건비外 "))
    assert line == "인건비外 3.0억↑"


def test_labor_leaves_never_appear_in_non_labor_line():
    """급여 · 상여 · 복리후생은 인건비 줄에서 이미 말했다 — 인건비外에 또 적으면 같은 금액을 두
    번 센 것처럼 읽힌다. 서버가 고르는 계정 목록 자체에서 빠져 있어야 한다."""
    keys = _non_labor_keys()

    assert not (set(keys) & {"sal", "bonus", "welf", "labor", "hr"})
    assert "B" not in keys and "hc" not in keys and "pct" not in keys
    assert {"edu", "travel", "dep", "rent", "comm"} <= set(keys)


# ---------- ④ 환차 ----------

def test_fx_line_shows_applied_rate_move():
    """환차가 왜 생겼는지 한 조각 — 기준 대비 움직인 달만 모아 평균 적용 환율을 적는다."""
    line = next(x for x in rd.draft_lines(inputs()) if x.startswith("환차 "))
    assert line == "환차 1.0억↑ (1~12월 평균 적용 환율 1,300→1,350원/$)"


def test_fx_line_only_counts_months_that_moved():
    """반올림 차이까지 환율 변동으로 적으면 환차가 없는 달까지 문장에 끌려 들어온다."""
    rate_cur = tuple([1300.0] * 9 + [1350.0, 1360.0, 1370.0])
    line = next(x for x in rd.draft_lines(inputs(rate_cur=rate_cur)) if x.startswith("환차 "))
    assert "(10~12월 평균 적용 환율" in line


def test_fx_line_adds_local_leg_when_big():
    """현지통화 연구소는 현지→$ 환차를 따로 적는다 — $→원과 원인이 다르다."""
    line = next(x for x in rd.draft_lines(inputs(fx=3 * EOK, fx_local=2 * EOK,
                                                 local_currency="ILS"))
                if x.startswith("환차 "))
    assert line.endswith("현지통화(ILS)→$ 2.0억↑")


def test_fx_line_skipped_when_small():
    """환차가 작으면 줄을 쓰지 않는다 — 0억짜리 줄은 읽는 사람의 시간만 쓴다."""
    lines = rd.draft_lines(inputs(fx=rd.MIN_AMOUNT - 1))
    assert not any(x.startswith("환차 ") for x in lines)


# ---------- 엔드포인트 계약 ----------

def test_labs_table_carries_a_draft_per_lab(db):
    """연구소별 요약 표의 줄마다 초안이 같이 온다 — 화면이 그 줄의 사유 칸에 바로 꽂는다.
    합계행은 빈 문자열이다(화면의 그 줄에는 사유 칸이 없다)."""
    out = compute_variance_labs(db, BASIS_YEAR, BASIS_MONTH, "forecast", "prev", None)

    total = next(r for r in out.labs if r.is_total)
    assert total.reason_draft == ""
    labs = [r for r in out.labs if not r.is_total]
    assert labs, "연구소 줄이 없다"
    for row in labs:
        assert row.reason_draft.startswith(rd.DRAFT_TAG), f"{row.lab_code}: 초안이 없다"


def test_draft_numbers_match_the_table_cell(db):
    """초안의 첫 줄은 그 줄 연간 칸(diff · fx · fx_outside)을 그대로 읽은 문장이어야 한다 —
    문장과 바로 옆 숫자가 어긋날 자리를 없애는 게 서버로 옮긴 이유다."""
    out = compute_variance_labs(db, BASIS_YEAR, BASIS_MONTH, "forecast", "prev", None)

    checked = 0
    for row in (r for r in out.labs if not r.is_total):
        cell = row.cells["y"]
        # 한쪽 버전이 비어 있는 연구소(쓰기로 등록만 해 두고 자료가 없는 곳)는 "데이터가 없습니다"
        # 한 줄로 끝난다 — 0억 증가로 적지 않는 게 맞다(위 test_missing_data_is_not_zero_change).
        if not cell.cur or not cell.base:
            assert "데이터가 없습니다" in row.reason_draft, f"{row.lab_code}: 안내가 없다"
            continue
        checked += 1
        head = row.reason_draft.split("\n")[1]
        assert head == (
            f"· 전체 {rd.eok(cell.diff)} = 환차 {rd.eok(cell.fx)}"
            f" + 환차外 {rd.eok(cell.fx_outside)}"
        ), f"{row.lab_code}: 초안 첫 줄이 표 칸과 다르다"
    assert checked, "자료가 있는 연구소가 없어 아무것도 검증하지 않았다"


def test_draft_is_stable_across_calls(db):
    """같은 데이터면 몇 번 조회해도 글자까지 같다 — LLM이 끼지 않은 규칙 기반 문장이라 그래야
    하고, 담당자가 저장한 사유와 초안을 비교해 "수정됨"을 판정하는 쪽이 그 성질에 기대고 있다."""
    args = (BASIS_YEAR, BASIS_MONTH, "forecast", "prev", None)
    first = {r.lab_code: r.reason_draft for r in compute_variance_labs(db, *args).labs}
    second = {r.lab_code: r.reason_draft for r in compute_variance_labs(db, *args).labs}

    assert first == second


@pytest.mark.parametrize("base_version", ["prev", "prior", "plan"])
def test_every_base_version_produces_a_draft(db, base_version):
    """비교 기준 셋 다 초안이 나온다. 경영계획 대비는 환차를 가를 수 없어 문장이 짧아지지만
    (인원 · 단가 분해 불가), 줄 자체가 없어지면 안 된다."""
    out = compute_variance_labs(db, BASIS_YEAR, BASIS_MONTH, "forecast", base_version, None)

    labs = [r for r in out.labs if not r.is_total]
    assert all(r.reason_draft.startswith(rd.DRAFT_TAG) for r in labs)
    if base_version == "plan":
        labor = [x for r in labs for x in r.reason_draft.split("\n") if x.startswith("· 인건비 ")]
        assert labor and all("분해 불가" in x for x in labor)
