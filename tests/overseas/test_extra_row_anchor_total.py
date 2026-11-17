"""추가 행은 **자기가 붙은 자리의 합계에만** 더해진다 — DB 없이 순수 함수만 본다.

규칙은 하나다: 전산비 아래에 넣으면 전산비 → 일반운영비 → 개발비 → 개발비(매출)로 타고 올라가고,
IT 관련 비용 아래에 넣으면 IT 관련 비용 합계까지만 간다(그 줄 자체가 어느 총계에도 안 올라가기
때문이다 — IT 관련 비용·외주용역비는 이미 다른 분류에 잡힌 금액을 다른 각도로 묶어 보여주는
참고 행이라, 총계에 또 더하면 같은 돈을 두 번 세게 된다). 빼면 그 합계에서 빠진다.

어느 합계에 넣을지는 **자리를 고를 때** 정한다 — 그래서 자리와 상관없이 총계에 얹어 주는 예외를
두지 않는다(그러면 "행이 붙은 자리"와 "금액이 세어지는 자리"가 달라져 규칙을 설명할 수 없다).
"""

from app.accounts.overseas.cost_groups import resolve_leaves
from app.accounts.overseas.services.aggregate import (
    LABOR_TREE,
    REF_ONLY_ANCHORS,
    TOT_TREE,
    agg_months,
    extra_keys,
    extra_total,
)

CLOUD = [10.0] * 12  # 'IT 관련 비용' 아래에 붙인 추가 행
TRIP = [7.0] * 12  # 출장비(travel) 아래에 붙인 추가 행
ITX_TREE = set(resolve_leaves("itx")) | {"itx"}
HR_TREE = set(resolve_leaves("hr")) | {"hr"}
EXTRA = {"itx": CLOUD, "travel": TRIP}


def test_reference_lines_are_the_ones_outside_the_total():
    """IT 관련 비용·외주용역비는 개발비 총계 트리 밖이다 — 목록을 손으로 적지 않고 트리에서 뽑는다."""
    assert "itx" in REF_ONLY_ANCHORS and "outs" in REF_ONLY_ANCHORS
    assert not (REF_ONLY_ANCHORS & TOT_TREE)
    assert "travel" not in REF_ONLY_ANCHORS and "misc" not in REF_ONLY_ANCHORS


def test_a_row_under_a_real_account_rolls_up_to_the_total():
    assert extra_total(EXTRA, TOT_TREE, 0) == 7.0  # 출장비 아래 행만 올라온다
    assert extra_keys(EXTRA, TOT_TREE) == ["travel"]


def test_a_row_under_a_reference_line_stays_there():
    """IT 관련 비용 아래 행은 그 줄 합계에만 잡힌다 — 개발비 합계에는 안 올라간다."""
    assert extra_total(EXTRA, ITX_TREE, 0) == 10.0
    assert extra_total(EXTRA, TOT_TREE, 0) == 7.0, "참고 행 아래 금액이 총계로 새어 올라갔다"


def test_it_does_not_leak_into_other_groups():
    assert extra_total(EXTRA, HR_TREE, 0) == 7.0  # 출장비는 인원성경비 하위다
    assert extra_total(EXTRA, LABOR_TREE, 0) == 0.0  # 인건비에는 안 든다


def test_an_empty_added_row_keeps_the_cell_from_going_blank():
    """값이 0인 추가 행도 "있는" 행이다 — 합계가 0이라고 빈칸으로 접으면 방금 만든 행이 사라진다.
    (행 추가는 v를 전부 0으로 만들고 나중에 칸을 채우는 흐름이라 이 상태가 정상이다.)"""
    assert agg_months({}, {"travel": [0.0] * 12}, TOT_TREE)[0] == 0.0


def test_nothing_uploaded_and_nothing_added_is_still_blank():
    assert agg_months({}, {}, TOT_TREE)[0] is None
