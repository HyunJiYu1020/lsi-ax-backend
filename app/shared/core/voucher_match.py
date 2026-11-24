"""ERP 전표 한 줄을 "어느 연구소 것인가"로 판정하는 규칙 — 해외연구소 실적결산 2단계(가공)의 AI 매칭.

왜 규칙으로 쓰는가: 전표에 연구소가 적혀 오지 않는다. 해외연구소 비용은 **과제코드 단위**로 떨어져서
(SARC가 쓴 돈도 DSRA가 쓴 돈도 같은 과제에 붙는다) 전표만 보고는 연구소를 알 수 없다. 단서는 셋이다 —
거래처, 적요, 코스트센터. 세 단서가 가리키는 곳이 같으면 거의 확실하고, 하나만 맞으면 사람이 봐야
한다. 그래서 판정과 함께 **신뢰도와 근거**를 돌려준다. 근거 없는 추천은 담당자가 검증할 수 없어서
결국 전표를 하나하나 다시 보게 된다.

자동 확정하지 않는다: 이 모듈은 추천만 하고, 반영은 화면에서 담당자가 [저장]할 때 일어난다. 신뢰도
'high'도 틀릴 수 있고(같은 거래처가 두 연구소에 납품한다) 틀린 매핑은 연구소 실적을 둘 다 틀리게
만든다.

제외·CP 규칙만 예외로 단정한다: NRE 공용 부서코드는 여러 연구소가 같이 쓰는 코드라 전표의 부서로는
가를 수 없고, 적요의 'CP' 표시만이 유일한 단서다. 적요에 CP가 있으면 CP(미청구 NRE) 연구소 몫이고,
없으면 해외연구소 실적이 아니다(국내가 부담한 공용 비용). 이건 업무 규칙이라 AI 추측이 아니다 —
그래서 신뢰도 'high'로 내고 근거에 규칙 이름을 적는다.
"""

import re

# 신뢰도 단계. 화면은 high를 'AI 자동', mid·low를 '확인 필요'로 가른다 — mid를 자동으로 넘기면
# "거래처만 맞는" 전표가 조용히 틀린 연구소에 붙는다.
HIGH, MID, LOW = "high", "mid", "low"

# 적요에서 CP(미청구 NRE)를 가리키는 표시. 낱말 경계를 보는 이유: 'CPU'·'CAPEX'에 걸리면 SARC:CPU
# 전표가 전부 CP 연구소로 간다.
CP_RX = re.compile(r"(^|[^A-Za-z])CP([^A-Za-z]|$)")

# 단서 하나하나의 점수. 합이 아니라 "몇 개가 같은 곳을 가리키나"로 신뢰도를 가른다.
W_PROJECT = 4  # 과제코드가 한 연구소에만 쓰이는 코드면 가장 강하다
W_VENDOR = 3  # 거래처는 보통 연구소 하나와만 거래한다
W_CENTER = 3  # 코스트센터가 연구소 전용 코드면 확실하다(공용 코드는 아래 shared 분기로 빠진다)
W_NAME = 3  # 적요에 연구소 이름이 그대로 적힌 전표
W_TOKEN = 1  # 적요 낱말이 연구소 이름·과제명과 겹치는 정도

# 이 점수 이상이면 high. 단서 두 개(거래처 3 + 적요 1, 과제 4)부터 자동으로 본다.
HIGH_SCORE = 4
MID_SCORE = 3

# 반복 수정을 규칙으로 물어볼 최소 횟수. 2로 두면 "그 달만 예외로 옮긴 전표"까지 규칙 후보가 되므로
# 3번째 같은 수정에서 묻는다 — 두 번은 우연일 수 있다.
RULE_MIN_HITS = 3


def normalize(value: str) -> str:
    """비교용 정규화 — 공백·구두점을 떼고 대문자로. 전표는 같은 거래처를 `(주)한빛상사`와
    `한빛상사(주)`로 섞어 보낸다."""
    return re.sub(r"[\s()\[\]·.,&\-_/'’]", "", str(value or "")).upper()


def _tokens(value: str) -> set[str]:
    """두 글자 이상 낱말만. 한 글자는 겹치기 쉬워서 근거가 못 된다."""
    return {t for t in re.split(r"[^0-9A-Za-z가-힣]+", str(value or "").upper()) if len(t) > 1}


def has_cp_mark(text: str) -> bool:
    """적요에 CP(미청구 NRE) 표시가 있는지. 공용 부서코드 전표를 CP 연구소와 제외로 가르는 유일한 단서."""
    return bool(CP_RX.search(str(text or "").upper()))


# 규칙의 match_field → 전표의 어느 칸을 보는지.
RULE_FIELDS = {
    "vendor": "vendor",
    "text": "text",
    "cost_center": "cost_center",
    "project": "project_code",
    "assignment": "assignment",
}
# match_op를 안 적은 규칙(이 컬럼이 생기기 전에 만든 것)이 쓰던 방식. 적요는 같은 내용을 매달 조금씩
# 다르게 적어 보내서 '포함'이고, 나머지는 코드·이름이라 '같음'이다. 일괄 백필하지 않고 여기서
# 해석하는 이유는 close_match_rules.match_op 주석에 적어 뒀다.
RULE_OP_DEFAULT = {"text": "contains"}


