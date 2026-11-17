from sqlalchemy import select

from app.accounts.overseas.endpoints.collect import ACCOUNT_KEY, _bulk_upsert_notes
from app.accounts.overseas.services.reason_rows import upsert_reason_rows
from app.accounts.overseas.models import OverseasReasonRow
from app.shared.models.common import CostEntry, CostEntryDetail, Note
from app.shared.models.plan import ManagementPlanEntry, ManagementPlanEntryDetail
from app.accounts.overseas.services.entries import (
    bulk_upsert_cost_entries,
    bulk_upsert_plan_entries,
    pick_plan_no,
)
from app.accounts.overseas.schemas import ReasonRow

# 실제 데이터와 안 겹치는 미래의 가짜 연월/연도를 키로 써서 테스트한다.
TEST_YM = "209901"
TEST_YEAR = 2099
TEST_PLAN_NO = "P01"


def _months(start: float, step: float = 1.0) -> list[float]:
    return [start + step * i for i in range(12)]


def test_bulk_upsert_cost_entries_inserts_header_and_details(db):
    rows = [
        ("SARC:CPU", "forecast", "hc", TEST_YM, _months(1.0)),
        ("SARC:GPU", "forecast", "hc", TEST_YM, _months(1.0, 2.0)),
    ]
    bulk_upsert_cost_entries(db, rows)
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
    bulk_upsert_cost_entries(db, rows)
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
    bulk_upsert_cost_entries(db, [("SARC:CPU", "forecast", "hc", TEST_YM, _months(1.0))])
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

    bulk_upsert_cost_entries(db, [("SARC:CPU", "forecast", "hc", TEST_YM, [100.0] * 12)])
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
    bulk_upsert_cost_entries(db, [])  # 에러 없이 조용히 리턴해야 한다


def test_bulk_upsert_plan_entries(db):
    bulk_upsert_plan_entries(db, [("SARC:CPU", "hc", TEST_YEAR, _months(1.0))], TEST_PLAN_NO)
    db.flush()

    entry = db.scalar(
        select(ManagementPlanEntry).where(
            ManagementPlanEntry.account_code == ACCOUNT_KEY,
            ManagementPlanEntry.scope_code == "SARC:CPU",
            ManagementPlanEntry.category_code == "hc",
            ManagementPlanEntry.year == TEST_YEAR,
            ManagementPlanEntry.plan_no == TEST_PLAN_NO,
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
    bulk_upsert_plan_entries(db, [], TEST_PLAN_NO)


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
    upsert_reason_rows(db, reasons, {r.lab_code for r in reasons})
    db.flush()

    row = db.scalar(
        select(OverseasReasonRow).where(OverseasReasonRow.lab_code == "SARC", OverseasReasonRow.category_key == "c0")
    )
    assert row is not None
    assert float(row.cur_value) == 3.0


# ---------- 적용 차수 고르기 (2026-11-17) ----------
# 전에는 읽기·쓰기가 모두 `PLAN_NO = "P00"` 상수였다 — P01·P02를 확정해도 화면·챗봇이 계속
# P00을 보고, 저장도 P00에만 들어갔다. 규칙은 pick_plan_no 한 곳에 있다.
class _Rev:
    def __init__(self, plan_no, is_final=False, is_confirmed=False):
        self.plan_no, self.is_final, self.is_confirmed = plan_no, is_final, is_confirmed


def test_pick_plan_no_prefers_the_designated_official_revision():
    """★(그 연도 공식 비교 기준)이 있으면 그게 적용 차수다 — 확정 상태보다 사람의 지정이 이긴다."""
    rows = [_Rev("P00", is_confirmed=True), _Rev("P01", is_final=True), _Rev("P02", is_confirmed=True)]
    assert pick_plan_no(rows) == "P01"


def test_pick_plan_no_falls_back_to_the_latest_confirmed():
    """★가 없으면 **확정된 차수 중 최신**이다. 작성 중인 P02가 있어도 그걸로 넘어가지 않는다 —
    작성 중 숫자로 경영계획 대비를 내면 아직 합의되지 않은 값과 비교하게 된다."""
    rows = [_Rev("P00", is_confirmed=True), _Rev("P01", is_confirmed=True), _Rev("P02")]
    assert pick_plan_no(rows) == "P01"


def test_pick_plan_no_uses_a_draft_only_when_nothing_is_confirmed():
    """확정된 게 하나도 없으면 가장 최근 차수를 쓴다(빈 화면보다 낫다 — 라벨로 밝힌다).
    차수가 아예 없으면 빈 문자열이다 = "경영계획 데이터 없음"이고 0원이 아니다."""
    assert pick_plan_no([_Rev("P00"), _Rev("P01")]) == "P01"
    assert pick_plan_no([]) == ""


def test_plan_no_order_does_not_depend_on_input_order():
    """차수 순서는 입력 순서가 아니라 plan_no 순이다 — DB 정렬이 흔들려도 같은 차수를 골라야 한다."""
    rows = [_Rev("P02", is_confirmed=True), _Rev("P00", is_confirmed=True), _Rev("P01", is_confirmed=True)]
    assert pick_plan_no(rows) == "P02"


def test_bulk_upsert_plan_entries_needs_a_revision():
    """차수 없이 저장하려 하면 막는다 — 기본값을 두면 호출부 하나가 빠뜨렸을 때 조용히 다른
    차수에 쌓인다(전에 모든 저장이 P00으로 들어갔던 자리다)."""
    import pytest

    with pytest.raises(ValueError):
        bulk_upsert_plan_entries(None, [("SARC:CPU", "hc", TEST_YEAR, _months(1.0))], "")
