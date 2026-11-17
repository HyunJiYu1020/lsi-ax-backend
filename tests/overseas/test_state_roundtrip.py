"""GET · PUT /overseas/state 왕복 스냅샷 — 지금 동작을 고정해 이후 리팩토링의 회귀 기준으로 쓴다.

PLAN.md Phase 0: 이 엔드포인트는 화면 전체 상태를 한 번에 주고받는 가장 큰 접합부(overseas.py:541-909)
이고, Phase 1(services/ 추출) · Phase 2(낙관적 잠금) · Phase 5(도메인별 분해)가 모두 이 위를 지나간다.
그래서 "무엇이 바뀌면 안 되는가"를 먼저 못 박아 둔다.

핵심 성질은 **왕복 동일성**이다: GET한 상태를 그대로 PUT하고 다시 GET하면 똑같아야 한다.
이 성질은 DB에 어떤 데이터가 들어 있든 성립해야 하므로(로컬 DB 내용에 의존하지 않는다) 금액을
하드코딩한 골든 스냅샷보다 회귀 기준으로 튼튼하다. 금액이 아니라 "구조와 규칙"을 고정한다.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.accounts.overseas.endpoints.collect import yyyymm, get_overseas_state, save_overseas_state
from app.shared.models.common import CostEntry, CostEntryDetail

BASIS_YEAR = 2026
VERSIONS = ("cur", "prev", "prior", "plan")


@pytest.fixture
def state(db):
    return get_overseas_state(basis_year=BASIS_YEAR, db=db)


def dump(s):
    """비교용 상태. state_token은 뺀다 — 저장을 하면 타임스탬프가 바뀌어 토큰도 당연히 바뀐다
    (그게 동시 편집 보호의 작동 원리다). 토큰 자체의 동작은 test_state_token_* 가 따로 본다."""
    d = s.model_dump()
    d.pop("state_token", None)
    return d


# ---------- 왕복 동일성 ----------


def test_get_put_get_is_identical(db, state):
    """GET → PUT(그대로) → GET이 완전히 같은 상태를 돌려주는지.

    이후 모든 단계의 검증 기준이다. 저장 경로가 읽기 경로와 어긋나면(예: 분기·연간 재계산을
    한쪽에만 넣으면) 여기서 바로 깨진다 — PLAN P1-3이 경고하는 "같은 데이터, 두 가지 쓰기 규칙"이
    실제로 생겼는지 보는 자리다."""
    before = dump(state)

    assert save_overseas_state(state=state, db=db)["status"] == "ok"
    after = dump(get_overseas_state(basis_year=BASIS_YEAR, db=db))

    assert after == before, "같은 상태를 저장했는데 다시 읽은 값이 달라졌다"


def test_put_twice_changes_nothing_more(db, state):
    """두 번 저장해도 결과가 같은지(멱등). 재시도·중복 저장이 데이터를 늘리지 않아야 한다 —
    vendor·cp는 '단위 교체'(delete+insert)라 특히 중복 누적이 생기기 쉬운 자리다."""
    save_overseas_state(state=state, db=db)
    once = dump(get_overseas_state(basis_year=BASIS_YEAR, db=db))

    save_overseas_state(state=get_overseas_state(basis_year=BASIS_YEAR, db=db), db=db)
    twice = dump(get_overseas_state(basis_year=BASIS_YEAR, db=db))

    assert twice == once
    # 행 수가 늘지 않았는지도 따로 본다(값이 같아도 중복 행이 생기면 다음 단계에서 터진다).
    assert len(twice["vendor"]) == len(once["vendor"])
    assert len(twice["cp"]) == len(once["cp"])
    assert len(twice["reason"]) == len(once["reason"])


# ---------- 응답 모양 (화면·챗봇이 의존하는 계약) ----------


def test_state_shape_is_frozen(db, state):
    """응답 구조. 프런트와 MCP 도구가 이 모양을 그대로 집어 쓰므로 바뀌면 둘 다 깨진다."""
    assert set(state.V) == set(VERSIONS)
    assert set(state.files) == set(VERSIONS)
    assert 0 <= state.basis_month <= 12
    assert state.basis_year == BASIS_YEAR
    assert state.step >= 0

    # 실적 스냅샷은 cur에만 쓰인다(schemas/overseas.py:21-22).
    assert set(state.actualV) <= {"cur"}
    for snap in state.actualV.values():
        assert 1 <= snap.thru_month <= 12

    for version in VERSIONS:
        vd = state.V[version]
        assert len(vd.etc) % 12 == 0 and len(vd.etc) >= 12
        for pk, cats in vd.d.items():
            assert pk != "_ETC", "원화 기타는 d가 아니라 etc로 따로 내려준다"
            for key, months in cats.items():
                # 12의 배수 — 기간을 다음 연도까지 늘려 받는 확장 지점(_entry_months_bulk).
                assert len(months) % 12 == 0 and len(months) >= 12, f"{version}/{pk}/{key}"

    # 메모 키는 "base|period|scope" 3토막이다 — PUT이 "|"로 쪼개 3개가 아니면 조용히 버린다
    # (overseas.py:829-831). GET이 다른 모양을 내면 저장이 소리 없이 유실된다.
    for key in state.note:
        assert len(key.split("|")) == 3, f"메모 키 모양이 깨졌다: {key!r}"

    for row in state.vendor:
        assert len(row.m) == 12
        assert row.row_no >= 1


def test_row_lists_have_a_defined_order(db, state):
    """증감사유 · 외주용역 · CP 목록이 정해진 순서로 내려오는지.

    정렬이 없으면 같은 요청이 매번 다른 순서를 돌려준다(upsert가 행을 건드리면 물리 순서가
    바뀐다 — 실제로 증감사유 1,936행이 저장 한 번에 섞였고, 그래서 위의 왕복 동일성 테스트가
    깨졌다). 순서가 흔들리면 화면의 증감사유 표가 새로 고칠 때마다 섞이고, 챗봇이 "위에서 세
    번째 줄"처럼 위치로 말할 때도 어긋난다.

    실제 순서값을 파이썬의 sorted()와 비교하지는 않는다 — DB 정렬은 Postgres 콜레이션을 따르므로
    ('A', 'amort', 'asset', 'B' 순) 파이썬의 코드포인트 정렬과 다르다. 중요한 건 "어떤 순서인가"가
    아니라 "매번 같은 순서인가"이고, 그건 ORDER BY 키가 행마다 유일할 때 보장된다.
    """
    # ① 정렬 키가 유일한지 — 같은 키가 둘 이상이면 그 사이 순서는 ORDER BY로도 정해지지 않는다.
    for name, rows, key in (
        ("증감사유", state.reason, lambda r: (r.lab_code, r.version, r.category_key, r.block)),
        ("외주용역", state.vendor, lambda r: (r.lab_code, r.version, r.row_no)),
        ("CP", state.cp, lambda r: (r.year, r.poc_type, r.poc_proj, r.title)),
    ):
        keys = [key(r) for r in rows]
        assert len(keys) == len(set(keys)), f"{name} 정렬 키에 중복이 있어 순서가 정해지지 않는다"

    # ② 저장을 한 번 끼워도 순서가 그대로인지(upsert가 물리 순서를 바꿔도 영향받지 않아야 한다).
    save_overseas_state(state=state, db=db)
    after = get_overseas_state(basis_year=BASIS_YEAR, db=db)
    assert [dump(r) for r in after.reason] == [dump(r) for r in state.reason]
    assert [dump(r) for r in after.cp] == [dump(r) for r in state.cp]
    assert [dump(r) for r in after.vendor] == [dump(r) for r in state.vendor]


def test_cp_only_contains_basis_year(db, state):
    """CP 계약은 기준연도 것만 내려준다 — 화면이 그 연도만 그리므로(core.js가 r.year로 거른다).

    PLAN P3에 "GET /overseas/state가 전 연도 CP 반환 → 2026-11-15 수정 완료"로 적힌 건이다.
    전 연도를 내려주면 PUT이 받은 연도를 전체 교체(overseas.py:878-881)하므로 되돌아가기 쉬워서
    여기서 고정해 둔다."""
    for row in state.cp:
        assert row.year == f"{BASIS_YEAR:04d}", f"기준연도가 아닌 CP가 섞였다: {row.year}"


def test_prev_is_derived_from_the_actual_snapshot(db, state):
    """전실행(prev)은 저장된 버전이 아니라 cur의 실적 스냅샷에서 유도한 값이다(overseas.py:617-619).

    PUT은 prev·prior를 아예 쓰지 않으므로(overseas.py:785-786), 이 유도 규칙이 깨지면 전실행
    대비 증감이 통째로 틀어진다. '유도된 값'이라는 사실 자체를 고정한다."""
    snap = state.actualV.get("cur")
    if snap is None:
        assert state.V["prev"].d == {}
        return

    assert state.V["prev"].d == snap.d
    assert state.V["prev"].etc == snap.etc


# ---------- 빈칸(미등록) 처리 ----------


def test_unregistered_months_survive_the_round_trip(db):
    """미등록(None)과 "0으로 올라옴"(0.0)의 구분이 왕복에서 살아남는지.

    공통 RULE(빈칸 처리): None은 빈칸, 0.0은 "0원으로 보고됨"이고 둘은 다른 뜻이다. 저장할 때
    None을 0으로 적으면 다시 읽을 때 구분이 사라진다(_bulk_upsert_cost_entries가 None인 달을
    건너뛰는 이유).

    미등록 칸을 테스트가 직접 만든다 — 시드 데이터는 12개월을 다 채우기 때문에 DB에 있는 걸
    그대로 쓰면 None 쪽은 한 번도 확인되지 않는다(실제로 처음엔 그래서 0.0만 검사하고 있었다).
    detail 행을 지우는 게 "아직 안 올라온 달"의 실제 모양이다."""
    state = get_overseas_state(basis_year=BASIS_YEAR, db=db)
    if not state.basis_month:
        pytest.skip("기준월이 없어 당월 속보를 읽을 수 없다")

    # 화면에 실제로 내려가는 칸에서 고른다 — 연구소가 추가한 행(label 있는 cost_entries)은 GET이
    # d[pk]에서 빼고 extra-rows로 따로 내려주므로, DB에서 아무 detail이나 집으면 상태에 없는
    # 키가 걸린다(파생 행 asset_cost_per_head_sarc_cpu가 그렇게 걸렸다).
    target = next(
        (
            (pk, cat, j)
            for pk, cats in state.V["cur"].d.items()
            for cat, months in cats.items()
            for j, v in enumerate(months[:12])
            if v is not None
        ),
        None,
    )
    if target is None:
        pytest.skip("당월 속보에 저장된 값이 없다")
    pk, cat, j = target

    detail = db.scalars(
        select(CostEntryDetail)
        .join(CostEntry, CostEntry.id == CostEntryDetail.cost_entry_id)
        .where(
            CostEntry.account_code == "overseas",
            CostEntry.version == "forecast",
            CostEntry.yyyymm == yyyymm(BASIS_YEAR, state.basis_month),
            CostEntry.scope_code == pk,
            CostEntry.category_code == cat,
            CostEntryDetail.period_type == "MONTH",
            CostEntryDetail.period == j + 1,
        )
    ).first()
    assert detail is not None, f"{pk}/{cat}[{j}]에 값이 있는데 detail 행을 못 찾았다"

    db.delete(detail)
    db.flush()

    state = get_overseas_state(basis_year=BASIS_YEAR, db=db)
    assert state.V["cur"].d[pk][cat][j] is None, "detail을 지웠는데 미등록으로 읽히지 않는다"

    zeros = [
        (p, k, i)
        for p, cats in state.V["cur"].d.items()
        for k, months in cats.items()
        for i, v in enumerate(months)
        if v == 0.0
    ]

    save_overseas_state(state=state, db=db)
    after = get_overseas_state(basis_year=BASIS_YEAR, db=db)

    assert after.V["cur"].d[pk][cat][j] is None, f"미등록 칸이 값으로 바뀌었다: {pk}/{cat}[{j}]"
    for p, k, i in zeros:
        assert after.V["cur"].d[p][k][i] == 0.0, f"0으로 올라온 칸이 빈칸이 됐다: {p}/{k}[{i}]"
    assert zeros, "0.0으로 올라온 칸이 하나도 없어 '0 vs 빈칸' 구분의 반쪽만 확인했다"


def test_put_cannot_clear_a_month(db, state):
    """PUT으로 None을 보내도 이미 저장된 달은 지워지지 않는다(덧쓰기만 한다).

    _bulk_upsert_cost_entries가 amount=None인 달을 건너뛰기 때문이다 — "미등록을 0으로 저장하지
    않기" 위한 선택이고, 그 대가로 "값을 빈칸으로 되돌리는" 경로가 없다. 지금은 화면에 지우는
    기능이 없어 문제되지 않지만, Phase 5에서 도메인별 저장 API를 만들 때 반드시 마주칠 제약이라
    의도된 동작으로 고정해 둔다(바뀌면 이 테스트가 알려준다)."""
    target = next(
        (
            (pk, key, j)
            for pk, cats in state.V["cur"].d.items()
            for key, months in cats.items()
            for j, v in enumerate(months)
            if v is not None
        ),
        None,
    )
    if target is None:
        pytest.skip("당월 속보에 저장된 값이 없다")
    pk, key, j = target
    kept = state.V["cur"].d[pk][key][j]

    state.V["cur"].d[pk][key][j] = None
    save_overseas_state(state=state, db=db)

    after = get_overseas_state(basis_year=BASIS_YEAR, db=db)
    assert after.V["cur"].d[pk][key][j] == kept, "None을 보냈더니 저장된 값이 지워졌다(동작이 바뀌었다)"


# ---------- 값 수정이 실제로 저장되는지 ----------


def test_edited_month_is_saved(db, state):
    """월 값을 하나 고쳐 PUT하면 그 값만 바뀌어 읽히는지. 왕복 동일성 테스트가 "아무것도 저장하지
    않아도" 통과할 수 있으므로(둘 다 빈 결과면 같다), 저장이 실제로 일어나는지 따로 확인한다."""
    target = next(
        (
            (pk, key, j)
            for pk, cats in state.V["cur"].d.items()
            for key, months in cats.items()
            for j, v in enumerate(months)
            if v is not None
        ),
        None,
    )
    if target is None:
        pytest.skip("당월 속보에 저장된 값이 없다")
    pk, key, j = target
    before = dump(state)
    new_value = (state.V["cur"].d[pk][key][j] or 0.0) + 1234.5

    state.V["cur"].d[pk][key][j] = new_value
    save_overseas_state(state=state, db=db)
    after = dump(get_overseas_state(basis_year=BASIS_YEAR, db=db))

    assert after["V"]["cur"]["d"][pk][key][j] == pytest.approx(new_value)
    # 고친 칸 하나 말고는 그대로여야 한다.
    before["V"]["cur"]["d"][pk][key][j] = pytest.approx(new_value)
    assert after["V"]["cur"] == before["V"]["cur"]