def rule_matches(voucher: dict, rule: dict) -> bool:
    """규칙 한 건이 이 전표에 걸리는지. match_op가 비어 있으면 필드별 기본 방식으로 본다."""
    field = rule.get("match_field")
    want = normalize(rule.get("match_value"))
    column = RULE_FIELDS.get(field)
    if not want or not column:
        return False
    got = normalize(voucher.get(column))
    if not got:
        return False
    op = rule.get("match_op") or RULE_OP_DEFAULT.get(field, "equals")
    if op == "contains":
        return want in got
    if op == "starts_with":
        return got.startswith(want)
    return got == want


def match_rule(voucher: dict, rules: list[dict]) -> dict | None:
    """담당자가 승인한 규칙(close_match_rules) 중 이 전표에 걸리는 것. 먼저 걸린 규칙이 이긴다.

    규칙은 AI 추천을 **이긴다** — 사람이 "앞으로 이렇게"라고 답한 것이라서, 기계가 다시 뒤집으면
    같은 수정을 매달 반복하게 된다.

    rejected=True인 행은 건너뛴다 — 그건 담당자가 제안에 [닫기]를 누른 기록이라 "규칙으로 쓰지
    말라"는 뜻이다. 같은 제안을 다시 묻지 않으려고 같은 테이블에 남기는 것이라, 매칭에서 빼지
    않으면 거절한 규칙이 그대로 적용돼 버린다."""
    for rule in rules:
        if rule.get("rejected"):
            continue
        if rule_matches(voucher, rule):
            return rule
    return None


def suggest_lab(voucher: dict, labs: list[dict], hints: dict | None = None) -> dict:
    """전표 한 줄 → 연구소 추천 한 건. `{scope_key, confidence, reason, excluded, exclude_reason}`.

    labs의 각 항목은 `{pk, lab_code, name}`이고, hints는 과거 매핑에서 뽑은 단서다:
    `{"vendor": {정규화된 거래처: pk}, "cost_center": {...}, "project": {...},
      "shared_centers": {공용 부서코드, ...}}`. hints가 비어 있으면(첫 달) 적요·이름 단서만 쓴다.

    hints를 과거 매핑에서 뽑는 이유: 거래처 ↔ 연구소 대응표를 따로 관리하게 하면 기준정보가 하나 더
    늘고, 그 표를 아무도 갱신하지 않는다. 이미 담당자가 지난달에 매핑한 결과가 가장 정확한 대응표다.
    """
    hints = hints or {}
    shared = {normalize(c) for c in (hints.get("shared_centers") or set())}
    center = normalize(voucher.get("cost_center"))

    # ── 공용(NRE) 부서코드: 적요의 CP 표시만으로 가른다. 업무 규칙이라 추측이 아니다.
    if center and center in shared:
        cp = next((lab for lab in labs if lab.get("lab_code") == "CP"), None)
        if has_cp_mark(voucher.get("text")):
            if cp:
                return {
                    "scope_key": cp["pk"],
                    "confidence": HIGH,
                    "reason": f"NRE 공용 부서코드({voucher.get('cost_center')}) · 적요에 CP 있음 → CP(미청구 NRE)",
                    "excluded": False,
                    "exclude_reason": "",
                }
            # CP 연구소가 기준정보에 없으면(사용 중지 등) 사람이 정해야 한다 — 임의로 다른 연구소에 붙이지 않는다.
            return {
                "scope_key": "",
                "confidence": LOW,
                "reason": "적요에 CP가 있지만 CP 연구소가 기준정보에 없다 — 담당자가 지정해야 한다",
                "excluded": False,
                "exclude_reason": "",
            }
        return {
            "scope_key": "",
            "confidence": HIGH,
            "reason": f"NRE 공용 부서코드({voucher.get('cost_center')}) · 적요에 CP 없음",
            "excluded": True,
            "exclude_reason": "NRE 공용 · 적요 CP 없음 → 해외연구소 실적 제외",
        }

    vendor = normalize(voucher.get("vendor"))
    project = normalize(voucher.get("project_code"))
    text_tokens = _tokens(voucher.get("text"))
    by_vendor = hints.get("vendor") or {}
    by_center = hints.get("cost_center") or {}
    by_project = hints.get("project") or {}

    scored: list[tuple[int, list[str], dict]] = []
    for lab in labs:
        score, why = 0, []
        if project and by_project.get(project) == lab["pk"]:
            score += W_PROJECT
            why.append(f"과제코드 {voucher.get('project_code')} 이력 일치")
        if vendor and by_vendor.get(vendor) == lab["pk"]:
            score += W_VENDOR
            why.append(f"거래처 {voucher.get('vendor')} 이력 일치")
        if center and by_center.get(center) == lab["pk"]:
            score += W_CENTER
            why.append(f"코스트센터 {voucher.get('cost_center')} 이력 일치")
        # 연구소 이름이 적요에 그대로 있는 전표 — 이름이 곧 답이다(`SARC 9월 임차료`).
        # 이름이 걸리면 낱말 겹침은 더하지 않는다: 둘은 **같은 근거**(적요에 이름이 있다)라서, 같이
        # 세면 단서 하나짜리 전표가 "두 단서가 일치"로 올라가 자동 확정된다.
        names = {normalize(lab.get("lab_code")), normalize(lab.get("name"))} - {""}
        if any(n and n in normalize(voucher.get("text")) for n in names):
            score += W_NAME
            why.append(f"적요에 연구소명({lab.get('lab_code')}) 있음")
        elif overlap := text_tokens & _tokens(lab.get("name")):
            score += W_TOKEN
            why.append("적요 낱말 일치: " + ", ".join(sorted(overlap)))
        if score:
            scored.append((score, why, lab))

    if not scored:
        return {
            "scope_key": "",
            "confidence": LOW,
            "reason": "거래처 · 적요 · 코스트센터로 연구소를 찾지 못했다",
            "excluded": False,
            "exclude_reason": "",
        }

    scored.sort(key=lambda x: (-x[0], x[2]["pk"]))
    top_score, why, lab = scored[0]
    # 2위도 쓸 만한 근거를 갖고 있으면(1위의 절반 이상) 단서가 **서로 다른 연구소를 가리키는** 것이다 —
    # 점수가 높은 쪽을 자동으로 고르면 모순을 아무도 못 본 채 반은 틀린다. 점수 차가 크면(2위가 적요
    # 낱말 하나만 걸친 정도) 모순이 아니라 잡음이라 그냥 1위로 간다.
    if len(scored) > 1 and scored[1][0] * 2 >= top_score:
        others = ", ".join(x[2].get("lab_code") or x[2]["pk"] for x in scored[:2])
        return {
            "scope_key": "",
            "confidence": LOW,
            "reason": f"단서가 {others}로 갈린다 — 담당자가 골라야 한다",
            "excluded": False,
            "exclude_reason": "",
        }
    confidence = HIGH if top_score >= HIGH_SCORE else MID if top_score >= MID_SCORE else LOW
    return {
        "scope_key": lab["pk"],
        "confidence": confidence,
        "reason": " · ".join(why),
        "excluded": False,
        "exclude_reason": "",
    }


