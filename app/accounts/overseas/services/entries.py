"""해외연구소 비용·경영계획 값의 읽기/쓰기 — cost_entries · management_plan_entries 접근.

**쓰기 경로를 한곳에 모으는 파일**이다 — 비용 값을 쓰는 길이 여기 하나다.

분기·연간(QUARTER/YEAR) 행 규칙: **저장은 연구소가 파일에 적어 보낸 칸만 담고, 월에서 계산해
만들어 두지 않는다.** 월에서 계산할 수 있는 값을 복사해 두면 그 복사본만 틀어진다 — 인력(명)을
합으로 재계산해 분기가 3배·연간이 12배가 된 행이 전체의 96%였다. 보는 쪽은 저장된 칸이 있으면 그 값을,
없으면 월에서 계산한 값을 그린다(core.js withPer → run17 · services/aggregate.to_17). 안 적어 보낸
칸과 월 합이 안 맞는 칸은 업로드 미리보기가 색으로 알린다(shared/core/anomaly.py).
"""

from datetime import datetime, timezone

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.accounts.overseas.cost_groups import AGGREGATE_ROWS
from app.shared.core.periods import ym_month, ym_year
from app.shared.models.common import CostEntry, CostEntryDetail
from app.shared.models.plan import ManagementPlanEntry, ManagementPlanEntryDetail, ManagementPlanVersion
from app.accounts.overseas.services.labs import ACCOUNT_KEY, summable_pks

# 분기 → 그 분기에 드는 월(1-based). cost.py의 QUARTERS와 같은 정의다 — 분기·연간을 두 경로가
# 다르게 자르면 같은 데이터가 다른 합계를 갖는다.
QUARTERS: dict[int, tuple[int, int, int]] = {1: (1, 2, 3), 2: (4, 5, 6), 3: (7, 8, 9), 4: (10, 11, 12)}
# 경영계획 차수를 고르는 규칙이 여기 한 곳에 있다. 차수 상수를 박아 두면 차수가 쌓여도
# (P00 → P01 → P02) 읽기·쓰기가 한 차수만 보게 되고, 확정한 새 차수가 화면·챗봇 어디에도
# 반영되지 않는다.
#
# 규칙(읽기): ★(is_final, 그 연도의 공식 비교 기준)이 지정돼 있으면 그것 → 없으면 **확정된
# (is_confirmed) 차수 중 가장 최근** → 확정된 게 없으면 가장 최근 차수. 마지막 단계는 작성 중인
# 차수를 보여 주는 것이라, 쓰는 쪽은 "어느 차수를 보고 있는지"를 라벨로 밝혀야 한다.
#
# 왜 ★가 확정보다 앞인가: ★는 "이 차수를 올해 비교 기준으로 쓴다"는 사람의 명시적 지정이고,
# 확정은 "그 차수 작성이 끝났다"는 상태다. 둘이 어긋날 때는 지정이 이긴다.
FIRST_PLAN_NO = "P00"  # 그 해 차수가 아예 없을 때 만들어 쓰는 첫 차수


def pick_plan_no(rows) -> str:
    """차수 목록(plan_no · is_final · is_confirmed를 가진 것들) → 적용할 차수. 위 규칙 그대로다.

    ManagementPlanVersion 행과 PlanRevisionOut 둘 다 받는다(필드 이름이 같다) — 규칙을 두 군데에
    적으면 화면이 보는 차수와 챗봇이 보는 차수가 갈린다."""
    ordered = sorted(rows, key=lambda r: r.plan_no)
    if not ordered:
        return ""
    for r in ordered:
        if r.is_final:
            return r.plan_no
    confirmed = [r for r in ordered if r.is_confirmed]
    if confirmed:
        return confirmed[-1].plan_no
    return ordered[-1].plan_no


def plan_version_rows(db: Session, year: int) -> list[ManagementPlanVersion]:
    return list(
        db.scalars(
            select(ManagementPlanVersion)
            .where(ManagementPlanVersion.year == year)
            .order_by(ManagementPlanVersion.plan_no)
        )
    )


