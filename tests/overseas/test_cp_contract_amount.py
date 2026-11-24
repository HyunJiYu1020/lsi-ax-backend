"""CP 계약 "금액만" 바꾸는 경로 — 'CP 2026 계약액 5.2M$로 바꿔줘'.

업로드(PUT /overseas/cp-rows)는 파일로 표 전체를 갈아끼우는 경로고, 이건 지금 올라와 있는 계약을
그대로 두고 금액 항목만 같은 비율로 움직인다. 못 박아 두는 것 세 가지:
 1) 분기 정산액이 전후로 정확히 따라간다(연 계약액 ÷ 4).
 2) 바꾼 버전부터만 적용된다 — 지난 버전의 분기 정산액은 그대로다.
 3) 인원·맨먼스는 안 바뀌고, 금액 항목끼리의 관계(direct + ovh = total)는 유지된다.
"""

import pytest
from fastapi import HTTPException

from app.accounts.overseas.endpoints.collect import change_cp_contract_amount, save_cp_rows
from app.accounts.overseas.schemas import CpContractAmountIn, CpRow, CpRowsIn
from app.accounts.overseas.services.cp import contract_at, cp_rows_at

# 개발 DB의 실제 계약과 안 겹치는 가짜 연도.
YEAR = 2097
YS = str(YEAR)
# CP 계약 금액은 저장값이 $ 단위다(K$가 아니다) — 5.2M$ = 5_200_000. 자릿수를 실제와 맞춰 둔다,
# 단위를 K$로 착각하면 1000배 틀린 금액이 조용히 저장되기 때문이다.
M = 1_000_000
JUN, SEP = f"{YS}-06", f"{YS}-09"


def _row(proj: str, total: float, title: str) -> CpRow:
    """양식의 합계 관계(인건비 + Direct + Overhead = Total)에 맞춰 둔다 — 실제 계약서
    (CP_계약_2026.xlsx)가 그 관계이고, 저장이 그걸 검사한다(services/cp.validate_cp_rows).
    인건비 비중 0.6을 고정해 둬서 배율이 관계를 보존하는지 본다."""
    return CpRow(
        year=YS, poc_type="POC", poc_proj=proj, poc_desc="설명", title=title,
        dedicated=2.0, common=1.0, man_month=12.0, cost_year=total / 2,
        poc_cost_sum=total * 0.6, poc_mm_sum=24.0,
        poc_direct=total * 0.25, poc_ovh=total * 0.15, poc_total=total,
        source_file="cp.xlsx",
    )


def _seed(db, *pocs: tuple[str, float]) -> None:
    rows = [_row(proj, total, title) for proj, total in pocs for title in ("VP", "CL4")]
    save_cp_rows(body=CpRowsIn(rows=rows, effective_from=JUN), db=db)


def test_year_total_change_moves_the_quarter_amount(db):
    _seed(db, ("P1", 4.0 * M))

    r = change_cp_contract_amount(
        body=CpContractAmountIn(year=YEAR, year_total=5.2 * M, effective_from=SEP), db=db
    )

    assert r.before.year_total == pytest.approx(4.0 * M)
    assert r.after.year_total == pytest.approx(5.2 * M)
    assert r.before.quarter_total == pytest.approx(1.0 * M)
    assert r.after.quarter_total == pytest.approx(1.3 * M)
    assert r.effective_from == SEP
    # 사람이 승인 전에 읽는 문장 — 저장 단위($)와 집계 표 단위(K$)를 같이 보여줘야 한다.
    assert "1,000,000$ → 1,300,000$" in r.note
    assert "1,000.0K$ → 1,300.0K$" in r.note


def test_past_versions_keep_the_old_amount(db):
    _seed(db, ("P1", 4.0 * M))

    change_cp_contract_amount(
        body=CpContractAmountIn(year=YEAR, year_total=5.2 * M, effective_from=SEP), db=db
    )

    assert contract_at(db, YEAR, f"{YS}-08").year_total == pytest.approx(4.0 * M), "지난 버전이 소급해서 바뀌었다"
    assert contract_at(db, YEAR, f"{YS}-08").quarter_total == pytest.approx(1.0 * M)
    assert contract_at(db, YEAR, f"{YS}-12").year_total == pytest.approx(5.2 * M)


