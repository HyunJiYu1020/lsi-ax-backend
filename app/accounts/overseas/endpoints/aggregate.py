"""해외연구소 집계 화면 전용 엔드포인트 — CP 계약 조회 · 확정 사전점검 · 인건비율.

overseas.py(취합·기준정보·행/열)와 overseas_variance.py(증감분석)에 이어, "집계 한 화면"의 일만
여기 둔다. 의존 방향은 한쪽이다 — 여기서 두 모듈의 헬퍼를 가져다 쓰고, 그쪽은 이 모듈을 모른다.

이 화면이 지켜야 하는 규칙 중 서버가 맡는 것:
- CP 분기 정산은 업로드된 CP PL이 아니라 기준정보 › CP 계약 금액으로 한다(목업 core.js ovCPc).
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
from app.accounts.overseas.services.entries import current_basis_month
from app.accounts.overseas.services.labs import ACCOUNT_KEY, LabView, all_labs, lab_views
from app.accounts.overseas.services.extra_rows import read_extra_rows
from app.accounts.overseas.services.state import build_state
from app.accounts.overseas.services.variance import compute_reason_check
from app.accounts.overseas.services.aggregate import (
    pl_rows,
    LABOR_TREE,
    TOT_TREE,
    cp_quarter_amounts,
    extra_row_months,
    krw_months,
)
from app.db.session import get_db
from app.accounts.overseas.models import OverseasCpRow
from app.shared.models.common import CostEntry, CostEntryDetail
from app.accounts.overseas.schemas import (
    AggregateRow,
    AggregateTable,
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
def get_cp_contract(year: int, db: Session = Depends(get_db)) -> CpContractOut:
    """그 해 CP 계약. POC 헤더값(poc_total · poc_cost_sum)은 직급 행마다 반복 저장돼 있어 POC 단위로
    접어서 더한다 — 행마다 더하면 직급 수만큼 부풀려진다(_cp_quarter_amounts와 같은 규칙).

    계약이 없으면 404가 아니라 registered=false로 200을 준다. 집계 화면은 "계약이 없다"는 사실을
    알고 CP 정산 칸을 빈칸으로 둬야 하는데, 404로 주면 화면이 "서버 오류"와 구분하지 못해서
    업로드된 CP PL로 그려 버린다(= 계약 없이 숫자가 서는 사고)."""
    rows = list(db.scalars(select(OverseasCpRow).where(OverseasCpRow.year == f"{year:04d}")))
    pocs: dict[tuple[str, str], CpContractPoc] = {}
    source_file, uploaded_at = "", ""
    for r in rows:
        key = (r.poc_type, r.poc_proj)
        if key not in pocs:
            pocs[key] = CpContractPoc(
                poc_type=r.poc_type, poc_proj=r.poc_proj, total=float(r.poc_total), cost_sum=float(r.poc_cost_sum)
            )
        source_file = source_file or (r.source_file or "")
        if r.uploaded_at and not uploaded_at:
            uploaded_at = r.uploaded_at.strftime("%m.%d %H:%M")
    year_total = sum(p.total for p in pocs.values())
    year_labor = sum(p.cost_sum for p in pocs.values())
    registered = bool(pocs)
    return CpContractOut(
        year=year,
        registered=registered,
        poc_count=len(pocs),
        year_total=year_total,
        year_labor=year_labor,
        quarter_total=year_total / 4,
        quarter_labor=year_labor / 4,
        source_file=source_file,
        uploaded_at=uploaded_at,
        pocs=list(pocs.values()),
        note=(
            f"{year}년 CP 계약 POC {len(pocs)}개. 분기 정산 계약이라 연 금액 ÷ 4를 3 · 6 · 9 · 12월에 넣는다."
            if registered
            else f"{year}년 CP 계약이 등록되지 않았다. 집계의 CP 정산 칸은 0이 아니라 빈칸이고 합계에서 빠진다."
        ),
    )


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
    # _rate_arrays가 "없는 달"로 실패하지 않고 12개월 값을 그대로 돌려준다(여기서 직접 판정한다).
    rates = rate_arrays(db, basis_year, month, version, set(), ())["USD"]
    fx_missing = [m for m, v in enumerate(rates, start=1) if not v]
    if fx_missing:
        blocking.append(
            f"적용 환율(원/$)이 없는 달이 있다({', '.join(f'{m}월' for m in fx_missing)}) — "
            "기준정보 › 환율에 그 달 값을 넣어야 확정할 수 있다."
        )

    # ② 미업로드 연구소 — 합계는 올라온 것만 더하므로 금액은 그대로지만, 그 사실을 단서로 남긴다.
    labs = [
        lab
        for lab in lab_views(db, f"{basis_year:04d}-{month:02d}")
        if lab.is_active and lab.pk != "_ETC" and (lab.part_code or "").upper() != "TOTAL"
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
    cp = get_cp_contract(year=basis_year, db=db)
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
    db: Session = Depends(get_db),
) -> OverseasLaborRatio:
    """인건비율(인건비 ÷ 개발비(매출)) — 전체와 연구소별. 집계 화면의 인건비율 팝업과 같은 값이다.

    기준은 /overseas/summary와 같다: 원화 환산 + CP는 계약 금액을 분기 말에 넣는다. 분기·연간은
    기간 합계끼리 나눈다 — 분모가 0이면 비율이 없으므로 0이 아니라 null로 돌려준다(화면 '—')."""
    if version not in ("cur", "prev", "prior", "plan"):
        raise HTTPException(400, "version은 cur | prev | prior | plan만 쓸 수 있다.")
    ix = PERIOD_IX.get(period)
    if ix is None:
        raise HTTPException(400, "period는 y(연간) | q1~q4 | m1~m12만 쓸 수 있다.")

    state = build_state(db, basis_year)
    vd = state.V[version]
    if not vd.d:
        label = {"cur": "당월 속보", "prev": "전실행", "prior": "전년 실적", "plan": "경영계획"}[version]
        raise HTTPException(404, f"{basis_year}년 {label} 데이터가 없다.")

    basis_month = state.basis_month or 1
    if version == "prior":
        rate_year, rate_month, rate_kind = basis_year - 1, 12, "actual"
    else:
        rate_year, rate_month, rate_kind = basis_year, basis_month, "forecast"
    labs = [
        lab
        for lab in lab_views(db, f"{rate_year:04d}-{rate_month:02d}")
        if lab.is_active and lab.pk != "_ETC" and (lab.part_code or "").upper() != "TOTAL"
    ]
    rate = rate_arrays(db, rate_year, rate_month, rate_kind, set(), ix)["USD"]
    api_version = "actual" if version in ("prev", "prior") else "forecast"
    extras = extra_row_months(
        read_extra_rows(db, yyyymm=yyyymm(basis_year, basis_month), version=api_version)
    )
    cp_total_q, cp_labor_q = cp_quarter_amounts(db, basis_year)

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
            acc["tot"] += sum(cp_total_q * rate[j] * 1000 for j in ix if j in QUARTER_END_IX)
            acc["lab"] += sum(cp_labor_q * rate[j] * 1000 for j in ix if j in QUARTER_END_IX)
            continue
        cats, extra = vd.d.get(lab.pk) or {}, extras.get(lab.pk) or {}
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
        rows=rows,
        note=(
            "인건비 ÷ 개발비(매출). 원화 · CP 분기 정산(계약 금액) 기준이고, 분기·연간은 기간 합계끼리 "
            "나눴다. ratio가 null인 줄은 분모(개발비)가 0이거나 아직 안 올라와서 비율을 낼 수 없는 것이다 "
            "— 0%로 말하면 안 된다. 올라온 연구소만 들어간다."
        ),
    )


@router.get("/overseas/aggregate-table", response_model=AggregateTable)
def get_aggregate_table(
    basis_year: int,
    version: str = "cur",
    mode: str = "krw",
    db: Session = Depends(get_db),
) -> AggregateTable:
    """집계 화면 ① 전체 계정 표(손익계산서형 45행 × 17칸)를 **서버가 계산해서** 내려준다.

    PLAN.md Phase 6: 같은 수식이 core.js · calc.js · 백엔드 세 곳에 복제돼 있어서 한 곳만
    고치면 화면 숫자와 챗봇 숫자가 달라졌다. 백엔드가 유일한 계산자가 되고 프런트는 표시만
    하게 만드는 중이고, 이 표가 그 첫 조각이다.

    mode=krw면 월별 적용 환율로 환산한다(실적 구간은 말일/월평균 규칙을 따른다). usd면 저장
    단위(K$) 그대로다 — 환율을 안 거치므로 환율이 안 들어간 달도 값이 보인다.
    """
    if version not in ("cur", "prev", "prior", "plan"):
        raise HTTPException(400, "version은 cur | prev | prior | plan만 쓸 수 있다.")
    if mode not in ("usd", "krw"):
        raise HTTPException(400, "mode는 usd | krw만 쓸 수 있다.")

    state = build_state(db, basis_year)
    month = state.basis_month or 1
    vd = state.V.get(version)
    if vd is None:
        raise HTTPException(404, f"그런 버전이 없다: {version!r}")

    # 전실행·전년은 실적(actual) 슬롯에, 나머지는 속보(forecast) 슬롯에 저장된다 — 이 모듈의
    # 다른 엔드포인트와 같은 관례다(위 get_labor_ratio 참조).
    api_version = "actual" if version in ("prev", "prior") else "forecast"
    labs = {lab.pk: lab for lab in lab_views(db, yyyymm(basis_year, month))}
    # 합계 행(part_code="TOTAL")과 원화 기타(_ETC)는 다른 행을 더해 만든 값이라 빼야 한다 —
    # 넣으면 SARC가 CPU+GPU+TOTAL로 두 번 세어져 금액이 정확히 두 배가 된다.
    pks = [
        pk for pk, lab in labs.items()
        if lab.is_active and pk != "_ETC" and (lab.part_code or "").upper() != "TOTAL"
    ]
    extras = extra_row_months(
        read_extra_rows(db, yyyymm=yyyymm(basis_year, month), version=api_version)
    )

    rate = None
    if mode == "krw":
        local = {labs[pk].currency for pk in pks if labs[pk].currency and labs[pk].currency != "USD"}
        rate = rate_arrays(db, basis_year, month, api_version, local, tuple(range(12)))["USD"]

    rows = pl_rows(pks, vd.d, extras, mode, rate)
    unit = "K$" if mode == "usd" else "원"
    return AggregateTable(
        basis_year=basis_year,
        basis_month=month,
        version=version,
        mode=mode,
        caption=f"① 전체 계정 (연구소 {len(pks)}곳 단순 합산, {unit})",
        note=(
            "업로드한 양식 그대로 연구소를 합친 표다 — CP도 연구소가 보낸 PL(매월 금액) 그대로이고 "
            "분기 정산은 반영하지 않는다. 소계·분기·연간은 서버가 계산한다. "
            "빈칸은 아직 안 올라온 달이고 0은 0원으로 보고된 달이다. "
            "MARK UP 비율(%)은 MARK UP ÷ 개발비(매출)다. "
            "IT 관련 비용과 외주용역비는 참고 행이라 개발비 합계에 들어가지 않는다."
        ),
        rows=[AggregateRow(**r) for r in rows],
    )