def test_step_round_trips(db, state):
    """진행 단계(step)는 상태와 같이 저장된다(overseas.py:896-901)."""
    state.step = 2
    save_overseas_state(state=state, db=db)
    assert get_overseas_state(basis_year=BASIS_YEAR, db=db).step == 2


# ---------- 거부해야 하는 입력 ----------


def test_put_rejects_unknown_lab_pk(db, state):
    """모르는 연구소 코드는 400으로 막는다 — 오타 하나로 유령 연구소 데이터가 생기면 집계가
    조용히 틀어진다(overseas.py:767-775)."""
    state.V["cur"].d["NOPE:XX"] = {"sal": [1.0] * 12}

    with pytest.raises(HTTPException) as e:
        save_overseas_state(state=state, db=db)
    assert e.value.status_code == 400
    assert "NOPE:XX" in str(e.value.detail)


def test_put_rejects_unknown_lab_code_in_reason(db, state):
    """증감사유·외주용역의 연구소 코드도 같이 검증한다(overseas.py:838-847). 이쪽은 파트 없는
    연구소 코드(lab_code)라 V의 pk와 검증 대상이 다르다."""
    from app.accounts.overseas.schemas import ReasonRow

    state.reason.append(ReasonRow(lab_code="NOPE", version="cur", category_key="sal", block="y"))

    with pytest.raises(HTTPException) as e:
        save_overseas_state(state=state, db=db)
    assert e.value.status_code == 400
    assert "NOPE" in str(e.value.detail)


