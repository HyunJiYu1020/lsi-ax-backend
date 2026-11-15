"""실적결산 · 전표매핑 엔드포인트 — 1단계 취합(ERP 전표 적재·계정 자동 분류), 2단계 가공(해외연구소
연구소 단위 매핑·제외·이관·AI 규칙), 계정별 결산 상태.

overseas.py / overseas_variance.py와 가른 이유: 그 둘은 "편성 양식 안의 값"을 다루고(취합·집계·
증감분석), 여기는 **ERP 전표**를 다룬다. 다루는 자료가 다르고(전표 vs 편성값), 생명주기가 다르고
(전표는 다시 오고 덮인다), 쓰는 사람이 다르다(결산 담당자). 의존 방향은 한쪽이다 — 여기서 저쪽
헬퍼(연구소 기준정보·환율·cost_entries 읽기)를 가져다 쓰고, 저쪽은 이 모듈을 모른다.

이 화면이 지켜야 하는 규칙(전부 여기 구현):
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

from app.api.v1.endpoints.overseas import _n
from app.api.v1.endpoints.overseas_variance import _active_labs, _load_cost_version
from app.core.cost_groups import resolve_leaves
from app.core.periods import month_value, yyyymm
from app.services.overseas.fxcalc import rate_arrays
from app.services.overseas.labs import ACCOUNT_KEY, LabView, all_labs, lab_views
from app.core.voucher_match import match_rule, normalize, suggest_lab, suggest_rules
from app.db.session import get_db
from app.models import (
    Account,
    CloseAccountStage,
    CloseDeptRule,
    CloseMatchRule,
    CloseVoucher,
    CloseVoucherMap,
    Version,
    VersionAccountStatus,
)
from app.schemas.close import (
    AccountSummaryRow,
    DeptRule,
    LabMappingRow,
    MapEditIn,
    MapEditResult,
    MatchRuleIn,
    MatchRuleOut,
    OverseasMappingResult,
    RuleSuggestion,
    StageIn,
    StageOut,
    VoucherIngestIn,
    VoucherIngestResult,
    VoucherMapOut,
    VoucherOut,
)

router = APIRouter()

# AI 매칭의 단서(거래처·과제·코스트센터 → 연구소)를 어디까지 거슬러 모을지. 12로 둔 이유: 거래처가
# 바뀌면(계약 종료) 1년 전 이력이 지금 틀린 추천을 만든다. 반대로 3개월만 보면 분기에 한 번 오는
# 전표(CP 분기 정산)가 단서를 못 쌓는다.
HINT_MONTHS = 12


def _check_month(month: int) -> None:
    if not 1 <= month <= 12:
        raise HTTPException(400, "basis_month는 1~12여야 한다.")


def _account_names(db: Session) -> dict[str, str]:
    return {a.key: a.name for a in db.scalars(select(Account))}


# ---------- 1단계 취합: 전표 적재 · 계정 자동 분류 ----------
def _dept_rules(db: Session) -> list[CloseDeptRule]:
    """분류 기준정보. 부서코드를 채운 행이 먼저 오게 정렬한다 — 구체적인 기준이 이겨야 "이 비용계정은
    전부 A계정, 단 이 부서코드만 B계정"을 두 줄로 적을 수 있다."""
    rows = list(db.scalars(select(CloseDeptRule)))
    return sorted(rows, key=lambda r: (0 if r.cost_center else 1, r.cost_center, r.account_code))


def _classify(voucher: CloseVoucher, rules: list[CloseDeptRule]) -> tuple[str, str, str]:
    """(계정 key, 근거, 설명). 못 찾으면 빈 문자열 — 오류가 아니라 "기준정보에 아직 없는 조합"이고,
    화면이 알림으로 띄워 담당자가 기준정보를 고치거나 직접 지정한다."""
    code = (voucher.account_code or "").strip()
    center = (voucher.cost_center or "").strip()
    for rule in rules:
        if rule.account_code != code:
            continue
        if rule.cost_center and rule.cost_center != center:
            continue
        if rule.cost_center:
            note = rule.note or f"부서코드 {rule.cost_center} + 비용계정 {code}"
            return rule.account_key, "rule", note
        # 부서코드 없는 기준은 "비용계정만으로 분류"다 — 맞기는 하지만 부서 확인이 안 된 분류라서
        # 화면이 따로 셀 수 있게 근거를 구분해 둔다(weak_count).
        note = rule.note or f"비용계정 {code} (부서코드 기준 없음)"
        return rule.account_key, "account", note
    return "", "", f"부서코드 {center or '–'} + 비용계정 {code or '–'} 조합이 기준정보에 없다"


