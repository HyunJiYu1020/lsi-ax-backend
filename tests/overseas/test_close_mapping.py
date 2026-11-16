"""해외연구소 실적결산 화면이 서버에 기대하는 계약 — 취합 → 가공 → 1차 완료.

화면 단위로 테스트한다(엔드포인트 직접 호출). 여기서 지켜야 하는 건 숫자의 정확성보다 **판단이
사라지지 않는 것**이다: 전표가 다시 와도 담당자 수정이 살아남고, 'AI 다시 매칭'이 사람 판단을
덮지 않고, 제외·이관한 전표가 집계에서 두 번 세어지지 않고, 해외연구소의 완료가 '1차 완료'에서
멈추는 것.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.accounts.overseas.endpoints import close as cl
from app.shared.models.close import CloseAccountStage, CloseMatchRule, CloseVoucher, CloseVoucherMap
from app.shared.schemas.close import DeptRule, MapEdit, MapEditIn, MatchRuleIn, StageIn, VoucherIngestIn, VoucherLine

YEAR, MONTH = 2026, 9
OV_CODE = "53840100"  # 해외연구소 비용계정 (테스트 안에서만 쓰는 기준정보)
NRE_CENTER = "C10I8000"  # NRE 공용 부서코드
LAB_CENTER = "C10I3000"  # 연구소 전용으로 쓰는 코스트센터


@pytest.fixture
def seeded(db):
    """분류 기준정보 2줄만 깔고 시작한다 — 전표는 테스트마다 다르게 넣는다."""
    cl.put_dept_rule(
        DeptRule(cost_center="", account_code=OV_CODE, account_key="overseas", note="해외연구소 비용계정"),
        db=db,
    )
    cl.put_dept_rule(
        DeptRule(
            cost_center=NRE_CENTER,
            account_code=OV_CODE,
            account_key="overseas",
            is_shared=True,
            note="NRE 공용 — 적요 CP로 가린다",
        ),
        db=db,
    )
    return db


def _line(no, **kw):
    base = dict(
        voucher_no=no,
        line_no=1,
        account_code=OV_CODE,
        account_name="해외연구소",
        cost_center=LAB_CENTER,
        cost_center_name="SoC설계그룹",
        currency="KRW",
        krw_amount=1_000_000,
    )
    return VoucherLine(**(base | kw))


def _ingest(db, lines, **kw):
    return cl.ingest_vouchers(
        VoucherIngestIn(basis_year=YEAR, basis_month=MONTH, source_file="테스트.xlsx", rows=lines, **kw), db=db
    )


def _mapping(db):
    return cl.get_overseas_mapping(basis_year=YEAR, basis_month=MONTH, db=db)


def _map_of(result, no):
    return next(m for m in result.maps if m.voucher_no == no)


def _lab_pk(db):
    """이 DB에 실제로 있는 연구소 pk 하나 — 기준정보를 테스트가 새로 만들지 않는다."""
    labs = cl._lab_list(db, YEAR, MONTH)
    if not labs:
        pytest.skip("연구소 기준정보가 없어 스킵합니다.")
    return labs[0]["pk"]


# ---------- 1단계 취합: 분류와 미분류 ----------
def test_classifies_by_dept_and_account_and_flags_the_rest(seeded):
    """부서코드+비용계정으로 분류하고, 기준정보에 없는 조합은 **미분류로 남긴다**(오류로 막지 않는다).
    막아 버리면 담당자가 기준정보를 고칠 기회 없이 적재 자체가 실패한다."""
    got = _ingest(seeded, [_line("9000001"), _line("9000002", account_code="99999999")])

    assert got.received == 2
    assert got.unclassified == 1
    by_key = {r.account_key: r for r in got.by_account}
    assert by_key["overseas"].voucher_count == 1
    assert by_key[""].voucher_count == 1  # 미분류는 빈 key 행으로 내려간다
    assert "미분류" in got.note


def test_reclassify_after_fixing_master_data(seeded):
    """기준정보를 고치고 재분류하면 미분류였던 전표가 계정에 붙고, 그때 비로소 AI 매칭이 생긴다 —
    분류만 되고 매칭이 없으면 2단계 표에 안 보인다."""
    _ingest(seeded, [_line("9000003", account_code="99999999")])
    cl.put_dept_rule(
        DeptRule(cost_center="", account_code="99999999", account_key="overseas", note="추가된 기준"), db=seeded
    )

    got = cl.reclassify(basis_year=YEAR, basis_month=MONTH, db=seeded)

    assert got.unclassified == 0
    assert got.new_maps == 1
    assert any(v.voucher_no == "9000003" for v in _mapping(seeded).vouchers)


# ---------- 재수신: 담당자 수정이 살아남는다 ----------
def test_reingest_keeps_manual_mapping(seeded):
    """ERP가 같은 달을 다시 보내도 담당자가 고친 매핑은 살아남는다(공통 RULE). 이게 깨지면 재수신
    한 번에 한 달치 수작업이 날아간다."""
    pk = _lab_pk(seeded)
    _ingest(seeded, [_line("9000010")])
    cl.put_overseas_mapping(
        MapEditIn(
            basis_year=YEAR, basis_month=MONTH, edits=[MapEdit(voucher_no="9000010", line_no=1, scope_key=pk)]
        ),
        db=seeded,
    )

    # 금액이 바뀐 같은 전표 + 새 전표가 다시 온다
    got = _ingest(seeded, [_line("9000010", krw_amount=2_000_000), _line("9000011")])

    assert got.new_maps == 1  # 새 전표만 매칭했다
    m = _map_of(_mapping(seeded), "9000010")
    assert (m.scope_key, m.decided_by) == (pk, "user")  # 사람이 고른 매핑 그대로
    assert next(v for v in _mapping(seeded).vouchers if v.voucher_no == "9000010").krw_amount == 2_000_000


def test_reingest_drops_cancelled_vouchers_but_not_their_mapping(seeded):
    """이번에 안 온 전표는 지운다(전기 취소분). 매핑은 남겨 둔다 — 같은 전표가 돌아오면 또 살아난다."""
    _ingest(seeded, [_line("9000020"), _line("9000021")])

    got = _ingest(seeded, [_line("9000020")])

    assert got.deleted == 1
    assert [v.voucher_no for v in _mapping(seeded).vouchers] == ["9000020"]
    assert seeded.scalar(
        select(CloseVoucherMap).where(CloseVoucherMap.voucher_no == "9000021")
    ) is not None


# ---------- 2단계 가공: NRE 공용 · 제외 · 이관 ----------
def test_nre_shared_center_without_cp_is_excluded_from_totals(seeded):
    """NRE 공용 부서코드 중 적요에 CP가 없는 전표는 제외로 빠지고, 연구소 합계에 들어가지 않는다.
    다만 제외분 합계로는 보여줘서 전표 총액과 맞춰볼 수 있게 한다."""
    _ingest(
        seeded,
        [
            _line("9000030", cost_center=NRE_CENTER, text="9월 공용 설비 사용분", krw_amount=5_000_000),
            _line("9000031", cost_center=NRE_CENTER, text="9월 CP 정산분", krw_amount=3_000_000),
        ],
    )

    got = _mapping(seeded)

    assert _map_of(got, "9000030").status == "excluded"
    assert got.excluded_total == 5_000_000
    assert got.actual_total == pytest.approx(
        sum(r.actual_amount for r in got.labs)
    )  # 제외분은 어느 연구소에도 안 붙는다
    assert 5_000_000 not in [r.actual_amount for r in got.labs]


def test_transfer_removes_amount_from_this_account(seeded):
    """다른 계정으로 이관한 전표는 이 계정 집계에서 빠지고 '이관 보냄'으로 남는다 — 양쪽 담당자가
    같은 전표를 두 번 세지 않게."""
    _ingest(seeded, [_line("9000040", krw_amount=7_000_000)])

    cl.put_overseas_mapping(
        MapEditIn(
            basis_year=YEAR,
            basis_month=MONTH,
            edits=[MapEdit(voucher_no="9000040", line_no=1, transferred_to="dep")],
        ),
        db=seeded,
    )
    got = _mapping(seeded)

    assert _map_of(got, "9000040").status == "sent"
    assert got.transfer_out_total == 7_000_000
    assert got.actual_total == 0  # 이관 보낸 금액은 해외연구소 실적이 아니다


def test_cannot_both_exclude_and_assign_a_lab(seeded):
    """제외와 연구소 지정은 배타적이다 — 둘 다 받으면 "제외했는데 합계에 들어가는" 전표가 생긴다."""
    pk = _lab_pk(seeded)
    _ingest(seeded, [_line("9000050")])

    with pytest.raises(HTTPException) as e:
        cl.put_overseas_mapping(
            MapEditIn(
                basis_year=YEAR,
                basis_month=MONTH,
                edits=[MapEdit(voucher_no="9000050", line_no=1, scope_key=pk, excluded=True)],
            ),
            db=seeded,
        )

    assert e.value.status_code == 400


def test_rematch_does_not_touch_manual_mapping(seeded):
    """'AI 다시 매칭'은 AI가 정한 것만 다시 계산한다. 사람이 고른 걸 되돌리면 같은 수정을 매달 다시
    해야 하고, 그러면 아무도 수정하지 않게 된다."""
    pk = _lab_pk(seeded)
    _ingest(seeded, [_line("9000060", text="알 수 없는 적요"), _line("9000061", text="알 수 없는 적요2")])
    cl.put_overseas_mapping(
        MapEditIn(
            basis_year=YEAR, basis_month=MONTH, edits=[MapEdit(voucher_no="9000060", line_no=1, scope_key=pk)]
        ),
        db=seeded,
    )

    cl.rematch_overseas(basis_year=YEAR, basis_month=MONTH, db=seeded)
    got = _mapping(seeded)

    assert _map_of(got, "9000060").scope_key == pk  # 사람 판단 유지
    assert _map_of(got, "9000061").decided_by == "ai"


# ---------- AI 규칙 ----------
def test_approved_rule_applies_to_this_month_but_not_over_manual_edits(seeded):
    """규칙을 승인하면 그 달 AI 매핑에 바로 적용된다 — 다음 달까지 기다리면 지금 눈앞의 전표를
    또 손으로 고쳐야 한다. 담당자가 이미 고른 전표는 건드리지 않는다."""
    pk = _lab_pk(seeded)
    _ingest(
        seeded,
        [
            _line("9000070", vendor="이노와이어리스", text="알 수 없는 적요"),
            _line("9000071", vendor="이노와이어리스", text="알 수 없는 적요"),
        ],
    )
    cl.put_overseas_mapping(
        MapEditIn(
            basis_year=YEAR, basis_month=MONTH, edits=[MapEdit(voucher_no="9000071", line_no=1, scope_key="")]
        ),
        db=seeded,
    )

    cl.create_match_rule(
        MatchRuleIn(
            match_field="vendor",
            match_value="이노와이어리스",
            scope_key=pk,
            hit_count=3,
            apply_basis_year=YEAR,
            apply_basis_month=MONTH,
        ),
        db=seeded,
    )
    got = _mapping(seeded)

    assert _map_of(got, "9000070").decided_by == "rule" and _map_of(got, "9000070").scope_key == pk
    assert _map_of(got, "9000071").decided_by == "user"  # 사람이 미매핑으로 둔 건 그대로


def test_rule_with_both_scope_and_exclude_is_rejected(seeded):
    with pytest.raises(HTTPException) as e:
        cl.create_match_rule(
            MatchRuleIn(match_field="vendor", match_value="X", scope_key=_lab_pk(seeded), excluded=True), db=seeded
        )

    assert e.value.status_code == 400


def test_deleting_a_rule_keeps_mappings_it_already_made(seeded):
    """규칙을 지워도 이미 그 규칙으로 붙은 매핑은 그대로다 — 지난달 보고한 숫자가 소급해서 바뀌면
    안 된다(공통 RULE: 기준정보 변경은 변경한 버전부터)."""
    pk = _lab_pk(seeded)
    _ingest(seeded, [_line("9000080", vendor="큐알티", text="알 수 없는 적요")])
    rule = cl.create_match_rule(
        MatchRuleIn(
            match_field="vendor",
            match_value="큐알티",
            scope_key=pk,
            apply_basis_year=YEAR,
            apply_basis_month=MONTH,
        ),
        db=seeded,
    )

    cl.delete_match_rule(rule.id, db=seeded)

    assert _map_of(_mapping(seeded), "9000080").scope_key == pk
    assert seeded.get(CloseMatchRule, rule.id) is None


# ---------- 확정: 해외연구소는 '1차 완료'까지 ----------
def test_first_done_sets_variance_first_tab_but_not_account_done(seeded):
    """해외연구소 매핑 완료는 **1차 완료**다 — 증감분석 [1차 분석] 탭을 열어 주지만(close_first_done)
    계정 완료(실적 시뮬레이션 [확정])는 건드리지 않는다. 둘을 묶으면 1차만 끝난 달이 "결산 끝"으로
    읽힌다."""
    _ingest(seeded, [_line("9000090")])

    got = cl.put_stage(
        StageIn(basis_year=YEAR, basis_month=MONTH, mapping_done=True, note="미매핑 0건"), db=seeded
    )

    assert got.mapping_done is True and got.first_done_at
    assert got.display_status == "1차 완료"
    assert got.account_done is False
    # Phase 4로 1차 완료는 close_account_stages.mapping_done 하나가 진실이다 — 예전에는
    # overseas_actual_stages.close_first_done이 같은 사실을 따로 들고 있었다.
    stage = seeded.scalar(
        select(CloseAccountStage).where(
            CloseAccountStage.account_key == "overseas",
            CloseAccountStage.basis_year == YEAR,
            CloseAccountStage.basis_month == MONTH,
        )
    )
    assert stage.mapping_done is True


def test_undo_first_done_is_blocked_while_first_is_locked(seeded):
    """[1차 고정] 상태에서 1차 완료를 되돌리면 이미 보고한 1차 값의 근거가 사라진다 — 고정을 먼저
    해제하게 막는다."""
    _ingest(seeded, [_line("9000091")])
    cl.put_stage(StageIn(basis_year=YEAR, basis_month=MONTH, mapping_done=True), db=seeded)
    stage = seeded.scalar(
        select(CloseAccountStage).where(
            CloseAccountStage.account_key == "overseas",
            CloseAccountStage.basis_year == YEAR,
            CloseAccountStage.basis_month == MONTH,
        )
    )
    stage.first_locked = True
    seeded.flush()

    with pytest.raises(HTTPException) as e:
        cl.put_stage(StageIn(basis_year=YEAR, basis_month=MONTH, mapping_done=False), db=seeded)

    assert e.value.status_code == 409


def test_stage_list_counts_unmapped_vouchers(seeded):
    """3단계 확정 화면은 계정마다 미매핑 건수를 같이 본다 — 합계만 보면 "맞는 것 같은데 안 맞는"
    표가 된다."""
    _ingest(seeded, [_line("9000100", text="알 수 없는 적요"), _line("9000101", text="알 수 없는 적요2")])

    row = next(r for r in cl.list_stages(basis_year=YEAR, basis_month=MONTH, db=seeded) if r.account_key == "overseas")

    assert row.voucher_count == 2
    assert row.unmapped_count == 2
    assert row.display_status == "작성중"


def test_amounts_are_never_changed_by_mapping(seeded):
    """매핑은 "어디에 붙일지"만 정한다 — 금액은 ERP가 준 값 그대로 남아야 다운로드가 ERP와 맞는다."""
    pk = _lab_pk(seeded)
    _ingest(seeded, [_line("9000110", krw_amount=1_234_567, fc_amount=1_000, currency="USD")])
    cl.put_overseas_mapping(
        MapEditIn(
            basis_year=YEAR, basis_month=MONTH, edits=[MapEdit(voucher_no="9000110", line_no=1, scope_key=pk)]
        ),
        db=seeded,
    )

    v = next(v for v in _mapping(seeded).vouchers if v.voucher_no == "9000110")

    assert (v.krw_amount, v.fc_amount, v.currency) == (1_234_567, 1_000, "USD")
    assert seeded.scalar(
        select(CloseVoucher.krw_amount).where(CloseVoucher.voucher_no == "9000110")
    ) == 1_234_567
