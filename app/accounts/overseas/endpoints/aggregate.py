"""해외연구소 집계 화면 전용 엔드포인트 — CP 계약 조회 · 확정 사전점검 · 인건비율.

overseas.py(취합·기준정보·행/열)와 overseas_variance.py(증감분석)에 이어, "집계 한 화면"의 일만
여기 둔다. 의존 방향은 한쪽이다 — 여기서 두 모듈의 헬퍼를 가져다 쓰고, 그쪽은 이 모듈을 모른다.

이 화면이 지켜야 하는 규칙 중 서버가 맡는 것:
- CP 분기 정산은 업로드된 CP PL이 아니라 기준정보 › CP 계약 금액으로 한다.
  계약이 등록되지 않았으면 "0원"이 아니라 "빈칸"이어야 하므로, 등록 여부를 금액과 같이 내려준다.
- [계정 확정]은 환율 미등록 월이 있으면 막고, 미업로드·사유 미작성은 막지 않고 밝힌다. 무엇을
  알고도 확정했는지는 확정 단서(note)로 남겨야 해서, 그 문장도 서버가 만들어 준다.
- 인건비율은 분기·연간을 "합계끼리" 나눈다 — 월별 비율을 평균 내면 달마다 금액이 다를 때 실제
  비율과 어긋난다.
"""

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.core.periods import PERIOD_IX, QUARTER_END_IX, yyyymm
from app.shared.core.values import n
from app.accounts.overseas.services.fxcalc import rate_arrays
from app.shared.services.fx_rates import APPLIED_MISSING_PREFIX
from app.accounts.overseas.services.entries import current_basis_month
from app.accounts.overseas.services.labs import ACCOUNT_KEY, ALL_SCOPE, LabView, all_labs, lab_views
from app.accounts.overseas.services.extra_rows import read_extra_rows
from app.accounts.overseas.services.state import build_state
from app.accounts.overseas.services.variance import compute_reason_check
from app.accounts.overseas.services.versions import table_inputs
from app.accounts.overseas.services.cp import contract_at
from app.accounts.overseas.services.aggregate import (
    LabGroup,
    derived_columns,
    lab_monthly_krw,
    labor_ratio_groups,
    pl_rows,
    lab_tables,
    LABOR_TREE,
    TOT_TREE,
    cp_quarter_amounts,
    extra_row_months,
    krw_months,
)

from app.db.session import get_db
from app.shared.models.common import CostEntry, CostEntryDetail, ExtraCol
from app.accounts.overseas.schemas import (
    AggregateDerivedCol,
    AggregateRow,
    AggregateTable,
    LabTable,
    LabMonthlyOut,
    LabMonthlyRow,
    LabTablesOut,
    LaborRatioBlock,
    LaborRatioTable,
    CpContractOut,
    CpContractPoc,
    OverseasConfirmCheck,
    OverseasLaborRatio,
    OverseasLaborRatioRow,
)

router = APIRouter()

_KIND_VERSION = {"forecast": "forecast", "actual": "actual"}


def _check_kind(kind: str) -> str:
    if kind not in _KIND_VERSION:
        raise HTTPException(400, "kind는 forecast(속보 편성) | actual(실적 시뮬레이션)만 쓸 수 있다.")
    return kind


# ---------- CP 계약 ----------
@router.get("/overseas/cp-contract", response_model=CpContractOut)
def get_cp_contract(year: int, as_of: str, db: Session = Depends(get_db)) -> CpContractOut:
    """그 해 CP 계약. POC 헤더값(poc_total · poc_cost_sum)은 직급 행마다 반복 저장돼 있어 POC 단위로
    접어서 더한다 — 행마다 더하면 직급 수만큼 부풀려진다(services/cp.contract_at).

    as_of("YYYY-MM")는 보고 있는 버전의 기준연월이고 생략할 수 없다 — 계약을 고쳐도 지난 버전의
    분기 정산액이 소급해서 바뀌지 않게, 그 버전 시점의 계약 세트를 읽는다(services/cp.py 참고).

    계약이 없으면 404가 아니라 registered=false로 200을 준다. 집계 화면은 "계약이 없다"는 사실을
    알고 CP 정산 칸을 빈칸으로 둬야 하는데, 404로 주면 화면이 "서버 오류"와 구분하지 못해서
    업로드된 CP PL로 그려 버린다(= 계약 없이 숫자가 서는 사고)."""
    return contract_at(db, year, as_of)