def _map_rows(db: Session, year: int, month: int, account_key: str | None = None) -> dict[tuple[str, int], CloseVoucherMap]:
    stmt = select(CloseVoucherMap).where(
        CloseVoucherMap.basis_year == year, CloseVoucherMap.basis_month == month
    )
    if account_key is not None:
        stmt = stmt.where(CloseVoucherMap.account_key == account_key)
    return {(m.voucher_no, m.line_no): m for m in db.scalars(stmt)}


def _vouchers(db: Session, year: int, month: int, account_key: str | None = None) -> list[CloseVoucher]:
    stmt = select(CloseVoucher).where(CloseVoucher.basis_year == year, CloseVoucher.basis_month == month)
    if account_key is not None:
        stmt = stmt.where(CloseVoucher.account_key == account_key)
    return sorted(db.scalars(stmt), key=lambda v: (v.voucher_no, v.line_no))


def _lab_list(db: Session, year: int, month: int) -> list[dict]:
    """AI 매칭에 넘길 연구소 목록. 합계 행·원화 기타는 _active_labs가 이미 빼 준다."""
    labs = _active_labs(db, year, month)
    return [
        {"pk": lab.pk, "lab_code": lab.lab_code, "name": lab.name}
        for lab in sorted(labs.values(), key=lambda x: (x.sort_order, x.pk))
    ]


def _hints(db: Session, year: int, month: int, account_key: str) -> dict:
    """과거 담당자 매핑에서 뽑은 단서표(거래처·과제·코스트센터 → 연구소).

    별도 기준정보로 두지 않는 이유: 표가 하나 더 늘면 아무도 갱신하지 않는다. 담당자가 지난달에
    직접 고른 매핑이 가장 정확하고 가장 최신인 대응표다. 같은 단서가 여러 연구소로 갔으면 단서에서
    **뺀다** — 애매한 단서로 추천하면 절반이 틀린다."""
    months: list[tuple[int, int]] = []
    y, m = year, month
    for _ in range(HINT_MONTHS):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        months.append((y, m))
    keys = {(yy, mm) for yy, mm in months}

    maps = db.scalars(
        select(CloseVoucherMap).where(
            CloseVoucherMap.account_key == account_key,
            CloseVoucherMap.decided_by.in_(("user", "rule")),
        )
    )
    by_key: dict[str, dict[str, set[str]]] = {"vendor": {}, "cost_center": {}, "project": {}}
    for mp in maps:
        if (mp.basis_year, mp.basis_month) not in keys:
            continue
        if not mp.scope_key or mp.transferred_to:
            continue
        vou = db.scalar(
            select(CloseVoucher).where(
                CloseVoucher.basis_year == mp.basis_year,
                CloseVoucher.basis_month == mp.basis_month,
                CloseVoucher.voucher_no == mp.voucher_no,
                CloseVoucher.line_no == mp.line_no,
            )
        )
        if vou is None:
            continue
        for field, raw in (
            ("vendor", vou.vendor),
            ("cost_center", vou.cost_center),
            ("project", vou.project_code),
        ):
            value = normalize(raw)
            if value:
                by_key[field].setdefault(value, set()).add(mp.scope_key)

    out: dict[str, dict[str, str]] = {}
    for field, table in by_key.items():
        out[field] = {value: next(iter(pks)) for value, pks in table.items() if len(pks) == 1}
    out["shared_centers"] = {r.cost_center for r in _dept_rules(db) if r.is_shared and r.cost_center}
    return out


def _rules_for(db: Session, account_key: str) -> list[dict]:
    return [
        {
            "match_field": r.match_field,
            "match_value": r.match_value,
            "scope_key": r.scope_key,
            "excluded": r.excluded,
            "note": r.note,
        }
        for r in db.scalars(
            select(CloseMatchRule).where(CloseMatchRule.account_key == account_key).order_by(CloseMatchRule.id)
        )
    ]


