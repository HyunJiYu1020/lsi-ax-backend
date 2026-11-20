"""실적결산 · 전표매핑 엔드포인트 — 1단계 취합(ERP 전표 적재·계정 자동 분류), 2단계 가공(해외연구소
연구소 단위 매핑·제외·이관·AI 규칙), 계정별 결산 상태.

overseas.py / overseas_variance.py와 가른 이유: 그 둘은 "편성 양식 안의 값"을 다루고(취합·집계·
증감분석), 여기는 **ERP 전표**를 다룬다. 다루는 자료가 다르고(전표 vs 편성값), 생명주기가 다르고
(전표는 다시 오고 덮인다), 쓰는 사람이 다르다(결산 담당자). 의존 방향은 한쪽이다 — 여기서 저쪽
헬퍼(연구소 기준정보·환율·cost_entries 읽기)를 가져다 쓰고, 저쪽은 이 모듈을 모른다.

이 화면이 지켜야 하는 규칙(계산·저장 로직은 services/close_mapping.py에 구현):
- 전표는 **원본을 고치지 않는다**. [저장]하는 건 매핑(어디에 붙일지)이고, 금액은 ERP가 준 값
  그대로다. 다운로드도 원래 금액을 내려야 ERP와 맞춰볼 수 있다.
- **재수신 시 기존 매핑 유지, 신규만 매칭.** 같은 달 전표가 다시 오면 전표는 덮어쓰지만 담당자가
  고친 매핑은 살린다. 전표 id가 아니라 (연월·전표번호·라인)을 키로 쓰는 이유가 이것이다.
- **AI는 추천만 한다.** 신뢰도와 근거를 같이 내려주고, 반영은 담당자가 [저장]할 때 일어난다.
  'AI 다시 매칭'은 AI가 정한 것만 되돌리고 담당자가 고른 것은 건드리지 않는다.
- **반복 수정은 물어보고 규칙으로.** 같은 수정을 3번 하면 "앞으로 이렇게 할까요?"를 내려주고,
  승인한 것만 규칙으로 등록한다(서버가 조용히 규칙을 만들지 않는다).
- **해외연구소의 완료는 '1차 완료'까지다.** 전표 매핑이 끝나도 그건 ERP 약식 1차일 뿐이고, 계정
  완료는 PL이 나온 뒤 실적 시뮬레이션 화면의 [확정]으로만 된다. 그래서 여기서 versions 쪽
  계정 상태를 건드리지 않고 close_account_stages.mapping_done만 켠다(증감분석 1차 탭이 열린다).
"""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.overseas.services.close_mapping import (
    account_done,
    auto_map,
    check_month,
    classify,
    dept_rules,
    hints as build_hints,
    lab_list,
    map_rows,
    prev_lab_krw,
    rules_for,
    save_map_edits,
    stage_out,
    stage_row,
    status_of,
    suggestions,
    summary_rows,
    voucher_out,
    vouchers,
)
from app.accounts.overseas.services.labs import ACCOUNT_KEY
from app.accounts.overseas.services.variance import active_labs
from app.shared.core.reference import log_change, snapshot
from app.db.session import get_db
from app.shared.models.account import Account
from app.shared.models.close import CloseAccountStage, CloseDeptRule, CloseMatchRule, CloseVoucher, CloseVoucherMap
from app.shared.schemas.close import (
    AccountSummaryRow,
    DeptRule,
    LabMappingRow,
    MapEditIn,
    MapEditResult,
    MatchRuleIn,
    MatchRuleOut,
    OverseasMappingResult,
    StageIn,
    StageOut,
    VoucherIngestIn,
    VoucherIngestResult,
    VoucherMapOut,
    VoucherOut,
)

router = APIRouter()


