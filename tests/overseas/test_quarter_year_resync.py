"""해외연구소 저장 경로가 분기·연간(QUARTER/YEAR) 행을 **만들지 않는지**.

월 값을 쓸 때마다 분기·연간을 12개월 합으로 재계산해서 저장하지 않는 이유:

- 월에서 계산할 수 있는 값을 DB에 복사해 두면 **그 복사본만 틀어진다.** 실제로 인력(명)을 합으로
  재계산해서 분기가 3배·연간이 12배가 된 행이 전체의 96%였고, 취합 그리드가 그 값을 그대로
  그리고 있었다(core.js withPer).
- 분기·연간 칸이 저장돼야 할 이유는 하나뿐이다 — **연구소가 파일에 월 합과 다르게 적어 보낸
  공식 분기값을 보존**하는 것. 그건 periods 인자로 들어오고 그대로 저장된다.
- 보는 쪽은 저장된 칸이 있으면 그 값을, 없으면 월에서 계산한 값을 그린다(core.js withPer →
  run17 · services/aggregate.to_17 · 증감분석은 월을 더해서 계산). 그래서 행이 없어도 화면이
  비지 않는다.
- 안 적어 보낸 칸과 월 합이 안 맞는 칸은 업로드 미리보기가 색으로 알린다(app/shared/core/anomaly.py
  의 blank · missing_cell · period_mismatch). 값을 서버가 고쳐 주지 않고 사람이 다시 올린다.

"정체된 분기 행"은 생기지 않는다 — 파생값을 아예 저장하지 않으니 정체될 복사본이 없다.
"""

import pytest

from app.accounts.overseas.endpoints.collect import save_overseas_state as _save_overseas_state
from fastapi import BackgroundTasks
from sqlalchemy import select

from app.shared.models.common import CostEntry, CostEntryDetail
from app.accounts.overseas.services.entries import bulk_upsert_cost_entries
from app.accounts.overseas.services.labs import ACCOUNT_KEY


# 이 엔드포인트들은 BackgroundTasks를 받는다 — 증감사유 번역을 응답 뒤로 미루기 때문이다
# (services/reason_rows.prewarm_in_background). 테스트는 BackgroundTasks를 만들어 넘기고 그 안에
# 쌓인 작업은 실행하지 않는다 — 저장 로직만 검증하고 LLM을 타지 않는다(번역은 test_reason_prewarm).
def save_overseas_state(*a, **kw):
    kw.setdefault("background", BackgroundTasks())
    return _save_overseas_state(*a, **kw)

# 실제 데이터와 겹치지 않는 미래 연월
YYYYMM = "209912"
YEAR = 2099
PK = "SARC:CPU"
CAT = "sal"
HC = "hc"


