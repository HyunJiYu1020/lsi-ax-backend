"""구조 미정 시트(위탁용역비·생소비 편성값) — 표 단위로 서버에 남는다.

계약 한 줄이 40여 칸이고 그 열 구성이 아직 달마다 바뀐다. 그래서 전용 테이블 대신 JSON 행
(`sheet_rows`)으로 받는다. 여기서 지키는 것:

- 표 단위 교체다 — 행 단위로 쪼개면 저장 한 번이 수백 요청이 되고, 중간에 실패하면 표가 반만
  바뀐 상태로 남는다
- 행 순서가 보존된다(편성 표는 순서가 뜻을 가진다 — 계약 묶음이 순서로 묶여 있다)
- 행 키가 중복이면 400으로 막는다(조용히 버리면 그 계약 금액이 사라진다)
- 버전·시트가 섞이지 않는다(업데이트 버전과 전월 편성값이 섞이면 전월 비교가 무의미해진다)
"""

import pytest
from fastapi import HTTPException

from app.shared.endpoints import sheet as ep
from app.shared.schemas.sheet import SheetRowIn, SheetRowsIn

ACC = "outsource"


def _row(name, **kw):
    return SheetRowIn(data=dict(name=name, dept="C10I3218", acct="54370700", m9=150_000_000, **kw))


def _put(db, version="cur", sheet_key="budget", rows=None):
    return ep.put_sheet_rows(
        SheetRowsIn(account_key=ACC, version=version, sheet_key=sheet_key, rows=rows or []), db=db
    )


def test_replace_and_read_back_in_order(db):
    out = _put(db, rows=[_row("계약A"), _row("계약B"), _row("계약C")])
    assert [r.data["name"] for r in out.rows] == ["계약A", "계약B", "계약C"]
    # 행 키를 안 보내면 서버가 순서 번호로 채운다(업로드 엑셀에는 행 키가 없다).
    assert [r.row_key for r in out.rows] == ["r0001", "r0002", "r0003"]

    again = ep.get_sheet_rows(account_key=ACC, version="cur", sheet_key="budget", db=db)
    assert [r.data["name"] for r in again.rows] == ["계약A", "계약B", "계약C"]


def test_replace_drops_old_rows(db):
    _put(db, rows=[_row("계약A"), _row("계약B")])
    out = _put(db, rows=[_row("계약C")])
    # 예전 행이 남으면 "업로드하면 이 표 전체가 파일 내용으로 바뀝니다"가 거짓이 된다.
    assert [r.data["name"] for r in out.rows] == ["계약C"]


def test_duplicate_row_key_rejected(db):
    with pytest.raises(HTTPException) as e:
        _put(db, rows=[SheetRowIn(row_key="같은키", data={"name": "A"}), SheetRowIn(row_key="같은키", data={"name": "B"})])
    assert e.value.status_code == 400
    assert "중복" in e.value.detail


def test_versions_and_sheets_are_separate(db):
    _put(db, version="cur", rows=[_row("당월 계약")])
    _put(db, version="prev", rows=[_row("전월 계약"), _row("전월 계약2")])
    _put(db, version="cur", sheet_key="appr", rows=[SheetRowIn(data={"apprNo": "KX-1"})])

    assert [r.data["name"] for r in ep.get_sheet_rows(account_key=ACC, version="cur", sheet_key="budget", db=db).rows] == ["당월 계약"]
    assert len(ep.get_sheet_rows(account_key=ACC, version="prev", sheet_key="budget", db=db).rows) == 2
    assert ep.get_sheet_rows(account_key=ACC, version="cur", sheet_key="appr", db=db).rows[0].data["apprNo"] == "KX-1"

    index = {(x["version"], x["sheet_key"]): x["rows"] for x in ep.list_sheet_index(account_key=ACC, db=db)}
    assert index[("cur", "budget")] == 1 and index[("prev", "budget")] == 2 and index[("cur", "appr")] == 1


def test_empty_table_reads_as_empty_not_error(db):
    """서버에 아직 없는 표는 빈 목록이다 — 화면이 그때 자기 값을 지우지 않도록 404가 아니어야 한다."""
    out = ep.get_sheet_rows(account_key=ACC, version="plan", sheet_key="budget", db=db)
    assert out.rows == []


def test_bad_version_and_account(db):
    with pytest.raises(HTTPException) as e:
        _put(db, version="forecast")  # cost_entries 쪽 축을 섞어 보낸 경우
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        ep.put_sheet_rows(SheetRowsIn(account_key="없는계정", version="cur", sheet_key="budget"), db=db)
    assert e.value.status_code == 404


def test_delete_clears_one_table_only(db):
    _put(db, version="cur", rows=[_row("당월")])
    _put(db, version="prev", rows=[_row("전월")])
    res = ep.delete_sheet_rows(account_key=ACC, version="cur", sheet_key="budget", db=db)
    assert res["deleted"] == 1
    assert ep.get_sheet_rows(account_key=ACC, version="cur", sheet_key="budget", db=db).rows == []
    assert len(ep.get_sheet_rows(account_key=ACC, version="prev", sheet_key="budget", db=db).rows) == 1
