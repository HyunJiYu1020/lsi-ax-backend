"""실적결산 · 전표매핑 로직 — 전표 분류, AI 매핑, 단서표, 규칙 제안, 결산 상태 계산.

endpoints/close.py에서 로직만 내려온 모듈이다. 엔드포인트는 요청을 받고 여기 함수를 불러
응답으로 바꾸는 얇은 층이고, "어떻게 분류·매핑·집계하나"는 전부 여기 있다. 규칙 설명(원본 보관,
재수신 시 매핑 유지, AI는 추천만 등)은 endpoints/close.py 모듈 주석에 있다.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.overseas.cost_groups import resolve_leaves
from app.accounts.overseas.services.fxcalc import rate_arrays
from app.accounts.overseas.services.labs import ACCOUNT_KEY
from app.accounts.overseas.services.variance import active_labs, load_cost_version
from app.shared.core.periods import month_value, yyyymm
from app.shared.core.values import n
from app.shared.core.voucher_match import match_rule, normalize, suggest_lab, suggest_rules
from app.shared.models.account import Account
from app.shared.models.close import CloseAccountStage, CloseDeptRule, CloseMatchRule, CloseVoucher, CloseVoucherMap
from app.shared.models.version import Version, VersionAccountStatus
from app.shared.schemas.close import (
    AccountSummaryRow,
    MapEditIn,
    MapEditResult,
    RuleSuggestion,
    StageOut,
    VoucherOut,
)

# AI 매칭의 단서(거래처·과제·코스트센터 → 연구소)를 어디까지 거슬러 모을지. 12로 둔 이유: 거래처가
# 바뀌면(계약 종료) 1년 전 이력이 지금 틀린 추천을 만든다. 반대로 3개월만 보면 분기에 한 번 오는
# 전표(CP 분기 정산)가 단서를 못 쌓는다.
HINT_MONTHS = 12


def check_month(month: int) -> None:
    if not 1 <= month <= 12:
        raise HTTPException(400, "basis_month는 1~12여야 한다.")


def account_names(db: Session) -> dict[str, str]:
    return {a.key: a.name for a in db.scalars(select(Account))}


# ---------- 1단계 취합: 전표 적재 · 계정 자동 분류 ----------
def dept_rules(db: Session) -> list[CloseDeptRule]:
    """분류 기준정보. 부서코드를 채운 행이 먼저 오게 정렬한다 — 구체적인 기준이 이겨야 "이 비용계정은
    전부 A계정, 단 이 부서코드만 B계정"을 두 줄로 적을 수 있다."""
    rows = list(db.scalars(select(CloseDeptRule)))
    return sorted(rows, key=lambda r: (0 if r.cost_center else 1, r.cost_center, r.account_code))


def classify(voucher: CloseVoucher, rules: list[CloseDeptRule]) -> tuple[str, str, str]:
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


def map_rows(db: Session, year: int, month: int, account_key: str | None = None) -> dict[tuple[str, int], CloseVoucherMap]:
    stmt = select(CloseVoucherMap).where(
        CloseVoucherMap.basis_year == year, CloseVoucherMap.basis_month == month
    )
    if account_key is not None:
        stmt = stmt.where(CloseVoucherMap.account_key == account_key)
    return {(m.voucher_no, m.line_no): m for m in db.scalars(stmt)}


def vouchers(db: Session, year: int, month: int, account_key: str | None = None) -> list[CloseVoucher]:
    stmt = select(CloseVoucher).where(CloseVoucher.basis_year == year, CloseVoucher.basis_month == month)
    if account_key is not None:
        stmt = stmt.where(CloseVoucher.account_key == account_key)
    return sorted(db.scalars(stmt), key=lambda v: (v.voucher_no, v.line_no))


def lab_list(db: Session, year: int, month: int) -> list[dict]:
    """AI 매칭에 넘길 연구소 목록. 합계 행·원화 기타는 _active_labs가 이미 빼 준다."""
    labs = active_labs(db, year, month)
    return [
        {"pk": lab.pk, "lab_code": lab.lab_code, "name": lab.name}
        for lab in sorted(labs.values(), key=lambda x: (x.sort_order, x.pk))
    ]


def hints(db: Session, year: int, month: int, account_key: str) -> dict:
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
    out["shared_centers"] = {r.cost_center for r in dept_rules(db) if r.is_shared and r.cost_center}
    return out


def rules_for(db: Session, account_key: str) -> list[dict]:
    return [
        {
            "match_field": r.match_field,
            "match_value": r.match_value,
            "match_op": r.match_op,
            "scope_key": r.scope_key,
            "excluded": r.excluded,
            "note": r.note,
            # 거절한 규칙도 같이 넘긴다 — match_rule이 건너뛰고(적용 안 함), suggest_rules는
            # 이걸 "이미 답한 것"으로 보고 같은 제안을 다시 내지 않는다.
            "rejected": r.rejected,
        }
        for r in db.scalars(
            select(CloseMatchRule).where(CloseMatchRule.account_key == account_key).order_by(CloseMatchRule.id)
        )
    ]


def auto_map(vou: CloseVoucher, labs: list[dict], rules: list[dict], hints: dict) -> CloseVoucherMap:
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
            "assignment": vou.assignment,
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


def summary_rows(db: Session, rows: list[CloseVoucher]) -> list[AccountSummaryRow]:
    names = account_names(db)
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


def voucher_out(v: CloseVoucher) -> VoucherOut:
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
        assignment=v.assignment,
        currency=v.currency,
        fc_amount=float(v.fc_amount or 0),
        krw_amount=float(v.krw_amount or 0),
        ref_no=v.ref_no,
        account_key=v.account_key,
        classify_by=v.classify_by,
        classify_note=v.classify_note,
        source_file=v.source_file,
    )