def _auto_map(vou: CloseVoucher, labs: list[dict], rules: list[dict], hints: dict) -> CloseVoucherMap:
    """전표 한 줄의 1차 매핑 — 승인된 규칙이 먼저, 없으면 AI 추천."""
    row = CloseVoucherMap(
        basis_year=vou.basis_year,
        basis_month=vou.basis_month,
        voucher_no=vou.voucher_no,
        line_no=vou.line_no,
        account_key=vou.account_key,
    )
    hit = match_rule(
        {
            "vendor": vou.vendor,
            "text": vou.text,
            "cost_center": vou.cost_center,
            "project_code": vou.project_code,
        },
        rules,
    )
    if hit is not None:
        row.decided_by = "rule"
        row.confidence = ""
        row.scope_key = "" if hit.get("excluded") else hit.get("scope_key") or ""
        row.excluded = bool(hit.get("excluded"))
        row.exclude_reason = hit.get("note") or "승인된 규칙에 따라 제외" if row.excluded else ""
        row.reason = f"승인된 규칙: {hit['match_field']} = {hit['match_value']}"
        return row
    s = suggest_lab(
        {
            "vendor": vou.vendor,
            "text": vou.text,
            "cost_center": vou.cost_center,
            "project_code": vou.project_code,
        },
        labs,
        hints,
    )
    row.decided_by = "ai"
    row.scope_key = s["scope_key"]
    row.confidence = s["confidence"]
    row.reason = s["reason"]
    row.excluded = s["excluded"]
    row.exclude_reason = s["exclude_reason"]
    return row


@router.post("/close/vouchers", response_model=VoucherIngestResult)
def ingest_vouchers(body: VoucherIngestIn, db: Session = Depends(get_db)) -> VoucherIngestResult:
    """ERP 전표 적재(1단계 취합). 업로드와 ERP I/F가 같은 경로를 쓴다 — 받은 자료의 모양이 같으므로
    적재·분류 규칙을 두 벌로 두면 둘이 어긋난다.

    재수신이 이 엔드포인트의 핵심이다: 전표는 덮어쓰지만 **매핑은 지우지 않는다**. 지난번에 담당자가
    고친 결과가 그대로 살아나고, 이번에 새로 온 전표만 AI가 매칭한다(공통 RULE). replace=True면
    이번에 안 온 전표를 지우지만(전기 취소분) 그 매핑도 남겨 둔다 — 다음 재수신에 같은 전표가
    돌아오면 또 살아난다."""
    _check_month(body.basis_month)
    rules = _dept_rules(db)
    existing = {(v.voucher_no, v.line_no): v for v in _vouchers(db, body.basis_year, body.basis_month)}
    maps = _map_rows(db, body.basis_year, body.basis_month)
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
        row.source_file = body.source_file or ""
        row.received_at = datetime.now(timezone.utc)
        row.account_key, row.classify_by, row.classify_note = _classify(row, rules)

    deleted = 0
    if body.replace:
        for key, row in existing.items():
            if key not in seen:
                db.delete(row)
                deleted += 1
    db.flush()

    # 새로 온 전표만 AI 매칭한다. 이미 매핑이 있으면(담당자 수정이든 지난번 AI 추천이든) 손대지 않는다.
    labs = _lab_list(db, body.basis_year, body.basis_month)
    by_account_rules: dict[str, list[dict]] = {}
    by_account_hints: dict[str, dict] = {}
    new_maps = 0
    for vou in _vouchers(db, body.basis_year, body.basis_month):
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
            by_account_rules[vou.account_key] = _rules_for(db, vou.account_key)
            by_account_hints[vou.account_key] = _hints(db, body.basis_year, body.basis_month, vou.account_key)
        db.add(_auto_map(vou, labs, by_account_rules[vou.account_key], by_account_hints[vou.account_key]))
        new_maps += 1

    db.commit()
    rows = _vouchers(db, body.basis_year, body.basis_month)
    unclassified = sum(1 for v in rows if not v.account_key)
    return VoucherIngestResult(
        basis_year=body.basis_year,
        basis_month=body.basis_month,
        received=len(seen),
        deleted=deleted,
        kept_maps=len(seen) - new_maps,
        new_maps=new_maps,
        unclassified=unclassified,
        by_account=_summary_rows(db, rows),
        note=(
            f"전표 {len(seen)}건 적재 · 기존 매핑 {len(seen) - new_maps}건 유지 · 신규 {new_maps}건 AI 매칭"
            + (f" · 미분류 {unclassified}건(기준정보 확인 필요)" if unclassified else "")
            + (f" · 이번에 안 온 전표 {deleted}건 삭제" if deleted else "")
        ),
    )


def _summary_rows(db: Session, rows: list[CloseVoucher]) -> list[AccountSummaryRow]:
    names = _account_names(db)
    agg: dict[str, dict] = {}
    for v in rows:
        key = v.account_key or ""
        slot = agg.setdefault(key, {"codes": set(), "n": 0, "krw": 0.0, "weak": 0})
        slot["codes"].add(v.account_code)
        slot["n"] += 1
        slot["krw"] += float(v.krw_amount or 0)
        if v.classify_by == "account":
            slot["weak"] += 1
    out = [
        AccountSummaryRow(
            account_key=key,
            account_name=names.get(key, "계정 미분류" if not key else key),
            account_codes=sorted(c for c in slot["codes"] if c),
            voucher_count=slot["n"],
            krw_total=slot["krw"],
            weak_count=slot["weak"],
        )
        for key, slot in agg.items()
    ]
    # 미분류를 맨 아래로 — 위에 두면 정상 분류된 계정보다 먼저 읽힌다.
    return sorted(out, key=lambda r: (not r.account_key, -r.krw_total))


