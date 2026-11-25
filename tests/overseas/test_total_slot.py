"""SARC 총계 시트(Format)를 적재해도 집계가 두 번 세지 않는지.

연구소가 보내는 SARC 워크북은 CPU · GPU · Format(= CPU+GPU 총계) 세 장이고, 총계도 "연구소가
보고한 공식 숫자"라 버리지 않고 SARC:TOTAL 자리에 적재한다(frontend-html/shared/core.js
ovCostPk). 그래서 cost_entries에 CPU · GPU · TOTAL이 함께 들어가는데, 집계·버전 비교·인건비율이
part_code="TOTAL"을 빼지 않으면 SARC 금액이 정확히 두 배가 된다.

빼는 규칙은 services/versions.active_labs 한 곳에 있다 — 이 테스트는 "그 규칙이 TOTAL을 실제로
걸러내고, 총계 자리는 그대로 읽힌다"를 못 박는다(적재 쪽을 켠 변경의 회귀 기준).
"""

import pytest
from sqlalchemy import select

from app.accounts.overseas.models import OverseasLab
from app.accounts.overseas.services.labs import all_labs
from app.accounts.overseas.services.versions import active_labs

BASIS_YEAR, BASIS_MONTH = 2026, 11


@pytest.fixture
def total_pks(db):
    pks = [lab.pk for lab in all_labs(db) if (lab.part_code or "").upper() == "TOTAL"]
    if not pks:
        pytest.skip("총계 자리(part_code=TOTAL)가 기준정보에 없습니다 — seed.py를 적재하세요.")
    return pks


def test_total_slot_exists_in_reference(db, total_pks):
    """총계 자리가 기준정보에 있어야 업로드가 그 자리로 적재된다(없으면 check_lab_pk가 400).

    SARC:TOTAL은 CPU·GPU와 같은 lab_code를 쓰므로 탭은 늘지 않는다 — 프런트는 part_code=TOTAL을
    L.parts에 섞지 않고(ovLabsFromApi) "총계" 탭에서 직접 조회한다."""
    for pk in total_pks:
        lab = db.scalars(select(OverseasLab).where(OverseasLab.pk == pk)).one()
        assert lab.lab_code and lab.lab_code != pk, f"{pk}는 lab_code로 묶여 있어야 한다"
        assert any(
            other.lab_code == lab.lab_code and (other.part_code or "").upper() != "TOTAL"
            for other in all_labs(db)
        ), f"{pk}의 파트 행(CPU·GPU)이 없다 — 총계만 있는 연구소는 두 번 셀 위험이 없어야 한다"


def test_active_labs_excludes_total(db, total_pks):
    """계산에 들어가는 연구소 목록에서 총계는 빠진다 — 들어가면 SARC가 CPU+GPU+TOTAL로 두 배가 된다."""
    labs = active_labs(db, BASIS_YEAR, BASIS_MONTH)

    for pk in total_pks:
        assert pk not in labs, f"{pk}가 집계 대상에 들어 있다 — SARC 금액이 두 배가 된다"
    assert labs, "집계 대상이 비었다 — 필터가 과하게 걸렸다"


def test_total_slot_is_still_a_valid_write_target(db, total_pks):
    """총계 자리는 집계에서 빠지지만 **쓰기는 허용**이다 — 업로드가 그 자리에 적재하기 때문이다.
    집계 제외와 쓰기 거부를 같은 규칙으로 묶으면(all_labs에서 아예 빼면) 적재가 400으로 막힌다."""
    from app.accounts.overseas.services.extra_rows import check_lab_pk

    for pk in total_pks:
        assert check_lab_pk(db, pk) == pk