def test_rejected_put_saves_nothing(db, state):
    """거부된 저장은 아무것도 남기지 않는다 — 검증이 쓰기보다 앞에 있어야 한다.

    PLAN P1-2가 "원자성(commit 1회)은 유지 가치"로 꼽은 성질이다. 부분 저장이 생기면 화면은
    400을 받았는데 DB에는 절반만 들어가 있는 상태가 된다."""
    before = dump(state)
    pk, key = next(
        ((pk, key) for pk, cats in state.V["cur"].d.items() for key in cats),
        (None, None),
    )
    if pk is None:
        pytest.skip("당월 속보에 저장된 값이 없다")

    # 유효한 수정 + 잘못된 연구소를 한 번에 보낸다.
    state.V["cur"].d[pk][key][0] = (state.V["cur"].d[pk][key][0] or 0.0) + 999.0
    state.V["cur"].d["NOPE:XX"] = {"sal": [1.0] * 12}

    with pytest.raises(HTTPException):
        save_overseas_state(state=state, db=db)

    after = dump(get_overseas_state(basis_year=BASIS_YEAR, db=db))
    assert after == before, "거부된 저장이 일부 반영됐다"


# ---------- 동시 편집 보호 (PLAN.md P1-2 · Phase 2) ----------


def test_state_token_is_issued_and_stable(db, state):
    """GET이 토큰을 내려주고, 아무것도 바뀌지 않았으면 같은 토큰이 나온다."""
    assert state.state_token, "GET이 state_token을 안 내려준다"
    again = get_overseas_state(basis_year=BASIS_YEAR, db=db)
    assert again.state_token == state.state_token