# ---------- 확정 사전점검 ----------
def _uploaded_lab_pks(db: Session, ym: str, version: str) -> set[str]:
    """그 버전·연월에 "칸이 하나라도 등록된" 연구소 pk.

    값이 0인 칸도 올라온 것으로 센다 — 0으로 보고한 연구소를 미업로드로 세면 "12개 중 n개 기준"이
    사실과 달라진다(화면 ovHas와 같은 규칙). 사용자가 추가한 행(label이 있는 행)은 업로드 판정에서
    빼서, 행 하나 추가한 것만으로 "올라왔다"가 되지 않게 한다."""
    rows = db.execute(
        select(CostEntry.scope_code)
        .join(CostEntryDetail, CostEntryDetail.cost_entry_id == CostEntry.id)
        .where(
            CostEntry.account_code == ACCOUNT_KEY,
            CostEntry.version == version,
            CostEntry.yyyymm == ym,
            CostEntry.label.is_(None),
            CostEntryDetail.period_type == "MONTH",
        )
        .distinct()
    ).all()
    return {r[0] for r in rows}


@router.get("/overseas/confirm-check", response_model=OverseasConfirmCheck)
def check_overseas_confirm(
    basis_year: int,
    basis_month: int | None = None,
    kind: str = "forecast",
    db: Session = Depends(get_db),
) -> OverseasConfirmCheck:
    """[계정 확정] 직전 점검. 막는 것(blocking)과 알리는 것(warnings)을 나눠 돌려준다.

    실적 시뮬레이션(kind=actual)도 같은 점검을 하고, 거기서의 확정은 "해외연구소 실적 계정 완료"를
    뜻한다 — 보는 버전만 다르고 규칙은 같다."""
    _check_kind(kind)
    month = basis_month or current_basis_month(db) or 1
    version = _KIND_VERSION[kind]
    ym = yyyymm(basis_year, month)

    blocking: list[str] = []
    warnings: list[str] = []

    # ① 환율 — 적용 환율(원/$)이 없는 달이 있으면 그 달은 원화 환산이 안 된다. ix를 비워 넘기면
    # rate_arrays가 "없는 달"로 실패하지 않고 12개월 값을 그대로 돌려준다(여기서 직접 판정한다).
    # 적용 환율 조합 자체가 없으면(그 연도 환율이 업로드된 적 없음) rate_arrays가 404로 멈추는데,
    # 이 엔드포인트는 "무엇이 없는지 보고하는 게 일"이라 멈추지 않고 12개월 전부 미등록으로 담는다.
    try:
        rates = rate_arrays(db, basis_year, month, version, set(), ())["USD"]
        fx_missing = [m for m, v in enumerate(rates, start=1) if not v]
    except HTTPException as e:
        if e.status_code != 404 or not str(e.detail).startswith(APPLIED_MISSING_PREFIX):
            raise
        fx_missing = list(range(1, 13))
    if fx_missing:
        blocking.append(
            f"적용 환율(원/$)이 없는 달이 있다({', '.join(f'{m}월' for m in fx_missing)}) — "
            "기준정보 › 환율에 그 달 값을 넣어야 확정할 수 있다."
        )

    # ② 미업로드 연구소 — 합계는 올라온 것만 더하므로 금액은 그대로지만, 그 사실을 단서로 남긴다.
    labs = [
        lab
        for lab in lab_views(db, f"{basis_year:04d}-{month:02d}")
        if lab.is_active and (lab.part_code or "").upper() != "TOTAL"
    ]
    uploaded_pks = _uploaded_lab_pks(db, ym, version)
    by_code: dict[str, list] = {}
    for lab in labs:
        by_code.setdefault(lab.lab_code, []).append(lab)
    # 파트가 여러 개인 연구소(SARC)는 파트가 모두 올라와야 1건으로 센다.
    missing_labs = [
        parts[0].name.split("·")[0].strip()
        for code, parts in sorted(by_code.items(), key=lambda kv: kv[1][0].sort_order)
        if not all(p.pk in uploaded_pks for p in parts)
    ]
    lab_count = len(by_code)
    if missing_labs:
        warnings.append(
            f"아직 안 올라온 연구소가 {len(missing_labs)}개 있다({', '.join(missing_labs)}) — "
            f"총액은 {lab_count}개 중 {lab_count - len(missing_labs)}개 기준이다."
        )

    # ③ CP 계약 — 미등록이면 CP 정산 칸이 빈칸인 채로 확정된다.
    cp = get_cp_contract(year=basis_year, as_of=f"{basis_year:04d}-{month:02d}", db=db)
    if not cp.registered:
        warnings.append(f"{basis_year}년 CP 계약이 등록되지 않아 CP 정산 칸이 빈칸이다(합계에서 빠진다).")

    # ④ 증감사유 — 증감률이 기준치를 넘었는데 사유가 비었거나, 사유 저장 뒤 금액이 바뀌어 재확인이
    # 필요한 항목. 판정은 증감분석 화면과 같은 함수를 쓴다(두 군데서 따로 세면 숫자가 어긋난다).
    reason_missing = reason_stale = 0
    try:
        chk = compute_reason_check(db, basis_year=basis_year, basis_month=month, kind=kind)
        reason_missing, reason_stale = len(chk.missing), len(chk.stale)
    except HTTPException:
        # 비교 기준(전실행 등)이 아직 없으면 사유를 따질 수 없다 — 그건 경고가 아니라 "해당 없음"이다.
        pass
    if reason_missing:
        warnings.append(f"증감사유가 비어 있는 항목이 {reason_missing}건 있다(증감분석 화면에서 적을 수 있다).")
    if reason_stale:
        warnings.append(f"사유를 적은 뒤 금액이 바뀌어 재확인이 필요한 항목이 {reason_stale}건 있다.")

    # 확정 단서 — "무엇을 알고도 확정했는지"를 짧게 남긴다(버전 화면에 그대로 붙는다).
    parts: list[str] = []
    if missing_labs:
        parts.append(f"미업로드 {len(missing_labs)}개 포함")
    if not cp.registered:
        parts.append("CP 계약 미등록")
    if reason_missing:
        parts.append(f"사유 미작성 {reason_missing}건")
    if reason_stale:
        parts.append(f"재확인 필요 {reason_stale}건")
    note = " · ".join(parts) + " 확정" if parts else ""

    return OverseasConfirmCheck(
        basis_year=basis_year,
        basis_month=month,
        kind=kind,
        can_confirm=not blocking,
        blocking=blocking,
        warnings=warnings,
        note=note,
        fx_missing_months=fx_missing,
        missing_labs=missing_labs,
        lab_count=lab_count,
        uploaded=lab_count - len(missing_labs),
        reason_missing=reason_missing,
        reason_stale=reason_stale,
        cp_contract_registered=cp.registered,
    )


