"""공통 RULE "빈칸": 미등록/미업로드 칸은 0이 아니라 빈칸(None)이고, 합계는 있는 값만 더한다.

0.0("0으로 올라옴")과 None("아직 안 올라옴")이 저장·조회를 왕복해도 구분되는지 본다 —
한 번이라도 0으로 뭉개지면 그 뒤로는 어디서도 되살릴 수 없기 때문에 왕복으로 확인한다.
"""

from sqlalchemy import select

from app.accounts.overseas.endpoints.collect import ACCOUNT_KEY
from app.shared.models.common import CostEntry, CostEntryDetail
from app.shared.core.values import n
from app.accounts.overseas.services.entries import bulk_upsert_cost_entries, entry_months_bulk
from app.accounts.overseas.services.aggregate import krw_months

TEST_YM = "209902"


def _entry(db, scope: str, category: str) -> CostEntry:
    return db.scalar(
        select(CostEntry).where(
            CostEntry.account_code == ACCOUNT_KEY,
            CostEntry.yyyymm == TEST_YM,
            CostEntry.version == "forecast",
            CostEntry.scope_code == scope,
            CostEntry.category_code == category,
        )
    )


def test_none_months_are_not_stored_and_come_back_as_none(db):
    # 1·2월만 올라왔고(0.0도 "올라온" 값이다) 3월부터는 미등록인 연구소
    months = [10.0, 0.0] + [None] * 10
    bulk_upsert_cost_entries(db, [("SARC:CPU", "forecast", "sal", TEST_YM, months)])
    db.flush()

    entry = _entry(db, "SARC:CPU", "sal")
    stored = {
        d.period
        for d in db.scalars(
            select(CostEntryDetail).where(
                CostEntryDetail.cost_entry_id == entry.id, CostEntryDetail.period_type == "MONTH"
            )
        )
    }
    # 미등록 달은 행 자체가 없어야 한다 — 0으로 넣어두면 다시 읽을 때 빈칸이 아니라 0이 된다.
    assert stored == {1, 2}

    got = entry_months_bulk(db, [entry])[entry.id]
    assert got[0] == 10.0
    assert got[1] == 0.0  # 0으로 올라온 달은 0으로 남는다
    assert got[2:] == [None] * 10  # 안 올라온 달은 빈칸


def test_all_none_row_stores_header_only(db):
    # 행은 만들어졌지만 아직 아무 달도 안 올라온 경우 — 디테일이 하나도 없어야 한다.
    bulk_upsert_cost_entries(db, [("DSRA", "forecast", "sal", TEST_YM, [None] * 12)])
    db.flush()

    entry = _entry(db, "DSRA", "sal")
    assert entry is not None
    assert entry_months_bulk(db, [entry])[entry.id] == [None] * 12


def test_krw_months_adds_only_present_values():
    # 합계는 있는 값만 더해 진행한다 — 미등록 달이 섞여도 나머지 달은 그대로 계산된다.
    rate = [1000.0] * 12
    # 저장값은 $다(2026-11-18 단위 전환) — 원화는 `값 × 환율`이고 ×1000이 더 붙지 않는다.
    cats = {"sal": [1.0, None, 2.0] + [None] * 9}
    got = krw_months(cats, {}, {"sal"}, rate)
    assert got[0] == 1.0 * 1000
    assert got[1] == 0.0  # 미등록 달은 더할 값이 없어 0
    assert got[2] == 2.0 * 1000


def test_n_helper_keeps_zero_distinct_from_none():
    assert n(None) == 0.0
    assert n(0.0) == 0.0
    assert n(3.5) == 3.5