@router.get("/close/vouchers", response_model=list[VoucherOut])
def list_vouchers(
    basis_year: int,
    basis_month: int,
    account_key: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
) -> list[VoucherOut]:
    """전표 목록. account_key=""(빈 문자열)을 주면 미분류만 — 1단계 알림에서 바로 넘어오는 경로다."""
    _check_month(basis_month)
    rows = _vouchers(db, basis_year, basis_month, account_key)
    if q:
        needle = q.upper()
        rows = [
            v
            for v in rows
            if needle in f"{v.voucher_no} {v.vendor} {v.text} {v.cost_center} {v.project_code}".upper()
        ]
    return [_voucher_out(v) for v in rows]


def _voucher_out(v: CloseVoucher) -> VoucherOut:
    return VoucherOut(
        voucher_no=v.voucher_no,
        line_no=v.line_no,
        posted_on=v.posted_on.isoformat() if v.posted_on else "",
        account_code=v.account_code,
        account_name=v.account_name,
        cost_center=v.cost_center,
        cost_center_name=v.cost_center_name,
        project_code=v.project_code,
        vendor=v.vendor,
        text=v.text,
        currency=v.currency,
        fc_amount=float(v.fc_amount or 0),
        krw_amount=float(v.krw_amount or 0),
        ref_no=v.ref_no,
        account_key=v.account_key,
        classify_by=v.classify_by,
        classify_note=v.classify_note,
        source_file=v.source_file,
    )


@router.get("/close/summary", response_model=list[AccountSummaryRow])
def get_close_summary(basis_year: int, basis_month: int, db: Session = Depends(get_db)) -> list[AccountSummaryRow]:
    """1단계 계정별 요약(건수·금액·미분류). 화면이 전표를 다 받아 세지 않게 서버가 같은 식으로 센다."""
    _check_month(basis_month)
    return _summary_rows(db, _vouchers(db, basis_year, basis_month))


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
        for r in _dept_rules(db)
    ]


@router.put("/close/dept-rules", response_model=DeptRule)
def put_dept_rule(body: DeptRule, db: Session = Depends(get_db)) -> DeptRule:
    """분류 기준 한 줄 저장. 고친 뒤 재분류는 따로 호출한다(/close/classify) — 기준을 여러 줄 고치는
    동안 매번 전표를 다시 분류하면 중간 상태로 몇 번씩 바뀐다."""
    if not db.scalar(select(Account).where(Account.key == body.account_key)):
        raise HTTPException(404, f"그런 계정이 없다: {body.account_key!r}")
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
    db.delete(row)
    db.commit()
    return {"deleted": 1}


@router.post("/close/classify", response_model=VoucherIngestResult)
def reclassify(basis_year: int, basis_month: int, db: Session = Depends(get_db)) -> VoucherIngestResult:
    """기준정보를 고친 뒤 그 달 전표를 다시 분류한다. 이미 있는 매핑은 유지하고, 분류가 새로 붙은
    전표만 AI 매칭한다 — 미분류였다가 분류된 전표가 매핑 없이 남으면 2단계에서 보이지 않는다."""
    _check_month(basis_month)
    rules = _dept_rules(db)
    rows = _vouchers(db, basis_year, basis_month)
    maps = _map_rows(db, basis_year, basis_month)
    labs = _lab_list(db, basis_year, basis_month)
    cache_rules: dict[str, list[dict]] = {}
    cache_hints: dict[str, dict] = {}
    new_maps = 0
    for vou in rows:
        vou.account_key, vou.classify_by, vou.classify_note = _classify(vou, rules)
        old = maps.get((vou.voucher_no, vou.line_no))
        if old is not None:
            if vou.account_key and not old.transferred_to:
                old.account_key = vou.account_key
            continue
        if not vou.account_key:
            continue
        if vou.account_key not in cache_rules:
            cache_rules[vou.account_key] = _rules_for(db, vou.account_key)
            cache_hints[vou.account_key] = _hints(db, basis_year, basis_month, vou.account_key)
        db.add(_auto_map(vou, labs, cache_rules[vou.account_key], cache_hints[vou.account_key]))
        new_maps += 1
    db.commit()
    rows = _vouchers(db, basis_year, basis_month)
    unclassified = sum(1 for v in rows if not v.account_key)
    return VoucherIngestResult(
        basis_year=basis_year,
        basis_month=basis_month,
        received=len(rows),
        kept_maps=len(rows) - new_maps,
        new_maps=new_maps,
        unclassified=unclassified,
        by_account=_summary_rows(db, rows),
        note=f"전표 {len(rows)}건 재분류 · 신규 매칭 {new_maps}건 · 미분류 {unclassified}건",
    )


