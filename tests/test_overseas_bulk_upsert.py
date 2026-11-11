from sqlalchemy import select

from app.api.v1.endpoints.overseas import (
    ACCOUNT_KEY,
    PLAN_NO,
    _bulk_upsert_cost_entries,
    _bulk_upsert_notes,
    _bulk_upsert_plan_entries,
    _bulk_upsert_reason_rows,
)
from app.models import CostEntry, CostEntryDetail, ManagementPlanEntry, ManagementPlanEntryDetail, Note, OverseasReasonRow
from app.schemas.overseas import ReasonRow

# 실제 데이터와 안 겹치는 미래의 가짜 연월/연도를 키로 써서 테스트한다.
TEST_YM = "209901"
TEST_YEAR = 2099


def _months(start: float, step: float = 1.0) -> list[float]:
    return [start + step * i for i in range(12)]


def test_bulk_upsert_cost_entries_inserts_header_and_details(db):
    rows = [
        ("SARC:CPU", "forecast", "hc", TEST_YM, _months(1.0)),
        ("SARC:GPU", "forecast", "hc", TEST_YM, _months(1.0, 2.0)),
    ]
    _bulk_upsert_cost_entries(db, rows)
    db.flush()

    entries = db.scalars(
        select(CostEntry).where(
            CostEntry.account_code == ACCOUNT_KEY, CostEntry.yyyymm == TEST_YM, CostEntry.version == "forecast"
        )
    ).all()
    assert {(e.scope_code, e.category_code) for e in entries} == {("SARC:CPU", "hc"), ("SARC:GPU", "hc")}

    by_scope = {e.scope_code: e for e in entries}
    details = {
        d.period: float(d.amount)
        for d in db.scalars(
            select(CostEntryDetail).where(
                CostEntryDetail.cost_entry_id == by_scope["SARC:CPU"].id, CostEntryDetail.period_type == "MONTH"
            )
        )
    }
    assert details[1] == 1.0
    assert details[12] == 12.0


def test_bulk_upsert_cost_entries_dedup_last_wins(db):
    """같은 (scope,version,category,yyyymm) 키가 두 번 들어오면 마지막 값이 이겨야 한다."""
    rows = [
        ("_ETC", "forecast", "etc", TEST_YM, [0.0] * 12),
        ("_ETC", "forecast", "etc", TEST_YM, [999.0] * 12),
    ]
    _bulk_upsert_cost_entries(db, rows)
    db.flush()

    entry = db.scalar(
        select(CostEntry).where(
            CostEntry.account_code == ACCOUNT_KEY,
            CostEntry.scope_code == "_ETC",
            CostEntry.yyyymm == TEST_YM,
            CostEntry.version == "forecast",
        )
    )
    amounts = {
        float(d.amount)
        for d in db.scalars(
            select(CostEntryDetail).where(
                CostEntryDetail.cost_entry_id == entry.id, CostEntryDetail.period_type == "MONTH"
            )
        )
    }
    assert amounts == {999.0}


def test_bulk_upsert_cost_entries_conflict_reuses_same_header(db):
    """이미 있는 헤더에 다시 upsert하면 새 헤더가 또 생기지 않고(같은 id), 값만 갱신돼야 한다."""
    _bulk_upsert_cost_entries(db, [("SARC:CPU", "forecast", "hc", TEST_YM, _months(1.0))])
    db.flush()
    first_id = db.scalar(
        select(CostEntry.id).where(
            CostEntry.account_code == ACCOUNT_KEY,
            CostEntry.scope_code == "SARC:CPU",
            CostEntry.category_code == "hc",
            CostEntry.yyyymm == TEST_YM,
            CostEntry.version == "forecast",
        )
    )

    _bulk_upsert_cost_entries(db, [("SARC:CPU", "forecast", "hc", TEST_YM, [100.0] * 12)])
    db.flush()
    second_id = db.scalar(
        select(CostEntry.id).where(
            CostEntry.account_code == ACCOUNT_KEY,
            CostEntry.scope_code == "SARC:CPU",
            CostEntry.category_code == "hc",
            CostEntry.yyyymm == TEST_YM,
            CostEntry.version == "forecast",
        )
    )
    assert first_id == second_id

    amount = db.scalar(
        select(CostEntryDetail.amount).where(
            CostEntryDetail.cost_entry_id == second_id,
            CostEntryDetail.period == 1,
            CostEntryDetail.period_type == "MONTH",
        )
    )
    assert float(amount) == 100.0


def test_bulk_upsert_cost_entries_empty_rows_is_noop(db):
    _bulk_upsert_cost_entries(db, [])  # 에러 없이 조용히 리턴해야 한다


def test_bulk_upsert_plan_entries(db):
    _bulk_upsert_plan_entries(db, [("SARC:CPU", "hc", TEST_YEAR, _months(1.0))])
    db.flush()

    entry = db.scalar(
        select(ManagementPlanEntry).where(
            ManagementPlanEntry.account_code == ACCOUNT_KEY,
            ManagementPlanEntry.scope_code == "SARC:CPU",
            ManagementPlanEntry.category_code == "hc",
            ManagementPlanEntry.year == TEST_YEAR,
            ManagementPlanEntry.plan_no == PLAN_NO,
        )
    )
    assert entry is not None

    details = {
        d.period: float(d.amount)
        for d in db.scalars(
            select(ManagementPlanEntryDetail).where(
                ManagementPlanEntryDetail.plan_entry_id == entry.id, ManagementPlanEntryDetail.period_type == "MONTH"
            )
        )
    }
    assert details[1] == 1.0
    assert details[12] == 12.0


def test_bulk_upsert_plan_entries_empty_rows_is_noop(db):
    _bulk_upsert_plan_entries(db, [])


def test_bulk_upsert_notes_dedup_last_wins(db):
    rows = [
        ("cur", "y", "ALL", "cat0", "first"),
        ("cur", "y", "ALL", "cat0", "second"),
        ("cur", "y", "ALL", "cat1", "other"),
    ]
    _bulk_upsert_notes(db, rows)
    db.flush()

    saved = {
        n.category_key: n.note_text
        for n in db.scalars(
            select(Note).where(
                Note.account_key == ACCOUNT_KEY, Note.base_version == "cur", Note.period == "y", Note.scope == "ALL"
            )
        )
    }
    assert saved == {"cat0": "second", "cat1": "other"}


def test_bulk_upsert_reason_rows(db):
    reasons = [
        ReasonRow(
            lab_code="SARC",
            version="cur",
            category_key="c0",
            block="y",
            mon_label="",
            plan_value=1.0,
            prev_value=2.0,
            cur_value=3.0,
            diff_plan=1.0,
            diff_prev=1.0,
            remark_plan="r1",
            remark_prev="r2",
            prev_label="",
            cur_label="",
            source_file="",
        )
    ]
    _bulk_upsert_reason_rows(db, reasons)
    db.flush()

    row = db.scalar(
        select(OverseasReasonRow).where(OverseasReasonRow.lab_code == "SARC", OverseasReasonRow.category_key == "c0")
    )
    assert row is not None
    assert float(row.cur_value) == 3.0
