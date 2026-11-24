"""CP 계약 전용 저장 API + 시점별 스냅샷.

CP는 연 1회 올리는 파일이라 "연도 단위 전체 교체"다 — 사라진 POC·직급은 지워야 한다.
외주용역과 같이, 전용 API와 일괄 저장(PUT /state)이 같은 서비스 함수를 부르는지가 핵심이다.

여기에 적용 시점(effective_from)이 붙었다: 분기 정산액은 저장된 값이 아니라 조회할 때마다 다시
계산되는 값이라, 시점이 없으면 계약을 고치는 순간 이미 마감한 과거 버전의 분기 정산액까지 새
계약액으로 다시 계산된다. "9월에 바꾸면 9월 이후 버전만, 8월 이하는 옛 값"이 성립하는지를 못 박는다.
"""

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.accounts.overseas.endpoints.collect import (
    get_overseas_state,
    save_cp_rows,
    save_overseas_state as _save_overseas_state,
)
from app.accounts.overseas.schemas import CpRow, CpRowsIn
from app.accounts.overseas.services.aggregate import cp_quarter_amounts
from app.accounts.overseas.services.cp import cp_rows_at


# 이 엔드포인트들은 BackgroundTasks를 받는다 — 증감사유 번역을 응답 뒤로 미루기 때문이다
# (services/reason_rows.prewarm_in_background). 테스트는 BackgroundTasks를 만들어 넘기고 그 안에
# 쌓인 작업은 실행하지 않는다 — 저장 로직만 검증하고 LLM을 타지 않는다(번역은 test_reason_prewarm).
def save_overseas_state(*a, **kw):
    kw.setdefault("background", BackgroundTasks())
    return _save_overseas_state(*a, **kw)

BASIS_YEAR = 2026  # 일괄 저장(PUT /overseas/state)에 넘기는 편성 기준연도. 실제 화면과 같게 둔다
# 계약을 넣고 지우는 연도는 **실제 데이터가 없는 가짜 연도**다. 개발 DB의 2026년 계약(POC 3개·
# 11행)을 상대로 테스트하면 모든 저장이 "계약을 줄이는 교체"가 되어 가드 1에 걸린다 — 그건 가드가
# 맞게 동작하는 것이고, 교체 규칙을 보려는 이 테스트가 볼 자리가 아니다.
YEAR = "2095"
OTHER = "2099"  # 건드리면 안 되는 다른 연도
# 적용 시점 테스트도 같은 이유로 또 다른 가짜 연도를 쓴다 — 위 YEAR 세트가 섞이면
# "그 시점 이전에는 계약이 없다"를 확인할 수 없다.
ASOF_YEAR = "2098"


def _row(year: str, title: str, total: float = 100.0, proj: str = "P1") -> CpRow:
    """금액은 양식의 합계 관계(Total = 인건비 + Direct R&D + Overhead)에 맞춰 둔다 — 저장이
    그 관계를 검사하기 때문이다(services/cp.validate_cp_rows). 관계가 깨진 세트는 계약이 아니라
    업로드 파싱이 깨진 것이라, 테스트 픽스처도 실제 계약서와 같은 모양이어야 한다."""
    return CpRow(
        year=year, poc_type="POC", poc_proj=proj, poc_desc="설명", title=title,
        dedicated=1.0, common=0.0, man_month=1.0, cost_year=total * 0.5,
        poc_cost_sum=total * 0.5, poc_mm_sum=5.0, poc_direct=total * 0.2, poc_ovh=total * 0.3,
        poc_total=total, source_file="cp.xlsx",
    )


def _stored(db, year: str, as_of: str = f"{BASIS_YEAR:04d}-12") -> list[tuple[str, str, float]]:
    """그 시점 버전이 보는 계약 세트. 시점을 안 주면 기준연도 말 — 방금 저장한 세트가 보인다.

    테이블을 통째로 읽지 않는 이유: 이제 같은 연도에 시점별 세트가 여러 벌 있을 수 있어서,
    전부 읽으면 "한 버전이 보는 계약"이 아니라 "지금까지의 모든 계약"이 나온다."""
    return sorted(
        (r.poc_proj, r.title, float(r.poc_total)) for r in cp_rows_at(db, year, as_of)
    )


def test_saves_rows(db):
    res = save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "VP"), _row(YEAR, "CL4")]), db=db)

    assert res["status"] == "ok" and res["replaced"] == 1
    assert _stored(db, YEAR) == [("P1", "CL4", 100.0), ("P1", "VP", 100.0)]


