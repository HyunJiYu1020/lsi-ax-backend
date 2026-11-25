"""이상 감지 기준(anomaly rules) 관리 + 스캔 — 반영 전 "이거 맞나요?".

판정 규칙은 app/shared/core/anomaly.py, 기준값은 overseas_anomaly_rules에 있다. 여기는 그 둘을
붙이고 비교 대상(전실행)을 DB에서 꺼내 오는 일을 한다. 반영을 막지 않는다 — 알리기만 한다.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.overseas.cost_groups import (
    GROUP_CALC,
    VARIANCE_ROWS,
    category_label,
    resolve_leaves,
)
from app.shared.core import anomaly
from app.shared.core.periods import ym_month, ym_year
from app.shared.models.generic import AnomalyRule, AnomalyRuleLog
from app.accounts.overseas.schemas import (
    AnomalyFinding,
    AnomalyRuleIn,
    AnomalyRuleOut,
    AnomalyScanIn,
    AnomalyScanResult,
)
from app.accounts.overseas.services.extra_rows import check_lab_pk, check_version, months_by_category
from app.accounts.overseas.services.labs import ACCOUNT_KEY, all_labs
from app.accounts.overseas.services.versions import load_basis, resolve_bases


# 금액이 아니라 "명"으로 적혀 오는 행(인력). 기간 합 검사의 허용값이 달라서 따로 알려 준다 —
# 단위는 VARIANCE_ROWS가 원본이니(공통코드에서 갱신된다) 여기서 베껴 적지 않고 거기서 뽑는다.
COUNT_KEYS = {k for k, _lv, unit in VARIANCE_ROWS if unit == "hc"}


def rule_rows(db: Session) -> list[AnomalyRule]:
    # anomaly_rules는 전 계정 공용 테이블이라 계정으로 걸러야 한다 — 안 걸면 다른 계정의
    # 급변 기준이 섞여 들어온다.
    return list(db.scalars(select(AnomalyRule).where(AnomalyRule.account_key == ACCOUNT_KEY)))


def rule_out(key: str, r: dict) -> AnomalyRuleOut:
    # desc는 "지금 값이 들어간 설명"이다 — 화면·챗봇이 기준을 말로 옮길 때 숫자를 따로 조립하지
    # 않게(두 곳이 다르게 적는 걸 막으려고) 서버가 완성해서 준다.
    fmt = {
        "pct": f"{r['pct']:g}" if r.get("pct") is not None else "",
        # 기준값은 $로 저장하고 사람에게는 화면과 같은 K$로 보여준다(anomaly._k와 같은 규칙).
        "amount": f"${r['amount'] / 1000:,.1f}K".replace(".0K", "K") if r.get("amount") is not None else "",
        "ratio": f"{r['ratio']:g}" if r.get("ratio") is not None else "",
    }
    return AnomalyRuleOut(
        rule_key=key, label=r["label"], enabled=r["enabled"], pct=r.get("pct"), amount=r.get("amount"),
        ratio=r.get("ratio"), desc=r["desc"].format(**fmt),
    )


def rules_out(db: Session, overrides: dict | None = None) -> list[AnomalyRuleOut]:
    merged = anomaly.merged_rules(rule_rows(db), overrides)
    return [rule_out(k, merged[k]) for k in anomaly.RULE_KEYS]


def upsert_rule(db: Session, body: AnomalyRuleIn) -> list[AnomalyRuleOut]:
    """기준 하나를 바꾼다(보낸 항목만). reset=true면 그 기준을 기본값으로 되돌린다.

    바꾼 내역은 overseas_anomaly_rule_logs에 before/after로 남긴다 — 기준이 바뀌면 경고 개수가
    달라져서, 나중에 "지난달엔 왜 이게 안 걸렸지"에 답할 수 있어야 한다."""
    key = anomaly.RULE_ALIASES.get(body.rule_key.strip(), body.rule_key.strip())
    if key not in anomaly.RULE_KEYS:
        raise HTTPException(
            status_code=400, detail=f"없는 기준입니다: {body.rule_key!r} (가능: {', '.join(anomaly.RULE_KEYS)})"
        )
    if body.pct is not None and not (0 < body.pct <= 10000):
        raise HTTPException(status_code=400, detail="비율(pct)은 0보다 크고 10000 이하여야 합니다.")
    if body.amount is not None and body.amount < 0:
        raise HTTPException(status_code=400, detail="금액(amount)은 0 이상이어야 합니다.")
    if body.ratio is not None and body.ratio <= 1:
        raise HTTPException(status_code=400, detail="배수(ratio)는 1보다 커야 합니다.")

    row = db.scalar(
        select(AnomalyRule).where(AnomalyRule.account_key == ACCOUNT_KEY, AnomalyRule.rule_key == key)
    )
    before = anomaly.merged_rules(rule_rows(db))[key]
    before_snap = {k: before[k] for k in ("enabled", "pct", "amount", "ratio")}
    if not row:
        row = AnomalyRule(account_key=ACCOUNT_KEY, rule_key=key)
        db.add(row)
    if body.reset:
        d = anomaly.DEFAULTS[key]
        row.enabled, row.pct, row.amount, row.ratio = d["enabled"], d["pct"], d["amount"], d["ratio"]
    else:
        # None은 "안 건드림"이다 — 그 기준이 원래 안 쓰는 파라미터를 NULL로 지우는 것과 구분한다.
        if body.enabled is not None:
            row.enabled = body.enabled
        if body.pct is not None:
            row.pct = body.pct
        if body.amount is not None:
            row.amount = body.amount
        if body.ratio is not None:
            row.ratio = body.ratio
    db.flush()
    after = anomaly.merged_rules(rule_rows(db))[key]
    after_snap = {k: after[k] for k in ("enabled", "pct", "amount", "ratio")}
    if before_snap != after_snap:
        db.add(
            AnomalyRuleLog(
                account_key=ACCOUNT_KEY, rule_key=key, before=before_snap, after=after_snap,
                source="reset" if body.reset else (body.source or "screen")[:10], note=body.note.strip()[:200],
            )
        )
    db.commit()
    return rules_out(db)


def scan_pks(db: Session, lab_pk: str, lab_wide: bool) -> list[str]:
    """검사가 읽을 자리(pk) 목록. lab_wide면 같은 연구소의 파트를 전부 — 증감사유·외주용역은
    연구소 단위 시트라(SARC = CPU+GPU) 파트 하나와 비교하면 전부 불일치로 걸린다. 총계 행
    (part_code=TOTAL)은 빼고 센다 — 다른 파트를 더해 만든 값이라 넣으면 두 번 세어진다."""
    if not lab_wide:
        return [lab_pk]
    lab_code = next((lab.lab_code for lab in all_labs(db) if lab.pk == lab_pk), lab_pk)
    return [
        lab.pk for lab in all_labs(db)
        if lab.lab_code == lab_code and (lab.part_code or "").upper() != "TOTAL"
    ] or [lab_pk]


def _sum_by_category(parts: list[dict[str, list]]) -> dict[str, list[float]]:
    """pk별 {계정 → 월 배열} 여러 벌을 계정별로 더한다(파트 합)."""
    out: dict[str, list[float]] = {}
    for one in parts:
        for cat, months in (one or {}).items():
            acc = out.setdefault(cat, [])
            for i, v in enumerate(months or []):
                if i < len(acc):
                    acc[i] += float(v or 0)
                else:
                    acc.append(float(v or 0))
    return out


def analysis_bases(db: Session, body: AnomalyScanIn) -> tuple[dict[str, dict | None], list[tuple[str, str]]]:
    """증감분석 세 열(PLAN · 전실행 · 당월)을 맞대볼 본사 값 + 못 구한 것의 이유.

    **비교 기준은 서버가 정한다**(API.md "비교 기준을 화면이 추측하면 안 된다") — 전실행은 직전
    버전이고 가리키는 대상이 달마다 바뀐다(11월 속보의 전실행 = 10월 실적, 1월 속보는 전년 12월
    실적). 경영계획은 "적용 차수"로 푼다(entries.pick_plan_no와 같은 규칙을 resolve_bases가 쓴다).
    급변 검사가 쓰는 base_vals(전월 **속보**)와는 다른 값이다 — 그쪽은 "지난번 편성과 얼마나
    달라졌나"를 보고, 이쪽은 연구소가 시트에 옮겨 적은 칸이 본사 장부와 같은지를 본다.

    당월 열은 저장된 값이 아니라 **같은 파일로 온 비용 시트**(body.cost_vals)와 맞댄다. 반영 전
    검사라 비용 값은 아직 DB에 없고, 보려는 것도 "한 파일 안에서 두 시트가 같은 말을 하는가"다.
    """
    out: dict[str, dict | None] = {"plan": None, "prev": None, "cur": None}
    skipped: list[tuple[str, str]] = []
    if not body.analysis:
        return out, skipped
    ym = body.yyyymm or 0
    if not (2000 <= ym_year(ym) <= 2999 and 1 <= ym_month(ym) <= 12):
        return out, [("analysis_prev", "증감분석 대조 — 기준 연월(yyyymm)을 읽을 수 없습니다")]
    year, month = ym_year(ym), ym_month(ym)
    pks = set(scan_pks(db, body.lab_pk, body.base_lab_wide))

    by_key = {b.key: b for b in resolve_bases(db, year, month, body.version).bases}
    for key, rule_key in (("plan", "analysis_plan"), ("prev", "analysis_prev")):
        b = by_key.get(key)
        label = anomaly.DEFAULTS[rule_key]["label"]
        if b is None or not b.has_data:
            note = (b.fallback_note if b else "") or "비교할 값이 없습니다"
            skipped.append((rule_key, f"{label} — {note}"))
            continue
        # load_basis는 {pk: {계정 → 월}}로 준다 — 파트가 나뉜 연구소는 그걸 계정별로 더해야
        # 연구소 단위 시트(증감분석)와 같은 자리를 가리킨다.
        loaded = load_basis(db, (b.year, b.month, b.kind, b.plan_no), pks)
        out[key] = _sum_by_category([loaded.get(pk, {}) for pk in pks])

    if body.cost_vals:
        out["cur"] = {k: list(v or []) for k, v in body.cost_vals.items()}
    else:
        skipped.append((
            "analysis_cur",
            f"{anomaly.DEFAULTS['analysis_cur']['label']} — 같은 파일의 비용 시트 값이 이 검사에"
            " 함께 오지 않았습니다",
        ))
    return out, skipped


def base_months(db: Session, lab_pk: str, version: str, ym: int, lab_wide: bool) -> dict[str, list[float]]:
    """급변·단위 검사가 비교할 전월 속보 값. lab_wide면 파트를 전부 더한다(scan_pks와 같은 이유)."""
    if not lab_wide:
        return months_by_category(db, lab_pk, version, ym)
    pks = scan_pks(db, lab_pk, True)
    out: dict[str, list[float]] = {}
    for pk in pks:
        for cat, months in months_by_category(db, pk, version, ym).items():
            acc = out.setdefault(cat, [])
            for i, v in enumerate(months):
                if i < len(acc):
                    acc[i] += float(v or 0)
                else:
                    acc.append(float(v or 0))
    return out


def scan(db: Session, body: AnomalyScanIn) -> AnomalyScanResult:
    """업로드 값(또는 이미 저장된 값)을 기준에 걸어 본다. 반영을 막지 않고 목록만 돌려준다.

    vals를 주면 그 값으로 검사하고(반영 대기 중인 파일), 안 주면 DB에 저장된 값으로 검사한다
    ([계정 확정] 전에 "아직 남아 있는 이상"을 세는 경로). 비교할 전실행(base_*)을 안 주면
    급변·단위(배수) 검사는 건너뛰고 그 사실을 skipped로 알린다 — 조용히 빠지면 "이상 없음"으로
    읽힌다."""
    check_lab_pk(db, body.lab_pk, allow_all=True)
    check_version(body.version)
    vals = body.vals
    if vals is None:
        vals = months_by_category(db, body.lab_pk, body.version, body.yyyymm)
    base_vals = None
    skipped: list[str] = []
    if body.base_yyyymm and body.base_version:
        check_version(body.base_version)
        base_vals = base_months(db, body.lab_pk, body.base_version, body.base_yyyymm, body.base_lab_wide)
        if not base_vals:
            base_vals = None
    if base_vals is None:
        skipped.append("급변·단위(배수) — 비교할 전월 속보 값이 없습니다")

    rules = anomaly.merged_rules(rule_rows(db), body.overrides)
    # 증감분석 세 열의 비교 상대. 못 구한 열은 그 이유를 skipped로 올린다 — 조용히 빼면
    # "증감분석 대조 이상 없음"으로 읽힌다(이 엔드포인트의 공통 약속).
    abases, abase_skipped = analysis_bases(db, body)
    skipped += [msg for rule_key, msg in abase_skipped if rules[rule_key]["enabled"]]
    findings = anomaly.detect(
        vals={k: list(v or []) for k, v in vals.items()},
        base_vals=base_vals,
        sums=body.sums,
        reasons=body.reasons,
        rules=rules,
        group_calc=GROUP_CALC,
        label_of=category_label,
        leaves_of=resolve_leaves,
        periods=body.periods,
        outs_rows=[r.model_dump() for r in body.outs_rows] if body.outs_rows else None,
        analysis=body.analysis,
        analysis_bases=abases,
        basis_month=body.basis_month,
        cols=body.cols,
        count_keys=COUNT_KEYS,
    )
    if not body.sums and rules["sum_mismatch"]["enabled"]:
        skipped.append("합계 불일치 — 파일에 소계·합계 행이 없습니다")
    # 열 목록은 업로드 파싱만 알 수 있다. 저장된 값으로 하는 검사([계정 확정] 전 재검사)는
    # 어떤 열로 들어온 자료인지 알 수 없으므로 이 검사를 건너뛴다 — "열 이상 없음"으로 읽히면 안 된다.
    if body.cols is None and rules["missing_column"]["enabled"]:
        skipped.append("열 누락 — 파일의 열 목록이 이 검사에 함께 오지 않았습니다")
    # 못 한 검사는 반드시 밝힌다 — 입력이 없어서 건너뛴 것과 "이상 없음"은 화면에서 구분이 안 된다.
    #
    # 문구는 **우리가 실제로 아는 것**까지만 적는다. 머리글은 있고 값만 빈 파일(연구소가 양식을
    # 그대로 돌려보낸 경우)에 "시트가 오지 않았습니다"라고 적으면, 담당자는 "양식이 잘못 왔다"로
    # 읽고 엉뚱한 재요청을 하게 된다. 이 엔드포인트가 아는 건 "이 검사에 쓸 값이 왔는가"뿐이다.
    if not body.periods and not body.outs_rows and rules["period_mismatch"]["enabled"]:
        skipped.append("기간 합 불일치 — 파일의 1Q~4Q·연간 칸에 값이 없습니다")
    # 증감분석 대조 세 기준 + 당월 빈칸. 쓰는 재료가 열마다 달라서 열 단위로 밝힌다.
    ANALYSIS_RULES = ("analysis_plan", "analysis_prev", "analysis_cur")
    if not body.analysis:
        # 화면은 비용 · 증감분석 · 외주용역을 각각 다른 카드로 검사한다 — 비용 카드에는 증감분석
        # 값이 실리지 않는다. 그걸 "시트가 안 왔다"로 적으면 같은 파일로 온 증감분석 시트를 두고도
        # 연구소에 다시 달라고 하게 된다.
        for key in ANALYSIS_RULES + ("analysis_missing",):
            if rules[key]["enabled"]:
                skipped.append(f"{rules[key]['label']} — 증감분석 값이 이 검사에 함께 오지 않았습니다")
    else:
        # 값은 왔는데 그 **열이** 통째로 비어 있는 경우 — 대조할 상대가 아니라 대조할 값이 없다.
        # 이걸 안 밝히면 "대조 이상 없음"으로 읽힌다.
        for key, field in zip(ANALYSIS_RULES, ("plan", "prev", "cur")):
            if not rules[key]["enabled"] or abases.get(field) is None:
                continue  # 비교 상대가 없는 건 위 abase_skipped가 이미 적었다
            if not any(
                ((blocks or {}).get(b) or {}).get(field) is not None
                for blocks in body.analysis.values()
                for b in ("y", "m")
            ):
                skipped.append(f"{rules[key]['label']} — 증감분석 시트의 그 열이 비어 있습니다")
    if body.analysis and not body.basis_month and rules["analysis_prev"]["enabled"]:
        # 당월 블록은 "기준월 한 칸"과 맞대므로 몇 월인지 모르면 검사할 수 없다(연간 블록만 본다).
        skipped.append("증감분석 당월 블록 대조 — 기준월(basis_month)이 오지 않았습니다")
    if base_vals is None and rules["new_category"]["enabled"]:
        skipped.append(f"{rules['new_category']['label']} — 비교할 전월 속보 값이 없습니다")
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["rule"]] = counts.get(f["rule"], 0) + 1
    return AnomalyScanResult(
        lab_pk=body.lab_pk,
        findings=[AnomalyFinding(**f) for f in findings],
        counts=counts,
        rules=[rule_out(k, rules[k]) for k in anomaly.RULE_KEYS],
        skipped=skipped,
    )