# ---------- 2단계 가공: 해외연구소 연구소 단위 매핑 ----------
def prev_lab_krw(db: Session, year: int, month: int) -> tuple[dict[str, float], float]:
    """전실행(당월 속보) 연구소별 당월 금액(원화)과 그 합계.

    속보 금액은 K$로 저장돼 있고 전표는 원화라서, 비교하려면 한쪽을 환산해야 한다. 속보 쪽을 원화로
    바꾸는 이유: 전표 금액을 건드리면 ERP와 안 맞아진다(공통 RULE: 원본 보관)."""
    labs = active_labs(db, year, month)
    if not labs:
        return {}, 0.0
    j = month - 1
    rate = rate_arrays(db, year, month, "forecast", set(), (j,))
    usd_krw = rate["USD"][j]
    data = load_cost_version(db, "forecast", yyyymm(year, month), set(labs))
    leaves = resolve_leaves("tot")
    out: dict[str, float] = {}
    for pk in labs:
        cats = data.get(pk, {})
        k = sum(n(month_value(cats, leaf, j)) for leaf in leaves)
    out[pk] = k * usd_krw
    return out, sum(out.values())


def status_of(mp: CloseVoucherMap) -> str:
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


def suggestions(db: Session, year: int, month: int, account_key: str, labs: dict) -> list[RuleSuggestion]:
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
        field_label = {
        "vendor": "거래처", "text": "적요", "cost_center": "코스트센터",
        "project": "과제코드", "assignment": "지정(Assignment)",
        }
    out = []
    for s in suggest_rules(rows, rules_for(db, account_key)):
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
                f"{field_label.get(s['match_field'], s['match_field'])} '{s['match_value']}'를 {s['hit_count']}번 "
                    f"{where}로 고치셨습니다. 앞으로도 이렇게 할까요?"
                ),
            )
        )
    return out


def stage_row(db: Session, year: int, month: int, account_key: str) -> CloseAccountStage | None:
    return db.scalar(
        select(CloseAccountStage).where(
            CloseAccountStage.basis_year == year,
            CloseAccountStage.basis_month == month,
            CloseAccountStage.account_key == account_key,
        )
    )


def account_done(db: Session, year: int, month: int, account_key: str) -> bool:
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


def save_map_edits(body: MapEditIn, db: Session) -> MapEditResult:
    """매핑 수정 저장 — 담당자가 고친 것만 들어온다(decided_by='user'로 박는다).

    user로 박아야 하는 이유: 'AI 다시 매칭'이 ai만 되돌리게 하려면 사람이 고친 것을 구분해 둬야 한다.
    구분이 없으면 다시 매칭 한 번에 담당자 수정이 전부 날아가고, 같은 수정을 매달 반복하게 된다.

    저장 직후 규칙 제안을 같이 내려준다 — 화면이 [저장] 다음 화면에서 바로 "앞으로 이렇게 할까요?"를
    물을 수 있어야 담당자가 그 자리에서 답한다(따로 호출하면 그 질문을 아무도 안 본다)."""
    check_month(body.basis_month)
    labs = active_labs(db, body.basis_year, body.basis_month)
    names = account_names(db)
    if body.account_key and body.account_key not in names:
        raise HTTPException(404, f"그런 계정이 없다: {body.account_key!r}")
    saved = 0
    for edit in body.edits:
        if edit.transferred_to and edit.transferred_to not in names:
            raise HTTPException(404, f"이관 대상 계정이 없다: {edit.transferred_to!r}")
        # 붙일 곳(scope_key)의 뜻이 계정마다 다르다 — 해외연구소는 연구소 pk라서 실재하는지 볼 수
        # 있지만, 다른 계정은 계약·과제·부서코드라 서버가 가진 목록이 없다. 그래서 해외연구소만
        # 검증한다(여기서 전 계정을 막으면 다른 계정 화면은 아무 매핑도 저장할 수 없다).
        if edit.scope_key and body.account_key == ACCOUNT_KEY and edit.scope_key not in labs:
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

    # 규칙 제안은 "연구소를 어떤 단서로 맞혔나"를 보는 것이라 해외연구소에서만 만든다.
    suggs = (
        suggestions(db, body.basis_year, body.basis_month, body.account_key, labs)
        if body.account_key == ACCOUNT_KEY
        else []
    )
    return MapEditResult(
        saved=saved,
        suggestions=suggs,
        note=f"매핑 {saved}건 저장"
        + (f" · 반복 수정 {len(suggs)}건을 규칙으로 등록할지 확인이 필요하다" if suggs else ""),
    )


# ---------- 계정별 결산 상태 (3단계 확정) ----------
def stage_out(db: Session, year: int, month: int, account_key: str) -> StageOut:
    stage = stage_row(db, year, month, account_key)
    rows = vouchers(db, year, month, account_key)
    maps = map_rows(db, year, month, account_key)
    unmapped = sum(
        1
        for v in rows
        if not (m := maps.get((v.voucher_no, v.line_no)))
        or (not m.scope_key and not m.excluded and not m.transferred_to)
    )
    done = bool(stage and stage.mapping_done)
    acct_done = account_done(db, year, month, account_key)
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
        account_name=account_names(db).get(account_key, account_key),
        mapping_done=done,
        first_done_at=stage.first_done_at.isoformat() if stage and stage.first_done_at else "",
        account_done=acct_done,
        display_status=display,
        voucher_count=len(rows),
        krw_total=sum(float(v.krw_amount or 0) for v in rows),
        unmapped_count=unmapped,
        note=(stage.note if stage else ""),
    )
