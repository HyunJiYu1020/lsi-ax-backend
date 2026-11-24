"""전용 저장(추가 행·추가 열 칸)의 동시 편집 지문 사슬.

왜 있나: 전용 저장도 지문(state_token)에 잡히는 테이블(cost_entries·details)을 쓴다. 응답에
새 지문을 안 실어 주면, 칸 하나 고친 화면이 낡은 지문을 들고 있다가 **다음 전체 저장이 자기
저장과 충돌**한다(409 "누군가 같은 화면을 저장했습니다" — 실제로 겪은 사고).

계약:
 ① PUT(추가 행·추가 열 칸)은 낡은 지문이면 409로 거부한다 — vendor-rows·PUT /state와 같은 규칙.
 ② 성공 응답에는 저장 뒤의 **새 지문**이 실려 있다(받은 낡은 지문을 되돌려주면 안 된다).
 ③ 그 지문으로 다음 전용 저장 → 또 그 지문으로 전체 저장까지 사슬이 이어진다(최종 회귀 시나리오).
 ④ 삭제는 409를 내지 않는다(그 409는 "단계 확인" 뜻으로 이미 쓰여서) — 지문이 맞았을 때만
    새 지문을 돌려주고, 아니면 None이다.
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.collect import (
    delete_extra_row,
    upsert_cost_extra,
    upsert_extra_row,
)
from app.accounts.overseas.schemas import ExtraRowIn, ExtraValueIn
from app.accounts.overseas.services.labs import all_labs
from app.accounts.overseas.services.state import state_token

YM = "209509"  # 실제 데이터가 없는 가짜 기준월


@pytest.fixture
def lab(db):
    pks = [x.pk for x in all_labs(db) if x.pk != "_ETC"]
    if not pks:
        pytest.skip("연구소 기준정보가 없어 스킵합니다")
    return pks[0]


def _row(db, lab, token, row_key="ux_tok"):
    return upsert_extra_row(
        ExtraRowIn(lab_pk=lab, version="forecast", row_key=row_key, label="지문시험",
                   yyyymm=YM, v=[0.0] * 12, state_token=token),
        db=db,
    )


def _cell(db, lab, token, value="메모"):
    return upsert_cost_extra(
        ExtraValueIn(lab_pk=lab, version="forecast", row_key="hc", yyyymm=YM,
                     label="지문열", value=value, state_token=token),
        db=db,
    )


def test_낡은_지문이면_409로_거부한다(db, lab):
    t0 = state_token(db)
    _row(db, lab, t0)  # 지문이 바뀐다
    with pytest.raises(HTTPException) as e:
        _cell(db, lab, t0)  # 낡은 지문으로 다시 쓰기
    assert e.value.status_code == 409


def test_응답의_지문은_저장_뒤의_새_지문이다(db, lab):
    t0 = state_token(db)
    out = _row(db, lab, t0)
    assert out.state_token and out.state_token != t0
    assert out.state_token == state_token(db)  # 지금 지문과 같아야 다음 저장이 통과한다


def test_지문_없이_부르면_검사하지_않는다(db, lab):
    _row(db, lab, None)  # 챗봇처럼 지문 없이 — 예외가 없어야 한다
    out = _cell(db, lab, None)
    assert out.state_token  # 새 지문은 그래도 준다(화면이 아니면 버리면 그만이다)


def test_사슬_전용저장_둘_뒤_전체저장이_통과한다(db, lab):
    """겪은 사고의 회귀: 칸 입력(전용) → 행 저장(전용) → 전체 저장이 409 없이 끝나야 한다."""
    from app.accounts.overseas.schemas import OverseasState
    from app.accounts.overseas.services.state import save_state

    t0 = state_token(db)
    t1 = _cell(db, lab, t0).state_token
    t2 = _row(db, lab, t1).state_token
    out = save_state(
        db,
        OverseasState(step=1, basis_year=2095, basis_month=9, V={}, files={}, note={}, state_token=t2),
    )
    assert out.get("status") == "ok"


def test_삭제는_지문이_맞을_때만_새_지문을_준다(db, lab):
    t0 = state_token(db)
    _row(db, lab, t0, row_key="ux_tokdel")
    fresh = state_token(db)
    # 낡은 지문으로 삭제 — 거부하지 않고 지워 주되 새 지문은 안 준다
    out = delete_extra_row(lab_pk=lab, version="forecast", row_key="ux_tokdel", yyyymm=YM,
                           step="취합", client_token=t0, db=db)
    assert out["status"] == "ok" and out["state_token"] is None
    # 맞는 지문으로 삭제 — 새 지문이 온다
    _row(db, lab, None, row_key="ux_tokdel2")
    cur = state_token(db)
    out = delete_extra_row(lab_pk=lab, version="forecast", row_key="ux_tokdel2", yyyymm=YM,
                           step="취합", client_token=cur, db=db)
    assert out["status"] == "ok" and out["state_token"]