def test_state_token_changes_after_a_save(db, state):
    """저장하면 토큰이 바뀐다 — 그래야 그 사이 저장이 있었는지 알 수 있다.
    그리고 PUT이 새 토큰을 돌려줘야 화면이 이어서 저장할 수 있다."""
    before = state.state_token
    res = save_overseas_state(state=state, db=db)

    assert res["state_token"] and res["state_token"] != before, "PUT이 새 토큰을 안 준다"
    assert get_overseas_state(basis_year=BASIS_YEAR, db=db).state_token == res["state_token"]


def test_stale_token_is_rejected_with_409(db, state):
    """다른 세션이 먼저 저장한 뒤에 옛 토큰으로 저장하면 막는다 — 조용히 덮어쓰면 먼저 저장한
    사람의 수정이 사라지고, 그 사실을 아무도 모른다(PLAN P1-2의 핵심 사고)."""
    stale = state.state_token

    # 다른 세션이 먼저 저장했다고 가정
    other = get_overseas_state(basis_year=BASIS_YEAR, db=db)
    save_overseas_state(state=other, db=db)

    state.state_token = stale
    with pytest.raises(HTTPException) as e:
        save_overseas_state(state=state, db=db)
    assert e.value.status_code == 409
    assert "다시 불러온" in str(e.value.detail)


