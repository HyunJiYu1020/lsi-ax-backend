"""추가 열 칸의 원문 보관(data_type + value)과 추가 행의 1Q~4Q·연간(per) 보존.

왜 있나: 추가 열은 양식 밖 자유 칸이라 금액·비율·글자가 섞여 오는데, 전에는 amount(Numeric(14,2))
하나뿐이라 글자는 버려지고 숫자도 소수 2자리에서 깎였다(0.825 → 0.83). 추가 행의 분기·연간도
파일에 적혀 와도 버려져서 화면이 월 합으로만 그렸다 — 고정 계정은 파일 값을 그대로 보여주는 것과
비대칭이었다.

핵심 계약:
 ① 타입 판정은 쓰기 한 곳(classify_extra_value)뿐이고, 응답의 JSON 타입이 곧 저장된 타입이다 —
    숫자면 number, 문자열이면 text. 읽는 쪽은 typeof 분기만 한다.
 ② 숫자의 참값은 원문(value)이다 — 0.825가 0.83으로 깎여 돌아오면 안 된다.
 ③ 선행 0이 붙은 숫자 꼴("01023" — 사번·코드)은 글자다. 숫자로 읽으면 0이 떨어져 나간다.
 ④ per는 None이면 안 건드리고, 리스트면 전체 교체([]는 지움) — 분기 칸 없이 다시 올라온
    파일이 지난 파일의 분기값을 남기면 안 된다(cost_headers 전체 교체와 같은 이유).
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.collect import list_cost_extras, upsert_cost_extra, upsert_extra_row
from app.accounts.overseas.schemas import ExtraRowIn, ExtraValueIn
from app.accounts.overseas.services.extra_rows import classify_extra_value, read_extra_rows
from app.accounts.overseas.services.labs import all_labs

# 실제 데이터가 없는 가짜 기준월 — 개발 DB의 2026년 자료를 건드리지 않는다(db 픽스처가 롤백도 한다).
YM = 209509


@pytest.fixture
def lab(db):
    pks = [x.pk for x in all_labs(db)]
    if not pks:
        pytest.skip("연구소 기준정보가 없어 스킵합니다")
    return pks[0]


def _put_cell(db, lab, value, row_key="hc", label="비고"):
    return upsert_cost_extra(
        ExtraValueIn(lab_pk=lab, version="forecast", row_key=row_key, yyyymm=YM, label=label, value=value),
        db=db,
    )


def _cell(db, lab, row_key="hc", label="비고"):
    rows = list_cost_extras(yyyymm=YM, version="forecast", lab_pk=lab, db=db)
    hit = [r.value for r in rows if r.row_key == row_key and r.label == label]
    return hit[0] if hit else None


def test_글자는_원문_그대로_문자열로_돌아온다(db, lab):
    out = _put_cell(db, lab, "환율 반영 (1,350원/$)")
    assert out.value == "환율 반영 (1,350원/$)"
    got = _cell(db, lab)
    assert got == "환율 반영 (1,350원/$)" and isinstance(got, str)


def test_숫자는_소수_셋째_자리까지도_깎이지_않는다(db, lab):
    # amount(Numeric(14,2))에만 담으면 0.825 → 0.83이 된다. 참값은 원문(value)이어야 한다.
    _put_cell(db, lab, 0.825, label="사용률")
    got = _cell(db, lab, label="사용률")
    assert got == 0.825 and isinstance(got, float)


def test_숫자로_읽히는_글자는_숫자가_된다(db, lab):
    _put_cell(db, lab, "1,200", label="Fee")
    assert _cell(db, lab, label="Fee") == 1200.0


def test_선행_0이_붙은_숫자_꼴은_글자다(db, lab):
    _put_cell(db, lab, "01023", label="코드")
    got = _cell(db, lab, label="코드")
    assert got == "01023" and isinstance(got, str)


def test_같은_칸을_글자에서_숫자로_덮어쓰면_타입도_바뀐다(db, lab):
    _put_cell(db, lab, "미정", label="단가")
    _put_cell(db, lab, 3.5, label="단가")
    assert _cell(db, lab, label="단가") == 3.5


def test_너무_긴_글자는_자르지_않고_거절한다(db, lab):
    with pytest.raises(HTTPException) as e:
        _put_cell(db, lab, "가" * 501)
    assert e.value.status_code == 400


def test_판정_규칙_한_곳의_경계값들():
    assert classify_extra_value("80%")[0] == "text"      # 단위가 붙으면 글자
    assert classify_extra_value("3.5명")[0] == "text"
    assert classify_extra_value("nan")[0] == "text"      # float()가 받아버리는 특수값
    assert classify_extra_value("-0.5") == ("number", "-0.5", -0.5)
    assert classify_extra_value(0) == ("number", "0", 0.0)
    assert classify_extra_value(True) == ("number", "1", 1.0)  # bool이 숫자 분기에 빨려 들어가지 않게


def _put_row(db, lab, per, v=None, row_key="ux_pertest"):
    return upsert_extra_row(
        ExtraRowIn(
            lab_pk=lab, version="forecast", row_key=row_key, label="Cloud Fee", yyyymm=YM,
            v=[0.0] * 12 if v is None else v, per=per,
        ),
        db=db,
    )


def _row_per(db, lab, row_key="ux_pertest"):
    rows = read_extra_rows(db, YM, version="forecast", lab_pk=lab)
    hit = [r.per for r in rows if r.row_key == row_key]
    return hit[0] if hit else "행 없음"


def test_추가_행의_분기_연간이_파일_값_그대로_돌아온다(db, lab):
    # 월 합(0)과 다른 분기값 — 연구소가 보고한 공식 값이 월 합으로 덮이면 안 된다.
    _put_row(db, lab, per=[10.0, 20.0, None, None, 100.0])
    assert _row_per(db, lab) == [10.0, 20.0, None, None, 100.0]


def test_per_None은_안_건드리고_빈_리스트는_지운다(db, lab):
    _put_row(db, lab, per=[10.0, 20.0, None, None, 100.0])
    _put_row(db, lab, per=None)        # 이름·자리만 고치는 저장 — 분기값이 남아야 한다
    assert _row_per(db, lab) == [10.0, 20.0, None, None, 100.0]
    _put_row(db, lab, per=[])          # 분기 칸 없이 다시 올라온 파일 — 지난 분기값이 지워져야 한다
    assert _row_per(db, lab) is None


def test_per_6칸_이상은_거절한다(db, lab):
    with pytest.raises(HTTPException) as e:
        _put_row(db, lab, per=[1.0] * 6)
    assert e.value.status_code == 400