def test_replaces_the_whole_year(db):
    """연 1회 올리는 파일이라 사라진 직급은 지워져야 한다 — upsert만 하면 작년 양식의 POC가 남는다."""
    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, t) for t in ("VP", "CL4", "CL3")]), db=db)
    assert len(_stored(db, YEAR)) == 3

    # 행이 3개 → 1개로 줄어드는 교체라 확인이 필요하다(아래 test_shrinking_upload_needs_confirmation).
    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "VP", total=777.0)], confirm_shrink=True), db=db)
    assert _stored(db, YEAR) == [("P1", "VP", 777.0)]


def test_other_years_are_untouched(db):
    """보내지 않은 연도는 건드리지 않는다."""
    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "VP"), _row(OTHER, "VP", total=5.0)]), db=db)

    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "VP", total=9.0)], confirm_shrink=True), db=db)

    assert _stored(db, YEAR) == [("P1", "VP", 9.0)]
    assert _stored(db, OTHER) == [("P1", "VP", 5.0)], "다른 연도가 사라졌다"


def test_matches_the_bulk_state_path(db):
    """전용 API와 일괄 저장이 같은 결과를 낸다 — 같은 서비스 함수를 부르므로 성립해야 한다."""
    rows = [_row(YEAR, "VP", 11.0), _row(YEAR, "CL4", 22.0)]

    save_cp_rows(body=CpRowsIn(rows=rows), db=db)
    via_api = _stored(db, YEAR)

    state = get_overseas_state(basis_year=BASIS_YEAR, db=db)
    state.cp = rows
    save_overseas_state(state=state, db=db)

    assert _stored(db, YEAR) == via_api, "전용 API와 일괄 저장의 결과가 다르다"


def test_empty_rows_changes_nothing(db):
    """빈 목록은 아무것도 지우지 않는다 — 어느 연도를 비울지 알 수 없으므로."""
    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "VP")]), db=db)
    before = _stored(db, YEAR)

    assert save_cp_rows(body=CpRowsIn(rows=[]), db=db)["replaced"] == 0
    assert _stored(db, YEAR) == before


def test_stale_token_is_rejected(db):
    stale = get_overseas_state(basis_year=BASIS_YEAR, db=db).state_token
    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "VP")]), db=db)  # 토큰이 낡는다

    with pytest.raises(HTTPException) as e:
        save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "CL4")], state_token=stale), db=db)
    assert e.value.status_code == 409


# ---------- 적용 시점: 바꾼 버전부터만 ----------


def test_change_applies_from_that_version_onward(db):
    """9월에 계약을 바꾸면 9월 이후 버전만 새 값으로 보고, 8월 이하 버전은 바뀌기 전 값 그대로다.
    공통 RULE(기준정보 변경은 변경한 버전부터 적용, 지난 버전 영향 없음)을 CP에 적용한 것."""
    save_cp_rows(
        body=CpRowsIn(rows=[_row(ASOF_YEAR, "VP", total=100.0)], effective_from=f"{ASOF_YEAR}-06"), db=db
    )
    save_cp_rows(
        body=CpRowsIn(
            rows=[_row(ASOF_YEAR, "VP", total=777.0)], effective_from=f"{ASOF_YEAR}-09",
        ),
        db=db,
    )

    assert _stored(db, ASOF_YEAR, f"{ASOF_YEAR}-06") == [("P1", "VP", 100.0)]
    assert _stored(db, ASOF_YEAR, f"{ASOF_YEAR}-08") == [("P1", "VP", 100.0)], "지난 버전이 소급해서 바뀌었다"
    assert _stored(db, ASOF_YEAR, f"{ASOF_YEAR}-09") == [("P1", "VP", 777.0)]
    assert _stored(db, ASOF_YEAR, f"{ASOF_YEAR}-12") == [("P1", "VP", 777.0)], "이후 버전에 적용되지 않았다"


def test_versions_before_the_first_contract_see_nothing(db):
    """계약이 올라오기 전 버전에는 계약이 없다 — 0원이 아니라 '없음'이어야 CP 정산 칸이 빈칸이 된다."""
    save_cp_rows(
        body=CpRowsIn(rows=[_row(ASOF_YEAR, "VP", total=100.0)], effective_from=f"{ASOF_YEAR}-09"), db=db
    )

    assert _stored(db, ASOF_YEAR, f"{ASOF_YEAR}-08") == []


