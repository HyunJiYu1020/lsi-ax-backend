"""열 누락 검증 — 양식의 기간 열(1~12월 · 1Q~4Q · 연간)이 파일에 **없을 때** 알리는지.

규칙은 둘로 나뉜다. 열이 **없는 것**과 열은 있는데 **값이 빈 것**은 서로 다른 사건이다:
  - 값이 빔  → missing_cell. 연구소가 그 칸을 안 적었다. 계정마다 다를 수 있다.
  - 열이 없음 → missing_column. 양식에서 그 열이 빠졌다. 모든 계정이 한꺼번에 빈다.
둘을 안 나누면 9월 열 하나가 빠진 파일에서 "9월 1칸 비어 있음" 경고가 계정 수만큼(30여 건) 쏟아지고,
정작 원인("열이 없다")은 어디에도 안 적힌다. 그래서 열 단위로 한 줄만 올리고, 없는 열의 칸은 빈칸
검사에서 뺀다.

양식 밖의 행·열(연구소가 더 보낸 것)은 이 검사의 대상이 아니다 — 그건 그려서 저장하면 되는
자료이고(추가 행·추가 열), "있어야 할 것이 없는" 상태가 아니다. 그 점도 같이 고정한다.
"""

from app.accounts.overseas.cost_groups import GROUP_CALC, category_label, resolve_leaves
from app.shared.core import anomaly

ALL_COLS = [f"m{m}" for m in range(1, 13)] + ["q1", "q2", "q3", "q4", "y"]


def detect(vals, cols=None, **over):
    return anomaly.detect(
        vals=vals, base_vals=None, sums=None, reasons=None,
        rules=anomaly.merged_rules([], over or None),
        group_calc=GROUP_CALC, label_of=category_label, leaves_of=resolve_leaves,
        cols=cols,
    )


def of(findings, rule):
    return [f for f in findings if f["rule"] == rule]


FULL = {"sal": [100.0] * 12, "bonus": [10.0] * 12}


def test_양식_열이_다_있으면_열_누락_경고가_없다():
    assert of(detect(FULL, cols=ALL_COLS), "missing_column") == []


def test_월_열이_빠지면_그_열을_한_줄로_알린다():
    """9월 열이 없는 파일 — 모든 계정의 9월이 비지만 경고는 열 한 줄이다."""
    vals = {k: [None if i == 8 else 100.0 for i in range(12)] for k in ("sal", "bonus")}
    f = detect(vals, cols=[c for c in ALL_COLS if c != "m9"])
    col = of(f, "missing_column")
    assert len(col) == 1
    assert "9월" in col[0]["message"] and col[0]["months"] == [9]
    # 같은 사건을 빈칸으로 또 세지 않는다 — 안 그러면 계정 수만큼 쌓인다.
    assert of(f, "missing_cell") == []


def test_분기_연간_열이_빠져도_알린다():
    """연구소가 월만 보내고 1Q~4Q·연간을 뺀 경우. 월 값은 다 있어서 빈칸으로는 안 걸린다."""
    col = of(detect(FULL, cols=[f"m{m}" for m in range(1, 13)]), "missing_column")
    assert len(col) == 1
    assert col[0]["months"] == [13, 14, 15, 16, 17]
    for label in ("1Q", "2Q", "3Q", "4Q", "연간"):
        assert label in col[0]["message"]


def test_열은_있고_값만_비면_빈칸으로_걸린다():
    """사용자 규칙의 두 번째 — 값이 빠져도 경고다. 열 누락과 섞이지 않아야 한다."""
    vals = {"sal": [None if i == 8 else 100.0 for i in range(12)], "bonus": [100.0] * 12}
    f = detect(vals, cols=ALL_COLS)
    assert of(f, "missing_column") == []
    cell = of(f, "missing_cell")
    assert len(cell) == 1 and cell[0]["category_key"] == "sal" and cell[0]["months"] == [9]


def test_열이_빠진_달과_값이_빈_달이_같이_있으면_둘_다_알린다():
    """9월 열이 없고(열 누락), 3월은 열은 있는데 급여만 비어 있다(빈칸)."""
    vals = {
        "sal": [None if i in (2, 8) else 100.0 for i in range(12)],
        "bonus": [None if i == 8 else 100.0 for i in range(12)],
    }
    f = detect(vals, cols=[c for c in ALL_COLS if c != "m9"])
    assert of(f, "missing_column")[0]["months"] == [9]
    cell = {x["category_key"]: x["months"] for x in of(f, "missing_cell")}
    assert cell == {"sal": [3]}  # bonus의 9월은 열 누락으로 이미 셌다


def test_열_목록을_안_주면_검사를_건너뛴다():
    """저장된 값으로 하는 재검사는 어떤 열로 들어온 자료인지 알 수 없다 —
    그때 "열이 17개 다 없다"가 되면 경고가 거짓이 된다."""
    assert of(detect(FULL, cols=None), "missing_column") == []


def test_기간을_늘려_받은_파일의_2년차_열은_양식_검사에_안_걸린다():
    """24개월 파일("m13"…)은 1년차 열이 다 있으면 통과다 — 양식은 1년 17칸이다."""
    cols = ALL_COLS + [f"m{m}" for m in range(13, 25)]
    assert of(detect(FULL, cols=cols), "missing_column") == []


def test_기준을_끄면_알리지_않는다():
    f = detect(FULL, cols=[f"m{m}" for m in range(1, 13)], missing_column={"enabled": False})
    assert of(f, "missing_column") == []
