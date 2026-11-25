"""19개 계정 레지스트리 — 전체 현황판이 서버에서 읽는 값(편성 주체 · 상태 · 비고 · 월별 금액).

이 값들이 브라우저에만 있던 동안은 담당자마다 다른 총액과 다른 상태를 보고 회의에 들어왔다.
여기서 지키는 것:

- 편성 주체(owner)·상태·비고가 저장되고 다시 읽힌다(상태는 코드만 받고 한글명을 같이 내려준다)
- 현황판 숫자(월 12칸 + 전년 총액)가 **한 번에** 저장된다 — 12번 따로 보내다 중간에 실패하면
  총액이 반만 바뀐 현황판이 남는다
- 숫자가 들어가는 자리는 새 테이블이 아니라 공용 `cost_entries`(scope="ALL")다 — 계정 전용
  화면이 생겼을 때 같은 자리를 더 잘게 쓰면 되고, 해외연구소의 실제 값(scope=연구소 pk)과
  겹치지 않는다
"""

import pytest
from fastapi import HTTPException

from app.shared.endpoints import account as ep
from app.shared.schemas.account import AccountSummaryIn, AccountUpdate

YM = 202609
KEY = "market"


def test_owner_status_memo_round_trip(db):
    out = ep.update_account(KEY, AccountUpdate(owner="전략마케팅팀", status="submitted", memo="9월 분까지 반영"), db=db)
    assert (out.owner, out.status, out.status_label, out.memo) == ("전략마케팅팀", "submitted", "제출완료", "9월 분까지 반영")

    # 목록에도 같이 나와야 한다 — 현황판은 계정마다 따로 부르지 않는다.
    row = next(a for a in ep.list_accounts(db=db) if a.key == KEY)
    assert (row.owner, row.status_label) == ("전략마케팅팀", "제출완료")


def test_partial_update_keeps_other_fields(db):
    ep.update_account(KEY, AccountUpdate(owner="전략마케팅팀", memo="메모"), db=db)
    out = ep.update_account(KEY, AccountUpdate(status="reviewed"), db=db)
    # 상태만 고쳤는데 편성 주체·메모가 지워지면, 현황판에서 상태 하나 바꿀 때마다 담당이 사라진다.
    assert (out.owner, out.memo) == ("전략마케팅팀", "메모")


def test_unknown_status_is_rejected(db):
    with pytest.raises(HTTPException) as e:
        ep.update_account(KEY, AccountUpdate(status="작성중"), db=db)  # 라벨은 코드가 아니다
    assert e.value.status_code == 400


def test_summary_round_trip(db):
    months = [float(i + 1) * 1_000_000 for i in range(12)]
    out = ep.put_account_summary(
        KEY, AccountSummaryIn(yyyymm=YM, version="forecast", monthly=months, prior_amount=50_000_000), db=db
    )
    assert out.monthly == months
    assert out.total == sum(months)
    assert out.prior_amount == 50_000_000

    rows = {s.key: s for s in ep.list_account_summaries(yyyymm=YM, version="forecast", db=db)}
    assert len(rows) == 19, "현황판은 19개 계정을 한 번에 받는다"
    assert rows[KEY].total == sum(months)
    # 아직 아무도 저장하지 않은 계정은 0으로 내려간다(빈칸과 0을 화면에서 가를 필요가 없다).
    assert rows["labor"].total == 0 and rows["labor"].monthly == [0.0] * 12


def test_summary_replaces_previous_values(db):
    ep.put_account_summary(KEY, AccountSummaryIn(yyyymm=YM, monthly=[100.0] * 12, prior_amount=1), db=db)
    out = ep.put_account_summary(KEY, AccountSummaryIn(yyyymm=YM, monthly=[7.0] * 3, prior_amount=2), db=db)
    # 12개보다 적게 보내면 나머지는 0이다 — 예전 값이 뒤에 남아 총액이 섞이면 안 된다.
    assert out.monthly == [7.0, 7.0, 7.0] + [0.0] * 9
    assert out.total == 21 and out.prior_amount == 2


def test_summary_is_per_version(db):
    """버전마다 따로다 — 속보에 저장한 숫자가 실적 현황판에 나타나면 안 된다."""
    ep.put_account_summary(KEY, AccountSummaryIn(yyyymm=YM, version="forecast", monthly=[5.0] * 12), db=db)
    actual = {s.key: s for s in ep.list_account_summaries(yyyymm=YM, version="actual", db=db)}
    assert actual[KEY].total == 0


def test_bad_inputs(db):
    with pytest.raises(HTTPException) as e:
        # 13월 — 정수이긴 해서 pydantic은 통과하고, _check_yyyymm이 400으로 막아야 한다.
        # (yyyymm이 varchar였을 때는 "2026-09" 같은 모양 오류를 여기서 걸렀다. 이제 그런 값은
        #  pydantic이 먼저 422로 거절하므로, 이 테스트는 "숫자지만 없는 달"을 본다.)
        ep.put_account_summary(KEY, AccountSummaryIn(yyyymm=202613), db=db)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        ep.put_account_summary(KEY, AccountSummaryIn(yyyymm=YM, version="plan"), db=db)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        ep.put_account_summary("없는계정", AccountSummaryIn(yyyymm=YM), db=db)
    assert e.value.status_code == 404
