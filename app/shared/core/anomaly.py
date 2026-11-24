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
                      바꾸면 이 경고 수도 같이 바뀐다. 표시 자리는 **급변이 찍은 그 칸**이다.
- unit_suspect(단위)   금액 칸에 단위를 틀려 적은 것으로 보이는 값(절대값이 너무 큼), 또는 전 버전
                      대비 1000배·1/1000 수준. 다른 계정 화면에도 같은 규칙이 있다(core.js:775).
                      연구소가 보내는 양식은 K$이고 저장은 $다(업로드가 ×1000). 그래서 K$ 칸에
                      $ 원본이나 원 단위를 적어 보내면 정확히 1000배로 들어온다 — 그 사고를 반영
                      전에 잡는 기준이다. 기준값은 $ 단위로 적는다(저장 단위와 같게).
- missing_cell(빈칸)   연구소가 숫자를 안 적어 보낸 칸(월 1~12 · 1Q~4Q · 연간 전부). **0과 다른
                      상태다** — 0은 "그 달에 안 썼다"는
                      보고이고 빈칸은 "아직 안 냈다"다. 그래서 업로드 파싱이 빈칸을 null로 들고 오고
                      (core.js ovParse) 이 기준이 그 null을 찾는다. 금액 기준이 없다(있고 없고의 문제).
- period_mismatch(기간) 월 1~12 합 ≠ 파일에 적혀 온 1Q~4Q·연간 칸. 연구소가 월 숫자만 고치고 분기·연간
                      칸을 안 고쳐 보내는 사고가 흔한데, 그러면 어느 쪽이 맞는지 본사가 알 수 없다.
                      비용 시트와 외주용역 목록(업체 행)에 같이 적용한다.
- new_category(전실행에 없던 값)
                      전실행에 값이 전혀 없던 계정에 값이 올라왔다. jump의 "신규"와 다른 점은 금액
                      하한이 없다는 것 — 금액이 작아도 쓰기 시작한 계정은 담당자가 알아야 한다.
                      **"신규 계정"이 아니다**: 계정 매칭은 공통코드(OVERSEAS_CATEGORY, 사용 중)와
                      하고, 거기 없는 이름으로 온 행이 신규다(화면 ovRowKey · row_alias). 이 기준은
                      계정은 이미 있고 값이 새로 생긴 경우라, 행 전체가 아니라 연간(합계) 칸에만
                      표시한다 — 행을 통째로 칠하면 "새 행 39개"처럼 읽힌다.

**행 전체 표시(month=None)는 "행이 통째로 새것"일 때만 쓴다** — jump의 신규 케이스뿐이다. 칸을 짚을 수 있는 지적까지 행으로 그리면 어디를 고쳐야 하는지가 가려진다(화면은 month이
없는 지적을 행 테두리로 그린다 · core.js ovModFlags). 빈 양식을 올렸을 때 사유 누락 29건이 행을
통째로 칠해서 "신규 행 29개"처럼 보인 적이 있다 — 그래서 missing_reason은 급변이 찍은 칸에,
sum_mismatch는 연간 칸에 붙인다.
- analysis_prev(증감-전월) 증감분석 시트의 전실행 열 값 ≠ 본사가 들고 있는 전실행 합계. 연구소가 전월
                      파일을 보고 옮겨 적는 칸이라, 어긋나면 비교 기준 자체가 다른 것이다.
- analysis_missing(증감-당월) 증감분석 당월 블록의 빈칸. 연간 블록은 비어 있어도 연간 합으로 메울 수
                      있지만 당월은 그 달 보고의 본문이라 비면 읽을 게 없다.
- missing_column(열 누락) 양식의 기간 열(1~12월 · 1Q~4Q · 연간) 중 파일에 아예 **없는 열**. 값이 빈
                      것(missing_cell)과 다른 사건이다: 열이 없으면 그 달은 모든 계정이 한꺼번에
                      비어서 빈칸 경고가 계정 수만큼 쏟아지는데, 정작 원인("양식에서 그 열이 빠졌다")은
                      어디에도 안 적힌다. 그래서 열 단위로 한 줄 올리고, 없는 열의 칸은 빈칸 검사에서
                      뺀다(같은 사건을 두 번 세지 않는다).
                      양식 밖의 행·열(연구소가 더 보낸 것)은 이 검사의 대상이 아니다 — 그건 그대로
                      그려서 저장하면 되는 자료이고, 있어야 할 것이 없는 상태가 아니다.