@router.post("/close/vouchers", response_model=VoucherIngestResult)
def ingest_vouchers(body: VoucherIngestIn, db: Session = Depends(get_db)) -> VoucherIngestResult:
    """ERP 전표 적재(1단계 취합). 업로드와 ERP I/F가 같은 경로를 쓴다 — 받은 자료의 모양이 같으므로
    적재·분류 규칙을 두 벌로 두면 둘이 어긋난다.

    재수신이 이 엔드포인트의 핵심이다: 전표는 덮어쓰지만 **매핑은 지우지 않는다**. 지난번에 담당자가
    고친 결과가 그대로 살아나고, 이번에 새로 온 전표만 AI가 매칭한다(공통 RULE). replace=True면
    이번에 안 온 전표를 지우지만(전기 취소분) 그 매핑도 남겨 둔다 — 다음 재수신에 같은 전표가
    돌아오면 또 살아난다."""
    check_month(body.basis_month)
    rules = dept_rules(db)
    existing = {(v.voucher_no, v.line_no): v for v in vouchers(db, body.basis_year, body.basis_month)}
    maps = map_rows(db, body.basis_year, body.basis_month)
    seen: set[tuple[str, int]] = set()

    for line in body.rows:
        no = (line.voucher_no or "").strip()
        if not no:
            continue
        key = (no, line.line_no or 1)
        seen.add(key)
        row = existing.get(key)
        if row is None:
            row = CloseVoucher(
                basis_year=body.basis_year, basis_month=body.basis_month, voucher_no=no, line_no=line.line_no or 1
            )
            db.add(row)
        row.posted_on = date.fromisoformat(line.posted_on) if line.posted_on else None
        row.account_code = (line.account_code or "").strip()
        row.account_name = line.account_name or ""
        row.cost_center = (line.cost_center or "").strip()
        row.cost_center_name = line.cost_center_name or ""
        row.project_code = (line.project_code or "").strip()
        row.vendor = line.vendor or ""
        row.text = line.text or ""
        row.currency = (line.currency or "KRW").upper()
        row.fc_amount = line.fc_amount or 0
        row.krw_amount = line.krw_amount or 0
        row.ref_no = line.ref_no or ""
        row.assignment = (line.assignment or "").strip()
        row.source_file = body.source_file or ""
        row.received_at = datetime.now(timezone.utc)
        row.account_key, row.classify_by, row.classify_note = classify(row, rules)

    deleted = 0
    if body.replace:
        for key, row in existing.items():
            if key not in seen:
                db.delete(row)
                deleted += 1
    db.flush()

    # 새로 온 전표만 AI 매칭한다. 이미 매핑이 있으면(담당자 수정이든 지난번 AI 추천이든) 손대지 않는다.
    labs = lab_list(db, body.basis_year, body.basis_month)
    by_account_rules: dict[str, list[dict]] = {}
    by_account_hints: dict[str, dict] = {}
    new_maps = 0
    for vou in vouchers(db, body.basis_year, body.basis_month):
        key = (vou.voucher_no, vou.line_no)
        old = maps.get(key)
        if old is not None:
            # 분류가 바뀌었으면(기준정보를 고친 뒤 재수신) 매핑이 가리키는 계정도 따라가야 한다.
            if vou.account_key and not old.transferred_to:
                old.account_key = vou.account_key
            continue
        if not vou.account_key:
            continue  # 미분류 전표는 매핑할 양식이 없다 — 분류부터 고쳐야 한다
        if vou.account_key not in by_account_rules:
            by_account_rules[vou.account_key] = rules_for(db, vou.account_key)
            by_account_hints[vou.account_key] = build_hints(db, body.basis_year, body.basis_month, vou.account_key)
        db.add(auto_map(vou, labs, by_account_rules[vou.account_key], by_account_hints[vou.account_key]))
        new_maps += 1

    db.commit()
    rows = vouchers(db, body.basis_year, body.basis_month)
    unclassified = sum(1 for v in rows if not v.account_key)
    return VoucherIngestResult(
        basis_year=body.basis_year,
        basis_month=body.basis_month,
        received=len(seen),
        deleted=deleted,
        kept_maps=len(seen) - new_maps,
        new_maps=new_maps,
        unclassified=unclassified,
        by_account=summary_rows(db, rows),
        note=(
            f"전표 {len(seen)}건 적재 · 기존 매핑 {len(seen) - new_maps}건 유지 · 신규 {new_maps}건 AI 매칭"
            + (f" · 미분류 {unclassified}건(기준정보 확인 필요)" if unclassified else "")
            + (f" · 이번에 안 온 전표 {deleted}건 삭제" if deleted else "")
        ),
    )