# ---------- 인건비율 ----------
@router.get("/overseas/labor-ratio", response_model=OverseasLaborRatio)
def get_labor_ratio(
    basis_year: int,
    version: str = "cur",
    period: str = "y",
    basis_month: int | None = None,
    kind: str = "forecast",
    plan_no: str | None = None,
    db: Session = Depends(get_db),
) -> OverseasLaborRatio:
    """인건비율(인건비 ÷ 개발비(매출)) — 전체와 연구소별. 집계 화면의 인건비율 팝업과 같은 값이다.

    기준은 /overseas/summary와 같다: 원화 환산 + CP는 계약 금액을 분기 말에 넣는다. 분기·연간은
    기간 합계끼리 나눈다 — 분모가 0이면 비율이 없으므로 0이 아니라 null로 돌려준다(화면 '—').

    basis_month · kind(· plan_no)는 집계표와 같은 "어느 버전인가"다 — 전에는
    기준월을 DB 최신 속보로 혼자 정해서 과거 버전의 인건비율을 낼 수 없었다. 재료 읽기는
    집계표와 **같은 함수**(table_inputs)를 쓴다: 연구소 추리기·추가 행·환율 규칙이 갈리면
    같은 화면의 집계표와 인건비율이 다른 분모를 쓴다."""
    ix = PERIOD_IX.get(period)
    if ix is None:
        raise HTTPException(400, "period는 y(연간) | q1~q4 | m1~m12만 쓸 수 있다.")

    inp = table_inputs(db, basis_year, version, False, basis_month, kind, plan_no)
    if not inp.d:
        raise HTTPException(404, f"{inp.label} 데이터가 없다.")
    basis_month = inp.basis_month
    # 환율은 조회 기간에 드는 달만 본다 — 전체 12개월을 요구하면 아직 환율이 안 들어온 달 때문에
    # 지난 분기 비율도 못 내게 된다(table_inputs는 12개월을 보므로 여기서 따로 읽는다).
    rate_year, rate_month, rate_kind = (
        (basis_year - 1, 12, "actual") if version == "prior" else (basis_year, basis_month, inp.api_version)
    )
    labs = [
        lab
        for lab in lab_views(db, f"{rate_year:04d}-{rate_month:02d}")
        if lab.is_active and (lab.part_code or "").upper() != "TOTAL"
    ]
    rate = rate_arrays(db, rate_year, rate_month, rate_kind, set(), ix)["USD"]
    cp_total_q, cp_labor_q = cp_quarter_amounts(db, basis_year, f"{basis_year:04d}-{basis_month:02d}")

    by_code: dict[str, dict] = {}
    for lab in labs:
        # 파트가 여러 개인 연구소는 첫 파트 이름이 "SARC+ACL · CPU"라서 파트 꼬리를 떼야
        # 연구소 줄 이름이 된다(/overseas/summary의 lab_rows와 같은 규칙).
        acc = by_code.setdefault(
            lab.lab_code,
            {"name": re.sub(r"\s*·.*$", "", lab.name), "sort": lab.sort_order, "tot": 0.0, "lab": 0.0},
        )
        if lab.lab_code == "CP":
            # CP만 PL 대신 계약 금액을 분기 말에 넣는다(/overseas/summary와 같은 규칙).
            acc["tot"] += sum(cp_total_q * rate[j] for j in ix if j in QUARTER_END_IX)
            acc["lab"] += sum(cp_labor_q * rate[j] for j in ix if j in QUARTER_END_IX)
            continue
        cats, extra = inp.d.get(lab.pk) or {}, inp.extras.get(lab.pk) or {}
        if not cats and not extra:
            continue
        acc["tot"] += sum(krw_months(cats, extra, TOT_TREE, rate)[j] for j in ix)
        acc["lab"] += sum(krw_months(cats, extra, LABOR_TREE, rate)[j] for j in ix)

    def _row(code: str, name: str, tot: float, labor: float) -> OverseasLaborRatioRow:
        return OverseasLaborRatioRow(
            lab_code=code, name=name, total=tot, labor=labor, ratio=(labor / tot * 100) if tot else None
        )

    rows = [
        _row("ALL", "전체 연구소", sum(a["tot"] for a in by_code.values()), sum(a["lab"] for a in by_code.values()))
    ]
    for code, a in sorted(by_code.items(), key=lambda kv: kv[1]["sort"]):
        rows.append(_row(code, a["name"], a["tot"], a["lab"]))

    return OverseasLaborRatio(
        basis_year=basis_year,
        basis_month=basis_month,
        period=period,
        version=version,
        kind=inp.kind,
        plan_no=inp.plan_no,
        version_label=inp.label,
        rows=rows,
        note=(
            "인건비 ÷ 개발비(매출). 원화 · CP 분기 정산(계약 금액) 기준이고, 분기·연간은 기간 합계끼리 "
            "나눴다. ratio가 null인 줄은 분모(개발비)가 0이거나 아직 안 올라와서 비율을 낼 수 없는 것이다 "
            "— 0%로 말하면 안 된다. 올라온 연구소만 들어간다."
        ),
    )


