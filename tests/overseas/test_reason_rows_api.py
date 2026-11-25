"""증감사유 전용 저장 API — `PUT /state`에서 떼어낸 도메인별 저장.

외주용역·CP와 의미가 다르다: 전체 교체가 아니라 **보낸 것만 누적 반영**이다. 증감사유는 연구소
회신을 항목별로 쌓아 가는 자료라, 한 시트를 올렸을 때 다른 항목의 사유가 사라지면 안 된다.
"""

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select

from app.accounts.overseas.endpoints.collect import (
    get_overseas_state,
    save_overseas_state as _save_overseas_state,
    save_reason_rows as _save_reason_rows,
)
from app.accounts.overseas.models import OverseasReasonRow
from app.accounts.overseas.schemas import ReasonRow, ReasonRowsIn
from app.accounts.overseas.services.labs import all_labs


# 이 엔드포인트들은 BackgroundTasks를 받는다 — 증감사유 번역을 응답 뒤로 미루기 때문이다
# (services/reason_rows.prewarm_in_background). 테스트는 BackgroundTasks를 만들어 넘기고 그 안에
# 쌓인 작업은 실행하지 않는다 — 저장 로직만 검증하고 LLM을 타지 않는다(번역은 test_reason_prewarm).
def save_overseas_state(*a, **kw):
    kw.setdefault("background", BackgroundTasks())
    return _save_overseas_state(*a, **kw)

def save_reason_rows(*a, **kw):
    kw.setdefault("background", BackgroundTasks())
    return _save_reason_rows(*a, **kw)

BASIS_YEAR = 2026
BASIS_MONTH = 9
# 증감사유는 이제 "어느 달 회신인지"(yyyymm)를 들고 다닌다 — 없으면 400이다.
YM = f"{BASIS_YEAR}{BASIS_MONTH:02d}"
VER = "forecast"   # version은 cost_entries와 같은 값만 받는다(forecast|actual)


@pytest.fixture
def lab_code(db) -> str:
    codes = sorted({lab.lab_code for lab in all_labs(db)})
    if not codes:
        pytest.skip("연구소가 없다")
    return codes[0]


def _row(lab: str, cat: str, remark: str = "사유", block: str = "y") -> ReasonRow:
    return ReasonRow(
        lab_code=lab, version=VER, yyyymm=YM, category_key=cat, block=block, mon_label="",
        plan_value=10, prev_value=20, cur_value=30, diff_plan=20, diff_prev=10,
        remark_plan=remark, remark_prev=remark, prev_label="M2608A", cur_label="M2609A",
        source_file="r.xlsx",
    )


def _stored(db, lab: str) -> dict[tuple[str, str], str]:
    return {
        (r.category_key, r.block): r.remark_plan
        for r in db.scalars(
            select(OverseasReasonRow).where(
                OverseasReasonRow.lab_code == lab, OverseasReasonRow.version == VER
            )
        )
    }


def test_saves_rows(db, lab_code):
    res = save_reason_rows(body=ReasonRowsIn(rows=[_row(lab_code, "sal"), _row(lab_code, "travel")]), db=db)

    assert res["status"] == "ok" and res["saved"] == 2
    got = _stored(db, lab_code)
    assert got[("sal", "y")] == "사유" and got[("travel", "y")] == "사유"


def test_accumulates_instead_of_replacing(db, lab_code):
    """보낸 것만 반영한다 — 다른 항목의 사유를 지우지 않는다(외주용역·CP와 다른 점)."""
    save_reason_rows(body=ReasonRowsIn(rows=[_row(lab_code, "sal", "급여사유")]), db=db)
    save_reason_rows(body=ReasonRowsIn(rows=[_row(lab_code, "travel", "출장사유")]), db=db)

    got = _stored(db, lab_code)
    assert got[("sal", "y")] == "급여사유", "앞서 넣은 사유가 사라졌다(전체 교체가 돼 버렸다)"
    assert got[("travel", "y")] == "출장사유"


def test_same_key_is_overwritten(db, lab_code):
    """같은 키(연구소×버전×항목×구간)를 다시 보내면 덮어쓴다 — 재업로드가 수정으로 동작해야 한다."""
    save_reason_rows(body=ReasonRowsIn(rows=[_row(lab_code, "sal", "처음")]), db=db)
    save_reason_rows(body=ReasonRowsIn(rows=[_row(lab_code, "sal", "고침")]), db=db)

    assert _stored(db, lab_code)[("sal", "y")] == "고침"


def test_duplicate_keys_in_one_request_last_wins(db, lab_code):
    """한 요청에 같은 키가 두 번 오면 마지막이 이긴다 — 안 걸러내면 Postgres가
    "cannot affect row a second time"으로 요청 전체를 거부한다."""
    res = save_reason_rows(
        body=ReasonRowsIn(rows=[_row(lab_code, "sal", "먼저"), _row(lab_code, "sal", "나중")]), db=db
    )

    assert res["saved"] == 1
    assert _stored(db, lab_code)[("sal", "y")] == "나중"


def test_year_and_month_blocks_are_separate(db, lab_code):
    """연간(y)과 월별(m)은 다른 행이다 — 같은 항목이라도 구간이 다르면 따로 쌓인다."""
    save_reason_rows(
        body=ReasonRowsIn(rows=[_row(lab_code, "sal", "연간", "y"), _row(lab_code, "sal", "월별", "m")]), db=db
    )

    got = _stored(db, lab_code)
    assert got[("sal", "y")] == "연간" and got[("sal", "m")] == "월별"


def test_matches_the_bulk_state_path(db, lab_code):
    """전용 API와 일괄 저장이 같은 결과를 낸다 — 같은 서비스 함수를 부르므로 성립해야 한다."""
    rows = [_row(lab_code, "sal", "A"), _row(lab_code, "edu", "B")]

    save_reason_rows(body=ReasonRowsIn(rows=rows), db=db)
    via_api = _stored(db, lab_code)

    state = get_overseas_state(basis_year=BASIS_YEAR, db=db)
    state.reason = rows
    save_overseas_state(state=state, db=db)

    assert _stored(db, lab_code) == via_api, "전용 API와 일괄 저장의 결과가 다르다"


def test_unknown_lab_is_rejected(db):
    with pytest.raises(HTTPException) as e:
        save_reason_rows(body=ReasonRowsIn(rows=[_row("NOPE", "sal")]), db=db)
    assert e.value.status_code == 400
    assert "NOPE" in str(e.value.detail)


def test_stale_token_is_rejected(db, lab_code):
    stale = get_overseas_state(basis_year=BASIS_YEAR, db=db).state_token
    save_reason_rows(body=ReasonRowsIn(rows=[_row(lab_code, "sal")]), db=db)  # 토큰이 낡는다

    with pytest.raises(HTTPException) as e:
        save_reason_rows(body=ReasonRowsIn(rows=[_row(lab_code, "edu")], state_token=stale), db=db)
    assert e.value.status_code == 409
