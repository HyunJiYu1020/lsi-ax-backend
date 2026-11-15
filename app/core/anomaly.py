"""업로드 값의 이상 감지 — 반영 전에 "이거 맞나요?"를 띄우는 판정.

막는 게 아니라 알리는 기능이다(공통 RULE: 반영은 막지 않음). 연구소가 보낸 파일은 그 연구소가
확정해서 보낸 값이라, 본사가 "이상해 보인다"는 이유로 반영을 거부할 수는 없다 — 대신 반영 전에
눈에 띄게 해서 담당자가 연구소에 확인하거나 그대로 가져갈지 고르게 한다.

기준 4개:
- jump(급변)          전실행 대비 ±N% **그리고** 변동액 M 이상. 비율만 보면 금액이 작은 항목
                      (잡비 $1K → $2K = +100%)이 전부 걸려서 경고가 수십 개 뜨고, 그러면 아무도
                      안 읽는다. 그래서 두 조건을 다 넘을 때만 알린다.
- sum_mismatch(합계)   파일에 적혀 온 소계·합계 행 ≠ 그 하위 항목 합. 오차 허용값을 두는 건
                      연구소가 반올림해서 보내는 경우가 흔하기 때문이다.
- missing_reason(사유) 급변으로 걸린 항목에 증감사유가 비어 있음. 급변 판정에 종속이라, 급변 기준을
                      바꾸면 이 경고 수도 같이 바뀐다.
- unit_suspect(단위)   K$ 칸에 원 단위로 들어온 것으로 보이는 값(절대값이 너무 큼), 또는 전 버전
                      대비 1000배·1/1000 수준. 다른 계정 화면에도 같은 규칙이 있다(core.js:775).

기준값은 코드에 박지 않고 DB(overseas_anomaly_rules)에 둔다 — 챗봇에서 "50% 넘는것만 알려줘"로
바꾸면 저장되고, 창을 새로 열어도·다른 담당자가 봐도 같은 기준이 적용돼야 한다. 여기 DEFAULTS는
그 테이블이 비어 있을 때의 출발값이고, "기준 원래대로"가 되돌릴 목표값이다.
"""

from typing import Any

# 기준의 출발값. pct·amount·ratio 셋 중 그 기준이 쓰는 것만 채운다(안 쓰는 건 None).
# amount·절대값 단위는 모두 K$다 — 업로드 파일과 cost_entries가 K$로 들어온다.
DEFAULTS: dict[str, dict[str, Any]] = {
    "jump": {
        "label": "급변",
        "enabled": True,
        "pct": 10.0,  # 전실행 대비 ±10%
        "amount": 10.0,  # 그리고 변동액 $10K 이상
        "ratio": None,
        "desc": "전실행 대비 {pct}% 이상 그리고 변동액 {amount} 이상",
    },
    "sum_mismatch": {
        "label": "합계 불일치",
        "enabled": True,
        "pct": None,
        "amount": 0.5,  # 소계와 하위 합의 차이 허용값(반올림 흡수)
        "ratio": None,
        "desc": "파일의 소계·합계 행과 하위 항목 합의 차이가 {amount} 초과",
    },
    "missing_reason": {
        "label": "사유 누락",
        "enabled": True,
        "pct": None,
        "amount": None,
        "ratio": None,
        "desc": "급변으로 걸린 항목에 증감사유가 비어 있음",
    },
    "unit_suspect": {
        "label": "단위 의심",
        "enabled": True,
        "pct": None,
        "amount": 1_000_000.0,  # K$ 칸에 이 값 이상이면 원 단위 입력 의심
        "ratio": 1000.0,  # 또는 전 버전 대비 1000배 · 1/1000
        "desc": "K$ 칸에 {amount} 이상, 또는 전 버전 대비 {ratio}배·1/{ratio}",
    },
}

RULE_KEYS = tuple(DEFAULTS)
# 챗봇이 자연어를 이 키로 옮길 때 쓰는 별칭(도구 설명에도 같이 싣는다).
RULE_ALIASES = {
    "급변": "jump", "변동": "jump", "증감": "jump",
    "합계": "sum_mismatch", "합계불일치": "sum_mismatch", "소계": "sum_mismatch",
    "사유": "missing_reason", "사유누락": "missing_reason",
    "단위": "unit_suspect", "단위의심": "unit_suspect",
}


def merged_rules(rows: list[Any], overrides: dict[str, dict] | None = None) -> dict[str, dict]:
    """DB 행 + (이번 조회만 쓰는) 임시 override를 기본값 위에 얹어 최종 기준을 만든다.

    DB에 행이 없어도 동작해야 한다 — 기준 테이블이 비어 있다고 이상 감지가 꺼지면, 마이그레이션을
    덜 돌린 환경에서 "경고가 하나도 없다"가 정상처럼 보인다."""
    out = {k: dict(v) for k, v in DEFAULTS.items()}
    for r in rows or []:
        if r.rule_key not in out:
            continue
        out[r.rule_key].update(
            enabled=r.enabled,
            pct=None if r.pct is None else float(r.pct),
            amount=None if r.amount is None else float(r.amount),
            ratio=None if r.ratio is None else float(r.ratio),
        )
    for key, patch in (overrides or {}).items():
        if key in out and isinstance(patch, dict):
            out[key].update({k: v for k, v in patch.items() if k in ("enabled", "pct", "amount", "ratio")})
    return out


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _total(months: list[Any]) -> float:
    return sum(_num(x) for x in months or [])


