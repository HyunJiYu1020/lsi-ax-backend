"""실적결산 전표매핑 — 해외연구소가 아닌 계정(시장조사비 · 생소비 …)도 매핑을 서버에 남긴다.

여태 매핑 저장 경로가 `/close/overseas/mapping` 하나였고 붙일 곳(scope_key)을 연구소 목록으로
검증했다. 그래서 다른 계정 화면은 아무 매핑도 저장할 수 없었고(연구소가 아닌 키는 404), 전표
매핑이 전부 브라우저에만 남았다. 여기서 지키는 것:

- 계정 공용 경로(`PUT/GET /close/maps`)로 **연구소가 아닌 붙일 곳**(부서코드·계약키)을 저장한다
- 해외연구소는 **그대로 연구소 목록으로 검증**한다(엉뚱한 키가 들어오면 막아야 한다)
- 조회는 계정으로 걸러지고, 사람이 고친 것(user)과 AI 추천(ai)이 구분돼 내려온다
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints import close as cl
from app.shared.schemas.close import DeptRule, MapEdit, MapEditIn, VoucherIngestIn, VoucherLine

YEAR, MONTH = 2026, 9
MK_CODE = "53790100"  # 시장조사비 — 붙일 곳이 연구소가 아니라 부서코드다
DEPT = "C10I2010"


def _line(no, code=MK_CODE, **kw):
    base = dict(voucher_no=no, line_no=1, account_code=code, cost_center=DEPT, krw_amount=1_000_000)
    base.update(kw)
    return VoucherLine(**base)


@pytest.fixture
def seeded(db):
    cl.put_dept_rule(DeptRule(cost_center="", account_code=MK_CODE, account_key="market", note="시장조사비"), db=db)
    cl.ingest_vouchers(
        VoucherIngestIn(
            basis_year=YEAR, basis_month=MONTH, source_file="테스트.xlsx",
            rows=[_line("9900001"), _line("9900002")],
        ),
        db=db,
    )
    return db


def test_non_overseas_account_can_save_mapping(seeded):
    """시장조사비 전표를 부서코드에 붙여 저장한다 — 연구소 목록 검증에 걸리지 않아야 한다."""
    res = cl.put_maps(
        MapEditIn(
            basis_year=YEAR, basis_month=MONTH, account_key="market",
            edits=[MapEdit(voucher_no="9900001", line_no=1, scope_key=DEPT)],
        ),
        db=seeded,
    )
    assert res.saved == 1

    rows = {m.voucher_no: m for m in cl.list_maps(basis_year=YEAR, basis_month=MONTH, account_key="market", db=seeded)}
    assert rows["9900001"].scope_key == DEPT
    # 사람이 고른 매핑에는 신뢰도 개념이 없고, 'AI 다시 매칭'이 되돌리지 않도록 user로 남아야 한다.
    assert (rows["9900001"].decided_by, rows["9900001"].confidence) == ("user", "")
    assert rows["9900001"].status == "user"


def test_overseas_still_validates_lab(seeded):
    """해외연구소는 붙일 곳이 연구소 pk다 — 없는 연구소를 보내면 막는다(검증을 같이 풀어버리면
    오타 하나로 집계에서 사라지는 전표가 생긴다)."""
    with pytest.raises(HTTPException) as e:
        cl.put_maps(
            MapEditIn(
                basis_year=YEAR, basis_month=MONTH, account_key="overseas",
                edits=[MapEdit(voucher_no="9900001", line_no=1, scope_key=DEPT)],
            ),
            db=seeded,
        )
    assert e.value.status_code == 404


def test_unknown_account_is_rejected(seeded):
    with pytest.raises(HTTPException) as e:
        cl.put_maps(
            MapEditIn(basis_year=YEAR, basis_month=MONTH, account_key="없는계정", edits=[]),
            db=seeded,
        )
    assert e.value.status_code == 404


def test_exclude_and_transfer_round_trip(seeded):
    """제외·이관도 사람 판단이라 서버에 남아야 한다 — 화면은 이 값으로 '_ex' · '_mv:계정'을 그린다."""
    cl.put_maps(
        MapEditIn(
            basis_year=YEAR, basis_month=MONTH, account_key="market",
            edits=[
                MapEdit(voucher_no="9900001", line_no=1, excluded=True, exclude_reason="대상 아님"),
                MapEdit(voucher_no="9900002", line_no=1, transferred_to="consum"),
            ],
        ),
        db=seeded,
    )
    rows = {m.voucher_no: m for m in cl.list_maps(basis_year=YEAR, basis_month=MONTH, db=seeded)}
    assert (rows["9900001"].excluded, rows["9900001"].exclude_reason) == (True, "대상 아님")
    assert rows["9900001"].status == "excluded"
    assert rows["9900002"].transferred_to == "consum"
    # 이관 보낸 전표는 보낸 계정 집계에서 빠져야 하니, 조회도 받는 계정 쪽에 잡힌다.
    got = [m.voucher_no for m in cl.list_maps(basis_year=YEAR, basis_month=MONTH, account_key="consum", db=seeded)]
    assert "9900002" in got


def test_mapping_survives_voucher_reload(seeded):
    """전표가 다시 와도 매핑은 살아남는다(공통 RULE) — 화면이 매달 같은 수정을 반복하지 않는 근거."""
    cl.put_maps(
        MapEditIn(
            basis_year=YEAR, basis_month=MONTH, account_key="market",
            edits=[MapEdit(voucher_no="9900001", line_no=1, scope_key=DEPT)],
        ),
        db=seeded,
    )
    res = cl.ingest_vouchers(
        VoucherIngestIn(
            basis_year=YEAR, basis_month=MONTH, source_file="재수신.xlsx",
            rows=[_line("9900001", krw_amount=2_000_000), _line("9900003")],
        ),
        db=seeded,
    )
    assert res.kept_maps >= 1
    rows = {m.voucher_no: m for m in cl.list_maps(basis_year=YEAR, basis_month=MONTH, account_key="market", db=seeded)}
    assert rows["9900001"].scope_key == DEPT and rows["9900001"].decided_by == "user"