def test_quarter_amount_follows_the_version(db):
    """분기 정산액은 매번 다시 계산되는 값이라, 보는 버전이 바뀌면 그 버전의 계약으로 계산된다."""
    save_cp_rows(
        body=CpRowsIn(rows=[_row(ASOF_YEAR, "VP", total=4000.0)], effective_from=f"{ASOF_YEAR}-06"), db=db
    )
    save_cp_rows(
        body=CpRowsIn(rows=[_row(ASOF_YEAR, "VP", total=8000.0)], effective_from=f"{ASOF_YEAR}-09"), db=db
    )

    before, _ = cp_quarter_amounts(db, int(ASOF_YEAR), f"{ASOF_YEAR}-08")
    after, _ = cp_quarter_amounts(db, int(ASOF_YEAR), f"{ASOF_YEAR}-09")

    # 계약도 비용도 $ 단위라 분기 분할(÷4)만 한다(전환 전에는 집계가 K$라 ÷1000이 더 붙었다).
    assert before == pytest.approx(4000.0 / 4)
    assert after == pytest.approx(8000.0 / 4)


def test_same_month_change_overwrites_that_set(db):
    """같은 달에 두 번 올리면 그 달 세트만 덮인다 — 지난 버전이 보는 세트는 그대로다."""
    save_cp_rows(
        body=CpRowsIn(rows=[_row(ASOF_YEAR, "VP", total=100.0)], effective_from=f"{ASOF_YEAR}-06"), db=db
    )
    save_cp_rows(
        body=CpRowsIn(rows=[_row(ASOF_YEAR, "VP", total=777.0)], effective_from=f"{ASOF_YEAR}-09"), db=db
    )
    save_cp_rows(
        body=CpRowsIn(rows=[_row(ASOF_YEAR, "VP", total=999.0)], effective_from=f"{ASOF_YEAR}-09"), db=db
    )

    assert _stored(db, ASOF_YEAR, f"{ASOF_YEAR}-09") == [("P1", "VP", 999.0)]
    assert _stored(db, ASOF_YEAR, f"{ASOF_YEAR}-08") == [("P1", "VP", 100.0)]


# ---------- 가드: 깨진 세트와 줄어드는 교체 ----------
# 2026-11-16에 실제로 일어난 사고를 못 박는다. 업로드 파싱이 머리글만 맞추고 본문을 한 줄로 읽어서
# (Type="P" · Projects="J" · Job Title="VP" · Total=$1) POC 3개·직급 행 11개($14,643,313)가 그 한 줄로
# 바뀌었다. CP 저장이 연도 단위 전체 교체라 나머지는 지워졌고, 숫자가 0이 된 자리는 CP 계약 화면이
# 아니라 집계 ② 개발비 탭이라 이틀 뒤에야 "값이 왜 없어졌냐"로 발견됐다.
#
# 가드는 두 가지를 따로 본다:
#  1) 데이터가 깨졌나 — 양식 안에서 합이 맞는지(validate_cp_rows). 금액 크기는 안 본다.
#  2) 사람이 의도했나 — 줄어드는 교체는 확인(confirm_shrink)을 받았을 때만(guard_cp_shrink).
# 하나로는 안 된다: 자기들끼리 합이 맞는 다른 파일(작년 계약서)을 잘못 올리는 건 1)을 통과하고,
# 화면 확인창은 챗봇·일괄 저장·스크립트 경로를 지나지 않는다.

BROKEN = CpRow(  # 그날 실제로 저장된 행
    year=YEAR, poc_type="P", poc_proj="J", title="VP", poc_total=1.0,
)


def test_rejects_a_set_whose_numbers_are_all_zero_but_the_total(db):
    """합계만 있고 인건비·Direct·Overhead·인원·맨먼스가 전부 0인 건 계약이 아니라 깨진 파싱이다."""
    with pytest.raises(HTTPException) as e:
        save_cp_rows(body=CpRowsIn(rows=[BROKEN]), db=db)

    assert e.value.status_code == 400
    assert "전부 0" in e.value.detail


def test_the_broken_set_cannot_wipe_an_existing_contract(db):
    """그날의 사고를 그대로 재현한다 — 깨진 한 줄이 올라와 있는 계약을 지우지 못해야 한다."""
    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, t) for t in ("VP", "CL4", "CL3")]), db=db)
    before = _stored(db, YEAR)

    with pytest.raises(HTTPException):
        save_cp_rows(body=CpRowsIn(rows=[BROKEN], confirm_shrink=True), db=db)

    assert _stored(db, YEAR) == before, "깨진 세트가 올라와 있던 계약을 지웠다"


