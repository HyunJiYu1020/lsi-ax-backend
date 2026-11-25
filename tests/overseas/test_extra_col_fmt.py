"""추가 열의 표시 형식(extra_cols.fmt) — 값이 아니라 **그리는 방법**을 열에 달아 둔다.

왜 있나: 양식 밖 열(비고 · 사용률 …)에는 글자·금액·비율이 섞여 오는데, 비율을 연구소마다 다른
모양으로 적어 보낸다("0.825" · "82.5" · "82.5%"). 화면이 값만 보고 맞히면 한쪽은 반드시 틀린다 —
0.825를 일률적으로 82.5%로 고치면 이미 퍼센트로 적은 연구소가 100배가 된다. 그래서 추측하지 않고
사람이 열마다 한 번 고르고, 고른 형식만 서버에 남는다.

핵심 계약:
 ① 저장된 칸 값은 형식과 무관하게 **원문 그대로**다 — 형식을 바꿔도 값은 건드리지 않는다.
 ② 기본은 raw(그대로)다 — 형식을 고른 적 없는 옛 열도 지금까지와 똑같이 그려진다.
 ③ 아는 형식만 받는다(raw | pct_ratio | pct_plain) — 모르는 값은 400으로 거절한다.
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.collect import (
    list_cost_extras, list_extra_cols, upsert_cost_extra, upsert_extra_col,
)
from app.accounts.overseas.schemas import ExtraColIn, ExtraValueIn
from app.accounts.overseas.services.labs import all_labs

# 실제 데이터가 없는 가짜 기준월 — 개발 DB의 2026년 자료를 건드리지 않는다(db 픽스처가 롤백도 한다).
YM = 209509


@pytest.fixture
def lab(db):
    pks = [x.pk for x in all_labs(db)]
    if not pks:
        pytest.skip("연구소 기준정보가 없어 스킵합니다")
    return pks[0]


def _col(db, lab, label="사용률", fmt="raw"):
    return upsert_extra_col(
        ExtraColIn(lab_pk=lab, version="forecast", yyyymm=YM, label=label, is_derived=False, fmt=fmt), db=db
    )


def _get(db, lab, label="사용률"):
    hit = [c for c in list_extra_cols(yyyymm=YM, version="forecast", lab_pk=lab, db=db) if c.label == label]
    return hit[0] if hit else None


def test_형식을_안_고르면_그대로다(db, lab):
    assert _col(db, lab).fmt == "raw"
    assert _get(db, lab).fmt == "raw"


def test_고른_형식이_그대로_돌아온다(db, lab):
    _col(db, lab, fmt="pct_ratio")
    assert _get(db, lab).fmt == "pct_ratio"
    # 같은 열을 다시 저장하면 형식만 바뀐다(열이 새로 생기지 않는다).
    _col(db, lab, fmt="pct_plain")
    cols = [c for c in list_extra_cols(yyyymm=YM, version="forecast", lab_pk=lab, db=db) if c.label == "사용률"]
    assert len(cols) == 1 and cols[0].fmt == "pct_plain"


def test_형식을_바꿔도_칸_값은_원문_그대로다(db, lab):
    # 0.825를 82.5%로 **그리기만** 한다 — 서버가 값을 ×100 해서 덮어쓰면 원문이 사라진다.
    upsert_cost_extra(
        ExtraValueIn(lab_pk=lab, version="forecast", row_key="hc", yyyymm=YM, label="사용률", value=0.825), db=db
    )
    _col(db, lab, fmt="pct_ratio")
    cells = [r.value for r in list_cost_extras(yyyymm=YM, version="forecast", lab_pk=lab, db=db) if r.label == "사용률"]
    assert cells == [0.825]


def test_모르는_형식은_거절한다(db, lab):
    with pytest.raises(HTTPException) as e:
        _col(db, lab, fmt="퍼센트")
    assert e.value.status_code == 400
