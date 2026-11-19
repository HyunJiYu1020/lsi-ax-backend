"""히어로 카드(화면 맨 위 편성 현황) 금액 계산.

frontend-html/shared/core.js의 ovRender 히어로 블록이 그리는 숫자다. 전에는 그쪽이 브라우저에서
계산했고 이 식은 그걸 재현한 것이다. 지금은 화면이 이 응답을 **그대로 그린다** —
그래서 여기를 고치면 챗봇 답과 화면 카드가 같이 바뀐다(둘이 어긋날 자리가 없어졌다).

화면과 맞추기 위해 꼭 지켜야 하는 세 가지(하나라도 빠지면 카드 숫자와 달라진다):
  ① CP는 업로드된 PL이 아니라 기준정보 › CP 계약 금액을 쓴다(분기 정산 계약이라 연 금액 ÷ 4를
     3·6·9·12월에 넣는다).
  ② 연구소가 추가한 행(extra-rows)도 anchor_key가 가리키는 계정에 얹어서 합계에 들어간다 — ovCustExtra.
  ③ 원화 기타(_ETC)는 히어로 합계에 넣지 않는다(OV_ALLPK에 없음). 참고용으로 etc_total만 따로 준다.
"""

import re

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.core.periods import QUARTER_END_IX
from app.shared.core.values import n
from app.shared.models.account import AccountProgress
from app.accounts.overseas.schemas import OverseasSummary, OverseasSummaryLab
from app.accounts.overseas.services.aggregate import (
    INPUT_KEYS,
    LABOR_TREE,
    STEP_COUNT,
    TOT_TREE,
    cp_quarter_amounts,
    krw_months,
)
from app.accounts.overseas.services.fxcalc import rate_arrays
from app.accounts.overseas.services.labs import LabView, overseas_account
from app.accounts.overseas.services.versions import table_inputs


def screen_step(db: Session) -> int:
    """화면 단계(1-based). build_state가 읽던 것과 같은 한 줄이라 거기 의존하지 않고 직접 읽는다 —
    히어로 카드 때문에 상태 전체를 다시 조립할 이유가 없다(GET /overseas/step과 같은 값)."""
    acct = overseas_account(db)
    progress = db.scalar(select(AccountProgress).where(AccountProgress.account_id == acct.id))
    return progress.current_step_order if progress else 1