def suggest_rules(corrections: list[dict], existing: list[dict]) -> list[dict]:
    """반복된 담당자 수정 → 규칙 후보. "앞으로 이렇게 할까요?"로 물어볼 것만 돌려준다.

    corrections는 담당자가 직접 고른 매핑들(`{vendor, cost_center, project_code, scope_key, excluded}`)이고
    existing은 이미 등록된 규칙이다. 같은 단서가 같은 곳으로 RULE_MIN_HITS번 이상 갔고, 그 단서가
    **다른 곳으로 간 적이 없을 때만** 후보로 낸다 — 거래처 하나가 두 연구소에 걸리는 경우를 규칙으로
    만들면 다음 달에 절반이 틀린 채 자동 적용된다.

    과제코드를 거래처보다 먼저 보는 이유: 과제는 한 연구소에 속하는 게 보통이라 규칙이 안전하고,
    거래처는 여러 연구소에 납품할 수 있다.
    """
    have = {(r.get("match_field"), normalize(r.get("match_value"))) for r in existing}
    buckets: dict[tuple[str, str], dict[str, int]] = {}
    labels: dict[tuple[str, str], str] = {}
    for c in corrections:
        target = "_EXCL" if c.get("excluded") else (c.get("scope_key") or "")
        if not target:
            continue  # 미매핑으로 되돌린 수정은 규칙이 될 수 없다
        for field, raw in (
            ("project", c.get("project_code")),
            ("vendor", c.get("vendor")),
            ("cost_center", c.get("cost_center")),
        ):
            value = normalize(raw)
            if not value:
                continue
            key = (field, value)
            labels.setdefault(key, str(raw))
            buckets.setdefault(key, {})
            buckets[key][target] = buckets[key].get(target, 0) + 1

    out = []
    for (field, value), targets in buckets.items():
        if (field, value) in have:
            continue
        if len(targets) > 1:
            continue  # 같은 단서가 여러 곳으로 갔다 — 규칙으로 만들면 안 된다
        target, hits = next(iter(targets.items()))
        if hits < RULE_MIN_HITS:
            continue
        out.append(
            {
                "match_field": field,
                "match_value": labels[(field, value)],
                "scope_key": "" if target == "_EXCL" else target,
                "excluded": target == "_EXCL",
                "hit_count": hits,
            }
        )
    # 많이 반복된 수정부터 묻는다 — 한 번에 다 묻지 않고 위에서부터 처리하게.
    return sorted(out, key=lambda x: (-x["hit_count"], x["match_field"], x["match_value"]))
