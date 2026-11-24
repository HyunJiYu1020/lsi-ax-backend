"""기준정보(비용계정 · 자산 Class 매핑 · 부서코드 · PLM 과제) — 버전 규칙이 핵심이다.

이 표들의 값이 틀리면 금액이 틀리는 게 아니라 **지난 달 보고서가 조용히 달라진다**. 그래서
테스트도 "지금 값이 맞는가"보다 "지난 버전이 그대로인가"를 지킨다:
- 연도 계정표를 고쳐도 **이미 연 버전**의 계정명은 안 바뀐다(스냅샷)
- 새 버전 부서코드·PLM 과제는 **직전 버전 복사**로 시작하고, 복사한 사실을 알려준다
- 경영계획 버전은 월 버전(속보·실적)을 복사해 오지 않는다
"""

import pytest
from fastapi import HTTPException

from app.shared.core.reference import parse_version
from app.shared.endpoints.reference import (
    get_cost_elements,
    get_dept_codes,
    get_plm_tasks,
    get_products,
    put_cost_elements,
    put_dept_codes,
    put_plm_tasks,
    put_products,
)
from app.shared.schemas.reference import (
    CostElementRow,
    CostElementsIn,
    DeptCodeRow,
    DeptCodesIn,
    PlmTaskRow,
    PlmTasksIn,
    ProductRow,
    ProductsIn,
)

# 시드 연도(2026)와 겹치지 않는 연도에서 논다 — 개발 DB의 실제 기준정보를 건드리지 않기 위해서다
# (db 픽스처가 롤백하긴 하지만, 테스트가 읽는 값까지 시드에 의존하면 시드가 바뀔 때 깨진다).
Y = 2031
V1 = f"{Y}-09-forecast"
V1A = f"{Y}-09-actual"
V2 = f"{Y}-10-forecast"


@pytest.fixture(autouse=True)
def clean_test_year(db):
    """테스트 연도(Y·Y+1) 행을 먼저 비운다 — 개발 DB에 그 연도 행이 남아 있으면(화면·수동 호출로
    생길 수 있다) 스냅샷 고정 테스트가 남의 데이터를 읽고 깨진다. db 픽스처가 롤백하므로 이
    삭제도 테스트 밖으로 나가지 않는다."""
    from app.shared.models.reference import (
        RefCostElement, RefCostElementSnapshot, RefDeptCode, RefPlmTask, RefProduct,
    )

    for model, col in (
        (RefCostElement, "year"), (RefCostElementSnapshot, "basis_year"), (RefDeptCode, "basis_year"),
        (RefPlmTask, "basis_year"), (RefProduct, "basis_year"),
    ):
        db.query(model).filter(getattr(model, col).in_([Y, Y + 1])).delete(synchronize_session=False)
    db.flush()
    return db


def _cost(db, *codes):
    put_cost_elements(
        CostElementsIn(year=Y, rows=[CostElementRow(code=c, name=f"계정-{c}", gb="감가상각비") for c in codes]), db
    )


def _dept(db, version, *codes, rep="Y"):
    put_dept_codes(
        DeptCodesIn(
            version=version,
            rows=[DeptCodeRow(code=c, name=f"부서-{c}", team="SOC개발실", fa="6000", rep=rep) for c in codes],
        ),
        db,
    )


def test_version_key_format():
    """버전 키는 세 가지 모양만 받는다. 화면 id("2026-09-S")를 그대로 보내면 막힌다 —
    조용히 다른 버전을 읽어 주면 담당자는 자기 버전 표를 보고 있다고 믿는다."""
    assert (parse_version("2026-09-forecast").year, parse_version("2026-09-forecast").seq) == (2026, 9)
    assert parse_version("2026-plan-01").kind == "plan"
    for bad in ("2026-09-S", "2026-13-forecast", "2026-09", "", "2026-plan"):
        with pytest.raises(HTTPException) as e:
            parse_version(bad)
        assert e.value.status_code == 400