def _lab_groups(labs: dict, pks: list[str]) -> list[LabGroup]:
    """표의 연구소 줄 순서. 프런트 ovLabsFromApi와 같은 규칙으로 묶고 정렬해야 한다 —
    화면은 /overseas/labs 목록을 lab_code로 묶고 sort_order로 정렬해서 행을 그린다.

    하위 줄 라벨은 파트 이름에서 뽑는다("SARC+ACL · GPU (ACL)" → "GPU (ACL)"). 프런트는
    여기를 SARC:CPU · SARC:GPU로 **하드코딩**해 뒀는데, 그러면 파트 구성이 바뀔 때 화면이
    못 따라간다 — 서버는 기준정보를 보고 만든다.
    """
    by_code: dict[str, list] = {}
    for pk in sorted(pks, key=lambda pk: (labs[pk].sort_order, pk)):
        by_code.setdefault(labs[pk].lab_code, []).append(labs[pk])

    groups = []
    for code, rows in by_code.items():
        first = rows[0]
        multi = len(rows) > 1
        name = first.name.split(" · ")[0] if multi else first.name
        parts = tuple(
            (r.pk, r.name.split(" · ", 1)[1] if " · " in r.name else (r.part_code or r.pk))
            for r in rows
        ) if multi else ()
        groups.append(LabGroup(code, name, first.country or "", tuple(r.pk for r in rows), parts))
    return groups


