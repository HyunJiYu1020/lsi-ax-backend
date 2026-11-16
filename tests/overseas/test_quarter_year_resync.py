"""해외연구소 저장 경로가 분기·연간(QUARTER/YEAR) 행을 다시 계산하는지 — PLAN.md P1-3.

왜 중요한가: 공용 경로(cost.py)는 월 값을 쓸 때마다 분기·연간을 12개월 합으로 재계산하는데,
해외연구소 경로는 월 행만 쓰고 재계산을 하지 않았다. 그래서 overseas의 분기·연간 행은 한 번
백필된 값에서 멈춰 있었다. "같은 데이터, 두 가지 쓰기 규칙"은 반드시 사고로 이어진다 —
집계 화면이 CostEntryDetail을 직접 읽으므로, 정체된 분기 행을 읽는 경로가 하나라도 생기면
화면 합계와 다른 숫자가 나온다.

Phase 1-4에서 쓰기 경로를 services/overseas/entries.py로 모으면서 재계산을 그 안에 넣었다.
호출부가 따로 불러 줘야 하는 구조면 언젠가 빠뜨리므로, 월을 쓰는 함수가 직접 한다.
"""

import pytest
from sqlalchemy import select

from app.shared.models.common import CostEntry, CostEntryDetail
from app.accounts.overseas.services.entries import bulk_upsert_cost_entries, resync_summaries
from app.accounts.overseas.services.labs import ACCOUNT_KEY

# 실제 데이터와 겹치지 않는 미래 연월
YYYYMM = "209912"
YEAR = 2099
PK = "SARC:CPU"
CAT = "sal"


def _summary(db, period_type: str) -> dict[int, float]:
    """분기·연간 행을 읽되, 행이 아예 없으면 그 사실을 말해 준다 — 재계산이 빠지면 KeyError로
    터져서 "왜 실패했는지"가 안 보인다(이게 P1-3 이전의 상태다)."""
    got = _details(db, period_type)
    assert got, f"{period_type} 행이 하나도 없다 — 저장 경로가 분기·연간을 재계산하지 않았다(PLAN P1-3)"
    return got


def _details(db, period_type: str) -> dict[int, float]:
    entry = db.scalar(
        select(CostEntry).where(
            CostEntry.account_code == ACCOUNT_KEY,
            CostEntry.scope_code == PK,
            CostEntry.category_code == CAT,
            CostEntry.yyyymm == YYYYMM,
            CostEntry.version == "forecast",
        )
    )
    assert entry is not None, "헤더가 만들어지지 않았다"
    return {
        r.period: float(r.amount)
        for r in db.scalars(
            select(CostEntryDetail).where(
                CostEntryDetail.cost_entry_id == entry.id,
                CostEntryDetail.year == YEAR,
                CostEntryDetail.period_type == period_type,
            )
        )
    }


def test_quarter_and_year_are_recalculated_on_save(db):
    """월 값을 저장하면 분기 4개 + 연간 1개가 같이 채워진다."""
    months = [float(m) for m in range(1, 13)]  # 1~12
    bulk_upsert_cost_entries(db, [(PK, "forecast", CAT, YYYYMM, months)])
    db.flush()

    assert _details(db, "MONTH") == {m: float(m) for m in range(1, 13)}
    # 1Q=1+2+3=6, 2Q=4+5+6=15, 3Q=7+8+9=24, 4Q=10+11+12=33
    assert _summary(db, "QUARTER") == {1: 6.0, 2: 15.0, 3: 24.0, 4: 33.0}
    assert _summary(db, "YEAR") == {0: 78.0}  # 1..12 합


