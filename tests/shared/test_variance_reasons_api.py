"""증감사유 공용 경로 — 감가상각비·위탁용역비·생소비도 사유를 서버에 남긴다.

여태 `variance_reasons`를 쓰는 경로가 해외연구소 전용이라, 다른 계정 화면의 사유는 브라우저에만
있었다. 보고서에 그대로 실리는 문장이라 그 자리는 안 된다. 여기서 지키는 것:

- 자리(계정 · 버전 · 비교 기준 · 기간 · 범위)가 섞이지 않는다 — 전실행 대비 사유가 전년 대비 칸에
  나타나면 보고서가 틀린 근거를 달고 나간다
- 담당자가 **지운 문장은 지워진다**(서버가 들고 있으면 다음 조회에 되살아난다)
- 안 보낸 칸은 건드리지 않는다(화면은 한 번에 보는 칸만 보낸다)
- 편성값을 다시 올리면 사유를 지우지 않고 "재확인 필요"만 켠다
"""

import pytest
from fastapi import HTTPException

from app.shared.endpoints import reason as ep
from app.shared.schemas.reason import ReasonRow, ReasonsIn

Y, M = 2026, 9
ACC = "dep"


def _put(db, rows, base="prev", account_key=ACC, **kw):
    return ep.put_reasons(
        ReasonsIn(
            account_key=account_key, basis_year=Y, basis_month=M, base_version=base,
            rows=rows, **kw,
        ),
        db=db,
    )


def _get(db, base="prev", account_key=ACC, **kw):
    return ep.list_reasons(
        account_key=account_key, basis_year=Y, basis_month=M, base_version=base, db=db, **kw
    )


def test_save_and_read_back(db):
    out = _put(db, [ReasonRow(category_key="up", reason_text="장비 입고 증가", diff=1_200_000_000)])
    assert [(r.category_key, r.reason_text) for r in out.rows] == [("up", "장비 입고 증가")]
    assert out.rows[0].saved_diff == 1_200_000_000
    assert out.rows[0].saved_at and out.rows[0].needs_recheck is False
    assert [r.reason_text for r in _get(db).rows] == ["장비 입고 증가"]


def test_bases_and_accounts_do_not_mix(db):
    """전실행 대비 사유가 전년 대비 칸에 나타나면 보고서가 틀린 근거를 달고 나간다."""
    _put(db, [ReasonRow(category_key="up", reason_text="전실행 사유")], base="prev")
    _put(db, [ReasonRow(category_key="up", reason_text="전년 사유")], base="prior")
    _put(db, [ReasonRow(category_key="C-001", reason_text="계약 연장")], account_key="outsource")

    assert [r.reason_text for r in _get(db, base="prev").rows] == ["전실행 사유"]
    assert [r.reason_text for r in _get(db, base="prior").rows] == ["전년 사유"]
    assert [r.reason_text for r in _get(db, account_key="outsource").rows] == ["계약 연장"]
    # 계정이 다르면 서로 안 보인다
    assert [r.category_key for r in _get(db).rows] == ["up"]


def test_empty_text_deletes_but_unsent_rows_survive(db):
    _put(db, [ReasonRow(category_key="up", reason_text="증가 사유"), ReasonRow(category_key="dn", reason_text="감소 사유")])
    # up만 비워 보낸다 → up은 지워지고 dn은 남는다
    out = _put(db, [ReasonRow(category_key="up", reason_text="")])
    assert [r.category_key for r in out.rows] == ["dn"]
    assert [r.reason_text for r in _get(db).rows] == ["감소 사유"]


def test_recheck_keeps_text(db):
    """편성값을 다시 올리면 금액이 바뀐다 — 사유는 유지하고 표시만 켠다(사람이 다시 확인한다)."""
    _put(db, [ReasonRow(category_key="up", reason_text="장비 입고 증가", diff=1_000)])
    res = ep.mark_recheck(account_key=ACC, basis_year=Y, basis_month=M, db=db)
    assert res["marked"] >= 1
    row = _get(db).rows[0]
    assert row.reason_text == "장비 입고 증가" and row.needs_recheck is True
    # 다시 저장하면 재확인 표시가 꺼지고 새 증감액이 박힌다
    row2 = _put(db, [ReasonRow(category_key="up", reason_text="장비 입고 증가(확인)", diff=1_900)]).rows[0]
    assert row2.needs_recheck is False and row2.saved_diff == 1_900


def test_delete_one_cell_and_whole_place(db):
    _put(db, [ReasonRow(category_key="up", reason_text="a"), ReasonRow(category_key="dn", reason_text="b")])
    ep.delete_reasons(account_key=ACC, basis_year=Y, basis_month=M, category_key="up", db=db)
    assert [r.category_key for r in _get(db).rows] == ["dn"]
    ep.delete_reasons(account_key=ACC, basis_year=Y, basis_month=M, db=db)
    assert _get(db).rows == []


def test_bad_inputs(db):
    with pytest.raises(HTTPException) as e:
        _put(db, [ReasonRow(category_key="up", reason_text="x")], account_key="없는계정")
    assert e.value.status_code == 404
    for kw in ({"kind": "plan"}, {"stage": "third"}, {"base": "전년"}, {"period": "Y"}):
        with pytest.raises(HTTPException) as e:
            _put(db, [ReasonRow(category_key="up", reason_text="x")], **kw)
        assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        _put(db, [ReasonRow(category_key="", reason_text="x")])
    assert e.value.status_code == 400


def test_overseas_path_still_sees_its_own_rows(db):
    """공용 경로로 해외연구소 사유를 쓰면 기존 해외연구소 화면도 같은 칸을 읽어야 한다 —
    같은 테이블이라 자리(키)가 어긋나면 두 화면이 서로 다른 사유를 보여준다."""
    from app.accounts.overseas.endpoints.variance import list_variance_reasons

    _put(
        db,
        [ReasonRow(category_key="labor", reason_text="인건비 증가")],
        account_key="overseas",
        scope="ALL",
    )
    rows = list_variance_reasons(basis_year=Y, base_version="prev", basis_month=M, db=db)
    assert ("labor", "인건비 증가") in [(r.category_key, r.reason_text) for r in rows]