@router.get("/overseas/lab-tables", response_model=LabTablesOut)
def get_lab_tables(
    basis_year: int,
    version: str = "cur",
    basis_month: int | None = None,
    kind: str = "forecast",
    plan_no: str | None = None,
    db: Session = Depends(get_db),
) -> LabTablesOut:
    """집계 화면 탭 ①②③(연구소별 표 10장)을 **서버가 계산해서** 내려준다.

    표를 한 번에 주는 이유: 비싼 건 표 조립이 아니라 화면 상태를 읽는 일이고, 그 읽기를 모든
    표가 같이 쓴다 — 탭마다 따로 부르면 같은 읽기를 열 번 한다.

    ① 개발비(총계 · 인건비 · 인건비외)는 **CP 분기 정산**을 반영한다 — CP는 분기 계약이라
    올라온 월별 PL 대신 기준정보 › CP 계약 금액 ÷ 4를 3 · 6 · 9 · 12월에 넣는다.
    ② 개발비(CP_PL) · ③ 인건비(CP_PL)는 같은 금액을 올라온 PL 그대로 본다(보정 없음).

    CP 계약이 없는 해는 ①을 빼고 돌려준다 — 보정할 재료가 없는데 표를 내면 "보정했다"고 읽히는
    빈 표가 나간다. 화면은 받은 표만 서버 값으로 그리고, ① 탭에는 계약을 올리라는 안내를 띄운다.

    basis_month · kind · plan_no는 "어느 버전의 표인가"다(④와 같다 — table_inputs 참고).
        """
    inp = table_inputs(db, basis_year, version, True, basis_month, kind, plan_no)
    cp_total, cp_labor = cp_quarter_amounts(db, basis_year, f"{basis_year:04d}-{inp.basis_month:02d}")
    tables = lab_tables(
        _lab_groups(inp.labs, inp.pks),
        inp.d,
        inp.extras,
        inp.rate,
        (cp_total, cp_labor) if (cp_total or cp_labor) else None,
    )
    return LabTablesOut(
        basis_year=basis_year,
        basis_month=inp.basis_month,
        version=version,
        kind=inp.kind,
        plan_no=inp.plan_no,
        version_label=inp.label,
        tables={k: LabTable(**v) for k, v in tables.items()},
    )