def applied_plan_no(db: Session, year: int) -> str:
    """그 해에 **적용되는** 경영계획 차수. 없으면 빈 문자열(= 경영계획 데이터 없음)."""
    return pick_plan_no(plan_version_rows(db, year))


def writable_plan_no(db: Session, year: int) -> str:
    """업로드가 **덮어써도 되는** 차수 = 확정되지 않은 차수 중 가장 최근. 없으면 빈 문자열.

    적용 차수(applied_plan_no)와 일부러 다르게 둔다 — 확정한 차수를 화면 저장이 덮으면 "확정"이
    뜻을 잃고, 이미 보고한 경영계획 대비 숫자가 소급해서 바뀐다(공통 RULE: 지난 버전 무영향)."""
    drafts = [r for r in plan_version_rows(db, year) if not r.is_confirmed]
    return drafts[-1].plan_no if drafts else ""


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


def bulk_upsert_cost_entries(
    db: Session,
    rows: list[tuple[str, str, str, str, list[float | None]]],
    clear_blanks: bool = False,
    periods: dict[tuple[str, str, str, str], list[float | None]] | None = None,
) -> None:
    """(scope_code, version, category_code, yyyymm, months) 목록을 헤더·디테일 각각 단일 upsert
    문으로 한 번에 처리한다 — save_overseas_state가 행마다 get_or_create_entry+월별 insert를
    따로 호출하면 (lab×category 조합 수)×14번 왕복이 나서, 그걸 피하려고 모아서 처리한다.
    같은 키가 두 번 들어오면 (호출부가 겹쳐 보낸 경우) 마지막 값이 이긴다 — 기존의 순차 upsert와 동일한 규칙.

    periods는 (scope_code, version, category_code, yyyymm) -> [1Q,2Q,3Q,4Q,연간]이다. 연구소가 파일에
    적어 보낸 분기·연간 칸을 **그대로** 저장한다. 안 주면(None) 그 칸은 **저장하지 않는다** — 월에서
    계산해 만들어 두지 않는다(머리 주석). 보는 쪽이 저장된 칸이 있으면 그 값을, 없으면 월에서 계산한
    값을 그린다. rows에 없는 계정(= 월값이 없는 소계 계정)이 와도 받는다 — 아래 header_keys를 볼 것.

    clear_blanks는 **빈칸의 뜻**을 고른다. 기본(False)은 "그 달은 건드리지 마라"(부분 갱신)이고,
    True는 "그 달은 아직 안 냈다"라서 저장된 상세 행을 지운다. 파일 업로드가 True를 쓴다 — 연구소가
    칸을 비워 다시 보냈는데 건너뛰기만 하면 지난 업로드의 숫자가 남아 화면이 거짓으로 채워지고,
    0으로 덮으면 "0으로 보고"가 된다. 0.0은 보고된 값이라 지우지 않는다."""
    dedup: dict[tuple[str, str, str, str], list[float | None]] = {
        (scope_code, version, category_code, yyyymm): months
        for scope_code, version, category_code, yyyymm, months in rows
    }
    # 월값 없이 분기·연간만 오는 자리가 있다: 소계 계정(인건비·개발비(A)…)이다. 그 줄의 월 12칸은
    # 하위 합으로 계산하므로 rows에 안 들어오지만, 연구소가 **그 줄의 1Q~4Q·연간 칸에 적어 보낸
    # 숫자**는 그대로 보여줘야 한다(리프 계정과 같은 규칙). 헤더를 같이 만들어 두지 않으면 아래
    # id_by_key에서 못 찾아 그 값이 조용히 사라진다 — 미리보기에는 보이는데 반영하면 없어진다.
    header_keys = list(dedup) + [k for k in (periods or {}) if k not in dedup]
    if not header_keys:
        return

    header_stmt = pg_insert(CostEntry).values(
        [
            {
                "yyyymm": yyyymm, "account_code": ACCOUNT_KEY, "scope_code": scope_code,
                "version": version, "category_code": category_code,
            }
            for scope_code, version, category_code, yyyymm in header_keys
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
    # 비워 보낸 달을 지운다(clear_blanks) — 올린 칸과 같은 규칙으로(연도 넘김 포함) 좌표를 만든다.
    if clear_blanks:
        blanks = [
            (id_by_key[(scope_code, category_code, yyyymm, version)], ym_year(yyyymm) + i // 12, i % 12 + 1)
            for (scope_code, version, category_code, yyyymm), months in dedup.items()
            for i, amount in enumerate(months)
            if amount is None
        ]
        if blanks:
            db.execute(
                delete(CostEntryDetail).where(
                    CostEntryDetail.period_type == "MONTH",
                    CostEntryDetail.col_name == "",
                    tuple_(CostEntryDetail.cost_entry_id, CostEntryDetail.year, CostEntryDetail.period).in_(
                        blanks
                    ),
                )
            )

    if detail_values:
        detail_stmt = pg_insert(CostEntryDetail).values(detail_values)
        detail_stmt = detail_stmt.on_conflict_do_update(
            index_elements=["cost_entry_id", "year", "period_type", "period", "col_name"],
            set_={"amount": detail_stmt.excluded.amount, "updated_at": datetime.now(timezone.utc)},
        )
        db.execute(detail_stmt)

    # 파일이 적어 보낸 분기·연간을 그대로 쓴다. 月 합으로 덮이지 않게 자리를 따로 모아 둔다.
    from_file: set[tuple[int, int]] = set()
    period_values = []
    for key, cells in (periods or {}).items():
        entry_id = id_by_key.get((key[0], key[2], key[3], key[1]))
        if entry_id is None or not cells:
            continue
        year = ym_year(key[3])
        for i, amount in enumerate(cells[:5]):
            if amount is None:
                continue
            kind = ("QUARTER", i + 1) if i < 4 else ("YEAR", 0)
            period_values.append({
                "cost_entry_id": entry_id, "year": year, "period_type": kind[0],
                "period": kind[1], "col_name": "", "amount": amount,
            })
            from_file.add((entry_id, year))
    if period_values:
        period_stmt = pg_insert(CostEntryDetail).values(period_values)
        db.execute(period_stmt.on_conflict_do_update(
            index_elements=["cost_entry_id", "year", "period_type", "period", "col_name"],
            set_={"amount": period_stmt.excluded.amount, "updated_at": datetime.now(timezone.utc)},
        ))

    # 분기·연간은 **연구소가 파일에 적어 보낸 칸만** 저장한다 — 월에서 계산해 만들어 두지 않는다
    # (2026-11-24). 월에서 만들 수 있는 값을 DB에 복사해 두면 그 복사본이 틀어질 뿐이다: 실제로
    # 인력(명)을 합으로 재계산해 분기가 3배·연간이 12배가 된 행이 전체의 96%였다.
    # 보는 쪽은 저장된 칸이 있으면 그 값을, 없으면 월에서 계산한 값을 그린다(core.js withPer →
    # run17 · aggregate.to_17). 안 적어 보낸 칸과 월 합이 안 맞는 칸은 업로드 미리보기가
    # 색으로 알린다(anomaly: blank · missing_cell · period_mismatch).


def bulk_upsert_plan_entries(
    db: Session, rows: list[tuple[str, str, int, list[float | None]]], plan_no: str
) -> None:
    """(scope_code, category_code, year, months) 목록을 헤더·디테일 각각 단일 upsert 문으로 처리한다
    (_bulk_upsert_cost_entries와 같은 이유).

    plan_no는 **어느 차수에 쓰는가**이고 생략할 수 없다 — 기본값을 두면 호출부 하나가 빠뜨렸을 때
    조용히 다른 차수에 쌓인다(전에 모든 저장이 P00으로 들어갔던 자리다)."""
    if not plan_no:
        raise ValueError("경영계획을 저장할 차수(plan_no)가 필요하다")
    dedup = {(r[0], r[1], r[2]): r[3] for r in rows}
    if not dedup:
        return
    for year in {year for _, _, year in dedup}:
        ensure_plan_version(db, year, plan_no)

    header_stmt = pg_insert(ManagementPlanEntry).values(
        [
            {
                "account_code": ACCOUNT_KEY, "scope_code": scope_code, "year": year,
                "plan_no": plan_no, "category_code": category_code,
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


def ensure_plan_version(db: Session, year: int, plan_no: str) -> None:
    """그 해 그 차수가 없으면 만든다(작성 중 상태로). 확정 여부는 건드리지 않는다."""
    if not db.scalar(
        select(ManagementPlanVersion.year).where(
            ManagementPlanVersion.year == year, ManagementPlanVersion.plan_no == plan_no
        )
    ):
        db.add(ManagementPlanVersion(year=year, plan_no=plan_no))
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


def filled_cost_yyyymm(
    db: Session, version: str, before: str | None = None, after: str | None = None
) -> str:
    """그 버전에서 **합산 대상 연구소 데이터가 실제로 들어 있는** 가장 늦은 편성 연월. 없으면 빈 문자열.

    왜 max(yyyymm)이 아닌가 (2026-11-23): 그냥 최댓값을 집으면 연구소 행이 하나도 없는 달도 "그
    버전이 있는 달"로 집힌다. 실제로 11월 속보에 값이 없는 줄 하나만 들어간 적이 있는데,
    그것 때문에 편성 기준월이 11월로 밀려서 화면이 11월로 열리고 증감분석은 "26.11 속보 데이터가
    없다"로 404가 났다 — 같은 달을 두고 한쪽은 "있다", 계산은 "없다"고 답한 것이다.

    그래서 판정 기준을 계산이 실제로 읽는 집합(versions.load_cost_version → scope_code가 합산 대상)
    과 같게 맞춘다. 합산 대상은 그 달 시점 기준정보로 가린다(labs.summable_pks) — 통화·활성 여부가
    달마다 다를 수 있어서 달을 먼저 알아야 하고, 그래서 최댓값 하나를 묻는 대신 연월을 내림차순으로
    훑어 처음 걸리는 달을 돌려준다(보통 첫 번째에서 끝난다).

    추가 행(label이 있는 행)도 "데이터 있음"으로 본다 — 연구소가 올린 금액이라 고정 계정이 비어도
    그 달은 일이 시작된 달이다(증감분석 표도 추가 행 줄에 그 금액을 보여준다).
    """
    stmt = (
        select(CostEntry.yyyymm)
        .where(CostEntry.account_code == ACCOUNT_KEY, CostEntry.version == version)
        .group_by(CostEntry.yyyymm)
        .order_by(CostEntry.yyyymm.desc())
    )
    if before is not None:
        stmt = stmt.where(CostEntry.yyyymm < before)
    if after is not None:
        stmt = stmt.where(CostEntry.yyyymm > after)
    for ym in db.scalars(stmt):
        if has_summable_rows(db, version, ym):
            return ym
    return ""


def has_summable_rows(db: Session, version: str, ym: str) -> bool:
    """그 버전·연월에 합산 대상 연구소 행이 하나라도 있나 — filled_cost_yyyymm과 같은 판정이다
    (달을 알고 있을 때 쓴다)."""
    if not ym:
        return False
    return bool(
        db.scalar(
            select(CostEntry.id)
            .where(
                CostEntry.account_code == ACCOUNT_KEY,
                CostEntry.version == version,
                CostEntry.yyyymm == ym,
                CostEntry.scope_code.in_(summable_pks(db, f"{ym[:4]}-{ym[4:6]}")),
            )
            .limit(1)
        )
    )


def current_basis_yyyymm(db: Session) -> str:
    """지금 편성 기준 연월 "YYYYMM" — forecast(속보) 중 가장 최근. 아무것도 없으면 빈 문자열.

    current_basis_month가 월만 돌려주는 것과 달리 연도까지 필요한 곳이 있다. "지나간 칸은 건드리지
    않는다"를 판정할 때가 그렇다 — 월만 보면 연말에 202512와 202601을 거꾸로 비교한다."""
    return filled_cost_yyyymm(db, "forecast")


def current_basis_month(db: Session) -> int:
    """지금 편성 기준월 — 속보 데이터가 실제로 들어 있는 가장 늦은 달의 월(filled_cost_yyyymm)."""
    latest = current_basis_yyyymm(db)
    return ym_month(latest) if latest else 0