@router.get("/close/vouchers", response_model=list[VoucherOut])
def list_vouchers(
    basis_year: int,
    basis_month: int,
    account_key: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
) -> list[VoucherOut]:
    """전표 목록. account_key=""(빈 문자열)을 주면 미분류만 — 1단계 알림에서 바로 넘어오는 경로다."""
    check_month(basis_month)
    rows = vouchers(db, basis_year, basis_month, account_key)
    if q:
        needle = q.upper()
        rows = [
            v
            for v in rows
            if needle in f"{v.voucher_no} {v.vendor} {v.text} {v.cost_center} {v.project_code}".upper()
        ]
    return [voucher_out(v) for v in rows]


@router.get("/close/summary", response_model=list[AccountSummaryRow])
def get_close_summary(basis_year: int, basis_month: int, db: Session = Depends(get_db)) -> list[AccountSummaryRow]:
    """1단계 계정별 요약(건수·금액·미분류). 화면이 전표를 다 받아 세지 않게 서버가 같은 식으로 센다."""
    check_month(basis_month)
    return summary_rows(db, vouchers(db, basis_year, basis_month))


# ---------- 분류 기준정보 ----------
@router.get("/close/dept-rules", response_model=list[DeptRule])
def list_dept_rules(db: Session = Depends(get_db)) -> list[DeptRule]:
    return [
        DeptRule(
            cost_center=r.cost_center,
            account_code=r.account_code,
            account_key=r.account_key,
            is_shared=r.is_shared,
            note=r.note,
        )
        for r in dept_rules(db)
    ]


@router.put("/close/dept-rules", response_model=DeptRule)
def put_dept_rule(body: DeptRule, db: Session = Depends(get_db)) -> DeptRule:
    """분류 기준 한 줄 저장. 고친 뒤 재분류는 따로 호출한다(/close/classify) — 기준을 여러 줄 고치는
    동안 매번 전표를 다시 분류하면 중간 상태로 몇 번씩 바뀐다."""
    if not db.scalar(select(Account).where(Account.key == body.account_key)):
        raise HTTPException(404, f"그런 계정이 없다: {body.account_key!r}")
    # 스냅샷은 새 행을 add하기 **전에** 뜬다 — 뒤에 뜨면 autoflush가 방금 추가한 행을 before에
    # 섞어 넣어서, 추가가 "변경 없음"으로 기록된다.
    before = snapshot("close_dept_rule", dept_rules(db))
    row = db.scalar(
        select(CloseDeptRule).where(
            CloseDeptRule.cost_center == body.cost_center, CloseDeptRule.account_code == body.account_code
        )
    )
    if row is None:
        row = CloseDeptRule(cost_center=body.cost_center, account_code=body.account_code)
        db.add(row)
    row.account_key = body.account_key
    row.is_shared = body.is_shared
    row.note = body.note
    db.flush()
    # 이 표가 바뀌면 그 달 전표가 다른 계정으로 간다 — 기준정보 다섯 표와 같은 이력에 남긴다
    # ("왜 이 계정으로 갔나"는 같은 질문이라서 두 곳에 흩어 두면 담당자가 한쪽만 본다).
    log_change(db, "close_dept_rule", before, snapshot("close_dept_rule", dept_rules(db)), how="screen")
    db.commit()
    return body


@router.delete("/close/dept-rules")
def delete_dept_rule(account_code: str, cost_center: str = "", db: Session = Depends(get_db)) -> dict:
    row = db.scalar(
        select(CloseDeptRule).where(
            CloseDeptRule.cost_center == cost_center, CloseDeptRule.account_code == account_code
        )
    )
    if row is None:
        raise HTTPException(404, "그런 분류 기준이 없다.")
    before = snapshot("close_dept_rule", dept_rules(db))
    db.delete(row)
    db.flush()
    log_change(db, "close_dept_rule", before, snapshot("close_dept_rule", dept_rules(db)), how="screen")
    db.commit()
    return {"deleted": 1}