# ---------- 2단계 가공: 해외연구소 연구소 단위 매핑 ----------
def _prev_lab_krw(db: Session, year: int, month: int) -> tuple[dict[str, float], float]:
    """전실행(당월 속보) 연구소별 당월 금액(원화)과 그 합계.

    속보 금액은 K$로 저장돼 있고 전표는 원화라서, 비교하려면 한쪽을 환산해야 한다. 속보 쪽을 원화로
    바꾸는 이유: 전표 금액을 건드리면 ERP와 안 맞아진다(공통 RULE: 원본 보관)."""
    labs = _active_labs(db, year, month)
    if not labs:
        return {}, 0.0
    j = month - 1
    rate = rate_arrays(db, year, month, "forecast", set(), (j,))
    usd_krw = rate["USD"][j]
    data = _load_cost_version(db, "forecast", yyyymm(year, month), set(labs))
    leaves = resolve_leaves("tot")
    out: dict[str, float] = {}
    for pk in labs:
        cats = data.get(pk, {})
        k = sum(_n(month_value(cats, leaf, j)) for leaf in leaves)
        out[pk] = k * usd_krw * 1000
    return out, sum(out.values())


def _status_of(mp: CloseVoucherMap) -> str:
    """화면 상태 칩. '확인 필요'가 따로 있는 이유: AI가 붙이긴 했지만 신뢰도가 낮은 전표를 '자동'으로
    보여주면 담당자가 안 본다 — 그게 틀린 매핑이 그대로 넘어가는 가장 흔한 경로다."""
    if mp.transferred_to:
        return "sent"
    if mp.transferred_from:
        return "received"
    if mp.excluded:
        return "excluded"
    if not mp.scope_key:
        return "unmapped"
    if mp.decided_by == "user":
        return "user"
    if mp.decided_by == "rule":
        return "rule"
    return "ai" if mp.confidence == "high" else "check"


def _suggestions(db: Session, year: int, month: int, account_key: str, labs: dict) -> list[RuleSuggestion]:
    """"3번 같은 수정을 하셨어요 — 앞으로 이렇게 할까요?". 이 달 것만 보지 않고 과거 수정까지
    같이 센다 — 매달 한 번씩 세 달 반복한 수정이 가장 전형적인 규칙 후보다."""
    start = (year, month)
    rows: list[dict] = []
    for mp in db.scalars(
        select(CloseVoucherMap).where(
            CloseVoucherMap.account_key == account_key, CloseVoucherMap.decided_by == "user"
        )
    ):
        if (mp.basis_year, mp.basis_month) > start:
            continue
        vou = db.scalar(
            select(CloseVoucher).where(
                CloseVoucher.basis_year == mp.basis_year,
                CloseVoucher.basis_month == mp.basis_month,
                CloseVoucher.voucher_no == mp.voucher_no,
                CloseVoucher.line_no == mp.line_no,
            )
        )
        if vou is None:
            continue
        rows.append(
            {
                "vendor": vou.vendor,
                "cost_center": vou.cost_center,
                "project_code": vou.project_code,
                "scope_key": mp.scope_key,
                "excluded": mp.excluded,
            }
        )
    field_label = {"vendor": "거래처", "text": "적요", "cost_center": "코스트센터", "project": "과제코드"}
    out = []
    for s in suggest_rules(rows, _rules_for(db, account_key)):
        where = "제외" if s["excluded"] else (labs.get(s["scope_key"]).name if labs.get(s["scope_key"]) else s["scope_key"])
        out.append(
            RuleSuggestion(
                match_field=s["match_field"],
                match_value=s["match_value"],
                scope_key=s["scope_key"],
                scope_label=where,
                excluded=s["excluded"],
                hit_count=s["hit_count"],
                question=(
                    f"{field_label[s['match_field']]} '{s['match_value']}'를 {s['hit_count']}번 "
                    f"{where}로 고치셨습니다. 앞으로도 이렇게 할까요?"
                ),
            )
        )
    return out


def _stage_row(db: Session, year: int, month: int, account_key: str) -> CloseAccountStage | None:
    return db.scalar(
        select(CloseAccountStage).where(
            CloseAccountStage.basis_year == year,
            CloseAccountStage.basis_month == month,
            CloseAccountStage.account_key == account_key,
        )
    )


