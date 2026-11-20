"""집계 파생 행·열의 "다음 버전에도 유지".

파생 행·열은 버전×기준연월 단위로 저장돼서 새 버전을 열면 사라진다. 승인한 것만 새 버전으로
복제하는 게 이 API의 일이고, 테스트는 그 경계를 지킨다: 승인한 것만 간다 / 값이 아니라 계산식이
간다 / 새 버전에서 고친 건 덮어쓰지 않는다 / 두 번 불러도 결과가 같다.
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints import collect as ov
from app.accounts.overseas.schemas import CarryDefIn, CarryForwardIn, ExtraRowIn

FROM_YM, TO_YM, VER = "209901", "209902", "forecast"


def _mk_derived_row(db, row_key, label, yyyymm=FROM_YM):
    """집계 화면에서 만드는 파생 행과 같은 모양으로 하나 만든다(전체 합산 범위 + 계산식)."""
    return ov.upsert_extra_row(
        ExtraRowIn(
            lab_pk=ov.ALL_SCOPE, version=VER, yyyymm=yyyymm, row_key=row_key, label=label,
            is_derived=True, formula={"op": "div_elementwise", "operands": ["labor", "hc"]}, v=[],
        ),
        db=db,
    )


def _carry(db, **kw):
    return ov.carry_forward_defs(
        CarryForwardIn(from_yyyymm=FROM_YM, from_version=VER, to_yyyymm=TO_YM, to_version=VER, **kw), db=db
    )


def test_only_approved_rows_are_carried(db):
    """유지 승인한 행만 새 버전으로 간다 — 그 달만 보려고 만든 지표가 계속 끌려다니면 안 된다."""
    _mk_derived_row(db, "der_keep", "인당 인건비")
    _mk_derived_row(db, "der_once", "이번달만 보는 지표")
    ov.upsert_carry_def(CarryDefIn(kind="row", def_key="der_keep", label="인당 인건비"), db=db)

    assert _carry(db).rows == 1
    moved = {r.row_key for r in ov.list_extra_rows(yyyymm=TO_YM, version=VER, lab_pk=ov.ALL_SCOPE, db=db)}
    assert "der_keep" in moved and "der_once" not in moved


def test_formula_is_copied_not_the_value(db):
    """가는 건 계산식이다 — 재료(업로드 값)가 새 버전 것으로 바뀌면 결과도 그 버전 숫자로 나와야 한다."""
    src = _mk_derived_row(db, "der_keep", "인당 인건비")
    ov.upsert_carry_def(CarryDefIn(kind="row", def_key="der_keep"), db=db)
    _carry(db)

    dst = next(r for r in ov.list_extra_rows(yyyymm=TO_YM, version=VER, lab_pk=ov.ALL_SCOPE, db=db) if r.row_key == "der_keep")
    assert dst.is_derived and dst.formula == src.formula
    assert dst.label == "인당 인건비" and dst.yyyymm == TO_YM


def test_existing_row_in_target_is_not_overwritten(db):
    """새 버전에서 사람이 이미 만든/고친 같은 이름의 행은 건드리지 않는다(되돌아가면 안 된다)."""
    _mk_derived_row(db, "der_keep", "지난 버전 이름")
    ov.upsert_carry_def(CarryDefIn(kind="row", def_key="der_keep"), db=db)
    _mk_derived_row(db, "der_keep", "새 버전에서 고친 이름", yyyymm=TO_YM)

    r = _carry(db)
    assert r.rows == 0 and r.skipped == ["지난 버전 이름"]
    dst = next(x for x in ov.list_extra_rows(yyyymm=TO_YM, version=VER, lab_pk=ov.ALL_SCOPE, db=db) if x.row_key == "der_keep")
    assert dst.label == "새 버전에서 고친 이름"


def test_carry_is_idempotent(db):
    """새 버전을 여러 번 열어도(두 번 호출해도) 결과가 같다."""
    _mk_derived_row(db, "der_keep", "인당 인건비")
    ov.upsert_carry_def(CarryDefIn(kind="row", def_key="der_keep"), db=db)

    assert _carry(db).rows == 1
    assert _carry(db).rows == 0  # 두 번째는 이미 있어서 건너뛴다
    keep = [r for r in ov.list_extra_rows(yyyymm=TO_YM, version=VER, lab_pk=ov.ALL_SCOPE, db=db) if r.row_key == "der_keep"]
    assert len(keep) == 1


def test_approval_can_be_withdrawn(db):
    """유지 설정을 해제하면 다음 버전부터 안 따라간다."""
    _mk_derived_row(db, "der_keep", "인당 인건비")
    ov.upsert_carry_def(CarryDefIn(kind="row", def_key="der_keep"), db=db)
    left = ov.upsert_carry_def(CarryDefIn(kind="row", def_key="der_keep", carry=False), db=db)

    assert not any(d.def_key == "der_keep" for d in left)
    assert _carry(db).rows == 0


def test_bad_requests_are_rejected(db):
    """같은 버전으로 복제하거나, 없는 종류를 승인하는 건 막는다."""
    with pytest.raises(HTTPException) as e:
        ov.carry_forward_defs(
            CarryForwardIn(from_yyyymm=FROM_YM, from_version=VER, to_yyyymm=FROM_YM, to_version=VER), db=db
        )
    assert e.value.status_code == 400

    for body in [CarryDefIn(kind="cell", def_key="x"), CarryDefIn(kind="row", def_key="  ")]:
        with pytest.raises(HTTPException) as e:
            ov.upsert_carry_def(body, db=db)
        assert e.value.status_code == 400
