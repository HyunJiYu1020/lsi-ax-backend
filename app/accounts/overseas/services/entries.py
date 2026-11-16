"""해외연구소 비용·경영계획 값의 읽기/쓰기 — cost_entries · management_plan_entries 접근.

왜 여기로 옮겼나: 원래 overseas.py의 `_entry_months_bulk` · `_bulk_upsert_*`였고 증감분석·집계
엔드포인트가 그 밑줄 이름을 직접 import해서 썼다(PLAN.md P1-1).

더 중요한 이유는 **쓰기 경로를 한곳에 모으는 것**이다(PLAN.md P1-3). 분기·연간(QUARTER/YEAR) 행은
공용 경로(cost.py의 _resync_summary)에서는 월 값을 쓸 때마다 12개월 합으로 재계산되는데,
해외연구소 경로(/overseas/state)는 월 행만 쓰고 재계산을 하지 않았다. 그래서 overseas의 분기·연간
행은 한 번 백필된 값에서 멈춰 있었다 — "같은 데이터, 두 가지 쓰기 규칙"이고, 정체된 그 행을 읽는
경로가 하나라도 생기면 화면 합계와 다른 숫자가 나온다. 쓰기가 이 모듈로 모이는 지금이 고칠 자리다
(resync_summaries).
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.shared.core.periods import ym_month, ym_year
from app.shared.models.common import CostEntry, CostEntryDetail
from app.shared.models.plan import ManagementPlanEntry, ManagementPlanEntryDetail, ManagementPlanVersion
from app.accounts.overseas.services.labs import ACCOUNT_KEY

# 분기 → 그 분기에 드는 월(1-based). cost.py의 QUARTERS와 같은 정의다 — 분기·연간을 두 경로가
# 다르게 자르면 같은 데이터가 다른 합계를 갖는다.
QUARTERS: dict[int, tuple[int, int, int]] = {1: (1, 2, 3), 2: (4, 5, 6), 3: (7, 8, 9), 4: (10, 11, 12)}
PLAN_NO = "P00"


def resync_summaries(db: Session, entry_years: set[tuple[int, int]]) -> None:
    """(cost_entry_id, year) 묶음의 QUARTER·YEAR 행을 저장된 MONTH 12개로 다시 계산해 덮어쓴다.

    PLAN.md P1-3 해결. cost.py의 _resync_summary와 같은 규칙이지만 두 가지가 다르다:

    ① 들어온 값 배열이 아니라 **저장된 MONTH 행을 다시 읽어서** 계산한다. 해외연구소 저장은
       None인 달을 건너뛰는 부분 갱신이라(bulk_upsert_cost_entries), 보낸 배열만 보고 더하면
       "이번에 안 보낸 달"이 0으로 빠져 분기 합이 줄어든다.
    ② 조합마다 따로 upsert하지 않고 한 문장으로 모아 쓴다 — 해외연구소는 연구소×비용항목 조합이
       많아서(수백 개) 조합당 5번씩 왕복하면 저장 한 번이 수천 번 왕복이 된다.
    """
    if not entry_years:
        return
    ids = {eid for eid, _ in entry_years}
    years = {y for _, y in entry_years}
    months: dict[tuple[int, int], dict[int, float]] = {}
    for r in db.scalars(
        select(CostEntryDetail).where(
            CostEntryDetail.cost_entry_id.in_(ids),
            CostEntryDetail.year.in_(years),
            CostEntryDetail.period_type == "MONTH",
        )
    ):
        months.setdefault((r.cost_entry_id, r.year), {})[r.period] = float(r.amount)

    values = []
    for eid, year in sorted(entry_years):
        got = months.get((eid, year), {})
        quarters = {q: sum(got.get(m, 0.0) for m in mos) for q, mos in QUARTERS.items()}
        for q, amount in quarters.items():
            values.append({"cost_entry_id": eid, "year": year, "period_type": "QUARTER",
                           "period": q, "col_name": "", "amount": amount})
        values.append({"cost_entry_id": eid, "year": year, "period_type": "YEAR",
                       "period": 0, "col_name": "", "amount": sum(quarters.values())})
    if not values:
        return
    stmt = pg_insert(CostEntryDetail).values(values)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["cost_entry_id", "year", "period_type", "period", "col_name"],
            set_={"amount": stmt.excluded.amount, "updated_at": datetime.now(timezone.utc)},
        )
    )


def entry_months_bulk(db: Session, entries: list[CostEntry]) -> dict[int, list[float | None]]:
    """엔트리별 월 값을 한 번의 쿼리로 모아 돌려준다 (호출부가 엔트리마다 따로 조회하지 않도록).
    기본은 12개월(그 헤더의 yyyymm 연도)이지만, 다음 연도치(year+1)까지 저장돼 있으면 24개월(그다음도
    저장돼 있으면 36개월…, 항상 12의 배수)로 이어 붙여 돌려준다 — 기간을 12개월 너머로 늘려 받을 때
    쓰는 확장 지점.

    디테일 행이 아예 없는 달은 0이 아니라 None(미등록)으로 돌려준다 — 0으로 채우면 "0원으로 올라온
    달"과 "아직 안 올라온 달"이 구분되지 않는다(공통 RULE: 빈칸 처리). 계산에 쓸 때는 _n()으로 0으로
    바꿔 더한다."""
    entry_ids = [e.id for e in entries]
    if not entry_ids:
        return {}
    base_year_by_id = {e.id: ym_year(e.yyyymm) for e in entries}
    by_entry: dict[int, dict[int, float]] = {}
    max_idx_by_entry: dict[int, int] = {}
    for r in db.scalars(
        select(CostEntryDetail).where(
            CostEntryDetail.cost_entry_id.in_(entry_ids), CostEntryDetail.period_type == "MONTH"
        )
    ):
        idx = (r.year - base_year_by_id[r.cost_entry_id]) * 12 + (r.period - 1)  # 0-based: 0~11=1년차, 12~23=2년차…
        by_entry.setdefault(r.cost_entry_id, {})[idx] = float(r.amount)
        max_idx_by_entry[r.cost_entry_id] = max(max_idx_by_entry.get(r.cost_entry_id, -1), idx)
    out: dict[int, list[float | None]] = {}
    for eid in entry_ids:
        # 디테일이 하나도 없어도(= 행은 있지만 아직 한 달도 안 올라옴) 12칸은 돌려준다 — 빈 배열을
        # 주면 받는 쪽이 월 인덱스로 집으려다 터진다. 그 12칸은 전부 None(미등록)이다.
        n = max(12, ((max_idx_by_entry.get(eid, -1) // 12) + 1) * 12)
        vals = by_entry.get(eid, {})
        out[eid] = [vals.get(i) for i in range(n)]
    return out


def plan_entry_months_bulk(db: Session, entry_ids: list[int]) -> dict[int, list[float | None]]:
    """경영계획 월 값. _entry_months_bulk와 같이 디테일이 없는 달은 None(미등록)이다."""
    if not entry_ids:
        return {}
    by_entry: dict[int, dict[int, float]] = {}
    for r in db.scalars(
        select(ManagementPlanEntryDetail).where(
            ManagementPlanEntryDetail.plan_entry_id.in_(entry_ids), ManagementPlanEntryDetail.period_type == "MONTH"
        )
    ):
        by_entry.setdefault(r.plan_entry_id, {})[r.period] = float(r.amount)
    return {eid: [by_entry.get(eid, {}).get(m) for m in range(1, 13)] for eid in entry_ids}


def bulk_upsert_cost_entries(db: Session, rows: list[tuple[str, str, str, str, list[float | None]]]) -> None:
    """(scope_code, version, category_code, yyyymm, months) 목록을 헤더·디테일 각각 단일 upsert
    문으로 한 번에 처리한다 — save_overseas_state가 행마다 get_or_create_entry+월별 insert를
    따로 호출하면 (lab×category 조합 수)×14번 왕복이 나서, 그걸 피하려고 모아서 처리한다.
    같은 키가 두 번 들어오면 (호출부가 겹쳐 보낸 경우) 마지막 값이 이긴다 — 기존의 순차 upsert와 동일한 규칙."""
    dedup: dict[tuple[str, str, str, str], list[float | None]] = {
        (scope_code, version, category_code, yyyymm): months
        for scope_code, version, category_code, yyyymm, months in rows
    }
    if not dedup:
        return

    header_stmt = pg_insert(CostEntry).values(
        [
            {
                "yyyymm": yyyymm, "account_code": ACCOUNT_KEY, "scope_code": scope_code,
                "version": version, "category_code": category_code,
            }
            for scope_code, version, category_code, yyyymm in dedup
        ]
    )
    header_stmt = header_stmt.on_conflict_do_update(
        index_elements=["account_code", "scope_code", "category_code", "yyyymm", "version"],
        set_={"yyyymm": header_stmt.excluded.yyyymm},  # no-op 갱신 — DO NOTHING은 충돌 행을 RETURNING 안 해줘서 못 씀
    ).returning(CostEntry.id, CostEntry.scope_code, CostEntry.category_code, CostEntry.yyyymm, CostEntry.version)
    id_by_key = {(r.scope_code, r.category_code, r.yyyymm, r.version): r.id for r in db.execute(header_stmt)}

    # amount가 None인 달은 건너뛴다 — 미등록을 0으로 저장하면 다시 읽을 때 빈칸이 아니라 0이 된다.
    detail_values = [
        {
            "cost_entry_id": id_by_key[(scope_code, category_code, yyyymm, version)],
            # months가 12개(기본)를 넘으면(12의 배수, 기간을 늘려 받는 경우) 12개씩 다음 연도로
            # 넘겨 저장한다 — upsert_cost_entry/_upsert_entry_months와 동일한 규칙.
            "year": ym_year(yyyymm) + i // 12, "period_type": "MONTH", "period": i % 12 + 1,
            "col_name": "", "amount": amount,
        }
        for (scope_code, version, category_code, yyyymm), months in dedup.items()
        for i, amount in enumerate(months)
        if amount is not None
    ]
    if not detail_values:
        return
    detail_stmt = pg_insert(CostEntryDetail).values(detail_values)
    detail_stmt = detail_stmt.on_conflict_do_update(
        index_elements=["cost_entry_id", "year", "period_type", "period", "col_name"],
        set_={"amount": detail_stmt.excluded.amount, "updated_at": datetime.now(timezone.utc)},
    )
    db.execute(detail_stmt)

    # 월 값을 쓴 바로 그 자리에서 분기·연간을 다시 계산한다(PLAN.md P1-3) — 호출부가 따로
    # 불러 줘야 하는 구조면 언젠가 빠뜨리고, 그러면 다시 "같은 데이터, 두 가지 쓰기 규칙"이 된다.
    # flush가 필요하다: resync가 방금 upsert한 MONTH 행을 다시 읽어 더하기 때문이다.
    db.flush()
    resync_summaries(db, {(v["cost_entry_id"], v["year"]) for v in detail_values})


def bulk_upsert_plan_entries(db: Session, rows: list[tuple[str, str, int, list[float | None]]]) -> None:
    """(scope_code, category_code, year, months) 목록을 헤더·디테일 각각 단일 upsert 문으로 처리한다
    (_bulk_upsert_cost_entries와 같은 이유)."""
    dedup = {(r[0], r[1], r[2]): r[3] for r in rows}
    if not dedup:
        return
    for year in {year for _, _, year in dedup}:
        ensure_plan_version(db, year)

    header_stmt = pg_insert(ManagementPlanEntry).values(
        [
            {
                "account_code": ACCOUNT_KEY, "scope_code": scope_code, "year": year,
                "plan_no": PLAN_NO, "category_code": category_code,
            }
            for scope_code, category_code, year in dedup
        ]
    )
    header_stmt = header_stmt.on_conflict_do_update(
        index_elements=["account_code", "scope_code", "category_code", "year", "plan_no"],
        set_={"year": header_stmt.excluded.year},
    ).returning(ManagementPlanEntry.id, ManagementPlanEntry.scope_code, ManagementPlanEntry.category_code, ManagementPlanEntry.year)
    id_by_key = {(r.scope_code, r.category_code, r.year): r.id for r in db.execute(header_stmt)}

    # 미등록(None)인 달은 저장하지 않는다 — _bulk_upsert_cost_entries와 같은 이유.
    detail_values = [
        {
            "plan_entry_id": id_by_key[(scope_code, category_code, year)],
            "year": year, "period_type": "MONTH", "period": m, "col_name": "", "amount": amount,
        }
        for (scope_code, category_code, year), months in dedup.items()
        for m, amount in enumerate(months, start=1)
        if amount is not None
    ]
    if not detail_values:
        return
    detail_stmt = pg_insert(ManagementPlanEntryDetail).values(detail_values)
    detail_stmt = detail_stmt.on_conflict_do_update(
        index_elements=["plan_entry_id", "year", "period_type", "period", "col_name"],
        set_={"amount": detail_stmt.excluded.amount},
    )
    db.execute(detail_stmt)


def get_or_create_entry(db: Session, scope_code: str, version: str, category_code: str, yyyymm: str) -> int:
    """비용 헤더(cost_entries)를 account_code×scope_code×category_code×yyyymm×version으로 upsert하고
    id를 돌려준다."""
    stmt = pg_insert(CostEntry).values(
        yyyymm=yyyymm, account_code=ACCOUNT_KEY, scope_code=scope_code, version=version, category_code=category_code,
    ).on_conflict_do_nothing(index_elements=["account_code", "scope_code", "category_code", "yyyymm", "version"])
    db.execute(stmt)
    return db.scalar(
        select(CostEntry.id).where(
            CostEntry.account_code == ACCOUNT_KEY,
            CostEntry.scope_code == scope_code,
            CostEntry.category_code == category_code,
            CostEntry.yyyymm == yyyymm,
            CostEntry.version == version,
        )
    )


def ensure_plan_version(db: Session, year: int) -> None:
    if not db.scalar(
        select(ManagementPlanVersion.year).where(
            ManagementPlanVersion.year == year, ManagementPlanVersion.plan_no == PLAN_NO
        )
    ):
        db.add(ManagementPlanVersion(year=year, plan_no=PLAN_NO))
        db.flush()


def upsert_entry_months(db: Session, cost_entry_id: int, base_year: int, months: list[float | None]) -> None:
    """months는 12개(1년치)가 기본이지만 12의 배수(24·36…)면 2년차부터 base_year+1, base_year+2…로
    이어서 저장한다 — 기간을 12개월 너머로 늘려 받을 때 쓰는 확장 지점.

    월마다 따로 실행하면 행 하나 저장에 12번(24개월이면 24번) DB 왕복이 나서, 디테일 전체를
    한 문장으로 모아 upsert한다(_bulk_upsert_cost_entries와 같은 이유).

    None인 달은 아예 쓰지 않는다 — 미등록은 "0으로 등록됨"이 아니라 "행이 없음"이어야 조회할 때
    다시 빈칸으로 나온다(공통 RULE: 빈칸 처리)."""
    values = [
        {
            "cost_entry_id": cost_entry_id, "year": base_year + i // 12, "period_type": "MONTH",
            "period": i % 12 + 1, "col_name": "", "amount": amount,
        }
        for i, amount in enumerate(months)
        if amount is not None
    ]
    if not values:
        return
    stmt = pg_insert(CostEntryDetail).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["cost_entry_id", "year", "period_type", "period", "col_name"],
        set_={"amount": stmt.excluded.amount, "updated_at": datetime.now(timezone.utc)},
    )
    db.execute(stmt)


def current_basis_month(db: Session) -> int:
    """forecast(속보) 버전 중 가장 최근(yyyymm 최댓값)의 월 — 지금 편성 기준월.
    yyyymm이 "YYYYMM" 6자리 고정폭이라 문자열 최댓값이 곧 날짜순 최댓값과 같다."""
    latest = db.scalar(
        select(func.max(CostEntry.yyyymm)).where(CostEntry.account_code == ACCOUNT_KEY, CostEntry.version == "forecast")
    )
    return ym_month(latest) if latest else 0
