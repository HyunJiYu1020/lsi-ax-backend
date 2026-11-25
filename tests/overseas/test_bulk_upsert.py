from sqlalchemy import select

from app.accounts.overseas.endpoints.collect import ACCOUNT_KEY
from app.accounts.overseas.services.notes import bulk_upsert_notes as _bulk_upsert_notes
from app.accounts.overseas.services.reason_rows import upsert_reason_rows
from app.accounts.overseas.models import OverseasReasonRow
from app.shared.models.common import CostEntry, CostEntryDetail, Note
from app.shared.models.plan import ManagementPlanEntry, ManagementPlanEntryDetail
from app.accounts.overseas.services.entries import (
    bulk_upsert_cost_entries,
    bulk_upsert_plan_entries,
    entry_months_bulk,
    pick_plan_no,
)
from app.accounts.overseas.services.extra_rows import months_by_category
from app.accounts.overseas.schemas import ReasonRow

# 실제 데이터와 안 겹치는 미래의 가짜 연월/연도를 키로 써서 테스트한다.
TEST_YM = 209901
# 중복 키 테스트용 자리 — 실제 연구소 pk를 쓴다(쓰지 않는 가상 scope를 쓰면,
# 그 가상 행이 없어졌을 때 테스트가 "무엇을 덮어쓰는지" 모르는 채로 통과한다).
TEST_SCOPE = "SARC:CPU"
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
        (TEST_SCOPE, "forecast", "misc", TEST_YM, [0.0] * 12),
        (TEST_SCOPE, "forecast", "misc", TEST_YM, [999.0] * 12),
    ]
    bulk_upsert_cost_entries(db, rows)
    db.flush()

    entry = db.scalar(
        select(CostEntry).where(
            CostEntry.account_code == ACCOUNT_KEY,
            CostEntry.scope_code == TEST_SCOPE,
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
            version="forecast",
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
    # 기준월은 이제 필수다 — 행에 없으면 호출부가 기본값으로 준다(없으면 400).
    upsert_reason_rows(db, reasons, {r.lab_code for r in reasons}, 202609)
    db.flush()

    row = db.scalar(
        select(OverseasReasonRow).where(OverseasReasonRow.lab_code == "SARC", OverseasReasonRow.category_key == "c0")
    )
    assert row is not None
    assert float(row.cur_value) == 3.0
    assert row.yyyymm == 202609

    # 같은 연구소·항목이라도 달이 다르면 다른 행이다 — 11월 회신이 9월 회신을 덮으면 안 된다.
    upsert_reason_rows(db, reasons, {r.lab_code for r in reasons}, 202611)
    db.flush()
    yms = set(
        db.scalars(
            select(OverseasReasonRow.yyyymm).where(
                OverseasReasonRow.lab_code == "SARC", OverseasReasonRow.category_key == "c0"
            )
        )
    )
    assert yms == {202609, 202611}


# ---------- 적용 차수 고르기 ----------
# 규칙은 pick_plan_no 한 곳에 있다 — 차수 상수를 박아 두면 P01·P02를 확정해도 화면·챗봇이 계속
# 한 차수만 보고, 저장도 거기로만 들어간다.
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


def test_blank_month_deletes_the_stored_row(db):
    """None으로 보낸 달은 저장된 상세 행을 지운다 — "미제출로 되돌리기"가 되어야 한다.

    연구소가 칸을 비워 다시 보내는 것은 "그 달은 아직 안 냈다"는 보고다. 건너뛰기만 하면 지난
    업로드의 숫자가 남아 화면이 거짓으로 채워지고, 0으로 덮으면 "0으로 보고"가 된다. 그래서
    지우는 것이 유일하게 뜻이 맞는 처리다. 0.0은 지우지 않는다(그건 보고된 값이다)."""
    pk, ym, cat = "DSRA", 209905, "sal"
    bulk_upsert_cost_entries(db, [(pk, "forecast", cat, ym, [1.0, 2.0, 0.0] + [None] * 9)])
    assert months_by_category(db, pk, "forecast", ym)[cat][:3] == [1.0, 2.0, 0.0]

    # 기본 모드에서는 None이 "건드리지 마라"다(부분 갱신) — 지워지지 않는다.
    bulk_upsert_cost_entries(db, [(pk, "forecast", cat, ym, [1.0, None, 0.0] + [None] * 9)])
    assert months_by_category(db, pk, "forecast", ym)[cat][1] == 2.0

    # 2월을 비워 다시 올린다(clear_blanks) — 1월·3월은 그대로, 2월만 미등록으로 돌아간다.
    bulk_upsert_cost_entries(
        db, [(pk, "forecast", cat, ym, [1.0, None, 0.0] + [None] * 9)], clear_blanks=True
    )
    entry = db.scalars(
        select(CostEntry).where(
            CostEntry.account_code == "overseas", CostEntry.scope_code == pk,
            CostEntry.version == "forecast", CostEntry.yyyymm == ym, CostEntry.category_code == cat,
        )
    ).one()
    months = entry_months_bulk(db, [entry])[entry.id]
    assert months[0] == 1.0
    assert months[1] is None, "비워 보낸 달이 지워지지 않았다 — 옛 숫자가 그대로 남는다"
    assert months[2] == 0.0, "0으로 보고한 달이 같이 지워졌다"