@router.post("/close/classify", response_model=VoucherIngestResult)
def reclassify(basis_year: int, basis_month: int, db: Session = Depends(get_db)) -> VoucherIngestResult:
    """기준정보를 고친 뒤 그 달 전표를 다시 분류한다. 이미 있는 매핑은 유지하고, 분류가 새로 붙은
    전표만 AI 매칭한다 — 미분류였다가 분류된 전표가 매핑 없이 남으면 2단계에서 보이지 않는다."""
    check_month(basis_month)
    rules = dept_rules(db)
    rows = vouchers(db, basis_year, basis_month)
    maps = map_rows(db, basis_year, basis_month)
    labs = lab_list(db, basis_year, basis_month)
    cache_rules: dict[str, list[dict]] = {}
    cache_hints: dict[str, dict] = {}
    new_maps = 0
    for vou in rows:
        vou.account_key, vou.classify_by, vou.classify_note = classify(vou, rules)
        old = maps.get((vou.voucher_no, vou.line_no))
        if old is not None:
            if vou.account_key and not old.transferred_to:
                old.account_key = vou.account_key
            continue
        if not vou.account_key:
            continue
        if vou.account_key not in cache_rules:
            cache_rules[vou.account_key] = rules_for(db, vou.account_key)
            cache_hints[vou.account_key] = build_hints(db, basis_year, basis_month, vou.account_key)
        db.add(auto_map(vou, labs, cache_rules[vou.account_key], cache_hints[vou.account_key]))
        new_maps += 1
    db.commit()
    rows = vouchers(db, basis_year, basis_month)
    unclassified = sum(1 for v in rows if not v.account_key)
    return VoucherIngestResult(
        basis_year=basis_year,
        basis_month=basis_month,
        received=len(rows),
        kept_maps=len(rows) - new_maps,
        new_maps=new_maps,
        unclassified=unclassified,
        by_account=summary_rows(db, rows),
        note=f"전표 {len(rows)}건 재분류 · 신규 매칭 {new_maps}건 · 미분류 {unclassified}건",
    )