def _account_done(db: Session, year: int, month: int, account_key: str) -> bool:
    """실적 버전의 계정 확정 여부(실적 시뮬레이션 [확정]). 해외연구소의 '완료'는 이쪽이고, 결산의
    '1차 완료'와 다른 층이다 — 행이 없으면 아직 확정 전이다(공통 RULE: 행 없음 = 작성 중)."""
    status = db.scalar(
        select(VersionAccountStatus.status)
        .join(Version, Version.id == VersionAccountStatus.version_id)
        .where(
            Version.year == year,
            Version.month == month,
            Version.kind == "actual",
            VersionAccountStatus.account_code == account_key,
        )
    )
    return status == "done"


@router.get("/close/overseas/mapping", response_model=OverseasMappingResult)
def get_overseas_mapping(basis_year: int, basis_month: int, db: Session = Depends(get_db)) -> OverseasMappingResult:
    """2단계 가공 · 해외연구소 작업대 한 벌.

    다른 계정과 달리 매핑 단위가 **연구소**다 — 해외연구소 전표는 과제코드 단위로 떨어져서 전표의
    코스트센터가 곧 편성 단위가 되지 못한다. 그래서 상단 표가 '부서'가 아니라 '연구소'이고, AI는
    거래처·적요·코스트센터 세 단서로 연구소를 추정한다.

    상단 표와 전표 목록을 한 응답으로 내려주는 이유: 두 번 호출해서 합치면 연구소 합계와 전표 합이
    서로 다른 시점 값이 되어, 합계는 맞는데 전표를 세면 안 맞는 표가 나온다."""
    _check_month(basis_month)
    labs = _active_labs(db, basis_year, basis_month)
    prev_by_pk, prev_total = _prev_lab_krw(db, basis_year, basis_month)
    rows = _vouchers(db, basis_year, basis_month, ACCOUNT_KEY)
    maps = _map_rows(db, basis_year, basis_month)
    # 이관으로 들어온 전표: 원래 다른 계정이었지만 매핑이 해외연구소를 가리키는 것.
    for mp in _map_rows(db, basis_year, basis_month, ACCOUNT_KEY).values():
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
        status = _status_of(mp)
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
    stage = _stage_row(db, basis_year, basis_month, ACCOUNT_KEY)
    done = bool(stage and stage.mapping_done)
    actual_total = sum(actual_by_pk.values())
    return OverseasMappingResult(
        basis_year=basis_year,
        basis_month=basis_month,
        prev_label=f"{basis_year}년 {basis_month}월 속보",
        labs=lab_rows,
        vouchers=[_voucher_out(v) for v in rows],
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
        suggestions=_suggestions(db, basis_year, basis_month, ACCOUNT_KEY, labs),
        mapping_done=done,
        first_done_at=stage.first_done_at.isoformat() if stage and stage.first_done_at else "",
        account_done=_account_done(db, basis_year, basis_month, ACCOUNT_KEY),
        note=(
            f"전표 {len(rows)}건 · 확인 필요 {check_count}건 · 미매핑 {unmapped_count}건 · 제외 {excluded_count}건"
            + (" · 1차 완료" if done else "")
        ),
    )