def build_summary(
    db: Session, basis_year: int, version: str, period: str, ix: tuple[int, ...],
    basis_month: int | None, kind: str, plan_no: str | None,
) -> OverseasSummary:
    """화면 맨 위 히어로 카드와 ③집계 단계 KPI 카드에 들어가는 금액.

    basis_month · kind · plan_no는 집계표와 같은 "어느 버전인가"다 — 전에는
    기준월을 DB 최신 속보로 혼자 정해서, 담당자가 상단에서 26.08 실적을 골라도 카드만 최신
    속보 금액을 보여 줬다. 재료 읽기는 집계표와 **같은 함수**(table_inputs)를 쓴다: 같은 화면
    위아래에 붙은 카드와 집계표가 다른 연구소 목록·환율을 쓰면 안 된다.
    """
    inp = table_inputs(db, basis_year, version, False, basis_month, kind, plan_no)
    if not inp.d:
        raise HTTPException(404, f"{inp.label} 데이터가 없다.")
    basis_month = inp.basis_month
    # 환율은 조회 기간에 드는 달만 본다 — 전체 12개월을 요구하면 아직 환율이 안 들어온 달 때문에
    # 지난 분기 카드도 못 내게 된다(인건비율과 같은 이유로 table_inputs의 rate를 쓰지 않는다).
    rate_year, rate_month, rate_kind = (
        (basis_year - 1, 12, "actual") if version == "prior" else (basis_year, basis_month, inp.api_version)
    )
    # 합계 행(part_code="TOTAL")과 원화 기타(_ETC)는 다른 행을 더해 만든 값이라 빼야 두 번 세지
    # 않는다 — table_inputs.labs가 이미 뺀 목록이다(labor-split · 집계표와 같은 규칙).
    labs = sorted(inp.labs.values(), key=lambda lab: (lab.sort_order, lab.pk))
    rate = rate_arrays(db, rate_year, rate_month, rate_kind, set(), ix)["USD"]
    cp_total_q, cp_labor_q = cp_quarter_amounts(db, basis_year, f"{basis_year:04d}-{basis_month:02d}")

    total = labor = 0.0
    for lab in labs:
        if lab.lab_code == "CP":
            # CP만 PL 대신 계약 금액을 분기말에 넣는다(①).
            total += sum(cp_total_q * rate[j] for j in ix if j in QUARTER_END_IX)
            labor += sum(cp_labor_q * rate[j] for j in ix if j in QUARTER_END_IX)
            continue
        cats, extra = inp.d.get(lab.pk) or {}, inp.extras.get(lab.pk) or {}
        if not cats and not extra:
            continue
        tot_m = krw_months(cats, extra, TOT_TREE, rate)
        lab_m = krw_months(cats, extra, LABOR_TREE, rate)
        total += sum(tot_m[j] for j in ix)
        labor += sum(lab_m[j] for j in ix)
    non_labor = total - labor

    # 원화 기타는 이미 원화(억원 단위 입력)라 환산하지 않는다. 화면 히어로는 이 값을 합계에 넣지
    # 않으므로 여기서도 total에 더하지 않고 따로만 준다.
    etc_total = sum(n(inp.etc[j]) * 100_000_000 for j in ix)

    # 업로드 진척: 파트가 여러 개인 연구소는 파트가 모두 올라와야 1건으로 센다(화면 ovHas와 같다).
    by_code: dict[str, list[LabView]] = {}
    for lab in labs:
        by_code.setdefault(lab.lab_code, []).append(lab)
    lab_rows = [
        OverseasSummaryLab(
            lab_code=code,
            name=re.sub(r"\s*·.*$", "", parts[0].name),
            uploaded=all(
                any(
                    v is not None
                    for key, months in (inp.d.get(p.pk) or {}).items()
                    if key in INPUT_KEYS
                    for v in months
                )
                for p in parts
            ),
        )
        for code, parts in sorted(by_code.items(), key=lambda kv: kv[1][0].sort_order)
    ]

    # 공통 RULE(상단 현황): 미업로드 연구소는 빼고 "n개 중 m개 기준"이라고 밝힌다. 합계는 이미
    # 올라온 값만 더하고 있으므로(없는 달은 더할 게 없다) 숫자는 그대로고, 그 사실을 말로 붙인다.
    missing_names = [r.name for r in lab_rows if not r.uploaded]
    coverage_note = (
        f"{len(lab_rows)}개 연구소가 모두 올라온 기준이다."
        if not missing_names
        else (
            f"{len(lab_rows)}개 중 {len(lab_rows) - len(missing_names)}개 기준이다 — 아직 안 올라온 "
            f"연구소({', '.join(missing_names)})는 빼고 더했으니, 이 금액을 연구소 전체 합계라고 "
            f"말하면 안 된다."
        )
    )

    mix = total or 1.0
    return OverseasSummary(
        basis_year=basis_year,
        basis_month=basis_month,
        basis_label=f"{basis_year % 100:02d}.{basis_month:02d}",
        period=period,
        version=version,
        kind=inp.kind,
        plan_no=inp.plan_no,
        version_label=inp.label,
        step=max(0, min(screen_step(db) - 1, STEP_COUNT - 1)),
        step_count=STEP_COUNT,
        total=total,
        etc_total=etc_total,
        labor=labor,
        non_labor=non_labor,
        labor_pct=labor / mix * 100,
        non_labor_pct=non_labor / mix * 100,
        uploaded=sum(1 for r in lab_rows if r.uploaded),
        lab_count=len(lab_rows),
        labs=lab_rows,
        coverage_note=coverage_note,
        note=(
            "화면 히어로 카드(해외연구소 편성 현황)와 같은 식이다. CP는 업로드 PL이 아니라 CP 계약 "
            "금액을 분기말에 넣고(분기 정산 계약), 연구소가 추가한 행도 anchor_key가 가리키는 계정에 "
            "얹어서 센다. 원화 기타(etc_total)는 화면과 같이 total에 넣지 않는다."
        ),
    )
