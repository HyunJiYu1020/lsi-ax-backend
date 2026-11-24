"""기준정보 조회·저장 — 비용계정 · 자산 Class 매핑 · 부서코드 · PLM 과제. 전 계정 공용.

화면(기준정보 › 비용계정 / 부서코드 / PLM 과제, 감가상각비 [기준정보] 팝업)이 이 네 표를 읽고
쓴다. "어느 버전이 어느 표를 보는가"는 app/shared/core/reference.py가 정한다 — 여기는 그걸
HTTP로 노출하고, 표 전체를 교체하는 PUT만 둔다.

왜 한 줄씩 고치는 PATCH가 없는가: 화면은 표를 통째로 고친 뒤 [저장]을 누르는 그리드다(엑셀
업로드도 "이 탭 전체가 파일 내용으로 바뀝니다"). 한 줄 단위 API를 두면 화면의 저장 한 번이
수십 개 요청으로 갈라져서, 중간에 하나가 실패하면 표가 반만 바뀐 상태로 남는다.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.shared.core import reference as ref
from app.shared.schemas.reference import (
    AssetClassesIn,
    AssetClassesOut,
    AssetClassRow,
    CostElementRow,
    CostElementsIn,
    CostElementsOut,
    DeptCodeRow,
    DeptCodesIn,
    DeptCodesOut,
    PlmTaskRow,
    PlmTasksIn,
    PlmTasksOut,
    ChangeLogOut,
    ChangeRowOut,
    ProductRow,
    ProductsIn,
    ProductsOut,
)

router = APIRouter()

_V_DESC = '버전 키 — "YYYY-MM-forecast" · "YYYY-MM-actual" · "YYYY-plan-NN"'


def _cost_row(r) -> CostElementRow:
    return CostElementRow(gb=r.gb, code=r.code, a1=r.a1, a2=r.a2, a3=r.a3, a4=r.a4, name=r.name)


def _dept_row(r) -> DeptCodeRow:
    return DeptCodeRow(
        code=r.code, job=r.job_category, name=r.name, bu=r.budget_unit, team=r.team_s, t2=r.team_m,
        t3=r.team_l, fa=r.functional_area, fam3=r.fam3, upd=r.updated_on,
        rep="Y" if r.is_rep else "N", state=r.state,
    )


def _prod_row(r) -> ProductRow:
    return ProductRow(
        device=r.device, yyyy=r.yyyy, sel_code=r.sel_code, section=r.section, sale_device=r.sale_device,
        gr_device=r.gr_device, fam1_code=r.fam1_code, fam2_code=r.fam2_code, fam3_code=r.fam3_code,
        fam4_code=r.fam4_code,
    )


def _plm_row(r) -> PlmTaskRow:
    return PlmTaskRow(
        plm=r.task_code, prod=r.product, name=r.name, nick=r.nickname, st=r.status, ps=r.plan_start,
        pe=r.plan_end, stg=r.stage, ty=r.target_type, ty2=r.target_subtype, ss=r.stage_start,
        se=r.stage_end, pm=r.project_manager, pl=r.project_leader, own=r.owner, cr=r.created_on,
    )


# ---------- 비용계정 ----------


@router.get("/reference/cost-elements", response_model=CostElementsOut)
def get_cost_elements(
    year: int | None = None,  # 연도표를 읽을 때. 화면의 기준정보 › 비용계정이 쓴다
    version: str | None = None,  # 그 버전이 보는 계정표(스냅샷)를 읽을 때. 형식은 _V_DESC
    db: Session = Depends(get_db),
) -> CostElementsOut:
    """`version`을 주면 **그 버전이 보는 계정표**(처음 조회하면 그 시점 연도표를 복사해 고정),
    `year`를 주면 **고치는 대상인 연도표**를 돌려준다.

    둘을 가른 이유가 이 표의 핵심이다 — 연도표는 계속 고치고, 버전은 만들 때의 표를 계속 쓴다.
    편성값에 계정명을 붙이는 쪽(집계·증감분석·엑셀)은 반드시 `version`으로 읽어야 한다. `year`로
    읽으면 11월에 고친 계정명이 9월 속보 표에 소급해서 나타난다."""
    if version:
        v = ref.parse_version(version)
        rows = ref.cost_elements_of_version(db, v)
        db.commit()  # 스냅샷을 방금 만들었을 수 있다
        return CostElementsOut(
            version=v.key,
            source_year=rows[0].source_year if rows else None,
            rows=[_cost_row(r) for r in rows],
        )
    y = year or 0
    return CostElementsOut(year=y, rows=[_cost_row(r) for r in ref.cost_elements(db, y)])


@router.put("/reference/cost-elements", response_model=CostElementsOut)
def put_cost_elements(body: CostElementsIn, db: Session = Depends(get_db)) -> CostElementsOut:
    """그 연도 계정표를 전체 교체한다. **이후에 만드는 버전부터** 반영된다(이미 만든 버전은
    자기 스냅샷을 계속 쓴다) — 화면 안내문과 같은 규칙이다."""
    rows = ref.replace_cost_elements(
        db, body.year, [r.model_dump() for r in body.rows], how=body.how, actor=body.actor, note=body.note
    )
    db.commit()
    return CostElementsOut(year=body.year, rows=[_cost_row(r) for r in rows])


# ---------- 자산 Class 매핑 ----------


@router.get("/reference/asset-classes", response_model=AssetClassesOut)
def get_asset_classes(db: Session = Depends(get_db)) -> AssetClassesOut:
    """자산 Class → 비용계정 매핑. 감가상각비만 쓰고, 버전·연도 구분이 없다."""
    return AssetClassesOut(
        rows=[
            AssetClassRow(cls=r.asset_class, text=r.class_text, code=r.code, name=r.name)
            for r in ref.asset_classes(db)
        ]
    )


@router.put("/reference/asset-classes", response_model=AssetClassesOut)
def put_asset_classes(body: AssetClassesIn, db: Session = Depends(get_db)) -> AssetClassesOut:
    rows = ref.replace_asset_classes(
        db, [r.model_dump() for r in body.rows], how=body.how, actor=body.actor, note=body.note
    )
    db.commit()
    return AssetClassesOut(
        rows=[AssetClassRow(cls=r.asset_class, text=r.class_text, code=r.code, name=r.name) for r in rows]
    )


# ---------- 부서코드 ----------


@router.get("/reference/dept-codes", response_model=DeptCodesOut)
def get_dept_codes(
    version: str = Query(..., description=_V_DESC), db: Session = Depends(get_db)
) -> DeptCodesOut:
    """그 버전의 부서코드표. 아직 없는 버전이면 **직전 버전을 복사해** 만들고 복사 출처를 같이
    알려준다 — 매달 18개 부서를 다시 입력하게 만들 수 없고, 복사한 사실을 숨기면 담당자가 직전
    달 표를 이번 달 확정값으로 착각한다."""
    v = ref.parse_version(version)
    rows, copied = ref.versioned_rows(db, "dept", v)
    db.commit()
    return DeptCodesOut(version=v.key, copied_from=copied, rows=[_dept_row(r) for r in rows])


@router.put("/reference/dept-codes", response_model=DeptCodesOut)
def put_dept_codes(body: DeptCodesIn, db: Session = Depends(get_db)) -> DeptCodesOut:
    """그 버전의 부서코드표를 전체 교체한다. 지난 버전은 그대로 둔다."""
    v = ref.parse_version(body.version)
    rows = ref.replace_versioned_rows(
        db, "dept", v, [r.model_dump() for r in body.rows], how=body.how, actor=body.actor, note=body.note
    )
    db.commit()
    return DeptCodesOut(version=v.key, rows=[_dept_row(r) for r in rows])


# ---------- PLM 과제 ----------


@router.get("/reference/plm-tasks", response_model=PlmTasksOut)
def get_plm_tasks(
    version: str = Query(..., description=_V_DESC),
    status: str = "",  # 상태로 걸러 받기(예: Drop). 비우면 전체
    db: Session = Depends(get_db),
) -> PlmTasksOut:
    """그 버전의 PLM 과제 목록(없으면 직전 버전 복사). `status=Drop`으로 Drop 과제만 받을 수 있다
    — 위탁용역비 [검증]이 쓰는 모양이다. 전체도 받을 수 있게 둔 이유는, 목록에 아예 없는 과제와
    Drop이 아닌 과제를 담당자가 구분해야 하기 때문이다."""
    v = ref.parse_version(version)
    rows, copied = ref.versioned_rows(db, "plm", v)
    db.commit()
    if status:
        rows = [r for r in rows if r.status.lower() == status.strip().lower()]
    return PlmTasksOut(version=v.key, copied_from=copied, rows=[_plm_row(r) for r in rows])


@router.put("/reference/plm-tasks", response_model=PlmTasksOut)
def put_plm_tasks(body: PlmTasksIn, db: Session = Depends(get_db)) -> PlmTasksOut:
    v = ref.parse_version(body.version)
    rows = ref.replace_versioned_rows(
        db, "plm", v, [r.model_dump() for r in body.rows], how=body.how, actor=body.actor, note=body.note
    )
    db.commit()
    return PlmTasksOut(version=v.key, rows=[_plm_row(r) for r in rows])


# ---------- 제품정보(SC_FMMS) ----------


@router.get("/reference/products", response_model=ProductsOut)
def get_products(
    version: str = Query(..., description=_V_DESC), db: Session = Depends(get_db)
) -> ProductsOut:
    """그 버전의 제품정보(제품 → 제품군). 없는 버전이면 직전 버전을 복사해 만든다.

    부서코드·PLM 과제와 같은 규칙인 이유: 신제품이 매달 들어오고 제품군 코드도 바뀐다. 9월 속보를
    11월 제품표로 다시 계산하면 이미 보고한 정률기술료 배부가 바뀐다."""
    v = ref.parse_version(version)
    rows, copied = ref.versioned_rows(db, "product", v)
    db.commit()
    return ProductsOut(version=v.key, copied_from=copied, rows=[_prod_row(r) for r in rows])


@router.put("/reference/products", response_model=ProductsOut)
def put_products(body: ProductsIn, db: Session = Depends(get_db)) -> ProductsOut:
    """그 버전의 제품정보를 전체 교체한다(SC_FMMS 업로드 = 전체 교체)."""
    v = ref.parse_version(body.version)
    rows = ref.replace_versioned_rows(
        db, "product", v, [r.model_dump() for r in body.rows], how=body.how, actor=body.actor, note=body.note
    )
    db.commit()
    return ProductsOut(version=v.key, rows=[_prod_row(r) for r in rows])


# ---------- 변경 이력 ----------


def _log_out(log, rows=()) -> ChangeLogOut:
    return ChangeLogOut(
        id=log.id, table_key=log.table_key, table_label=ref.CHANGE_LABELS.get(log.table_key, log.table_key),
        scope=log.scope, how=log.how, actor=log.actor, note=log.note, rows_before=log.rows_before,
        rows_after=log.rows_after, added=log.added, removed=log.removed, changed=log.changed,
        detail_omitted=log.detail_omitted, summary=log.summary, changed_at=log.changed_at,
        rows=[ChangeRowOut(row_key=r.row_key, change=r.change, before=r.before or {}, after=r.after or {}) for r in rows],
    )


@router.get("/reference/changes", response_model=list[ChangeLogOut])
def list_changes(
    table: str | None = None,  # cost_element | asset_class | dept | plm | product | close_dept_rule
    scope: str | None = None,  # 연도("2026") 또는 버전 키("2026-09-forecast")
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[ChangeLogOut]:
    """기준정보 변경 목록(최근 순). 바뀐 행 상세는 빼고 요약만 — 목록 화면이 쓰는 모양이다.

    여기서 답하는 질문: "부서코드가 18행에서 3행으로 왜 줄었나"(요약과 how·actor를 본다),
    "이 표를 마지막으로 누가 언제 고쳤나"."""
    if table and table not in ref.CHANGE_SPECS:
        raise HTTPException(400, f"table은 {' | '.join(ref.CHANGE_SPECS)} 중 하나여야 합니다: {table!r}")
    return [_log_out(log) for log in ref.changes(db, table, scope, limit)]


@router.get("/reference/changes/{log_id}", response_model=ChangeLogOut)
def get_change(log_id: int, db: Session = Depends(get_db)) -> ChangeLogOut:
    """변경 한 건의 **바뀐 행 before/after**. 상한을 넘겨 상세를 남기지 않은 건은 `detail_omitted`가
    True이고 rows가 빈 목록이다 — "상세 없음"과 "안 바뀜"을 화면이 구분해야 한다."""
    log = db.get(ref.RefChangeLog, log_id)
    if log is None:
        raise HTTPException(404, f"그런 변경 이력이 없습니다: {log_id}")
    return _log_out(log, ref.change_rows(db, log_id))