월 번호 약속: 1~12는 달, **13~16은 1Q~4Q, 17은 연간**이다. 화면 표가 17칸(월 12 + 분기 4 + 연간)으로
그려지므로(core.js ov17) 그 위치를 그대로 쓴다 — 분기 칸을 표시하려면 month=13..16으로 보낸다.

기준값은 코드에 박지 않고 DB(overseas_anomaly_rules)에 둔다 — 챗봇에서 "50% 넘는것만 알려줘"로
바꾸면 저장되고, 창을 새로 열어도·다른 담당자가 봐도 같은 기준이 적용돼야 한다. 여기 DEFAULTS는
그 테이블이 비어 있을 때의 출발값이고, "기준 원래대로"가 되돌릴 목표값이다.
"""

from typing import Any

# 기준의 출발값. pct·amount·ratio 셋 중 그 기준이 쓰는 것만 채운다(안 쓰는 건 None).
# amount·절대값 단위는 모두 $다 — cost_entries가 $로 저장되고 이 검사도
# 저장 단위로 들어온 값을 본다.
DEFAULTS: dict[str, dict[str, Any]] = {
    "jump": {
        "label": "급변",
        "enabled": True,
        "pct": 10.0,  # 전실행 대비 ±10%
        "amount": 10_000.0,  # 그리고 변동액 $10,000(=10K$) 이상
        "ratio": None,
        "desc": "전실행 대비 {pct}% 이상 그리고 변동액 {amount} 이상",
    },
    "sum_mismatch": {
        "label": "합계 불일치",
        "enabled": True,
        "pct": None,
        "amount": 500.0,  # 소계와 하위 합의 차이 허용값($ — 0.5K$, 반올림 흡수)
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
        # 한 칸(연구소×계정×월)에 이 금액 이상이면 단위를 틀려 적은 것으로 본다. 실측 최대가
        # 1,000K$라서 그 20배인 20,000K$(=$20,000,000)에 걸어 둔다 — 연구소가 커지거나 CP 정산처럼
        # 큰 금액이 들어와도 오경보가 안 나고, K$ 칸에 $ 원본을 적어 보낸 1000배 사고는 걸린다.
        "amount": 20_000_000.0,
        "ratio": 1000.0,  # 또는 전 버전 대비 1000배 · 1/1000
        "desc": "한 칸에 {amount} 이상, 또는 전 버전 대비 {ratio}배·1/{ratio}",
    },
    "missing_cell": {
        "label": "빈칸",
        "enabled": True,
        "pct": None,
        "amount": None,
        "ratio": None,
        "desc": "연구소가 숫자를 적지 않은 칸(0이 아니라 미제출)",
    },
    "period_mismatch": {
        "label": "기간 합 불일치",
        "enabled": True,
        "pct": None,
        "amount": 500.0,  # 월 합과 분기·연간 칸의 차이 허용값($ — 0.5K$, 반올림 흡수)
        "ratio": None,
        "desc": "월 1~12 합과 파일의 1Q~4Q·연간 칸 차이가 {amount} 초과",
    },
    "new_category": {
        # "신규 계정"이 아니다 — 계정 매칭은 공통코드와 하고(사용 중 목록), 거기 없는 이름으로 온
        # 행이 신규다. 이 기준은 "계정은 있는데 전실행엔 값이 없던" 경우라 이름을 그대로 적는다.
        "label": "전실행에 없던 값",
        "enabled": True,
        "pct": None,
        "amount": None,
        "ratio": None,
        "desc": "전실행에 값이 없던 계정에 값이 올라옴(금액 하한 없음)",
    },
    "analysis_prev": {
        "label": "증감분석 전월 대조",
        "enabled": True,
        "pct": None,
        # 소계·기간 합과 같은 "반올림 흡수" 허용값이다($ — 0.5K$). 연구소는 K$를 소수 한 자리까지
        # 적어 보내고 우리 합계는 $ 원본이라, 같은 값을 옮겨 적어도 수십~수백 달러가 남는다.
        # 여기에 0.5($)를 걸어 뒀던 동안은 그 반올림까지 전부 "전실행이 다르다"로 걸려서, 제대로 적어
        # 보낸 파일도 계정마다 경고가 떴다(sum_mismatch · period_mismatch와 같은 500.0으로 맞춘다).
        "amount": 500.0,
        "ratio": None,
        "desc": "증감분석의 전실행 열과 본사 전실행 합계 차이가 {amount} 초과",
    },
    "analysis_missing": {
        "label": "증감분석 당월 빈칸",
        "enabled": True,
        "pct": None,
        "amount": None,
        "ratio": None,
        "desc": "증감분석 당월 블록(PLAN·전실행·당월)에 빈칸이 있음",
    },
    "missing_column": {
        "label": "열 누락",
        "enabled": True,
        "pct": None,
        "amount": None,
        "ratio": None,
        "desc": "양식의 기간 열(1~12월 · 1Q~4Q · 연간) 중 파일에 없는 열",
    },
}

# 월 번호로 쓰는 분기·연간 칸 위치(화면 17칸 표와 같은 순서). 13=1Q … 16=4Q, 17=연간.
PERIOD_MONTHS = (13, 14, 15, 16, 17)
PERIOD_LABELS = ("1Q", "2Q", "3Q", "4Q", "연간")

# 양식이 반드시 갖는 기간 열 — 열 자리 키 → (월 번호, 사람이 읽는 이름).
# 키는 프런트(core.js ovParse의 hdr)와 overseas_cost_headers.col_key가 쓰는 것과 같은 규칙이다
# ("m1".."m12" 월 · "q1".."q4" 분기 · "y" 연간). 한쪽만 바꾸면 열 누락 검사가 조용히 틀어지므로
# test_missing_column_crosscheck가 프런트 파싱 결과로 이 키들을 맞춰 본다.
FORM_COLUMNS: tuple[tuple[str, int, str], ...] = tuple(
    [(f"m{m}", m, f"{m}월") for m in range(1, 13)]
    + [(f"q{q}", 12 + q, f"{q}Q") for q in range(1, 5)]
    + [("y", 17, "연간")]
)

RULE_KEYS = tuple(DEFAULTS)
# 챗봇이 자연어를 이 키로 옮길 때 쓰는 별칭(도구 설명에도 같이 싣는다).
RULE_ALIASES = {
    "급변": "jump", "변동": "jump", "증감": "jump",
    "합계": "sum_mismatch", "합계불일치": "sum_mismatch", "소계": "sum_mismatch",
    "사유": "missing_reason", "사유누락": "missing_reason",
    "단위": "unit_suspect", "단위의심": "unit_suspect",
    "빈칸": "missing_cell", "미제출": "missing_cell", "널": "missing_cell",
    "기간": "period_mismatch", "분기": "period_mismatch", "연간": "period_mismatch",
    "신규": "new_category", "신규계정": "new_category", "새계정": "new_category",
    "증감전월": "analysis_prev", "전월대조": "analysis_prev",
    "증감당월": "analysis_missing", "당월빈칸": "analysis_missing",
    "열": "missing_column", "열누락": "missing_column", "컬럼": "missing_column",
}


def merged_rules(rows: list[Any], overrides: dict[str, dict] | None = None) -> dict[str, dict]:
    """DB 행 + (이번 조회만 쓰는) 임시 override를 기본값 위에 얹어 최종 기준을 만든다.

    DB에 행이 없어도 동작해야 한다 — 기준 테이블이 비어 있다고 이상 감지가 꺼지면, 마이그레이션을
    덜 돌린 환경에서 "경고가 하나도 없다"가 정상처럼 보인다."""
    out = {k: dict(v) for k, v in DEFAULTS.items()}
    for r in rows or []:
        if r.rule_key not in out:
            continue
        # NULL 컬럼은 "그 값은 안 건드렸다"는 뜻이라 기본값을 그대로 둔다 — 덮어쓰면 비율만 바꾼
        # 사용자가 금액 하한을 잃는다("50% 넘는것만 알려줘"가 변동액 조건까지 지워버린다).
        # 저장은 보낸 필드만 쓰는 부분 갱신이므로(upsert_anomaly_rule), 안 보낸 필드는 NULL로
        # 남는다. 그래서 NULL을 "미설정"으로 읽어야 저장 규칙과 읽기 규칙이 맞는다.
        # (기본값을 되돌리는 건 reset=true가 맡는다.)
        patch = {"enabled": r.enabled}
        for field in ("pct", "amount", "ratio"):
            v = getattr(r, field)
            if v is not None:
                patch[field] = float(v)
        out[r.rule_key].update(patch)
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


def _months_txt(cells: list[int]) -> str:
    """칸 번호를 사람이 읽는 이름으로. 1~12는 "3월", 13~16은 "1Q", 17은 "연간"."""
    names = [PERIOD_LABELS[c - 13] if c >= 13 else f"{c}월" for c in cells]
    return " · ".join(names[:6]) + (f" 외 {len(names) - 6}칸" if len(names) > 6 else "")


def _k(v: float) -> str:
    """금액 표기. 저장값은 $이고 사람이 읽는 화면·챗봇 문장은 K$라서 여기서 1000으로 나눠 적는다
    (화면 표시 단위와 같게 — 경고 문장의 숫자가 표의 숫자와 달라 보이면 확인할 수가 없다)."""
    k = v / 1000
    return f"${k:,.1f}K" if abs(k) < 10 else f"${k:,.0f}K"


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
    periods: dict[str, list[Any]] | None = None,
    outs_rows: list[dict] | None = None,
    analysis: dict[str, dict[str, dict[str, Any]]] | None = None,
    basis_month: int | None = None,
    cols: list[str] | None = None,
) -> list[dict]:
    """findings 목록. 각 건은 화면이 그대로 띄울 수 있는 문장(message)과 칸 표시용 좌표를 갖는다.

    category_key·month로 어느 칸을 빨갛게 칠할지 정한다(month=None이면 그 행 전체).
    group_calc·label_of·leaves_of는 계정 정의(app/core/cost_groups)를 주입받는다 — 이 모듈이
    계정 트리를 따로 들고 있으면 계정이 늘 때 두 곳이 어긋난다.

    cols는 **파일에 실제로 있던 기간 열**의 자리 키 목록이다("m1".."m12" · "q1".."q4" · "y").
    열 누락 검사가 이걸 양식(FORM_COLUMNS)과 대조한다. 안 주면 그 검사를 건너뛴다 — 열 목록을
    모르는 호출(이미 저장된 값으로 하는 검사)에서 "열이 다 없다"고 쏟아지면 안 된다."""
    found: list[dict] = []
    base_vals = base_vals or {}
    sums = sums or {}
    reasons = reasons or {}

    def add(rule: str, cat: str, month: int | None, msg: str, **extra: Any) -> None:
        # months: 한 건으로 묶되 표시할 칸이 여럿일 때 쓴다(빈칸 36계정 × 12달을 건별로 올리면
        # 목록이 수백 줄이 되어 아무도 안 읽는다 — 계정당 한 줄, 칸은 전부 표시).
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

    def cells_of(months: list[Any]) -> list[int]:
        """값이 비어 있는(None) 칸 번호 목록. 0은 보고된 값이라 세지 않는다."""
        return [i + 1 for i, v in enumerate(months or []) if v is None]

    jump = rules["jump"]
    # cat -> 급변을 찍은 칸(신규 케이스는 None = 행 전체). missing_reason이 같은 자리에 붙는다.
    jumped: dict[str, int | None] = {}
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
                jumped[cat] = None
                add("jump", cat, None, f"전실행에 없던 항목에 {_k(cur)}가 올라왔습니다 (신규)", value=cur, base=base)
                continue
            rate = diff / abs(base) * 100
            if abs(rate) >= pct_limit:
                # 어느 달이 변동을 끌었는지 같이 집어준다 — 그 칸부터 보게 하려는 것이다.
                per = [(abs(_num(m) - _num((base_vals.get(cat) or [None] * 12)[i] if i < len(base_vals.get(cat) or []) else 0)), i) for i, m in enumerate(months or [])]
                top = max(per)[1] + 1 if per else None
                jumped[cat] = top
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
                    # 비교한 값이 12개월 합이라 연간 칸(17)을 가리킨다 — 행 전체 표시는 신규 행 몫이다.
                    "sum_mismatch", cat, PERIOD_MONTHS[-1],
                    f"파일의 합계 {_k(reported)} ≠ 하위 항목 합 {_k(computed)} (차이 {_k(gap)})",
                    value=reported, base=computed,
                )

    if rules["missing_reason"]["enabled"]:
        for cat, month in sorted(jumped.items()):
            if not str(reasons.get(cat) or "").strip():
                # 급변과 같은 칸에 붙인다 — 사유는 행 단위로 쓰지만, 표시는 "이 칸 때문에 사유가
                # 필요하다"를 가리켜야 한다. 신규(month=None)만 행 전체로 남는다.
                add("missing_reason", cat, month, "급변으로 걸렸는데 증감사유가 비어 있습니다")

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
                    # 방향을 나눠 적는다 — 1/1000 쪽(값을 너무 작게 적은 경우)에 배수를 그대로
                    # 찍으면 "0.001배"가 소수점 없이 "0배"로 나와서 문장이 뜻을 잃는다.
                    times = (
                        f"{abs(v / b):,.0f}배" if abs(v / b) >= 1 else f"1/{abs(b / v):,.0f}"
                    )
                    add(
                        "unit_suspect", cat, i + 1,
                        f"{i + 1}월 값이 전 버전({_k(b)})의 {times}입니다. 단위를 확인하세요",
                        value=v, base=b,
                    )
                    break

    # 열 누락 — 양식의 기간 열 중 파일에 아예 없는 열. 값이 빈 것과는 다른 사건이라 따로 올린다.
    # 없는 열은 아래 빈칸 검사에서 빼기 때문에, 이 검사를 먼저 해서 그 달 번호를 모아 둔다.
    absent_months: set[int] = set()
    mcol = rules["missing_column"]
    if cols is not None:
        have = set(cols)
        gone = [(key, mon, label) for key, mon, label in FORM_COLUMNS if key not in have]
        # 열이 없으면 그 칸은 모든 계정이 한꺼번에 비는데, 그건 "연구소가 그 칸을 안 적었다"가
        # 아니라 "열 자체가 없다"다. 빈칸으로 또 세면 계정 수만큼 같은 사건이 쌓인다.
        # 월 열뿐 아니라 분기·연간 열도 같다(빈칸 검사가 그 칸까지 보게 된 뒤로는).
        absent_months = {mon for _, mon, _ in gone}
        if gone and mcol["enabled"]:
            add(
                "missing_column", "", None,
                f"양식의 기간 열 {len(gone)}개가 파일에 없습니다 ({' · '.join(label for _, _, label in gone)})"
                " — 그 열의 값은 비어 있는 것으로 들어갑니다",
                months=[mon for _, mon, _ in gone],
            )

    mc = rules["missing_cell"]
    if mc["enabled"]:
        for cat, months in vals.items():
            if cat in group_calc:  # 소계는 하위 합으로 만드는 값이라 빈칸이라는 개념이 없다
                continue
            # 열이 없는 달은 빼고 센다 — 위 열 누락이 그 사건을 이미 한 줄로 올렸다.
            empty = [m for m in cells_of(months) if m not in absent_months]
            # 분기·연간 칸(13~17)도 같은 사건으로 센다 — 저장이 그 칸을 월 합으로 만들어 채우지
            # 않으므로(services/overseas/entries.py 머리 주석) 연구소가 안 적어 보내면 실제로 빈다.
            # periods가 아예 없으면(분기 열이 없는 양식) 세지 않는다 — 안 보낸 것과
            # 비워 보낸 것을 구분할 수 없고, 열이 없는 경우는 위 열 누락이 이미 알렸다.
            if periods:
                cells = periods.get(cat) or []
                empty += [
                    m for i, m in enumerate(PERIOD_MONTHS)
                    if (cells[i] if i < len(cells) else None) is None and m not in absent_months
                ]
            if empty:
                add(
                    "missing_cell", cat, None,
                    f"{len(empty)}개 칸이 비어 있습니다 ({_months_txt(empty)}) — 0이 아니라 미제출입니다",
                    months=empty,
                )

    pm = rules["period_mismatch"]
    if pm["enabled"] and (periods or outs_rows):
        tol = _num(pm["amount"])

        def period_check(key: str, label: str, months: list[Any], cells: list[Any], row_label: str = "") -> None:
            """월 합 vs 분기·연간 칸. 칸이 비어 있으면(연구소가 안 적었으면) 그 칸은 건너뛴다 —
            안 적은 것은 빈칸(missing_cell)이 같은 칸 번호로 올린다. 여기서 또 올리면 두 번 걸린다."""
            for idx, cell in enumerate(cells or []):
                if cell is None or idx >= len(PERIOD_MONTHS):
                    continue
                if idx < 4:
                    computed = sum(_num(m) for m in (months or [])[idx * 3 : idx * 3 + 3])
                else:
                    computed = _total(months)
                gap = _num(cell) - computed
                if abs(gap) > tol:
                    add(
                        "period_mismatch", key, PERIOD_MONTHS[idx],
                        f"{row_label or label} {PERIOD_LABELS[idx]} 칸 {_k(_num(cell))} ≠ 월 합 {_k(computed)} (차이 {_k(gap)})",
                        value=_num(cell), base=computed, row_label=row_label,
                    )

        for cat, cells in (periods or {}).items():
            if cat in group_calc:
                continue
            period_check(cat, label_of(cat), vals.get(cat) or [], cells)
        for row in outs_rows or []:
            period_check("outs", label_of("outs"), row.get("months") or [], row.get("periods") or [], str(row.get("label") or ""))

    nc = rules["new_category"]
    if nc["enabled"] and base_vals:
        for cat, months in vals.items():
            if cat in group_calc:
                continue
            if _total(months) and not _total(base_vals.get(cat)):
                add(
                    "new_category", cat, None,
                    f"전실행에는 값이 없던 계정입니다 ({_k(_total(months))}) — 이번에 쓰기 시작한 게 맞는지 확인하세요",
                    value=_total(months),
                )

    ap = rules["analysis_prev"]
    if ap["enabled"] and analysis and base_vals:
        tol = _num(ap["amount"])
        for cat, blocks in analysis.items():
            cell = ((blocks or {}).get("y") or {}).get("prev")
            if cell is None:
                continue
            # 전실행에 그 계정이 아예 없으면 여기서 올리지 않는다 — 같은 사실을 new_category가
            # "전실행에 없던 계정"으로 이미 알린다. 둘 다 올리면 신규 계정마다 경고가 두 줄이 된다.
            if cat not in base_vals:
                continue
            computed = _total(base_vals.get(cat))
            gap = _num(cell) - computed
            if abs(gap) > tol:
                add(
                    "analysis_prev", cat, None,
                    f"증감분석의 전실행 값 {_k(_num(cell))} ≠ 본사 전실행 합계 {_k(computed)} (차이 {_k(gap)})",
                    value=_num(cell), base=computed, block="y",
                )

    am = rules["analysis_missing"]
    if am["enabled"] and analysis:
        mon_txt = f"{basis_month}월" if basis_month else "당월"
        for cat, blocks in analysis.items():
            block = (blocks or {}).get("m") or {}
            empty = [k for k in ("plan", "prev", "cur") if block.get(k) is None]
            if empty and block:
                add(
                    "analysis_missing", cat, None,
                    f"증감분석 {mon_txt} 블록에 빈칸이 있습니다 ({' · '.join(empty)})",
                    block="m", fields=empty,
                )

    # 심각도는 두지 않는다 — 전부 "확인해 달라"는 같은 무게의 알림이고, 등급을 매기면 낮은 등급은
    # 아무도 안 본다. 대신 기준별로 묶어서 보여준다(화면이 rule로 그룹핑한다).
    return found
