"""이미 만든 추가 행을 다른 항목 아래로 "옮기기" — 금액은 그대로 두고 자리만 바꾼다.

왜 금액을 안 받나: 옮기는 데 금액이 필요 없는데 받으면 그 왕복에서 값이 상한다. 화면이 $ 단위로
보고 있으면 서버가 받은 금액을 1000으로 나누므로(chat.py _to_kdollar), 조회해서 받은 K$ 값을 그대로
되보내면 1000배 작아진다 — 오류 없이 금액만 줄어드는 형태라 아무도 못 알아챈다.
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.collect import delete_extra_row, list_extra_rows, upsert_extra_row
from app.accounts.overseas.schemas import ExtraRowIn

LAB = "SARC:CPU"
VERSION = "forecast"
YYYYMM = "209706"
ROW = "test_move_row"
MONTHS = [float(m) for m in range(1, 13)]


@pytest.fixture
def row(db):
    upsert_extra_row(
        body=ExtraRowIn(
            lab_pk=LAB, version=VERSION, row_key=ROW, label="클라우드 사용료",
            yyyymm=YYYYMM, anchor_key="misc", v=MONTHS,
        ),
        db=db,
    )
    yield
    delete_extra_row(lab_pk=LAB, version=VERSION, row_key=ROW, yyyymm=YYYYMM, step="취합", db=db)


def _stored(db) -> dict:
    rows = list_extra_rows(lab_pk=LAB, version=VERSION, yyyymm=YYYYMM, db=db)
    return next(r for r in rows if r.row_key == ROW).model_dump()


def test_moving_keeps_the_amounts(db, row):
    """v 없이 부르면 자리만 바뀌고 금액은 그대로다."""
    out = upsert_extra_row(
        body=ExtraRowIn(
            lab_pk=LAB, version=VERSION, row_key=ROW, label="클라우드 사용료",
            yyyymm=YYYYMM, anchor_key="itx",
        ),
        db=db,
    )

    assert out.v == MONTHS, "응답에 금액이 안 실려 오면 부르는 쪽이 '지워졌다'로 읽는다"
    saved = _stored(db)
    assert saved["anchor_key"] == "itx"
    assert saved["v"] == MONTHS


def test_a_brand_new_row_still_needs_its_amounts(db):
    """없는 행을 v 없이 만들려는 건 '옮기기'가 아니다 — 빈 행이 조용히 생기면 안 된다."""
    with pytest.raises(HTTPException) as e:
        upsert_extra_row(
            body=ExtraRowIn(
                lab_pk=LAB, version=VERSION, row_key="test_move_missing",
                label="없는 행", yyyymm=YYYYMM, anchor_key="misc",
            ),
            db=db,
        )
    assert e.value.status_code == 400


def test_sending_amounts_still_overwrites(db, row):
    """금액을 같이 보내면 지금까지대로 덮어쓴다 — 옮기기는 'v를 뺀 경우'만의 뜻이다."""
    zeros = [0.0] * 12
    upsert_extra_row(
        body=ExtraRowIn(
            lab_pk=LAB, version=VERSION, row_key=ROW, label="클라우드 사용료",
            yyyymm=YYYYMM, anchor_key="itx", v=zeros,
        ),
        db=db,
    )

    assert _stored(db)["v"] == zeros
