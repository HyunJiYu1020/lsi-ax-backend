"""추가 행 이름이 이미 있는 행과 겹치면 챗봇 경로에서 막는다 (TC-016).

"인건비 행 추가해줘"는 보통 "인건비 **아래에** 하위 항목을 넣어 달라"는 뜻이지 "인건비라는 행을 또
만들어 달라"가 아니다. 그런데 row_key만 다르면 서버는 그대로 만들어 주고, 집계표에 '인건비'가 두
줄로 나온다 — row_match.py가 애초에 해결하려던 그 문제(같은 이름이 두 줄로 쪼개지는 것)와 같다.

화면 경로(source="screen")는 그대로 통과시킨다. 사람이 표를 보면서 누르는 거라 같은 이름이 이미
있으면 눈에 보이고, 무엇보다 화면은 이 PUT의 실패를 무시한다(core.js의 .catch(()=>{})) — 거기서
400을 주면 화면에는 행이 보이는데 서버에는 없는 상태가 조용히 생긴다.
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.collect import upsert_extra_row
from app.accounts.overseas.schemas import ExtraRowIn

LAB = "SARC:CPU"
VER = "forecast"
YM = 209801


def _row(**kw):
    base = dict(lab_pk=LAB, version=VER, row_key="t_dup", label="새 행", yyyymm=YM,
                anchor_key="misc", v=[1.0] * 12, source="chat")
    base.update(kw)
    return ExtraRowIn(**base)


@pytest.mark.parametrize("label,expected", [
    ("인건비", "인건비"),            # 고정 계정 한글명
    ("Salaries & Wages", "급여"),    # 고정 계정 영문명 — 파일은 영문으로 온다
    ("인건비 ", "인건비"),           # 공백만 다른 이름도 같은 행이다
    ("travelexp", "출장비"),         # 정규화하면 같은 이름
])
def test_blocks_fixed_account_name(db, label, expected):
    with pytest.raises(HTTPException) as e:
        upsert_extra_row(_row(label=label), db)
    assert e.value.status_code == 400
    assert expected in e.value.detail
    # 모델이 그냥 재시도하면 같은 일이 반복된다 — 무엇을 해야 하는지 같이 알려줘야 한다.
    assert "재시도하지 말고" in e.value.detail
    assert "anchor_key" in e.value.detail


def test_blocks_existing_extra_row_name(db):
    """같은 연구소·버전·연월에 이미 쓰이는 추가 행 이름도 막는다."""
    upsert_extra_row(_row(row_key="t_first", label="클라우드 사용료", source="screen"), db)

    with pytest.raises(HTTPException) as e:
        upsert_extra_row(_row(row_key="t_second", label="클라우드 사용료"), db)
    assert "이미 있다" in e.value.detail
    assert "t_first" in e.value.detail, "어느 행과 겹쳤는지 알려줘야 사용자에게 설명할 수 있다"


def test_same_row_key_is_not_a_duplicate(db):
    """같은 row_key로 다시 부르는 건 중복이 아니라 **수정**이다 — 자기 자신과 겹친다고 막으면
    이름·자리만 고치는 흐름(v 없이 호출)이 전부 400이 된다."""
    upsert_extra_row(_row(row_key="t_same", label="클라우드 사용료", source="screen"), db)
    out = upsert_extra_row(_row(row_key="t_same", label="클라우드 사용료", anchor_key="it", v=None), db)
    assert out.anchor_key == "it"
    assert out.v[:3] == [1.0, 1.0, 1.0], "옮기기인데 금액이 바뀌었다"


def test_screen_path_is_not_blocked(db):
    """화면 경로는 막지 않는다 — 화면은 이 PUT의 실패를 무시해서, 400을 주면 화면과 서버가
    조용히 어긋난다(행이 화면에는 있고 서버에는 없다)."""
    out = upsert_extra_row(_row(label="인건비", source="screen"), db)
    assert out.label == "인건비"


def test_other_lab_or_month_is_not_a_duplicate(db):
    """다른 연구소·다른 연월의 같은 이름은 중복이 아니다 — 12개 연구소가 같은 이름을 쓰는 게 정상이다."""
    upsert_extra_row(_row(row_key="t_l1", label="클라우드 사용료", source="screen"), db)
    assert upsert_extra_row(_row(row_key="t_l2", label="클라우드 사용료", lab_pk="DSRA"), db)
    assert upsert_extra_row(_row(row_key="t_l3", label="클라우드 사용료", yyyymm=209802), db)