# ---------- 2단계 가공: 해외연구소 연구소 단위 매핑 ----------
@router.get("/close/overseas/mapping", response_model=OverseasMappingResult)
def get_overseas_mapping(basis_year: int, basis_month: int, db: Session = Depends(get_db)) -> OverseasMappingResult:
    """2단계 가공 · 해외연구소 작업대 한 벌.

    다른 계정과 달리 매핑 단위가 **연구소**다 — 해외연구소 전표는 과제코드 단위로 떨어져서 전표의
    코스트센터가 곧 편성 단위가 되지 못한다. 그래서 상단 표가 '부서'가 아니라 '연구소'이고, AI는
    거래처·적요·코스트센터 세 단서로 연구소를 추정한다.

    상단 표와 전표 목록을 한 응답으로 내려주는 이유: 두 번 호출해서 합치면 연구소 합계와 전표 합이
    서로 다른 시점 값이 되어, 합계는 맞는데 전표를 세면 안 맞는 표가 나온다."""
    check_month(basis_month)
    labs = active_labs(db, basis_year, basis_month)
    prev_by_pk, prev_total = prev_lab_krw(db, basis_year, basis_month)
    rows = vouchers(db, basis_year, basis_month, ACCOUNT_KEY)
    maps = map_rows(db, basis_year, basis_month)
    # 이관으로 들어온 전표: 원래 다른 계정이었지만 매핑이 해외연구소를 가리키는 것.
    for mp in map_rows(db, basis_year, basis_month, ACCOUNT_KEY).values():
        if mp.transferred_from:
            vou = db.scalar(
                select(CloseVoucher).where(
                    CloseVoucher.basis_year == basis_year,
                    CloseVoucher.basis_month == basis_month,
                    CloseVoucher.voucher_no == mp.voucher_no,
                    CloseVoucher.line_no == mp.line_no,
                )
            )
            if vou is not None and vou not in rows:
                rows.append(vou)
    rows.sort(key=lambda v: (v.voucher_no, v.line_no))

    actual_by_pk: dict[str, float] = {}
    count_by_pk: dict[str, int] = {}
    unmapped_count = unmapped_total = check_count = 0
    excluded_count = 0
    excluded_total = transfer_in = transfer_out = 0.0
    out_maps: list[VoucherMapOut] = []
    for vou in rows:
        mp = maps.get((vou.voucher_no, vou.line_no))
        if mp is None:
            # 매핑 행이 없는 전표(분류만 되고 매칭 전) — 미매핑으로 보여준다.
            mp = CloseVoucherMap(
                basis_year=basis_year,
                basis_month=basis_month,
                voucher_no=vou.voucher_no,
                line_no=vou.line_no,
                account_key=ACCOUNT_KEY,
                reason="아직 매칭하지 않았다",
                confidence="low",
            )
        amount = float(vou.krw_amount or 0)
        status = status_of(mp)
        if status == "sent":
            transfer_out += amount
        elif status == "received":
            transfer_in += amount
        if mp.excluded:
            excluded_count += 1
            excluded_total += amount
        elif mp.transferred_to:
            pass  # 이관 보낸 전표는 이 계정 집계에서 빠진다
        elif mp.scope_key:
            actual_by_pk[mp.scope_key] = actual_by_pk.get(mp.scope_key, 0.0) + amount
            count_by_pk[mp.scope_key] = count_by_pk.get(mp.scope_key, 0) + 1
        else:
            unmapped_count += 1
            unmapped_total += amount
        if status == "check":
            check_count += 1
        lab = labs.get(mp.scope_key)
        out_maps.append(
            VoucherMapOut(
                voucher_no=mp.voucher_no,
                line_no=mp.line_no,
                scope_key=mp.scope_key,
                scope_label=lab.name if lab else "",
                decided_by=mp.decided_by,
                confidence=mp.confidence,
                reason=mp.reason,
                excluded=mp.excluded,
                exclude_reason=mp.exclude_reason,
                transferred_to=mp.transferred_to,
                transferred_from=mp.transferred_from,
                status=status,
            )
        )

    lab_rows = [
        LabMappingRow(
            scope_key=lab.pk,
            lab_code=lab.lab_code,
            label=lab.name,
            prev_amount=prev_by_pk.get(lab.pk, 0.0),
            actual_amount=actual_by_pk.get(lab.pk, 0.0),
            diff=actual_by_pk.get(lab.pk, 0.0) - prev_by_pk.get(lab.pk, 0.0),
            voucher_count=count_by_pk.get(lab.pk, 0),
        )
        for lab in sorted(labs.values(), key=lambda x: (x.sort_order, x.pk))
    ]
    stage = stage_row(db, basis_year, basis_month, ACCOUNT_KEY)
    done = bool(stage and stage.mapping_done)
    actual_total = sum(actual_by_pk.values())
    return OverseasMappingResult(
        basis_year=basis_year,
        basis_month=basis_month,
        prev_label=f"{basis_year}년 {basis_month}월 속보",
        labs=lab_rows,
        vouchers=[voucher_out(v) for v in rows],
        maps=out_maps,
        prev_total=prev_total,
        actual_total=actual_total,
        unmapped_count=unmapped_count,
        unmapped_total=unmapped_total,
        check_count=check_count,
        excluded_count=excluded_count,
        excluded_total=excluded_total,
        transfer_in_total=transfer_in,
        transfer_out_total=transfer_out,
        suggestions=suggestions(db, basis_year, basis_month, ACCOUNT_KEY, labs),
        mapping_done=done,
        first_done_at=stage.first_done_at.isoformat() if stage and stage.first_done_at else "",
        account_done=account_done(db, basis_year, basis_month, ACCOUNT_KEY),
        note=(
            f"전표 {len(rows)}건 · 확인 필요 {check_count}건 · 미매핑 {unmapped_count}건 · 제외 {excluded_count}건"
            + (" · 1차 완료" if done else "")
        ),
    )


@router.put("/close/overseas/mapping", response_model=MapEditResult)
def put_overseas_mapping(body: MapEditIn, db: Session = Depends(get_db)) -> MapEditResult:
    """해외연구소 작업대의 매핑 저장. 본문은 계정 공용(`PUT /close/maps`)과 같다 — 이 경로는
    해외연구소 화면이 이미 쓰고 있어서 남겨 둔다."""
    return save_map_edits(body, db)


