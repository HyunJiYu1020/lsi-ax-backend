"""GET /overseas/summary — 화면 맨 위 히어로 카드와 ③집계 단계 KPI 카드의 금액.

전에는 이 카드들을 브라우저가 localStorage 값 + 브라우저 환율로 직접 계산했고, 서버는
기준월을 "DB 최신 속보"로 혼자 정했다(basis_month를 안 받았다). 그래서 담당자가 상단에서
과거 차수를 골라도 카드만 최신 속보 금액을 보여 줬다. 여기서 지키는 것:

- 차수를 **받아서** 읽는다 (basis_month · kind · plan_no — 집계표와 같은 파라미터)
- 카드 총액 = 집계 ② 표의 합계 행 연간 칸. 같은 화면 위아래가 다른 숫자면 안 된다
  (① 전체 계정 표와는 다르다 — ①은 CP 분기 정산을 반영하지 않는다)
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.aggregate import get_lab_tables
from app.accounts.overseas.endpoints.collect import get_overseas_summary

BASIS_YEAR = 2026
BASIS_MONTH = 9


def test_total_is_labor_plus_non_labor(db):
    s = get_overseas_summary(BASIS_YEAR, "cur", "y", BASIS_MONTH, "forecast", None, db)
    assert s.total > 0
    assert s.labor + s.non_labor == pytest.approx(s.total)
    assert s.labor_pct + s.non_labor_pct == pytest.approx(100.0)


def test_matches_the_aggregate_lab_table(db):
    """카드 총액 = 집계 ② 표 합계 행. 둘 다 CP 분기 정산을 반영한 원화 금액이다."""
    s = get_overseas_summary(BASIS_YEAR, "cur", "y", BASIS_MONTH, "forecast", None, db)
    t = get_lab_tables(BASIS_YEAR, "cur", BASIS_MONTH, "forecast", None, db)
    total_row = next(r for r in t.tables["t2k"].rows if r.total)
    assert total_row.values[16] == pytest.approx(s.total)


def test_reports_which_version_it_read(db):
    """어느 버전을 읽었는지 되돌려준다 — 화면·챗봇이 같은 이름으로 부르게."""
    s = get_overseas_summary(BASIS_YEAR, "cur", "y", BASIS_MONTH, "forecast", None, db)
    assert s.basis_month == BASIS_MONTH
    assert s.kind == "forecast"
    assert s.version_label

    prev = get_overseas_summary(BASIS_YEAR, "prev", "y", BASIS_MONTH, "forecast", None, db)
    # 전실행은 "속보 기준의 전실행"이라 라벨이 다르다(같으면 버전 해석이 안 된 것이다).
    assert prev.version_label != s.version_label


def test_period_narrows_the_months(db):
    """분기를 고르면 그 분기만 더한다 — 연간보다 작아야 한다."""
    year = get_overseas_summary(BASIS_YEAR, "cur", "y", BASIS_MONTH, "forecast", None, db)
    q1 = get_overseas_summary(BASIS_YEAR, "cur", "q1", BASIS_MONTH, "forecast", None, db)
    assert 0 < q1.total < year.total


def test_rejects_bad_period_and_month(db):
    with pytest.raises(HTTPException) as e:
        get_overseas_summary(BASIS_YEAR, "cur", "m13", BASIS_MONTH, "forecast", None, db)
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        get_overseas_summary(BASIS_YEAR, "cur", "y", 13, "forecast", None, db)
    assert e.value.status_code == 400
