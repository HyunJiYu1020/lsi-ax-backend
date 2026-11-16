"""버전 × 계정의 "계정 확정" 상태·일시.

화면이 localStorage에 들고 있던 값을 서버로 옮긴 것이라, 테스트도 그 이유를 지킨다:
확정하면 일시가 찍히고, 확정을 취소하면 일시와 단서(note)가 같이 지워진다(공통 RULE:
완료 후 수정 시 작성중 + 일자 삭제).
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.shared.endpoints.version import list_version_accounts, update_version_account
from app.shared.models.version import Version
from app.shared.schemas.version import VersionAccountStatusUpdate as Upd

KIND = "forecast"


@pytest.fixture
def ver(db):
    """테스트용으로 안 쓰는 연월 버전 하나를 연다(바깥 트랜잭션이 롤백되니 DB에는 안 남는다)."""
    v = db.scalar(select(Version).where(Version.year == 2099, Version.month == 1, Version.kind == KIND))
    if not v:
        v = Version(year=2099, month=1, kind=KIND, status="drafting")
        db.add(v)
        db.flush()
    return v


def _args(ver):
    return dict(year=ver.year, month=ver.month, kind=ver.kind)


def test_confirm_stamps_time_and_note(db, ver):
    """확정하면 확정 일시가 찍히고, "무엇을 알고도 확정했는지"가 note로 남는다 —
    미업로드가 있는데 확정한 총액을 나중에 전체 합계로 오해하지 않게 하는 단서다."""
    out = update_version_account(**_args(ver), account_code="overseas", body=Upd(status="done", note="미업로드 2개 포함 확정"), db=db)

    assert out.status == "done" and out.confirmed_at is not None
    assert out.note == "미업로드 2개 포함 확정"
    listed = list_version_accounts(**_args(ver), db=db)
    assert [(r.account_code, r.status) for r in listed] == [("overseas", "done")]


def test_unconfirm_clears_time_and_note(db, ver):
    """작성중으로 되돌리면 확정 일시와 note를 같이 지운다 — 되돌렸는데 "미업로드 2개 포함 확정"이
    남아 있으면 그게 지금 상태인 줄 읽힌다."""
    update_version_account(**_args(ver), account_code="overseas", body=Upd(status="done", note="미업로드 2개 포함 확정"), db=db)
    out = update_version_account(**_args(ver), account_code="overseas", body=Upd(status="drafting"), db=db)

    assert out.status == "drafting" and out.confirmed_at is None and out.note == ""


def test_reconfirm_updates_the_time(db, ver):
    """다시 확정하면 일시도 다시 찍는다(예전 확정 시각이 남아 있으면 안 된다)."""
    first = update_version_account(**_args(ver), account_code="overseas", body=Upd(status="done"), db=db)
    update_version_account(**_args(ver), account_code="overseas", body=Upd(status="drafting"), db=db)
    second = update_version_account(**_args(ver), account_code="overseas", body=Upd(status="done"), db=db)

    assert second.confirmed_at is not None and second.confirmed_at >= first.confirmed_at


def test_only_confirmed_accounts_are_listed(db, ver):
    """행이 없는 계정은 "작성 중"이라서 목록에 안 나온다 — 버전마다 19개 행을 미리 만들지 않는다."""
    assert list_version_accounts(**_args(ver), db=db) == []


def test_unknown_version_or_account_is_rejected(db, ver):
    """열리지 않은 버전이나 없는 계정에는 확정을 남길 수 없다."""
    with pytest.raises(HTTPException) as e:
        update_version_account(year=1999, month=1, kind=KIND, account_code="overseas", body=Upd(status="done"), db=db)
    assert e.value.status_code == 404

    with pytest.raises(HTTPException) as e:
        update_version_account(**_args(ver), account_code="nope", body=Upd(status="done"), db=db)
    assert e.value.status_code == 404

    with pytest.raises(HTTPException) as e:
        update_version_account(**_args(ver), account_code="overseas", body=Upd(status="made_up"), db=db)
    assert e.value.status_code == 400
