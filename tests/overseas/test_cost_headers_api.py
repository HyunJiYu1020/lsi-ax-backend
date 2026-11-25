"""비용 시트 기간 열 머리글 원문 — 연구소가 보낸 글자를 그대로 저장하고 그대로 돌려주는가.

왜 이 테이블이 있나: 취합 › 비용 표는 연구소 파일 하나를 그대로 비춰 보는 자리인데, 열 이름은
화면 코드가 만든 글자("1월")였다. 연구소가 "'26.1월 실적" · "M2601A"처럼 적어 보내면 담당자가
보는 표와 연구소 보고서의 머리글이 달라진다 — 그래서 파일 머리글을 저장해서 그 표에만 쓴다.

핵심은 세 가지다:
 ① 글자가 **그대로** 오간다(따옴표·한글·공백을 다듬어 바꾸지 않는다 — 앞뒤 공백만 떼어낸다).
 ② 업로드 한 번이 그 시트의 머리글 **전체를 교체**한다. 파일마다 열 구성이 달라지므로, 열별
    덮어쓰기로는 지난 파일에만 있던 열이 머리글로 남는다(= 연구소가 뺀 열이 계속 보인다).
 ③ 교체 범위는 (연구소 · 버전 · 기준월) 하나다. 한 연구소 파일을 올렸을 때 다른 연구소·다른 달의
    머리글이 사라지면 안 된다.
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.collect import list_cost_headers, upsert_cost_headers
from app.accounts.overseas.schemas import CostHeaderIn
from app.accounts.overseas.services.labs import all_labs

# 실제 데이터가 없는 가짜 기준월. 개발 DB의 2026년 자료를 건드리지 않고 교체 규칙만 본다
# (conftest의 db 픽스처가 롤백하긴 하지만, 어느 달을 보고 있는지가 테스트에서도 분명해야 한다).
YM = 209509
OTHER_YM = 209510  # 건드리면 안 되는 다른 달


@pytest.fixture
def labs(db):
    """실재하는 연구소 pk 두 개. 파트가 있는 연구소("SARC:CPU")도 그대로 받는지 같이 본다 —
    비용 시트는 증감사유·외주용역과 달리 파트 단위로 올라온다."""
    pks = [lab.pk for lab in all_labs(db)]
    if len(pks) < 2:
        pytest.skip("연구소 기준정보가 2개 미만이라 스킵합니다")
    return pks[0], pks[1]


def _put(db, lab_pk, labels, version="forecast", yyyymm=YM, source_file="SARC_2095.xlsx"):
    return upsert_cost_headers(
        CostHeaderIn(
            lab_pk=lab_pk, version=version, yyyymm=yyyymm, labels=labels, source_file=source_file
        ),
        db=db,
    )


def _labels(db, lab_pk, version="forecast", yyyymm=YM):
    rows = list_cost_headers(yyyymm=yyyymm, version=version, lab_pk=lab_pk, db=db)
    return rows[0].labels if rows else {}


def test_글자를_그대로_저장하고_그대로_돌려준다(db, labs):
    lab, _ = labs
    sent = {"m1": "'26.1월 실적", "m9": "'26.9월 속보", "q1": "1Q", "y": "FY26 Total"}
    out = _put(db, lab, sent)
    assert out.labels == sent          # 응답이 저장된 것과 같아야 한다
    assert _labels(db, lab) == sent    # 다시 읽어도 같아야 한다


def test_앞뒤_공백만_떼고_빈_머리글은_저장하지_않는다(db, labs):
    """빈 머리글은 "파일에 그 열이 없었다"와 같은 사건이다 — 빈 문자열을 넣어 두면 그 열만
    이름 없는 칸이 된다(화면은 머리글이 없는 열에 코드 머리글 "1월"을 쓴다)."""
    lab, _ = labs
    out = _put(db, lab, {"m1": "  '26.1월  ", "m2": "   ", "m3": ""})
    assert out.labels == {"m1": "'26.1월"}
    assert _labels(db, lab) == {"m1": "'26.1월"}


def test_업로드_한_번이_그_시트의_머리글을_통째로_교체한다(db, labs):
    """열을 줄여 보낸 파일을 올리면 지난 파일의 열이 남지 않아야 한다 — 남으면 연구소가 뺀
    열이 화면 머리글로 계속 보인다."""
    lab, _ = labs
    _put(db, lab, {"m1": "1월", "m2": "2월", "q1": "1Q", "y": "연간"})
    _put(db, lab, {"m1": "'26.1월 실적"})
    assert _labels(db, lab) == {"m1": "'26.1월 실적"}


def test_빈_한벌을_보내면_머리글이_지워진다(db, labs):
    """머리글을 못 읽은 파일을 다시 올렸을 때 지난 파일의 머리글이 남으면, 표가 연구소 보고서와
    다른 글자를 "파일 그대로"라고 보여주게 된다. 그래서 지우기를 따로 두지 않고 빈 한 벌로 지운다."""
    lab, _ = labs
    _put(db, lab, {"m1": "1월"})
    out = _put(db, lab, {})
    assert out.labels == {}
    assert _labels(db, lab) == {}
    # 머리글이 없는 연구소는 목록에 아예 나오지 않는다 — "없다"와 "빈 머리글이 있다"는 다르다.
    assert [r.lab_pk for r in list_cost_headers(yyyymm=YM, version="forecast", lab_pk=lab, db=db)] == []


def test_다른_연구소_버전_기준월은_건드리지_않는다(db, labs):
    lab, other = labs
    _put(db, lab, {"m1": "내 연구소 1월"})
    _put(db, other, {"m1": "다른 연구소 1월"})
    _put(db, lab, {"m1": "같은 달 실적본 1월"}, version="actual")
    _put(db, lab, {"m1": "다른 달 1월"}, yyyymm=OTHER_YM)

    # 같은 연구소·같은 달의 속보를 다시 올려 교체한다 — 나머지 셋은 그대로여야 한다.
    _put(db, lab, {"m2": "속보 2월"})
    assert _labels(db, lab) == {"m2": "속보 2월"}
    assert _labels(db, other) == {"m1": "다른 연구소 1월"}
    assert _labels(db, lab, version="actual") == {"m1": "같은 달 실적본 1월"}
    assert _labels(db, lab, yyyymm=OTHER_YM) == {"m1": "다른 달 1월"}


def test_기간을_늘려_받은_파일의_13월_이후_열도_받는다(db, labs):
    """기간을 24·36개월로 받는 파일이 있다(ovParse의 nMo). 2년차 열은 "m13"…으로 들어온다."""
    lab, _ = labs
    out = _put(db, lab, {"m12": "'26.12월", "m13": "'27.1월", "m24": "'27.12월"})
    assert out.labels == {"m12": "'26.12월", "m13": "'27.1월", "m24": "'27.12월"}


@pytest.mark.parametrize(
    "labels",
    [
        {"m0": "0월"},        # 월은 1부터
        {"m13x": "이상"},      # 열 자리 키 형식이 아니다
        {"q5": "5분기"},       # 분기는 4개까지
        {"year": "연간"},      # 연간은 "y"다
        {"": "빈 키"},
    ],
)
def test_열_자리_키가_아니면_거절한다(db, labs, labels):
    """키를 조용히 버리면 "머리글을 보냈는데 표에 안 나온다"가 되고, 왜인지 알 방법이 없다."""
    lab, _ = labs
    with pytest.raises(HTTPException) as e:
        _put(db, lab, labels)
    assert e.value.status_code == 400


def test_너무_긴_머리글은_자르지_않고_거절한다(db, labs):
    """잘라서 저장하면 "그대로 가져온다"는 약속이 조용히 깨진 채 표에 그려진다."""
    lab, _ = labs
    with pytest.raises(HTTPException) as e:
        _put(db, lab, {"m1": "가" * 101})
    assert e.value.status_code == 400
    _put(db, lab, {"m1": "가" * 100})  # 상한까지는 받는다


def test_알_수_없는_연구소와_버전은_거절한다(db, labs):
    lab, _ = labs
    with pytest.raises(HTTPException) as e:
        _put(db, "NOPE", {"m1": "1월"})
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        _put(db, lab, {"m1": "1월"}, version="sokbo")
    assert e.value.status_code == 400