def _k(v: float) -> str:
    """금액 표기(K$). 화면·챗봇이 그대로 읽어 쓰는 문장에 들어간다."""
    return f"${v:,.1f}K" if abs(v) < 10 else f"${v:,.0f}K"


def detect(
    *,
    vals: dict[str, list[Any]],
    base_vals: dict[str, list[Any]] | None,
    sums: dict[str, list[Any]] | None,
    reasons: dict[str, str] | None,
    rules: dict[str, dict],
    group_calc: dict[str, tuple[str, ...]],
    label_of,
    leaves_of,
) -> list[dict]:
    """findings 목록. 각 건은 화면이 그대로 띄울 수 있는 문장(message)과 칸 표시용 좌표를 갖는다.

    category_key·month로 어느 칸을 빨갛게 칠할지 정한다(month=None이면 그 행 전체).
    group_calc·label_of·leaves_of는 계정 정의(app/core/cost_groups)를 주입받는다 — 이 모듈이
    계정 트리를 따로 들고 있으면 계정이 늘 때 두 곳이 어긋난다."""
    found: list[dict] = []
    base_vals = base_vals or {}
    sums = sums or {}
    reasons = reasons or {}

    def add(rule: str, cat: str, month: int | None, msg: str, **extra: Any) -> None:
        found.append(
            {
                "rule": rule,
                "rule_label": rules[rule]["label"],
                "category_key": cat,
                "category_label": label_of(cat),
                "month": month,
                "message": msg,
                **extra,
            }
        )

    jump = rules["jump"]
    jumped: set[str] = set()
    if jump["enabled"] and base_vals:
        pct_limit, amt_limit = _num(jump["pct"]), _num(jump["amount"])
        for cat, months in vals.items():
            if cat in group_calc:  # 소계·합계 행은 하위 항목이 이미 걸리므로 중복으로 안 올린다
                continue
            cur, base = _total(months), _total(base_vals.get(cat))
            diff = cur - base
            if abs(diff) < amt_limit:
                continue
            if base == 0:
                # 전실행에 없던 항목이 새로 생긴 것 — 비율을 낼 수 없어서 금액만으로 알린다.
                jumped.add(cat)
                add("jump", cat, None, f"전실행에 없던 항목에 {_k(cur)}가 올라왔습니다 (신규)", value=cur, base=base)
                continue
            rate = diff / abs(base) * 100
            if abs(rate) >= pct_limit:
                jumped.add(cat)
                # 어느 달이 변동을 끌었는지 같이 집어준다 — 그 칸부터 보게 하려는 것이다.
                per = [(abs(_num(m) - _num((base_vals.get(cat) or [None] * 12)[i] if i < len(base_vals.get(cat) or []) else 0)), i) for i, m in enumerate(months or [])]
                top = max(per)[1] + 1 if per else None
                add(
                    "jump", cat, top,
                    f"전실행 대비 {rate:+.1f}% ({_k(base)} → {_k(cur)}, {_k(diff)})",
                    value=cur, base=base, rate=round(rate, 1),
                )

    sm = rules["sum_mismatch"]
    if sm["enabled"] and sums:
        tol = _num(sm["amount"])
        for cat, months in sums.items():
            if cat not in group_calc:
                continue
            reported = _total(months)
            computed = sum(_total(vals.get(leaf)) for leaf in leaves_of(cat))
            gap = reported - computed
            if abs(gap) > tol:
                add(
                    "sum_mismatch", cat, None,
                    f"파일의 합계 {_k(reported)} ≠ 하위 항목 합 {_k(computed)} (차이 {_k(gap)})",
                    value=reported, base=computed,
                )

    if rules["missing_reason"]["enabled"]:
        for cat in sorted(jumped):
            if not str(reasons.get(cat) or "").strip():
                add("missing_reason", cat, None, "급변으로 걸렸는데 증감사유가 비어 있습니다")

    us = rules["unit_suspect"]
    if us["enabled"]:
        big, ratio_limit = _num(us["amount"]), _num(us["ratio"])
        for cat, months in vals.items():
            if cat in group_calc:
                continue
            for i, m in enumerate(months or []):
                v = _num(m)
                if big and abs(v) >= big:
                    add("unit_suspect", cat, i + 1, f"{i + 1}월 값이 {_k(v)}입니다. 원 단위로 올린 건 아닌지 확인하세요", value=v)
                    break
                b = _num((base_vals.get(cat) or [])[i]) if i < len(base_vals.get(cat) or []) else 0.0
                if ratio_limit and b and v and (abs(v / b) >= ratio_limit or abs(b / v) >= ratio_limit):
                    add(
                        "unit_suspect", cat, i + 1,
                        f"{i + 1}월 값이 전 버전({_k(b)})의 {abs(v / b):,.0f}배입니다. 단위를 확인하세요",
                        value=v, base=b,
                    )
                    break

    # 심각도는 두지 않는다 — 전부 "확인해 달라"는 같은 무게의 알림이고, 등급을 매기면 낮은 등급은
    # 아무도 안 본다. 대신 기준별로 묶어서 보여준다(화면이 rule로 그룹핑한다).
    return found