def test_conflict_saves_nothing(db, state):
    """409로 막힌 저장은 아무것도 남기지 않는다 — 검증이 쓰기보다 앞에 있어야 한다."""
    stale = state.state_token
    other = get_overseas_state(basis_year=BASIS_YEAR, db=db)
    save_overseas_state(state=other, db=db)
    after_other = dump(get_overseas_state(basis_year=BASIS_YEAR, db=db))

    # 값을 고쳐서 옛 토큰으로 보낸다
    pk, cat = next(((p, c) for p, cats in state.V["cur"].d.items() for c in cats), (None, None))
    if pk is None:
        pytest.skip("당월 속보에 저장된 값이 없다")
    state.V["cur"].d[pk][cat][0] = (state.V["cur"].d[pk][cat][0] or 0.0) + 777.0
    state.state_token = stale

    with pytest.raises(HTTPException):
        save_overseas_state(state=state, db=db)

    assert dump(get_overseas_state(basis_year=BASIS_YEAR, db=db)) == after_other, "충돌인데 일부가 저장됐다"


def test_empty_token_skips_the_check(db, state):
    """토큰을 안 보내면 검사하지 않는다 — 토큰을 모르는 기존 클라이언트를 깨지 않기 위함이고,
    서버에서 이 동작만 끄면 기능을 롤백할 수 있다(PLAN Phase 2의 롤백 조건)."""
    other = get_overseas_state(basis_year=BASIS_YEAR, db=db)
    save_overseas_state(state=other, db=db)  # 토큰이 낡게 만든다

    state.state_token = ""  # 옛 클라이언트처럼
    assert save_overseas_state(state=state, db=db)["status"] == "ok"


