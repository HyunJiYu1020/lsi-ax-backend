"""외주용역 전용 저장 API — PLAN.md Phase 5(PUT /state 점진 분해)의 첫 조각.

저장이 `PUT /overseas/state` 하나에 뭉쳐 있어서 외주용역 한 시트를 올려도 화면 상태 전부가
전송됐다. 외주용역은 이미 "연구소×버전 단위 전체 교체" 의미라 떼어내기 가장 쉬운 자리다.

가장 중요한 성질은 **두 경로가 같은 결과를 낸다**는 것이다 — 전용 API와 일괄 저장이 같은 서비스
함수를 부르므로, 롤백을 위해 둘을 병행해 둬도 데이터가 갈리지 않는다(PLAN Phase 5의 롤백 조건).
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.accounts.overseas.endpoints.collect import (
    get_overseas_state,
    save_overseas_state,
    save_vendor_rows,
)
from app.accounts.overseas.models import OverseasVendorRow, OverseasVendorRowDetail
from app.accounts.overseas.schemas import VendorRow, VendorRowsIn
from app.accounts.overseas.services.labs import all_labs

BASIS_YEAR = 2026
VER = "cur"


@pytest.fixture
def lab_code(db) -> str:
    """실재하는 연구소 코드 하나. 없는 코드를 쓰면 400으로 막히므로 DB에서 고른다."""
    codes = sorted({lab.lab_code for lab in all_labs(db)} - {"_ETC"})
    if not codes:
        pytest.skip("연구소가 없다")
    return codes[0]


def _row(lab: str, no: int, amount: float, company: str = "테스트상사") -> VendorRow:
    return VendorRow(
        lab_code=lab, version=VER, row_no=no, category="개발", project=f"P{no}",
        company=company, description="설명", m=[amount] * 12, source_file="t.xlsx",
    )


def _stored(db, lab: str) -> list[tuple[int, str, float]]:
    """(row_no, company, 1월 금액) — 저장 결과를 읽어 비교하기 쉬운 모양으로."""
    rows = list(
        db.scalars(
            select(OverseasVendorRow)
            .where(OverseasVendorRow.lab_code == lab, OverseasVendorRow.version == VER)
            .order_by(OverseasVendorRow.row_no)
        )
    )
    out = []
    for r in rows:
        jan = db.scalar(
            select(OverseasVendorRowDetail.amount).where(
                OverseasVendorRowDetail.vendor_row_id == r.id,
                OverseasVendorRowDetail.year == BASIS_YEAR,
                OverseasVendorRowDetail.month == 1,
            )
        )
        out.append((r.row_no, r.company, float(jan) if jan is not None else None))
    return out


def test_saves_rows_with_month_details(db, lab_code):
    """행과 월 금액이 같이 저장된다(마스터 + 디테일 12개월)."""
    res = save_vendor_rows(
        body=VendorRowsIn(basis_year=BASIS_YEAR, rows=[_row(lab_code, 1, 10.0), _row(lab_code, 2, 20.0)]),
        db=db,
    )

    assert res["status"] == "ok" and res["replaced"] == 1
    assert _stored(db, lab_code) == [(1, "테스트상사", 10.0), (2, "테스트상사", 20.0)]


def test_replaces_the_whole_unit_not_upsert(db, lab_code):
    """연구소×버전을 통째로 바꾼다 — 업로드마다 행 수가 달라서 upsert만 하면 지워진 행이 남고,
    연구소가 뺀 항목이 화면에 계속 보인다."""
    save_vendor_rows(body=VendorRowsIn(basis_year=BASIS_YEAR, rows=[_row(lab_code, i, 1.0) for i in (1, 2, 3)]), db=db)
    assert len(_stored(db, lab_code)) == 3

    # 두 줄짜리로 다시 올리면 세 번째 줄은 사라져야 한다.
    save_vendor_rows(body=VendorRowsIn(basis_year=BASIS_YEAR, rows=[_row(lab_code, 1, 9.0), _row(lab_code, 2, 9.0)]), db=db)
    assert _stored(db, lab_code) == [(1, "테스트상사", 9.0), (2, "테스트상사", 9.0)]


def test_other_labs_are_untouched(db):
    """보내지 않은 연구소는 건드리지 않는다 — 한 연구소 시트를 올렸는데 다른 연구소 자료가
    사라지면 안 된다."""
    codes = sorted({lab.lab_code for lab in all_labs(db)} - {"_ETC"})
    if len(codes) < 2:
        pytest.skip("연구소가 2곳 미만")
    a, b = codes[0], codes[1]
    save_vendor_rows(body=VendorRowsIn(basis_year=BASIS_YEAR, rows=[_row(a, 1, 1.0), _row(b, 1, 2.0)]), db=db)

    save_vendor_rows(body=VendorRowsIn(basis_year=BASIS_YEAR, rows=[_row(a, 1, 99.0)]), db=db)

    assert _stored(db, a) == [(1, "테스트상사", 99.0)]
    assert _stored(db, b) == [(1, "테스트상사", 2.0)], "다른 연구소 자료가 사라졌다"


def test_matches_the_bulk_state_path(db, lab_code):
    """전용 API와 일괄 저장(PUT /state)이 같은 결과를 낸다.

    같은 서비스 함수를 부르므로 성립해야 하는 성질이다 — 이게 깨지면 롤백을 위해 두 경로를
    병행해 둘 수 없다(PLAN Phase 5의 롤백 조건)."""
    rows = [_row(lab_code, 1, 7.5, "A사"), _row(lab_code, 2, 8.5, "B사")]

    save_vendor_rows(body=VendorRowsIn(basis_year=BASIS_YEAR, rows=rows), db=db)
    via_api = _stored(db, lab_code)

    # 같은 내용을 일괄 경로로 저장
    state = get_overseas_state(basis_year=BASIS_YEAR, db=db)
    state.vendor = rows
    save_overseas_state(state=state, db=db)
    via_bulk = _stored(db, lab_code)

    assert via_api == via_bulk, "전용 API와 일괄 저장의 결과가 다르다"


def test_unknown_lab_is_rejected(db):
    """모르는 연구소 코드는 400 — 오타 하나로 유령 연구소 자료가 생기면 집계가 조용히 틀어진다."""
    with pytest.raises(HTTPException) as e:
        save_vendor_rows(body=VendorRowsIn(basis_year=BASIS_YEAR, rows=[_row("NOPE", 1, 1.0)]), db=db)
    assert e.value.status_code == 400
    assert "NOPE" in str(e.value.detail)


def test_stale_token_is_rejected(db, lab_code):
    """동시 편집 보호는 /overseas/state와 같은 지문을 쓴다 — 한 화면의 자료라 판정도 같아야 한다."""
    stale = get_overseas_state(basis_year=BASIS_YEAR, db=db).state_token
    save_vendor_rows(body=VendorRowsIn(basis_year=BASIS_YEAR, rows=[_row(lab_code, 1, 1.0)]), db=db)  # 토큰이 낡는다

    with pytest.raises(HTTPException) as e:
        save_vendor_rows(
            body=VendorRowsIn(basis_year=BASIS_YEAR, rows=[_row(lab_code, 1, 2.0)], state_token=stale), db=db
        )
    assert e.value.status_code == 409


def test_fresh_token_is_accepted_and_returned(db, lab_code):
    """올바른 토큰은 통과하고, 새 토큰을 돌려준다 — 안 주면 다음 저장이 낡은 토큰으로 가서 409가 된다."""
    token = get_overseas_state(basis_year=BASIS_YEAR, db=db).state_token

    res = save_vendor_rows(
        body=VendorRowsIn(basis_year=BASIS_YEAR, rows=[_row(lab_code, 1, 3.0)], state_token=token), db=db
    )

    assert res["state_token"] and res["state_token"] != token
    # 그 새 토큰으로 이어서 저장할 수 있다.
    assert save_vendor_rows(
        body=VendorRowsIn(basis_year=BASIS_YEAR, rows=[_row(lab_code, 1, 4.0)], state_token=res["state_token"]), db=db
    )["status"] == "ok"


def test_empty_rows_changes_nothing(db, lab_code):
    """빈 목록은 아무것도 지우지 않는다 — 무엇을 비울지 알 수 없으므로(그건 삭제 API의 일이다)."""
    save_vendor_rows(body=VendorRowsIn(basis_year=BASIS_YEAR, rows=[_row(lab_code, 1, 5.0)]), db=db)
    before = _stored(db, lab_code)

    res = save_vendor_rows(body=VendorRowsIn(basis_year=BASIS_YEAR, rows=[]), db=db)

    assert res["replaced"] == 0
    assert _stored(db, lab_code) == before
