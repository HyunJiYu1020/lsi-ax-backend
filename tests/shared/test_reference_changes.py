"""기준정보 변경 이력 — "부서코드가 18행에서 3행으로 왜 줄었나"에 답할 수 있는가.

기준정보 다섯 표는 모두 표 전체 교체로 저장된다. 그래서 이력이 없으면 "한 줄 오타를 고친 것"과
"엑셀을 잘못 올려 표가 통째로 갈린 것"을 구분할 수 없다. 여기서 지키는 것:

- **변경이 없는 저장은 기록하지 않는다**(화면은 [저장]마다 PUT을 보낸다 — 그대로 쌓으면 이력이
  "아무 일도 없었음"으로 가득 차서 진짜 변경을 못 찾는다)
- 바뀐 행만 before/after로 남는다(추가는 before 없음, 삭제는 after 없음)
- 상한을 넘으면 **생략했다는 사실**을 남긴다(상세 없음과 안 바뀜은 다르다)
- 같은 Cost Elem.이 두 줄 있어도 둘을 구분한다
- 전표 분류 기준(close_dept_rules)도 같은 이력에 들어간다
"""

import pytest
from fastapi import HTTPException

from app.shared.core import reference as ref
from app.shared.endpoints.reference import (
    get_change,
    list_changes,
    put_cost_elements,
    put_dept_codes,
    put_products,
)
from app.shared.schemas.reference import (
    CostElementRow,
    CostElementsIn,
    DeptCodeRow,
    DeptCodesIn,
    ProductRow,
    ProductsIn,
)

Y = 2032
V1 = f"{Y}-09-forecast"


@pytest.fixture(autouse=True)
def clean(db):
    from app.shared.models.reference import (
        RefChangeLog, RefChangeRow, RefCostElement, RefDeptCode, RefProduct,
    )

    db.query(RefChangeRow).delete(synchronize_session=False)
    db.query(RefChangeLog).delete(synchronize_session=False)
    for model, col in ((RefCostElement, "year"), (RefDeptCode, "basis_year"), (RefProduct, "basis_year")):
        db.query(model).filter(getattr(model, col) == Y).delete(synchronize_session=False)
    db.flush()
    return db


def _dept(db, codes, **kw):
    return put_dept_codes(
        DeptCodesIn(version=V1, rows=[DeptCodeRow(code=c, name=f"부서-{c}") for c in codes], **kw), db
    )


def test_no_change_no_log(db):
    """같은 내용을 다시 저장하면 이력이 늘지 않는다."""
    _dept(db, ["C1", "C2"])
    assert len(list_changes(table="dept", db=db)) == 1
    _dept(db, ["C1", "C2"])
    assert len(list_changes(table="dept", db=db)) == 1, "변경 없는 저장이 이력을 늘리면 진짜 변경을 못 찾는다"


def test_shrink_is_visible_with_who_and_how(db):
    """18행 → 3행처럼 줄어든 저장은 요약·건수·어떻게·누가로 바로 보인다."""
    _dept(db, [f"C{i}" for i in range(18)], actor="유현지", how="screen")
    _dept(db, ["C0", "C1", "C2"], actor="유현지", how="upload", note="부서코드_2611.xlsx")

    logs = list_changes(table="dept", scope=V1, db=db)
    last = logs[0]
    assert (last.rows_before, last.rows_after, last.removed) == (18, 3, 15)
    assert last.how == "upload" and last.note == "부서코드_2611.xlsx" and last.actor == "유현지"
    assert "18행 → 3행" in last.summary and "삭제 15" in last.summary

    detail = get_change(last.id, db=db)
    assert len(detail.rows) == 15
    assert {r.change for r in detail.rows} == {"del"}
    # 삭제된 행은 after가 비어 있고 before에 사라진 값이 남는다
    gone = next(r for r in detail.rows if r.row_key == "C5")
    assert gone.after == {} and gone.before["name"] == "부서-C5"


def test_changed_cells_only(db):
    """값이 바뀐 행은 **바뀐 칸만** 남는다 — 40칸짜리 행 전체를 복사하면 이력이 표보다 커진다."""
    _dept(db, ["C1"])
    put_dept_codes(
        DeptCodesIn(version=V1, rows=[DeptCodeRow(code="C1", name="부서-C1(변경)", fa="6000")], actor="유현지"), db
    )
    rows = get_change(list_changes(table="dept", db=db)[0].id, db=db).rows
    assert len(rows) == 1 and rows[0].change == "chg"
    assert set(rows[0].after) == {"name", "functional_area"}
    assert rows[0].before["name"] == "부서-C1" and rows[0].after["name"] == "부서-C1(변경)"