def test_cost_elements_keep_row_order_and_duplicates(db):
    """표 순서가 키다 — 같은 Cost Elem.이 두 줄 있어도 둘 다 남고 순서도 그대로다.
    (감가상각비 53450300은 기계장치 Class와 신규투자 기본계정으로 실제 두 번 나온다.)"""
    _cost(db, "53450300", "53450600", "53450300")
    rows = get_cost_elements(year=Y, version=None, db=db).rows
    assert [r.code for r in rows] == ["53450300", "53450600", "53450300"]


def test_version_snapshot_is_frozen(db):
    """버전이 보는 계정표는 그 버전을 처음 연 시점에 고정된다 — 연도표를 나중에 고쳐도
    지난 버전 계정명은 그대로여야 한다(공통 RULE: 기준정보 변경은 변경한 버전부터)."""
    _cost(db, "53450300")
    before = get_cost_elements(year=None, version=V1, db=db)
    assert [r.name for r in before.rows] == ["계정-53450300"]
    assert before.source_year == Y

    # 연도표를 통째로 바꾼다(이름 변경 + 행 추가)
    put_cost_elements(
        CostElementsIn(
            year=Y,
            rows=[CostElementRow(code="53450300", name="감가상각비-기계장치(변경)"), CostElementRow(code="54370700", name="위탁용역비")],
        ),
        db,
    )

    again = get_cost_elements(year=None, version=V1, db=db)
    assert [r.name for r in again.rows] == ["계정-53450300"], "이미 연 버전이 바뀐 계정표를 따라가면 안 된다"
    # 이후에 만드는 버전부터 반영된다
    new_ver = get_cost_elements(year=None, version=V2, db=db)
    assert [r.name for r in new_ver.rows] == ["감가상각비-기계장치(변경)", "위탁용역비"]


def test_cost_elements_version_without_year_table(db):
    """그 버전 연도의 계정표가 없으면 그 이하 가장 최근 연도표를 떠 온다 — 1월 속보를
    새해 계정표보다 먼저 여는 게 실무 순서라서, 여기서 빈 표를 주면 계정명이 전부 사라진다."""
    _cost(db, "53450300")
    out = get_cost_elements(year=None, version=f"{Y + 1}-01-forecast", db=db)
    assert out.source_year == Y
    assert [r.code for r in out.rows] == ["53450300"]


def test_dept_codes_copy_from_previous_version(db):
    """새 버전 부서코드는 직전 버전을 복사해 시작하고, 어느 버전에서 복사했는지 알려준다.
    복사 사실을 숨기면 담당자가 직전 달 표를 이번 달 확정값으로 착각한다."""
    _dept(db, V1, "C10I3000", "C10I3200")

    # 같은 달 실적은 그 달 속보를 복사한다(프런트 verPrevOf와 같은 순서)
    act = get_dept_codes(version=V1A, db=db)
    assert act.copied_from == V1
    assert [r.code for r in act.rows] == ["C10I3000", "C10I3200"]

    # 복사본은 그 버전 것이다 — 고쳐도 직전 버전은 그대로
    _dept(db, V1A, "C10I3000")
    assert [r.code for r in get_dept_codes(version=V1, db=db).rows] == ["C10I3000", "C10I3200"]

    # 10월 속보는 (중간 버전을 안 열었어도) 가장 최근 버전에서 복사한다
    nxt = get_dept_codes(version=V2, db=db)
    assert nxt.copied_from == V1A
    assert [r.code for r in nxt.rows] == ["C10I3000"]


def test_plan_version_does_not_copy_monthly(db):
    """경영계획 버전은 속보·실적을 복사해 오지 않는다 — P01이 전월 속보 표를 들고 오면
    경영계획 편성이 월 편성의 조직 상태에 끌려간다."""
    _dept(db, V1, "C10I3000")
    plan = get_dept_codes(version=f"{Y}-plan-01", db=db)
    assert plan.copied_from == ""
    assert plan.rows == []