@router.put("/close/overseas/mapping", response_model=MapEditResult)
def put_overseas_mapping(body: MapEditIn, db: Session = Depends(get_db)) -> MapEditResult:
    """매핑 수정 저장 — 담당자가 고친 것만 들어온다(decided_by='user'로 박는다).

    user로 박아야 하는 이유: 'AI 다시 매칭'이 ai만 되돌리게 하려면 사람이 고친 것을 구분해 둬야 한다.
    구분이 없으면 다시 매칭 한 번에 담당자 수정이 전부 날아가고, 같은 수정을 매달 반복하게 된다.

    저장 직후 규칙 제안을 같이 내려준다 — 화면이 [저장] 다음 화면에서 바로 "앞으로 이렇게 할까요?"를
    물을 수 있어야 담당자가 그 자리에서 답한다(따로 호출하면 그 질문을 아무도 안 본다)."""
    _check_month(body.basis_month)
    labs = _active_labs(db, body.basis_year, body.basis_month)
    names = _account_names(db)
    saved = 0
    for edit in body.edits:
        if edit.transferred_to and edit.transferred_to not in names:
            raise HTTPException(404, f"이관 대상 계정이 없다: {edit.transferred_to!r}")
        if edit.scope_key and edit.scope_key not in labs:
            raise HTTPException(404, f"그런 연구소가 없다: {edit.scope_key!r}")
        if edit.scope_key and edit.excluded:
            raise HTTPException(400, "제외한 전표는 연구소에 붙이지 않는다 — 둘 중 하나만 지정해야 한다.")
        vou = db.scalar(
            select(CloseVoucher).where(
                CloseVoucher.basis_year == body.basis_year,
                CloseVoucher.basis_month == body.basis_month,
                CloseVoucher.voucher_no == edit.voucher_no,
                CloseVoucher.line_no == edit.line_no,
            )
        )
        if vou is None:
            raise HTTPException(404, f"그런 전표가 없다: {edit.voucher_no} 라인 {edit.line_no}")
        row = db.scalar(
            select(CloseVoucherMap).where(
                CloseVoucherMap.basis_year == body.basis_year,
                CloseVoucherMap.basis_month == body.basis_month,
                CloseVoucherMap.voucher_no == edit.voucher_no,
                CloseVoucherMap.line_no == edit.line_no,
            )
        )
        if row is None:
            row = CloseVoucherMap(
                basis_year=body.basis_year,
                basis_month=body.basis_month,
                voucher_no=edit.voucher_no,
                line_no=edit.line_no,
            )
            db.add(row)
        if edit.transferred_to:
            # 이관: 보낸 쪽에 '보냄', 받는 쪽에 '받음'을 남겨 양쪽이 같은 전표를 두 번 세지 않게 한다.
            row.transferred_to = edit.transferred_to
            row.transferred_from = ""
            row.account_key = edit.transferred_to
            row.scope_key = ""
            row.excluded = False
            row.exclude_reason = ""
            row.reason = f"{names.get(body.account_key, body.account_key)} → {names[edit.transferred_to]} 이관"
        else:
            row.transferred_to = ""
            row.account_key = body.account_key
            row.scope_key = edit.scope_key
            row.excluded = edit.excluded
            row.exclude_reason = edit.exclude_reason if edit.excluded else ""
            row.reason = "담당자 지정"
        row.decided_by = "user"
        row.confidence = ""  # 사람이 고른 매핑에는 신뢰도 개념이 없다
        saved += 1
    db.commit()
    suggestions = _suggestions(db, body.basis_year, body.basis_month, body.account_key, labs)
    return MapEditResult(
        saved=saved,
        suggestions=suggestions,
        note=f"매핑 {saved}건 저장"
        + (f" · 반복 수정 {len(suggestions)}건을 규칙으로 등록할지 확인이 필요하다" if suggestions else ""),
    )


@router.post("/close/overseas/rematch", response_model=MapEditResult)
def rematch_overseas(basis_year: int, basis_month: int, db: Session = Depends(get_db)) -> MapEditResult:
    """AI 다시 매칭 — AI가 정한 매핑만 새로 계산한다.

    담당자가 고친 것(user)과 승인된 규칙(rule)은 건드리지 않는다. 사람이 판단한 걸 기계가 되돌리면
    같은 수정을 매달 다시 해야 하고, 그러면 아무도 수정하지 않게 된다."""
    _check_month(basis_month)
    labs = _lab_list(db, basis_year, basis_month)
    rules = _rules_for(db, ACCOUNT_KEY)
    hints = _hints(db, basis_year, basis_month, ACCOUNT_KEY)
    maps = _map_rows(db, basis_year, basis_month, ACCOUNT_KEY)
    changed = 0
    for vou in _vouchers(db, basis_year, basis_month, ACCOUNT_KEY):
        row = maps.get((vou.voucher_no, vou.line_no))
        if row is not None and row.decided_by != "ai":
            continue
        fresh = _auto_map(vou, labs, rules, hints)
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
            scope_key=r.scope_key,
            excluded=r.excluded,
            hit_count=r.hit_count,
            note=r.note,
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
    if body.match_field not in ("vendor", "text", "cost_center", "project"):
        raise HTTPException(400, "match_field는 vendor | text | cost_center | project만 쓸 수 있다.")
    if not body.match_value.strip():
        raise HTTPException(400, "match_value가 비어 있다.")
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
    row.scope_key = body.scope_key
    row.excluded = body.excluded
    row.hit_count = body.hit_count
    row.note = body.note
    db.commit()
    db.refresh(row)

    if body.apply_basis_year and body.apply_basis_month:
        _check_month(body.apply_basis_month)
        rules = _rules_for(db, body.account_key)
        maps = _map_rows(db, body.apply_basis_year, body.apply_basis_month, body.account_key)
        labs = _lab_list(db, body.apply_basis_year, body.apply_basis_month)
        hints = _hints(db, body.apply_basis_year, body.apply_basis_month, body.account_key)
        for vou in _vouchers(db, body.apply_basis_year, body.apply_basis_month, body.account_key):
            mp = maps.get((vou.voucher_no, vou.line_no))
            if mp is not None and mp.decided_by == "user":
                continue
            fresh = _auto_map(vou, labs, rules, hints)
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
        scope_key=row.scope_key,
        excluded=row.excluded,
        hit_count=row.hit_count,
        note=row.note,
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
def _stage_out(db: Session, year: int, month: int, account_key: str) -> StageOut:
    stage = _stage_row(db, year, month, account_key)
    rows = _vouchers(db, year, month, account_key)
    maps = _map_rows(db, year, month, account_key)
    unmapped = sum(
        1
        for v in rows
        if not (m := maps.get((v.voucher_no, v.line_no)))
        or (not m.scope_key and not m.excluded and not m.transferred_to)
    )
    done = bool(stage and stage.mapping_done)
    acct_done = _account_done(db, year, month, account_key)
    if account_key == ACCOUNT_KEY:
        # 해외연구소만 '1차 완료'와 '완료'를 가른다. 1차는 ERP 약식이고 완료는 PL 확정이라,
        # 둘을 한 칭호로 묶으면 1차만 끝난 달을 "결산 끝"으로 읽는다.
        display = "완료" if acct_done else "1차 완료" if done else "작성중" if rows else "미착수"
    else:
        display = "완료" if done else "작성중" if rows else "미착수"
    return StageOut(
        basis_year=year,
        basis_month=month,
        account_key=account_key,
        account_name=_account_names(db).get(account_key, account_key),
        mapping_done=done,
        first_done_at=stage.first_done_at.isoformat() if stage and stage.first_done_at else "",
        account_done=acct_done,
        display_status=display,
        voucher_count=len(rows),
        krw_total=sum(float(v.krw_amount or 0) for v in rows),
        unmapped_count=unmapped,
        note=(stage.note if stage else ""),
    )