def test_quarter_follows_when_one_month_changes(db):
    """한 달만 고쳐 저장하면 그 달이 속한 분기와 연간도 같이 따라온다.

    재계산이 없으면 월 값만 바뀌고 분기 행은 옛 값으로 남는다 — 그게 P1-3이 말하는 정체다."""
    bulk_upsert_cost_entries(db, [(PK, "forecast", CAT, YYYYMM, [1.0] * 12)])
    db.flush()
    assert _summary(db, "QUARTER")[1] == 3.0
    assert _summary(db, "YEAR")[0] == 12.0

    # 1월만 100으로 — 나머지 달은 None(= 안 보냄)
    bulk_upsert_cost_entries(db, [(PK, "forecast", CAT, YYYYMM, [100.0] + [None] * 11)])
    db.flush()

    assert _details(db, "MONTH")[1] == 100.0
    assert _details(db, "MONTH")[2] == 1.0, "안 보낸 달이 지워졌다(부분 갱신이 깨졌다)"
    # 1Q = 100 + 1 + 1 = 102 — 저장된 값을 다시 읽어 더해야 나오는 숫자다.
    # 들어온 배열(100, None×11)만 보고 더하면 100이 되어 안 보낸 두 달이 사라진다.
    assert _summary(db, "QUARTER")[1] == 102.0
    assert _summary(db, "QUARTER")[2] == 3.0, "건드리지 않은 분기가 바뀌었다"
    assert _summary(db, "YEAR")[0] == 111.0  # 102 + 3 + 3 + 3


def test_zero_is_stored_and_summed_as_zero(db):
    """0으로 올라온 달은 0으로 더한다 — 미등록(None)과 구분된다(공통 RULE: 빈칸 처리)."""
    bulk_upsert_cost_entries(db, [(PK, "forecast", CAT, YYYYMM, [0.0, 5.0, 0.0] + [None] * 9)])
    db.flush()

    assert _details(db, "MONTH") == {1: 0.0, 2: 5.0, 3: 0.0}
    assert _summary(db, "QUARTER")[1] == 5.0
    # 값이 하나도 없는 분기는 0으로 채워진다 — 화면이 분기 행을 읽을 때 "행이 없다"와
    # "0원이다"를 구분하지 않아도 되게(집계는 있는 값만 더하므로 결과가 같다).
    assert _summary(db, "QUARTER")[2] == 0.0
    assert _summary(db, "YEAR")[0] == 5.0


def test_resync_is_idempotent(db):
    """같은 입력으로 두 번 저장해도 분기·연간이 늘어나거나 달라지지 않는다."""
    bulk_upsert_cost_entries(db, [(PK, "forecast", CAT, YYYYMM, [2.0] * 12)])
    db.flush()
    once_q, once_y = _summary(db, "QUARTER"), _summary(db, "YEAR")

    bulk_upsert_cost_entries(db, [(PK, "forecast", CAT, YYYYMM, [2.0] * 12)])
    db.flush()

    assert _summary(db, "QUARTER") == once_q == {1: 6.0, 2: 6.0, 3: 6.0, 4: 6.0}
    assert _summary(db, "YEAR") == once_y == {0: 24.0}


def test_resync_with_no_targets_is_noop(db):
    """빈 묶음을 주면 아무 일도 하지 않는다(쿼리도 쏘지 않는다)."""
    resync_summaries(db, set())  # 터지지 않으면 통과


def test_state_save_path_also_resyncs(db):
    """PUT /overseas/state로 저장해도 분기·연간이 따라온다 — P1-3이 지적한 바로 그 경로다."""
    from app.accounts.overseas.endpoints.collect import get_overseas_state, save_overseas_state

    state = get_overseas_state(basis_year=2026, db=db)
    if not state.basis_month or not state.V["cur"].d:
        pytest.skip("당월 속보 데이터가 없어 저장 경로를 확인할 수 없다")

    pk = next(iter(state.V["cur"].d))
    cat = next(iter(state.V["cur"].d[pk]))
    state.V["cur"].d[pk][cat] = [3.0] * 12
    save_overseas_state(state=state, db=db)

    entry = db.scalar(
        select(CostEntry).where(
            CostEntry.account_code == ACCOUNT_KEY, CostEntry.scope_code == pk,
            CostEntry.category_code == cat, CostEntry.version == "forecast",
            CostEntry.yyyymm == f"{2026:04d}{state.basis_month:02d}",
        )
    )
    got = {
        (r.period_type, r.period): float(r.amount)
        for r in db.scalars(
            select(CostEntryDetail).where(
                CostEntryDetail.cost_entry_id == entry.id, CostEntryDetail.year == 2026
            )
        )
    }
    assert got[("QUARTER", 1)] == 9.0, "상태 저장 경로가 분기를 재계산하지 않았다"
    assert got[("YEAR", 0)] == 36.0