def test_dept_codes_reject_duplicate_code(db):
    """부서코드 중복은 막는다. 조용히 한 줄을 버리면 담당자는 저장됐다고 믿고 그 부서 금액이
    맵핑에서 사라진다."""
    with pytest.raises(HTTPException) as e:
        put_dept_codes(
            DeptCodesIn(
                version=V1,
                rows=[DeptCodeRow(code="C10I3000"), DeptCodeRow(code="C10I3000", name="중복")],
            ),
            db,
        )
    assert e.value.status_code == 400
    assert "중복" in e.value.detail


def test_dept_rep_and_state_normalized(db):
    """대표코드·사용/폐기는 업로드 엑셀의 표기 흔들림을 흡수한다("Yes" · "대표" → Y)."""
    put_dept_codes(
        DeptCodesIn(
            version=V1,
            rows=[
                DeptCodeRow(code="C10I3000", rep="Yes", state="사용"),
                DeptCodeRow(code="C10I3010", rep="", state="폐기"),
                DeptCodeRow(code="C10I3110", rep="대표", state=""),
            ],
        ),
        db,
    )
    rows = {r.code: r for r in get_dept_codes(version=V1, db=db).rows}
    assert rows["C10I3000"].rep == "Y" and rows["C10I3000"].state == "사용"
    assert rows["C10I3010"].rep == "N" and rows["C10I3010"].state == "폐기"
    assert rows["C10I3110"].rep == "Y" and rows["C10I3110"].state == "사용"


def test_plm_tasks_status_filter(db):
    """PLM 과제는 전체를 받아 두고 status로 Drop만 거른다 — Drop만 저장하면 "목록에 없는
    과제"와 "Drop이 아닌 과제"를 담당자가 구분할 수 없다."""
    put_plm_tasks(
        PlmTasksIn(
            version=V1,
            rows=[
                PlmTaskRow(plm="S5K3J1-R02", st="Drop", prod="IMAGE SENSOR"),
                PlmTaskRow(plm="S5E9965-A01", st="정상진행"),
                PlmTaskRow(plm="S6E3HA-L00", st="Drop"),
            ],
        ),
        db,
    )
    assert len(get_plm_tasks(version=V1, status="", db=db).rows) == 3
    dropped = get_plm_tasks(version=V1, status="drop", db=db).rows  # 대소문자 무관
    assert [r.plm for r in dropped] == ["S5K3J1-R02", "S6E3HA-L00"]


def test_products_are_versioned_and_copy_forward(db):
    """제품정보도 버전별이다 — 신제품이 매달 들어오고 제품군 코드도 바뀐다. 9월 속보를 11월
    제품표로 다시 계산하면 이미 보고한 정률기술료 배부가 바뀐다."""
    put_products(
        ProductsIn(
            version=V1,
            rows=[
                ProductRow(device="SA59955", sale_device="SA59955XA0", fam3_code="MAP_FLAG"),
                ProductRow(device="SC59965", fam3_code="MAP_PRM"),
            ],
        ),
        db,
    )
    cur = get_products(version=V1, db=db)
    assert [r.device for r in cur.rows] == ["SA59955", "SC59965"]
    assert cur.rows[0].fam3_code == "MAP_FLAG"

    nxt = get_products(version=V2, db=db)
    assert nxt.copied_from == V1
    assert [r.device for r in nxt.rows] == ["SA59955", "SC59965"]

    # 다음 버전에서 고쳐도 지난 버전은 그대로여야 한다.
    put_products(ProductsIn(version=V2, rows=[ProductRow(device="SA59955", fam3_code="MAP_MID")]), db)
    assert get_products(version=V1, db=db).rows[0].fam3_code == "MAP_FLAG"


def test_products_reject_empty_device(db):
    with pytest.raises(HTTPException) as e:
        put_products(ProductsIn(version=V1, rows=[ProductRow(device="")]), db)
    assert e.value.status_code == 400