def test_rejects_a_set_whose_parts_do_not_add_up(db):
    """인건비 + Direct + Overhead ≠ Total이면 거부한다 — 금액이 작아서가 아니라 합이 안 맞아서다.

    상대 오차로 본다: 실제 계약서도 반올림으로 $1쯤 어긋나지만(총액 $6.8M 기준 0.00002%),
    깨진 세트는 100% 어긋난다."""
    rows = [_row(YEAR, "VP")]
    rows[0].poc_ovh = rows[0].poc_ovh * 3  # 합이 Total을 넘어간다

    with pytest.raises(HTTPException) as e:
        save_cp_rows(body=CpRowsIn(rows=rows), db=db)

    assert e.value.status_code == 400
    assert "합이 맞지 않는다" in e.value.detail


def test_small_contracts_are_allowed(db):
    """금액 크기로 판단하지 않는다 — 합이 맞으면 총액 $4짜리 계약도 들어간다.
    "너무 작으면 틀렸다"로 막으면 계약이 진짜 작은 해나 단위를 다르게 적은 해를 못 넣는다."""
    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "VP", total=4.0)]), db=db)

    assert _stored(db, YEAR) == [("P1", "VP", 4.0)]


def test_rounding_in_the_real_form_is_accepted(db):
    """실제 계약서(CP_계약_2026.xlsx)의 POC 하나는 반올림으로 $1 어긋난다 — 거부되면 안 된다."""
    rows = [
        CpRow(
            year=YEAR, poc_type="POC", poc_proj="Multi sensor camera", title=title,
            dedicated=0.25, common=0.0, man_month=0.25, cost_year=cost_year,
            poc_cost_sum=3_460_007.0, poc_mm_sum=16.25,
            poc_direct=1_400_000.0, poc_ovh=1_944_003.0, poc_total=6_804_009.0,  # 합은 6,804,010
            source_file="CP_계약_2026.xlsx",
        )
        for title, cost_year in (("VP", 149_130.0), ("CL4", 424_949.0))
    ]

    save_cp_rows(body=CpRowsIn(rows=rows), db=db)

    assert len(_stored(db, YEAR)) == 2


def test_shrinking_upload_needs_confirmation(db):
    """POC·행·총액 중 하나라도 줄어드는 교체는 확인 없이는 거부된다 — 확인하면 지나간다."""
    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, t) for t in ("VP", "CL4", "CL3")]), db=db)
    before = _stored(db, YEAR)

    with pytest.raises(HTTPException) as e:
        save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "VP")]), db=db)

    assert e.value.status_code == 400
    assert "계약을 줄입니다" in e.value.detail
    assert "직급 행 3개 → 1개" in e.value.detail, "사람이 읽을 비교 숫자가 없다"
    assert _stored(db, YEAR) == before, "거부했는데 계약이 바뀌었다"

    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "VP")], confirm_shrink=True), db=db)
    assert _stored(db, YEAR) == [("P1", "VP", 100.0)]


def test_growing_upload_does_not_ask(db):
    """늘어나는 교체는 묻지 않는다 — 매 업로드마다 확인창이 뜨면 아무도 안 읽는다."""
    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, "VP")]), db=db)

    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, t) for t in ("VP", "CL4")]), db=db)

    assert len(_stored(db, YEAR)) == 2


def test_first_upload_of_a_year_does_not_ask(db):
    """처음 올리는 해는 줄어들 게 없다."""
    save_cp_rows(body=CpRowsIn(rows=[_row(ASOF_YEAR, "VP")], effective_from=f"{ASOF_YEAR}-06"), db=db)

    assert _stored(db, ASOF_YEAR, f"{ASOF_YEAR}-06") == [("P1", "VP", 100.0)]


def test_bulk_state_path_has_the_same_guard(db):
    """일괄 저장(PUT /overseas/state)도 같은 규칙이다 — 화면 확인창은 그 화면에만 있으니
    서버가 경로마다 다르게 판단하면 가드가 있는 쪽을 피해 가면 된다."""
    save_cp_rows(body=CpRowsIn(rows=[_row(YEAR, t) for t in ("VP", "CL4")]), db=db)
    before = _stored(db, YEAR)

    state = get_overseas_state(basis_year=BASIS_YEAR, db=db)
    state.cp = [_row(YEAR, "VP")]
    with pytest.raises(HTTPException) as e:
        save_overseas_state(state=state, db=db)
    assert e.value.status_code == 400
    assert _stored(db, YEAR) == before

    state = get_overseas_state(basis_year=BASIS_YEAR, db=db)
    state.cp = [_row(YEAR, "VP")]
    state.confirm_cp_shrink = True
    save_overseas_state(state=state, db=db)
    assert _stored(db, YEAR) == [("P1", "VP", 100.0)]