@router.put("/close/maps", response_model=MapEditResult)
def put_maps(body: MapEditIn, db: Session = Depends(get_db)) -> MapEditResult:
    """계정 공용 매핑 저장 — 19개 계정의 전표매핑 화면이 쓴다.

    `/close/overseas/mapping`과 같은 일을 하지만 경로에 계정 이름이 없다. 해외연구소가 아닌 계정이
    "overseas" 경로로 저장하면, 나중에 읽는 사람이 그 계정 매핑도 연구소 단위라고 오해한다."""
    return save_map_edits(body, db)


@router.get("/close/maps", response_model=list[VoucherMapOut])
def list_maps(
    basis_year: int, basis_month: int, account_key: str | None = None, db: Session = Depends(get_db)
) -> list[VoucherMapOut]:
    """그 연월의 매핑 판단 목록(계정으로 걸러 받을 수 있다).

    화면이 전표(`/close/vouchers`)와 따로 받는 이유: 전표는 ERP가 다시 보내면 덮이지만 매핑은
    살아남는다(공통 RULE). 둘을 한 응답으로 묶으면 "전표는 새로 받고 매핑은 유지"를 화면이
    표현할 수 없다.

    `decided_by`를 그대로 내려준다 — 화면은 사람이 고친 것(user)·규칙(rule)만 자기 수정 목록으로
    들고, AI 추천(ai)은 화면이 매번 다시 계산한 추천을 쓴다."""
    check_month(basis_month)
    rows = db.scalars(
        select(CloseVoucherMap)
        .where(
            CloseVoucherMap.basis_year == basis_year,
            CloseVoucherMap.basis_month == basis_month,
            *([CloseVoucherMap.account_key == account_key] if account_key is not None else []),
        )
        .order_by(CloseVoucherMap.voucher_no, CloseVoucherMap.line_no)
    ).all()
    return [
        VoucherMapOut(
            voucher_no=r.voucher_no, line_no=r.line_no, scope_key=r.scope_key, decided_by=r.decided_by,
            confidence=r.confidence, reason=r.reason, excluded=r.excluded, exclude_reason=r.exclude_reason,
            transferred_to=r.transferred_to, transferred_from=r.transferred_from, status=status_of(r),
        )
        for r in rows
    ]


@router.post("/close/overseas/rematch", response_model=MapEditResult)
def rematch_overseas(basis_year: int, basis_month: int, db: Session = Depends(get_db)) -> MapEditResult:
    """AI 다시 매칭 — AI가 정한 매핑만 새로 계산한다.

    담당자가 고친 것(user)과 승인된 규칙(rule)은 건드리지 않는다. 사람이 판단한 걸 기계가 되돌리면
    같은 수정을 매달 다시 해야 하고, 그러면 아무도 수정하지 않게 된다."""
    check_month(basis_month)
    labs = lab_list(db, basis_year, basis_month)
    rules = rules_for(db, ACCOUNT_KEY)
    hint_table = build_hints(db, basis_year, basis_month, ACCOUNT_KEY)
    maps = map_rows(db, basis_year, basis_month, ACCOUNT_KEY)
    changed = 0
    for vou in vouchers(db, basis_year, basis_month, ACCOUNT_KEY):
        row = maps.get((vou.voucher_no, vou.line_no))
        if row is not None and row.decided_by != "ai":
            continue
        fresh = auto_map(vou, labs, rules, hint_table)
        if row is None:
            db.add(fresh)
        else:
            row.scope_key = fresh.scope_key
            row.decided_by = fresh.decided_by
            row.confidence = fresh.confidence
            row.reason = fresh.reason
            row.excluded = fresh.excluded
            row.exclude_reason = fresh.exclude_reason
        changed += 1
    db.commit()
    return MapEditResult(saved=changed, note=f"AI 추천 {changed}건을 다시 계산했다(담당자 수정·규칙은 유지).")