@router.get("/overseas/aggregate-table", response_model=AggregateTable)
def get_aggregate_table(
    basis_year: int,
    version: str = "cur",
    mode: str = "krw",
    basis_month: int | None = None,
    kind: str = "forecast",
    plan_no: str | None = None,
    db: Session = Depends(get_db),
) -> AggregateTable:
    """집계 화면 ① 전체 계정 표(손익계산서형 45행 × 17칸)를 **서버가 계산해서** 내려준다.

    같은 수식이 core.js와 백엔드 두 곳에 복제돼 있어서 한 곳만
    고치면 화면 숫자와 챗봇 숫자가 달라졌다. 백엔드가 유일한 계산자가 되고 프런트는 표시만
    하게 만드는 중이고, 이 표가 그 첫 조각이다.

    mode=krw면 월별 적용 환율로 환산한다(실적 구간은 말일/월평균 규칙을 따른다). usd면 저장
    단위(K$) 그대로다 — 환율을 안 거치므로 환율이 안 들어간 달도 값이 보인다.

    basis_month · kind(속보|실적|경영계획) · plan_no는 "어느 버전의 표인가"다. 안 주면 지금
    편성 중인 속보로 떨어진다 — 화면이 과거 버전을 띄울 때는 반드시 같이 보내야 한다
    (안 보내면 어느 버전을 보고 있든 최신 속보 표가 그려진다 · table_inputs 참고).
    """
    if mode not in ("usd", "krw"):
        raise HTTPException(400, "mode는 usd | krw만 쓸 수 있다.")

    inp = table_inputs(db, basis_year, version, mode == "krw", basis_month, kind, plan_no)
    month, pks, rate = inp.basis_month, inp.pks, inp.rate
    rows = pl_rows(pks, inp.d, inp.extras, mode, rate)
    unit = "K$" if mode == "usd" else "원"
    # 집계에서 추가한 파생 행(계산 행)은 **재료를 환산한 뒤** 계산한다(services/extra_rows.
    # scale_materials). 계산 결과에 환율을 곱하면 비율 행이 뜻 없는 숫자가 되기 때문이고, 그래서
    # $ 표와 원화 표가 각자 자기 단위로 평가된 값을 받는다. 파생 행은 전체 합산 범위(ALL)에만 있다.
    derived_rows = [
        r
        for r in read_extra_rows(
            db, yyyymm=inp.extras_yyyymm, version=inp.api_version, lab_pk=ALL_SCOPE,
            rate=rate if mode == "krw" else None,
        )
        if r.is_derived
    ]
    derived_cols = _derived_cols(db, inp, rows, mode, derived_rows)
    return AggregateTable(
        basis_year=basis_year,
        basis_month=month,
        version=version,
        kind=inp.kind,
        plan_no=inp.plan_no,
        version_label=inp.label,
        mode=mode,
        # 캡션에 버전을 적는다 — 과거 버전을 띄웠을 때 화면·엑셀만 보고도 어느 버전 표인지 알아야
        # 한다(연도만 적혀 있어서 최신 속보와 구분이 안 되던 자리다).
        caption=f"① 전체 계정 · {inp.label} (연구소 {len(pks)}곳 단순 합산, {unit})",
        note=(
            "업로드한 양식 그대로 연구소를 합친 표다 — CP도 연구소가 보낸 PL(매월 금액) 그대로이고 "
            "분기 정산은 반영하지 않는다. 소계·분기·연간은 서버가 계산한다. "
            "빈칸은 아직 안 올라온 달이고 0은 0원으로 보고된 달이다. "
            "MARK UP 비율(%)은 MARK UP ÷ 개발비(매출)다. "
            "IT 관련 비용과 외주용역비는 참고 행이라 개발비 합계에 들어가지 않는다."
        ),
        rows=[AggregateRow(**r) for r in rows],
        derived_cols=[AggregateDerivedCol(**c) for c in derived_cols],
        derived_rows=derived_rows,
    )



