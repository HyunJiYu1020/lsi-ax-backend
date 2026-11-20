"""말로 시켜서 추가 행을 만들 때는 "어느 항목 아래"인지를 먼저 받는다 — DB 없이 순수 함수만 본다.

왜 막아야 하나: anchor_key를 비우면 표에서는 맨 끝에 붙지만 **집계에서는 기타경비로 합산된다**
(services/aggregate.py의 ANCHORABLE 판정, core.js ovCustExtra와 같은 규칙). 금액이 틀리는 게 아니라
자리가 틀리는 형태라 숫자만 봐서는 못 알아챈다. 화면의 '+행 추가' 버튼은 행이 어디 붙었는지 눈에
보이니 그대로 두고, 보이지 않는 이 경로에서만 위치를 먼저 받는다.
"""

from app.shared.endpoints.chat import _anchor_missing_hint, _write_block_hint

ROW = {"lab_pk": "SARC:CPU", "version": "forecast", "row_key": "r1", "label": "클라우드 사용료"}


def test_blocks_a_raw_row_without_a_place():
    hint = _anchor_missing_hint("upsert_extra_row", ROW)

    assert hint is not None
    assert "재시도하지 말고" in hint  # 모델이 같은 호출을 되풀이하지 않게
    assert "기타경비" in hint  # 비워 두면 어디로 가는지 사용자에게 설명할 수 있어야 한다
    assert "itx(IT 관련 비용, 그룹)" in hint  # 후보를 지어내지 않도록 유효값을 같이 준다
    # 자리에 따라 어느 합계에 잡히는지가 달라진다는 걸 짚어 줘야 한다.
    assert "개발비 합계에는 안 들어간다" in hint
    assert "anchor_key='A'" in hint  # 중분류에 안 넣고 총계에만 넣는 자리도 알려준다


def test_allows_a_row_that_names_its_place():
    assert _anchor_missing_hint("upsert_extra_row", {**ROW, "anchor_key": "itx"}) is None
    # 공백만 있는 값은 "정했다"로 치지 않는다 — 빈 문자열과 같은 결과가 된다.
    assert _anchor_missing_hint("upsert_extra_row", {**ROW, "anchor_key": "  "}) is not None


def test_derived_rows_are_exempt():
    """파생 행은 '개발비(A)' 합계에 더해지지 않아서 기타경비로 섞일 일이 없다."""
    assert _anchor_missing_hint("upsert_extra_row", {**ROW, "is_derived": True}) is None


def test_other_tools_are_untouched():
    assert _anchor_missing_hint("upsert_cost_entry", {"amount": 1}) is None
    assert _anchor_missing_hint("upsert_extra_col", {"col_key": "q1"}) is None


def test_write_block_hint_is_the_single_entry_point():
    """공급자별 턴 루프가 검사를 따로 호출하면 한쪽만 고치고 다른 쪽을 빼먹는다 — 입구는 하나다."""
    # 자리 미지정(이 검사)
    assert _write_block_hint("upsert_extra_row", ROW, {"step": "취합"}) is not None
    # 파생 행을 '집계'가 아닌 탭에서 만들려는 경우(기존 검사)도 같은 입구로 걸린다
    derived = {**ROW, "is_derived": True, "anchor_key": "itx"}
    assert _write_block_hint("upsert_extra_row", derived, {"step": "취합"}) is not None
    assert _write_block_hint("upsert_extra_row", derived, {"step": "집계"}) is None