# ---------- AI 규칙 ----------
@router.get("/close/match-rules", response_model=list[MatchRuleOut])
def list_match_rules(account_key: str = ACCOUNT_KEY, db: Session = Depends(get_db)) -> list[MatchRuleOut]:
    return [
        MatchRuleOut(
            id=r.id,
            account_key=r.account_key,
            match_field=r.match_field,
            match_value=r.match_value,
            match_op=r.match_op,
            scope_key=r.scope_key,
            excluded=r.excluded,
            hit_count=r.hit_count,
            note=r.note,
            rejected=r.rejected,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in db.scalars(
            select(CloseMatchRule).where(CloseMatchRule.account_key == account_key).order_by(CloseMatchRule.id)
        )
    ]


@router.post("/close/match-rules", response_model=MatchRuleOut)
def create_match_rule(body: MatchRuleIn, db: Session = Depends(get_db)) -> MatchRuleOut:
    """규칙 등록 = 담당자가 "앞으로 이렇게"에 승인한 것. 서버가 스스로 규칙을 만들지 않는 이유는
    close_match_rules 주석에 적어 뒀다(한 달만 예외로 옮긴 전표도 반복처럼 보인다).

    apply_basis_* 를 주면 그 달의 AI 매핑에 바로 적용한다 — 담당자 수정은 건드리지 않는다."""
    if body.match_field not in ("vendor", "text", "cost_center", "project", "assignment"):
        raise HTTPException(
            400, "match_field는 vendor | text | cost_center | project | assignment만 쓸 수 있다."
        )
    if body.match_op not in ("", "equals", "contains", "starts_with"):
        raise HTTPException(400, "match_op는 equals | contains | starts_with만 쓸 수 있다(생략하면 필드 기본값).")
    if not body.match_value.strip():
        raise HTTPException(400, "match_value가 비어 있다.")
    # 거절(rejected)에는 붙일 곳이 없다 — "규칙으로 만들지 않는다"는 기록이라 scope_key·excluded를
    # 같이 받으면 "제외 규칙"과 구분이 안 된다(제외는 적용되는 규칙이고, 거절은 적용되지 않는다).
    if body.rejected and (body.scope_key or body.excluded):
        raise HTTPException(400, "거절 기록에는 연구소·제외를 지정하지 않는다.")
    if body.scope_key and body.excluded:
        raise HTTPException(400, "제외 규칙에는 연구소를 지정하지 않는다 — 둘 중 하나만 지정해야 한다.")
    row = db.scalar(
        select(CloseMatchRule).where(
            CloseMatchRule.account_key == body.account_key,
            CloseMatchRule.match_field == body.match_field,
            CloseMatchRule.match_value == body.match_value,
        )
    )
    if row is None:
        row = CloseMatchRule(
            account_key=body.account_key, match_field=body.match_field, match_value=body.match_value
        )
        db.add(row)
    row.match_op = body.match_op
    row.rejected = body.rejected
    row.scope_key = body.scope_key
    row.excluded = body.excluded
    row.hit_count = body.hit_count
    row.note = body.note
    db.commit()
    db.refresh(row)

    if body.apply_basis_year and body.apply_basis_month:
        check_month(body.apply_basis_month)
        rules = rules_for(db, body.account_key)
        maps = map_rows(db, body.apply_basis_year, body.apply_basis_month, body.account_key)
        labs = lab_list(db, body.apply_basis_year, body.apply_basis_month)
        hint_table = build_hints(db, body.apply_basis_year, body.apply_basis_month, body.account_key)
        for vou in vouchers(db, body.apply_basis_year, body.apply_basis_month, body.account_key):
            mp = maps.get((vou.voucher_no, vou.line_no))
            if mp is not None and mp.decided_by == "user":
                continue
            fresh = auto_map(vou, labs, rules, hint_table)
            if mp is None:
                db.add(fresh)
            else:
                mp.scope_key = fresh.scope_key
                mp.decided_by = fresh.decided_by
                mp.confidence = fresh.confidence
                mp.reason = fresh.reason
                mp.excluded = fresh.excluded
                mp.exclude_reason = fresh.exclude_reason
        db.commit()

    return MatchRuleOut(
        id=row.id,
        account_key=row.account_key,
        match_field=row.match_field,
        match_value=row.match_value,
        match_op=row.match_op,
        scope_key=row.scope_key,
        excluded=row.excluded,
        hit_count=row.hit_count,
        note=row.note,
        rejected=row.rejected,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


@router.delete("/close/match-rules/{rule_id}")
def delete_match_rule(rule_id: int, db: Session = Depends(get_db)) -> dict:
    """규칙 삭제. 이미 그 규칙으로 붙은 매핑은 그대로 둔다 — 지난달 보고한 숫자가 규칙을 지웠다고
    소급해서 바뀌면 안 된다(공통 RULE: 기준정보 변경은 변경한 버전부터)."""
    row = db.get(CloseMatchRule, rule_id)
    if row is None:
        raise HTTPException(404, "그런 규칙이 없다.")
    db.delete(row)
    db.commit()
    return {"deleted": 1}


# ---------- 계정별 결산 상태 (3단계 확정) ----------
@router.get("/close/stages", response_model=list[StageOut])
def list_stages(basis_year: int, basis_month: int, db: Session = Depends(get_db)) -> list[StageOut]:
    """3단계 확정 화면의 계정별 status·숫자. 해외연구소 행만 '1차 완료 / 완료'를 가른다."""
    check_month(basis_month)
    keys = {v.account_key for v in vouchers(db, basis_year, basis_month) if v.account_key}
    keys |= {
        s.account_key
        for s in db.scalars(
            select(CloseAccountStage).where(
                CloseAccountStage.basis_year == basis_year, CloseAccountStage.basis_month == basis_month
            )
        )
    }
    keys.add(ACCOUNT_KEY)
    return [stage_out(db, basis_year, basis_month, k) for k in sorted(keys)]


@router.put("/close/stages", response_model=StageOut)
def put_stage(body: StageIn, db: Session = Depends(get_db)) -> StageOut:
    """매핑 완료 토글. **해외연구소는 여기서 '1차 완료'까지만** 한다 — 계정 완료는 PL이 나온 뒤
    실적 시뮬레이션 화면의 [확정]으로만 되고, 1차 없이도 2차를 진행할 수 있다. 그래서 versions 쪽
    계정 상태(version_account_status)를 건드리지 않는다. 증감분석 [1차 분석] 탭은 이 행의
    mapping_done을 보고 열린다 — "1차 완료"의 진실은 그 컬럼 하나다.

    다른 계정은 매핑 완료가 곧 계정 확정이지만, 그 토글은 아직 이 엔드포인트가 하지 않는다 —
    전표매핑 계정 화면이 붙을 때 같이 넣어야 중간 상태가 안 생긴다."""
    check_month(body.basis_month)
    if not db.scalar(select(Account).where(Account.key == body.account_key)):
        raise HTTPException(404, f"그런 계정이 없다: {body.account_key!r}")
    row = stage_row(db, body.basis_year, body.basis_month, body.account_key)
    if row is None:
        row = CloseAccountStage(
            basis_year=body.basis_year, basis_month=body.basis_month, account_key=body.account_key
        )
        db.add(row)
    # 1차를 고정해 둔 달은 되돌릴 수 없다 — 보고까지 끝낸 1차 값·사유가 조용히 열리면 안 된다.
    # 고정 플래그가 같은 행에 있으므로 다른 테이블을 들여다볼 필요가 없다.
    if body.account_key == ACCOUNT_KEY and not body.mapping_done and row.first_locked:
        raise HTTPException(
            409,
            f"{body.basis_year}년 {body.basis_month}월 1차가 고정돼 있어 1차 완료를 되돌릴 수 없다. "
            "증감분석 화면에서 [1차 고정]을 먼저 해제하라.",
        )
    row.mapping_done = body.mapping_done
    row.first_done_at = datetime.now(timezone.utc) if body.mapping_done else None
    row.note = body.note
    db.commit()
    return stage_out(db, body.basis_year, body.basis_month, body.account_key)


@router.get("/close/stages/{account_key}", response_model=StageOut)
def get_stage(account_key: str, basis_year: int, basis_month: int, db: Session = Depends(get_db)) -> StageOut:
    check_month(basis_month)
    return stage_out(db, basis_year, basis_month, account_key)
