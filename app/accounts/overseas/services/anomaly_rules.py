"""이상 감지 기준(anomaly rules) 관리 + 스캔 — 반영 전 "이거 맞나요?".

판정 규칙은 app/shared/core/anomaly.py, 기준값은 overseas_anomaly_rules에 있다. 여기는 그 둘을
붙이고 비교 대상(전실행)을 DB에서 꺼내 오는 일을 한다. 반영을 막지 않는다 — 알리기만 한다.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.overseas.cost_groups import GROUP_CALC, category_label, resolve_leaves
from app.shared.core import anomaly
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


def base_months(db: Session, lab_pk: str, version: str, ym: str, lab_wide: bool) -> dict[str, list[float]]:
    """비교할 전실행 값. lab_wide면 같은 연구소의 파트를 전부 더해서 돌려준다 — 증감사유·외주용역은
    연구소 단위 시트라 SARC를 CPU 하나와 비교하면 전부 불일치로 걸린다. 총계 행(part_code=TOTAL)은
    더하지 않는다 — 다른 파트를 더해 만든 값이라 넣으면 두 번 세어진다."""
    if not lab_wide:
        return months_by_category(db, lab_pk, version, ym)
    lab_code = next((lab.lab_code for lab in all_labs(db) if lab.pk == lab_pk), lab_pk)
    pks = [
        lab.pk for lab in all_labs(db)
        if lab.lab_code == lab_code and (lab.part_code or "").upper() != "TOTAL"
    ] or [lab_pk]
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
        skipped.append("급변·단위(배수) — 비교할 전실행 값이 없습니다")

    rules = anomaly.merged_rules(rule_rows(db), body.overrides)
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
        basis_month=body.basis_month,
    )
    if not body.sums and rules["sum_mismatch"]["enabled"]:
        skipped.append("합계 불일치 — 파일에 소계·합계 행이 없습니다")
    # 못 한 검사는 반드시 밝힌다 — 입력이 없어서 건너뛴 것과 "이상 없음"은 화면에서 구분이 안 된다.
    #
    # 문구는 **우리가 실제로 아는 것**까지만 적는다. 머리글은 있고 값만 빈 파일(연구소가 양식을
    # 그대로 돌려보낸 경우)에 "시트가 오지 않았습니다"라고 적으면, 담당자는 "양식이 잘못 왔다"로
    # 읽고 엉뚱한 재요청을 하게 된다. 이 엔드포인트가 아는 건 "이 검사에 쓸 값이 왔는가"뿐이다.
    if not body.periods and not body.outs_rows and rules["period_mismatch"]["enabled"]:
        skipped.append("기간 합 불일치 — 파일의 1Q~4Q·연간 칸에 값이 없습니다")
    if not body.analysis:
        # 화면은 비용 · 증감분석 · 외주용역을 각각 다른 카드로 검사한다 — 비용 카드에는 증감분석
        # 값이 실리지 않는다. 그걸 "시트가 안 왔다"로 적으면 같은 파일로 온 증감분석 시트를 두고도
        # 연구소에 다시 달라고 하게 된다.
        for key in ("analysis_prev", "analysis_missing"):
            if rules[key]["enabled"]:
                skipped.append(f"{rules[key]['label']} — 증감분석 값이 이 검사에 함께 오지 않았습니다")
    elif rules["analysis_prev"]["enabled"] and not any(
        ((blocks or {}).get("y") or {}).get("prev") is not None for blocks in body.analysis.values()
    ):
        # 값은 왔는데 전실행 열이 통째로 비어 있는 경우 — 대조할 상대가 없다.
        # 이걸 안 밝히면 "전월 대조 이상 없음"으로 읽힌다.
        skipped.append(f"{rules['analysis_prev']['label']} — 증감분석 시트의 전실행 열이 비어 있습니다")
    if base_vals is None and rules["new_category"]["enabled"]:
        skipped.append("신규 계정 — 비교할 전실행 값이 없습니다")
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