def _derived_cols(db: Session, inp, rows: list[dict], mode: str, derived_rows: list) -> list[dict]:
    """집계 ① 표의 파생 열 값. 열 자체는 DB에 있고(extra_cols · formula) 칸 값은 저장하지 않아
    매번 계산한다 — 프런트 ovXVal이 하던 계산을 옮긴 것이다.

    행 세 종류를 모두 담는다. 화면이 표를 그릴 때 45행 사이에 추가 행·파생 행을 끼워 넣는데
    (그 끼워 넣기는 **배치**라 화면에 남겨 뒀다), 그래서 값은 순서가 아니라 **행 키로** 찾게 준다:
        k:계정키 · l:라벨(원시 추가 행 — 집계표에서는 라벨로 합쳐진 한 줄) · d:행키(파생 행)

    취합에서 온 원시 열(사람이 칸마다 값을 넣는 열)은 여기서 다루지 않는다 — 그건 계산이 아니라
    저장된 값이고, 화면이 /overseas/cost-extras로 이미 받아 쓴다.
    """
    cols = [
        (c.label, (c.formula or {}).get("op", "sum_months"), list((c.formula or {}).get("months") or []))
        for c in db.scalars(
            select(ExtraCol).where(
                ExtraCol.account_key == ACCOUNT_KEY,
                ExtraCol.yyyymm == inp.extras_yyyymm,
                ExtraCol.version == inp.api_version,
                ExtraCol.scope_key == ALL_SCOPE,
                ExtraCol.is_derived.is_(True),
            )
        )
        if (c.formula or {}).get("months")
    ]
    if not cols:
        return []

    # ① 고정 45행 — 서버가 방금 만든 표의 월 칸을 그대로 쓴다(같은 표에서 꺼내므로 어긋날 수 없다).
    key_rows: list[tuple[str, list, str, tuple | None]] = [
        ("k:" + r["key"], list(r["values"][:12]), r["format"], None) for r in rows if r.get("key")
    ]

    # ② 취합에서 연구소가 추가한 원시 행 — 집계표에서는 **라벨로 합친 한 줄**이다(프런트
    # ovPLCustomRows와 같은 규칙). 원화 표에서는 그 연구소 환율로 환산해서 더한다.
    by_label: dict[str, list[float]] = {}
    for row in inp.extra_rows:
        if row.is_derived or row.lab_pk == ALL_SCOPE:
            continue
        label = (row.label or "").strip() or "(이름 없음)"
        acc = by_label.setdefault(label, [0.0] * 12)
        for j, v in enumerate((row.v or [])[:12]):
            acc[j] += n(v) * inp.rate[j] if (mode == "krw" and inp.rate) else n(v)
    fmt = "usd" if mode == "usd" else "krw"
    key_rows += [("l:" + label, mon, fmt, None) for label, mon in by_label.items()]

    # ③ 집계에서 추가한 파생 행 — 비율이면 "구간 분자 합 ÷ 구간 분모 합"이라 근거(basis)를 같이 넘긴다.
    # 값과 근거는 호출부가 이미 이 표의 단위로 평가해서 넘겨준다(원화면 재료를 환산한 뒤 계산한
    # 것이다) — 여기서 환율을 곱하면 두 번 곱해지고, 비율 행은 뜻이 깨진다.
    for row in derived_rows:
        basis = row.basis or {}
        operands = basis.get("operands") or []
        ratio = None
        if basis.get("op") == "div_elementwise" and len(operands) == 2:
            ratio = (operands[0].get("months") or [], operands[1].get("months") or [])
        key_rows.append(("d:" + row.row_key, list((row.v or [])[:12]), fmt, ratio))

    return derived_columns(cols, key_rows)