@router.get("/close/stages", response_model=list[StageOut])
def list_stages(basis_year: int, basis_month: int, db: Session = Depends(get_db)) -> list[StageOut]:
    """3단계 확정 화면의 계정별 status·숫자. 해외연구소 행만 '1차 완료 / 완료'를 가른다."""
    _check_month(basis_month)
    keys = {v.account_key for v in _vouchers(db, basis_year, basis_month) if v.account_key}
    keys |= {
        s.account_key
        for s in db.scalars(
            select(CloseAccountStage).where(
                CloseAccountStage.basis_year == basis_year, CloseAccountStage.basis_month == basis_month
            )
        )
    }
    keys.add(ACCOUNT_KEY)
    return [_stage_out(db, basis_year, basis_month, k) for k in sorted(keys)]


@router.put("/close/stages", response_model=StageOut)
def put_stage(body: StageIn, db: Session = Depends(get_db)) -> StageOut:
    """매핑 완료 토글. **해외연구소는 여기서 '1차 완료'까지만** 한다 — 계정 완료는 PL이 나온 뒤
    실적 시뮬레이션 화면의 [확정]으로만 되고, 1차 없이도 2차를 진행할 수 있다. 그래서 versions 쪽
    계정 상태(version_account_status)를 건드리지 않는다. 증감분석 [1차 분석] 탭은 이 행의
    mapping_done을 보고 열린다 — "1차 완료"의 진실은 그 컬럼 하나다(PLAN.md Phase 4).

    다른 계정은 매핑 완료가 곧 계정 확정이지만, 그 토글은 아직 이 엔드포인트가 하지 않는다 —
    전표매핑 계정 화면이 붙을 때 같이 넣어야 중간 상태가 안 생긴다."""
    _check_month(body.basis_month)
    if not db.scalar(select(Account).where(Account.key == body.account_key)):
        raise HTTPException(404, f"그런 계정이 없다: {body.account_key!r}")
    row = _stage_row(db, body.basis_year, body.basis_month, body.account_key)
    if row is None:
        row = CloseAccountStage(
            basis_year=body.basis_year, basis_month=body.basis_month, account_key=body.account_key
        )
        db.add(row)
    # 1차를 고정해 둔 달은 되돌릴 수 없다 — 보고까지 끝낸 1차 값·사유가 조용히 열리면 안 된다.
    # 고정 플래그가 같은 행에 있으므로 다른 테이블을 들여다볼 필요가 없다(PLAN.md Phase 4).
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
    return _stage_out(db, body.basis_year, body.basis_month, body.account_key)


@router.get("/close/stages/{account_key}", response_model=StageOut)
def get_stage(account_key: str, basis_year: int, basis_month: int, db: Session = Depends(get_db)) -> StageOut:
    _check_month(basis_month)
    return _stage_out(db, basis_year, basis_month, account_key)