def test_year_total_splits_by_current_share(db):
    """연 합계만 말했을 때 POC가 여러 개면 지금 비중대로 나눈다(3:1 → 3.9M : 1.3M)."""
    _seed(db, ("P1", 3.0 * M), ("P2", 1.0 * M))

    r = change_cp_contract_amount(
        body=CpContractAmountIn(year=YEAR, year_total=5.2 * M, effective_from=SEP), db=db
    )

    after = {p.poc_proj: p.total for p in r.after.pocs}
    assert after["P1"] == pytest.approx(3.9 * M)
    assert after["P2"] == pytest.approx(1.3 * M)
    assert "비중대로" in r.note  # 나눴다는 사실이 사용자에게 보여야 한다


def test_single_poc_change_leaves_the_others_alone(db):
    _seed(db, ("P1", 3.0 * M), ("P2", 1.0 * M))

    r = change_cp_contract_amount(
        body=CpContractAmountIn(year=YEAR, poc_proj="P2", poc_total=2.0 * M, effective_from=SEP), db=db
    )

    after = {p.poc_proj: p.total for p in r.after.pocs}
    assert after["P2"] == pytest.approx(2.0 * M)
    assert after["P1"] == pytest.approx(3.0 * M), "안 건드린 POC가 같이 움직였다"
    assert r.after.year_total == pytest.approx(5.0 * M)


def test_money_scales_together_but_headcount_does_not(db):
    """금액끼리의 관계(인건비 + direct + ovh = total)는 유지되고, 인원·맨먼스는 그대로다 —
    "계약액이 얼마로 바뀌었다"는 돈 이야기지 인력 구성 이야기가 아니다."""
    _seed(db, ("P1", 4.0 * M))

    change_cp_contract_amount(
        body=CpContractAmountIn(year=YEAR, year_total=6.0 * M, effective_from=SEP), db=db
    )

    for r in cp_rows_at(db, YEAR, SEP):
        parts = float(r.poc_cost_sum) + float(r.poc_direct) + float(r.poc_ovh)
        assert parts == pytest.approx(float(r.poc_total))
        assert float(r.poc_total) == pytest.approx(6.0 * M)
        assert float(r.poc_cost_sum) == pytest.approx(3.6 * M)  # 0.6 비율 유지
        assert float(r.cost_year) == pytest.approx(3.0 * M)  # 직급 행 금액도 같이 움직인다
        assert float(r.dedicated) == 2.0 and float(r.man_month) == 12.0, "인원·맨먼스가 바뀌었다"
        assert float(r.poc_mm_sum) == 24.0


def test_rejects_ambiguous_or_impossible_requests(db):
    _seed(db, ("P1", 4.0 * M))

    # 연 합계와 POC 금액을 같이 주면 어느 쪽인지 서버가 짐작하지 않는다
    with pytest.raises(HTTPException) as e:
        change_cp_contract_amount(
            body=CpContractAmountIn(year=YEAR, year_total=1.0, poc_proj="P1", poc_total=1.0), db=db
        )
    assert e.value.status_code == 400

    # 아무것도 안 주는 것도 마찬가지
    with pytest.raises(HTTPException) as e:
        change_cp_contract_amount(body=CpContractAmountIn(year=YEAR), db=db)
    assert e.value.status_code == 400

    # 없는 POC
    with pytest.raises(HTTPException) as e:
        change_cp_contract_amount(
            body=CpContractAmountIn(year=YEAR, poc_proj="없는과제", poc_total=10.0), db=db
        )
    assert e.value.status_code == 400

    # 0 이하
    with pytest.raises(HTTPException) as e:
        change_cp_contract_amount(body=CpContractAmountIn(year=YEAR, year_total=0.0), db=db)
    assert e.value.status_code == 400


def test_rejects_when_no_contract_at_that_version(db):
    """계약이 없는데 "금액만" 바꾸라는 건 성립하지 않는다 — 조용히 새 계약을 만들어 내면,
    업로드한 적 없는 금액이 집계에 선다."""
    _seed(db, ("P1", 4.0 * M))

    with pytest.raises(HTTPException) as e:
        change_cp_contract_amount(
            body=CpContractAmountIn(year=YEAR, year_total=5.2 * M, effective_from=f"{YS}-03"), db=db
        )
    assert e.value.status_code == 400
    assert "먼저 올려야" in e.value.detail
