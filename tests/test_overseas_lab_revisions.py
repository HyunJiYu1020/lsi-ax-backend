"""공통 RULE "기준정보 변경": 변경한 버전부터 적용되고 지난 버전은 영향받지 않는다.

연구소 통화를 바꾸면 환차를 현지통화로 쪼개는지가 달라지므로, 지금 값으로 과거 버전을 다시
계산하면 이미 마감해서 보고한 증감분석 숫자가 바뀐다. 그래서 "그 시점 값"으로 읽히는지 본다.
"""

import pytest

from app.api.v1.endpoints.overseas import _lab_views, upsert_lab
from app.schemas.overseas import LabIn

CODE = "ZZTEST"  # 실제 연구소와 안 겹치는 테스트 전용 코드


def _view(db, as_of=None):
    got = {lab.lab_code: lab for lab in _lab_views(db, as_of)}
    return got.get(CODE)


def test_currency_change_applies_from_that_version_only(db):
    upsert_lab(LabIn(lab_code=CODE, name="테스트연구소", currency="USD", effective_from="2026-01"), db=db)
    upsert_lab(LabIn(lab_code=CODE, name="테스트연구소", currency="ILS", effective_from="2026-09"), db=db)

    # 변경 전 버전은 바뀌기 전 통화 그대로
    assert _view(db, "2026-08").currency == "USD"
    # 변경한 버전부터 새 통화
    assert _view(db, "2026-09").currency == "ILS"
    assert _view(db, "2026-12").currency == "ILS"
    # as_of를 안 주면 지금 값
    assert _view(db).currency == "ILS"


def test_new_lab_does_not_appear_in_past_versions(db):
    upsert_lab(LabIn(lab_code=CODE, name="테스트연구소", currency="USD", effective_from="2026-09"), db=db)

    # 만들기 전 버전에서는 "없던 연구소"라 집계·업로드 진척에서 빠진다
    assert _view(db, "2026-08").is_active is False
    assert _view(db, "2026-09").is_active is True


def test_deactivation_does_not_shrink_past_totals(db):
    upsert_lab(LabIn(lab_code=CODE, name="테스트연구소", currency="USD", effective_from="2026-01"), db=db)
    upsert_lab(
        LabIn(lab_code=CODE, name="테스트연구소", currency="USD", is_active=False, effective_from="2026-09"), db=db
    )

    assert _view(db, "2026-08").is_active is True  # 지난 버전 합계에는 그대로 들어간다
    assert _view(db, "2026-09").is_active is False


def test_second_change_does_not_overwrite_the_original_baseline(db):
    upsert_lab(LabIn(lab_code=CODE, name="테스트연구소", currency="USD", effective_from="2026-01"), db=db)
    upsert_lab(LabIn(lab_code=CODE, name="테스트연구소", currency="ILS", effective_from="2026-05"), db=db)
    upsert_lab(LabIn(lab_code=CODE, name="테스트연구소", currency="INR", effective_from="2026-09"), db=db)

    # 두 번째 변경이 "원래 값" 고정 줄을 덮어쓰면 과거가 다시 흔들린다 — 안 흔들리는지 확인
    assert _view(db, "2026-04").currency == "USD"
    assert _view(db, "2026-05").currency == "ILS"
    assert _view(db, "2026-09").currency == "INR"


def test_bad_effective_from_is_rejected(db):
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        upsert_lab(LabIn(lab_code=CODE, name="테스트연구소", currency="USD", effective_from="2026/01"), db=db)
