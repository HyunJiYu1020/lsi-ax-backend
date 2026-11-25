"""이미 만든 추가 행을 다른 항목 아래로 "옮기기" — 행이 금액을 데리고 옮겨간다.

금액이 제자리에 남는 게 아니다: 월별 금액은 그 행(cost_entry)에 매달려 있어서 행과 함께 옮겨가고,
집계도 새 anchor_key 아래에서 센다(services/aggregate.py의 extra_row_months). 기타경비 아래 있던
120을 전산비 아래로 옮기면 기타경비가 0, 전산비가 120이 된다.

왜 요청에 금액을 안 받나: 옮기는 데 금액이 필요 없는데 받으면 그 왕복에서 값이 상한다. 화면이 $ 단위로
보고 있으면 서버가 받은 금액을 1000으로 나누므로(chat.py _to_kdollar), 조회해서 받은 K$ 값을 그대로
되보내면 1000배 작아진다 — 오류 없이 금액만 줄어드는 형태라 아무도 못 알아챈다.
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.collect import delete_extra_row, list_extra_rows, upsert_extra_row
from app.accounts.overseas.schemas import ExtraRowIn

LAB = "SARC:CPU"
VERSION = "forecast"
YYYYMM = 209706
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


def test_amount_follows_the_row_to_the_new_anchor(db):
    """옮긴 금액은 **새 상위 항목의 합계에서** 센다 — 제자리에 남지 않는다.

    이걸 테스트로 고정하는 이유: "금액은 그대로 두고 자리만 바꾼다"는 설명이 "금액은 원래 자리에
    남는다"로 읽혀서 실제로 오해가 났다. 뜻은 "요청에 금액을 다시 싣지 않는다"였다. 말로 적어 둔
    설명은 또 오해되니, 금액이 어느 합계에 잡히는지를 숫자로 박아 둔다.
    """
    from app.accounts.overseas.services.aggregate import extra_row_months
    from app.accounts.overseas.services.extra_rows import read_extra_rows

    def totals():
        per = extra_row_months(read_extra_rows(db, yyyymm=YYYYMM, version=VERSION)).get(LAB, {})
        return sum(per.get("misc") or [0]), sum(per.get("it") or [0])

    upsert_extra_row(
        ExtraRowIn(lab_pk=LAB, version=VERSION, row_key="t_anchor_move", label="클라우드",
                   yyyymm=YYYYMM, anchor_key="misc", v=[10.0] * 12),
        db,
    )
    assert totals() == (120.0, 0.0)

    # v 없이 anchor만 바꾼다 = 옮기기
    upsert_extra_row(
        ExtraRowIn(lab_pk=LAB, version=VERSION, row_key="t_anchor_move", label="클라우드",
                   yyyymm=YYYYMM, anchor_key="it", v=None),
        db,
    )
    assert totals() == (0.0, 120.0), "금액이 새 상위 항목으로 따라오지 않았다"


def test_mcp_move_omits_amount_instead_of_sending_empty(db):
    """MCP 도구가 v를 생략할 때 빈 배열([])을 보내면 안 된다 — 서버가 "빈 배열을 보냈다"로 읽어서
    400으로 막고, 문서화된 '행 옮기기' 흐름 자체가 죽는다(실제로 그 버그가 있었다)."""
    import inspect

    from app import mcp_server

    src = inspect.getsource(mcp_server.upsert_extra_row)
    assert '"v": v,' in src, "v를 그대로 넘겨야 _put이 None인 키를 빼 준다"
    assert "else []" not in src