def _details(db, period_type: str, cat: str = CAT) -> dict[int, float]:
    entry = db.scalar(
        select(CostEntry).where(
            CostEntry.account_code == ACCOUNT_KEY,
            CostEntry.scope_code == PK,
            CostEntry.category_code == cat,
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


def test_months_are_stored_and_periods_are_not_invented(db):
    """월 값만 저장한다 — 분기·연간 행은 만들지 않는다."""
    months = [float(m) for m in range(1, 13)]  # 1~12
    bulk_upsert_cost_entries(db, [(PK, "forecast", CAT, YYYYMM, months)])
    db.flush()

    assert _details(db, "MONTH") == {m: float(m) for m in range(1, 13)}
    assert _details(db, "QUARTER") == {}, "분기를 월에서 만들어 저장했다"
    assert _details(db, "YEAR") == {}, "연간을 월에서 만들어 저장했다"


def test_uploaded_periods_are_stored_as_they_came(db):
    """연구소가 파일에 적어 보낸 분기·연간은 **그대로** 저장한다 — 월 합과 달라도 고치지 않는다.

    여기 1Q는 월 합(6)과 다른 99다. 그 차이는 업로드 미리보기가 period_mismatch로 색칠해 알리고,
    값은 연구소가 보낸 대로 남는다(사람이 고쳐서 다시 올린다)."""
    months = [float(m) for m in range(1, 13)]
    bulk_upsert_cost_entries(
        db,
        [(PK, "forecast", CAT, YYYYMM, months)],
        periods={(PK, "forecast", CAT, YYYYMM): [99.0, None, None, None, 1234.0]},
    )
    db.flush()

    assert _details(db, "QUARTER") == {1: 99.0}, "보낸 칸만 저장돼야 한다(안 보낸 분기는 행이 없다)"
    assert _details(db, "YEAR") == {0: 1234.0}


def test_a_later_save_does_not_overwrite_reported_periods(db):
    """월 값을 고쳐 다시 저장해도 연구소가 적어 보낸 분기·연간은 건드리지 않는다.

    전에는 저장이 분기를 월 합으로 다시 계산해서, 담당자가 화면에서 [저장]만 눌러도 연구소
    보고값이 사라졌다."""
    bulk_upsert_cost_entries(
        db,
        [(PK, "forecast", CAT, YYYYMM, [1.0] * 12)],
        periods={(PK, "forecast", CAT, YYYYMM): [99.0, None, None, None, 1234.0]},
    )
    db.flush()

    bulk_upsert_cost_entries(db, [(PK, "forecast", CAT, YYYYMM, [100.0] + [None] * 11)])
    db.flush()

    assert _details(db, "MONTH")[1] == 100.0
    assert _details(db, "MONTH")[2] == 1.0, "안 보낸 달이 지워졌다(부분 갱신이 깨졌다)"
    assert _details(db, "QUARTER") == {1: 99.0}, "연구소가 보고한 분기값이 덮였다"
    assert _details(db, "YEAR") == {0: 1234.0}


def test_headcount_is_not_summed_into_quarters(db):
    """인력(명)도 분기·연간을 만들지 않는다 — 전에는 월 합으로 저장해서 분기가 3배가 됐다."""
    bulk_upsert_cost_entries(db, [(PK, "forecast", HC, YYYYMM, [65.0] * 12)])
    db.flush()

    assert _details(db, "MONTH", HC)[1] == 65.0
    assert _details(db, "QUARTER", HC) == {}, "인력 분기가 월 합(195)으로 저장됐다"
    assert _details(db, "YEAR", HC) == {}


def test_save_is_idempotent(db):
    """같은 입력으로 두 번 저장해도 행이 늘거나 달라지지 않는다."""
    bulk_upsert_cost_entries(db, [(PK, "forecast", CAT, YYYYMM, [2.0] * 12)])
    db.flush()
    once = _details(db, "MONTH")

    bulk_upsert_cost_entries(db, [(PK, "forecast", CAT, YYYYMM, [2.0] * 12)])
    db.flush()

    assert _details(db, "MONTH") == once == {m: 2.0 for m in range(1, 13)}
    assert _details(db, "QUARTER") == {}
    assert _details(db, "YEAR") == {}


def test_state_save_path_does_not_invent_periods(db):
    """PUT /overseas/state로 저장해도 분기·연간을 만들지 않는다 — 화면 전체 저장이 그 경로다."""
    from app.accounts.overseas.endpoints.collect import get_overseas_state

    state = get_overseas_state(basis_year=2026, db=db)
    if not state.basis_month:
        pytest.skip("속보 행이 없어 저장 경로를 확인할 수 없다")
    before = {
        (r.cost_entry_id, r.year, r.period_type, r.period): float(r.amount)
        for r in db.scalars(
            select(CostEntryDetail).where(CostEntryDetail.period_type.in_(("QUARTER", "YEAR")))
        )
    }

    assert save_overseas_state(state=state, db=db)["status"] == "ok"
    db.flush()

    after = {
        (r.cost_entry_id, r.year, r.period_type, r.period): float(r.amount)
        for r in db.scalars(
            select(CostEntryDetail).where(CostEntryDetail.period_type.in_(("QUARTER", "YEAR")))
        )
    }
    assert after == before, "화면 전체 저장이 분기·연간 행을 만들거나 고쳤다"