def test_duplicate_cost_element_rows_are_distinguished(db):
    """같은 Cost Elem.이 두 줄 있는 표에서도 둘을 구분한다(감가상각비 53450300이 실제로 그렇다)."""
    put_cost_elements(
        CostElementsIn(
            year=Y,
            rows=[
                CostElementRow(code="53450300", gb="감가상각비", name="기계장치"),
                CostElementRow(code="53450300", gb="감가상각비", name="신규투자 기본"),
            ],
        ),
        db,
    )
    put_cost_elements(
        CostElementsIn(
            year=Y,
            rows=[
                CostElementRow(code="53450300", gb="감가상각비", name="기계장치"),
                CostElementRow(code="53450300", gb="감가상각비", name="신규투자 기본(수정)"),
            ],
        ),
        db,
    )
    detail = get_change(list_changes(table="cost_element", scope=str(Y), db=db)[0].id, db=db)
    assert [r.row_key for r in detail.rows] == ["53450300|감가상각비#2"]
    assert detail.rows[0].after["name"] == "신규투자 기본(수정)"


def test_detail_omitted_is_recorded(db, monkeypatch):
    """상세 상한을 넘으면 '생략했다'를 남긴다 — 조용히 자르면 상세 없음이 '안 바뀜'으로 읽힌다."""
    monkeypatch.setattr(ref, "CHANGE_DETAIL_LIMIT", 3)
    # 새 버전 제품정보는 직전 버전을 복사해 시작하므로(기준정보 규칙) 이 저장은 "그 복사본을
    # 10행으로 교체"가 된다 — 바뀐 행이 상한을 넘는 상황 그대로다.
    put_products(ProductsIn(version=V1, rows=[ProductRow(device=f"D{i}") for i in range(10)]), db)
    log = list_changes(table="product", db=db)[0]
    assert log.added == 10 and log.detail_omitted is True
    assert "상세 생략" in log.summary
    assert get_change(log.id, db=db).rows == [], "상세를 생략했으면 행은 비어 있고 플래그로만 알린다"


def test_scope_separates_versions_and_years(db):
    """적용 범위가 섞이지 않는다 — 9월 속보 부서코드 이력이 10월 칸에 보이면 안 된다."""
    _dept(db, ["C1"])
    put_dept_codes(DeptCodesIn(version=f"{Y}-10-forecast", rows=[DeptCodeRow(code="C9")]), db)
    assert [l.scope for l in list_changes(table="dept", scope=V1, db=db)] == [V1]
    assert [l.scope for l in list_changes(table="dept", scope=f"{Y}-10-forecast", db=db)] == [f"{Y}-10-forecast"]


def test_label_and_bad_table(db):
    _dept(db, ["C1"])
    assert list_changes(table="dept", db=db)[0].table_label == "부서코드"
    with pytest.raises(HTTPException) as e:
        list_changes(table="없는표", db=db)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        get_change(999_999_999, db=db)
    assert e.value.status_code == 404


def test_close_dept_rule_change_is_logged(db):
    """전표 분류 기준도 같은 이력에 들어간다 — 바뀌면 그 달 전표가 다른 계정으로 간다."""
    from app.accounts.overseas.endpoints import close as cl
    from app.shared.schemas.close import DeptRule

    # 시드에 없는 계정코드로 '추가'를 본다(시드에 있는 코드로 하면 note만 바뀌어 '변경'이 된다 —
    # 그것도 맞는 동작이지만 추가 경로를 확인하려면 새 코드가 필요하다).
    cl.put_dept_rule(DeptRule(cost_center="", account_code="99999999", account_key="market", note="테스트"), db=db)
    logs = list_changes(table="close_dept_rule", db=db)
    assert logs and logs[0].added == 1 and logs[0].table_label == "전표 분류 기준"
    assert get_change(logs[0].id, db=db).rows[0].after["account_key"] == "market"

    cl.delete_dept_rule(account_code="99999999", cost_center="", db=db)
    assert list_changes(table="close_dept_rule", db=db)[0].removed == 1