@router.get("/overseas/labor-ratio-table", response_model=LaborRatioTable)
def get_labor_ratio_table(
    basis_year: int,
    version: str = "cur",
    basis_month: int | None = None,
    kind: str = "forecast",
    plan_no: str | None = None,
    db: Session = Depends(get_db),
) -> LaborRatioTable:
    """집계 화면 **인건비율 팝업**(연구소 줄을 펼치면 인건비 · 개발비가 나오는 그 표).

    `/overseas/labor-ratio`와 헷갈리지 말 것: 그쪽은 원화 · CP 분기 정산 · 한 기간(챗봇이 "인건비율
    얼마야"에 답하는 값)이고, 이쪽은 팝업 모양 그대로 달러(K$) · CP는 올라온 PL 그대로 · 17칸 ·
    파트까지다. 단위가 다른 건 팝업이 "환율과 상관없이 비율만 본다"는 화면이기 때문이다.
    환율을 안 쓰므로 적용 환율이 없는 달에도 비율은 나온다(with_rate=False).
    """
    inp = table_inputs(db, basis_year, version, False, basis_month, kind, plan_no)
    if not inp.d:
        raise HTTPException(404, f"{inp.label} 데이터가 없다.")
    blocks = labor_ratio_groups(_lab_groups(inp.labs, inp.pks), inp.d, inp.extras)
    return LaborRatioTable(
        basis_year=basis_year,
        basis_month=inp.basis_month,
        version=version,
        kind=inp.kind,
        plan_no=inp.plan_no,
        version_label=inp.label,
        unit="K$",
        blocks=[LaborRatioBlock(**b) for b in blocks],
        note=(
            "인건비율 = 인건비 ÷ 개발비(매출). 달러(K$) 기준이고 CP도 연구소가 보낸 PL 그대로다 "
            "(분기 정산 보정 없음). 분기 · 연간은 기간 합계끼리 나눈 값이고, 분모가 0이거나 아직 "
            "안 올라왔으면 null이다 — 0%로 말하면 안 된다."
        ),
    )


@router.get("/overseas/lab-monthly", response_model=LabMonthlyOut)
def get_lab_monthly(
    basis_year: int,
    version: str = "cur",
    basis_month: int | None = None,
    kind: str = "forecast",
    plan_no: str | None = None,
    db: Session = Depends(get_db),
) -> LabMonthlyOut:
    """연구소 **파트** 단위 월별 원화 개발비(매출) — 마감 2단계 전표 매핑이 쓴다.

    매핑 단위가 파트인 이유: SARC는 CPU · GPU 두 워크북이고 전표가 어느 파트 것인지는 과제코드로
    갈린다.

    CP 분기 보정을 넣는다(계약 금액 ÷ 4를 3 · 6 · 9 · 12월에) — 마감 화면이 쓰던 krwq 모드와 같다.
    """
    inp = table_inputs(db, basis_year, version, True, basis_month, kind, plan_no)
    if not inp.d:
        raise HTTPException(404, f"{inp.label} 데이터가 없다.")
    cp_total, cp_labor = cp_quarter_amounts(db, basis_year, f"{basis_year:04d}-{inp.basis_month:02d}")
    rows, total = lab_monthly_krw(
        inp.labs, inp.pks, inp.d, inp.extras, inp.rate,
        (cp_total, cp_labor) if (cp_total or cp_labor) else None,
    )
    return LabMonthlyOut(
        basis_year=basis_year,
        basis_month=inp.basis_month,
        version=version,
        kind=inp.kind,
        plan_no=inp.plan_no,
        version_label=inp.label,
        unit="원",
        rows=[LabMonthlyRow(**r) for r in rows],
        total=total,
        note=(
            "개발비(매출)을 그 달 적용 환율로 환산한 금액이다(원). CP는 올라온 PL 대신 기준정보 › "
            "CP 계약 금액 ÷ 4를 3 · 6 · 9 · 12월에 넣었다. 미등록 달은 null이고 0원이 아니다."
        ),
    )