# ---------- 경영계획 차수 (2026-11-17) ----------
# 전에는 읽기·쓰기가 `PLAN_NO = "P00"` 고정이었다. 이제 읽기는 적용 차수(★ → 확정 최신 → 최신),
# 쓰기는 "확정되지 않은 차수"만 대상으로 한다.
def test_state_tells_which_plan_revision_it_is(db, state):
    """GET이 V["plan"]의 차수를 같이 준다 — 화면이 "P02 기준"이라고 밝힐 수 있어야 한다.
    차수가 없으면 빈 문자열이고, 그건 "경영계획 0원"이 아니라 "데이터 없음"이다."""
    from app.accounts.overseas.services.entries import applied_plan_no

    assert state.plan_no == applied_plan_no(db, BASIS_YEAR)
    if not state.plan_no:
        assert not state.V["plan"].d


def test_save_does_not_overwrite_a_confirmed_plan_revision(db, state):
    """확정된 차수는 화면 저장이 덮지 않는다 — 덮으면 "확정"이 뜻을 잃고, 이미 보고한 경영계획
    대비 숫자가 소급해서 바뀐다. 막지 않고 건너뛰되 응답(plan_skipped)에 밝혀야 한다 —
    조용히 버리면 경영계획이 안 들어간 걸 아무도 모른다."""
    from app.accounts.overseas.services.entries import plan_version_rows
    from app.shared.models.plan import ManagementPlanEntryDetail

    rows = plan_version_rows(db, BASIS_YEAR)
    if not rows:
        pytest.skip("그 해 경영계획 차수가 없어 확정 상태를 만들 수 없다")
    for r in rows:
        r.is_confirmed = True
    db.flush()

    # 경영계획 값을 하나 넣어 보낸다(화면이 들고 있는 비교용 값과 같은 모양).
    pk = next(iter(state.V["cur"].d), None)
    if pk is None:
        pytest.skip("저장할 연구소 값이 없다")
    state.V["plan"].d = {pk: {"hc": [9.0] * 12}}
    before = db.scalar(select(ManagementPlanEntryDetail.amount).where(ManagementPlanEntryDetail.amount == 9.0))

    out = save_overseas_state(state, db=db)

    assert out["status"] == "ok", "경영계획 때문에 저장 전체를 세우지는 않는다"
    assert "plan_skipped" in out and "확정" in out["plan_skipped"]
    assert db.scalar(select(ManagementPlanEntryDetail.amount).where(ManagementPlanEntryDetail.amount == 9.0)) == before


def test_save_opens_the_first_revision_when_the_year_has_none(db, state):
    """그 해 차수가 아예 없으면 첫 차수(P00)를 만들어 저장한다 — 종전 동작 그대로다.
    차수가 하나라도 있으면 그때부터 "확정되지 않은 차수"만 대상이다(위 테스트)."""
    from app.shared.models.plan import ManagementPlanVersion
    from app.accounts.overseas.services.entries import FIRST_PLAN_NO

    year = 2097  # 실제 데이터와 안 겹치는 미래 연도
    assert not db.scalars(select(ManagementPlanVersion).where(ManagementPlanVersion.year == year)).all()
    pk = next(iter(state.V["cur"].d), None)
    if pk is None:
        pytest.skip("저장할 연구소 값이 없다")
    state.basis_year = year
    state.V["plan"].d = {pk: {"hc": [3.0] * 12}}

    out = save_overseas_state(state, db=db)

    assert "plan_skipped" not in out
    opened = db.scalar(
        select(ManagementPlanVersion).where(
            ManagementPlanVersion.year == year, ManagementPlanVersion.plan_no == FIRST_PLAN_NO
        )
    )
    assert opened is not None and opened.is_confirmed is False
