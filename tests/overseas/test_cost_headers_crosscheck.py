"""머리글 대조 — 프런트가 파일에서 읽은 열 머리글이 서버에 그대로 저장되는지.

이 기능의 약속은 하나다: **연구소 파일에 적혀 온 글자가 취합 비용 표에 그대로 보인다.** 그 약속이
지켜지려면 두 곳이 같은 규칙을 써야 한다 —
  ① 프런트(core.js ovParse)가 머리글을 어떤 열 자리 키로 묶는가("m1" · "q1" · "y")
  ② 서버(services/cost_headers.check_labels)가 그 키를 받아주는가
키 규칙이 한쪽만 바뀌면 조용히 틀어진다: 서버가 400으로 거절하면 머리글이 **아예 안 보이고**(화면은
업로드 흐름을 막지 않으려고 저장 실패를 삼킨다), 반대로 프런트가 키를 바꾸면 머리글이 엉뚱한 열에
붙는다. 그래서 파싱을 파이썬으로 베껴 적지 않고 js_upload_harness.mjs로 **실제 실행**해서 그 결과를
실제 저장 API에 먹인다(test_upload_validation_crosscheck와 같은 방식).

같이 고정하는 것: 꾸밈말이 붙은 머리글("'26.1월 실적" · "M2601A")도 그 달로 읽힌다는 것. ovMonOf는
깔끔한 머리글만 월로 인정해서, 그런 열은 월이 아니라 "추가 열"로 떨어졌다 — 머리글을 파일 글자
그대로 쓰기로 하면서 드러난 구멍이고, 이 테스트가 그 회귀를 잡는다.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.accounts.overseas.endpoints.collect import list_cost_headers, upsert_cost_headers
from app.accounts.overseas.schemas import CostHeaderIn
from app.accounts.overseas.services.labs import all_labs

HARNESS = Path(__file__).parent / "js_upload_harness.mjs"
REPO = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(
    not shutil.which("node") or not (REPO / "frontend-html/shared/core.js").exists(),
    reason="node 또는 프런트 소스(frontend-html/shared/core.js)가 없어 대조할 수 없다",
)

YM = "209509"  # 실제 데이터가 없는 가짜 기준월(test_cost_headers_api와 같은 이유)

# 계정 행 — ovParse는 고정 계정을 3개 이상 찾아야 시트로 인정한다.
ROWS = [
    ["", "(급여)", "Salaries & Wages"],
    ["", "(상여)", "Bonus"],
    ["", "복리후생", "Welfare, Health Insurance"],
    ["", "인력(정규직, 주재파견 제외)", "Headcount(F/T only, w/o dispatcher)"],
]


def sheet(month_headers):
    """비용 시트 하나. 월 머리글만 바꿔 끼우고 분기·연간은 고정한다."""
    hdr = ["계정코드", "구분", "Cost Element"] + list(month_headers) + ["1Q", "2Q", "3Q", "4Q", "연간"]
    rows = [r + [100] * len(month_headers) + [300, 300, 300, 300, 1200] for r in ROWS]
    return {"name": "비용", "aoa": [["", "TEST Expense Report (Unit : K$)"], hdr] + rows}


def run_js_card(month_headers, quarters=("1Q", "2Q", "3Q", "4Q", "연간")):
    """비용 카드 전체(머리글 + 이상 감지 payload)를 돌려준다."""
    hdr = ["계정코드", "구분", "Cost Element"] + list(month_headers) + list(quarters)
    rows = [r + [100] * len(month_headers) + [300] * len(quarters) for r in ROWS]
    sheets = [{"name": "비용", "aoa": [["", "TEST Expense Report (Unit : K$)"], hdr] + rows}]
    job = json.dumps(
        {"file": "해외연구소_SIRC_M2509.xlsx", "sheets": sheets, "lab": "SIRC", "basis": "2095-09"},
        ensure_ascii=False,
    )
    p = subprocess.run(["node", str(HARNESS)], input=job, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        pytest.fail(
            "프런트 업로드 파싱을 실행할 수 없다. core.js 구조가 바뀌었으면 "
            f"js_upload_harness.mjs를 같이 고칠 것:\n{p.stderr[-3000:]}"
        )
    out = json.loads(p.stdout)
    cards = [c for c in out["pends"] if c["cost_rows"]]
    assert cards, f"비용 카드가 안 만들어졌다(파싱 실패): {out.get('error')!r}"
    return cards[0]


def run_js(month_headers):
    job = json.dumps(
        {"file": "해외연구소_SIRC_M2509.xlsx", "sheets": [sheet(month_headers)], "lab": "SIRC", "basis": "2095-09"},
        ensure_ascii=False,
    )
    p = subprocess.run(["node", str(HARNESS)], input=job, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        pytest.fail(
            "프런트 업로드 파싱을 실행할 수 없다. core.js 구조가 바뀌었으면 "
            f"js_upload_harness.mjs를 같이 고칠 것:\n{p.stderr[-3000:]}"
        )
    out = json.loads(p.stdout)
    cards = [c for c in out["pends"] if c["cost_rows"]]
    assert cards, f"비용 카드가 안 만들어졌다(파싱 실패): {out.get('error')!r}"
    return cards[0]["hdr"]


# 연구소가 실제로 보내는 머리글 모양들. 꾸밈말이 붙은 것(첫 둘)이 이 기능의 본래 목적이고,
# 나머지는 지금까지 읽히던 모양이라 결과가 바뀌면 회귀다.
HEADER_STYLES = {
    "꾸밈말 붙은 한글": [f"'26.{m}월 " + ("실적" if m < 9 else "속보") for m in range(1, 13)],
    "월 코드(M2601A)": [f"M26{m:02d}" + ("A" if m < 9 else "F") for m in range(1, 13)],
    "깔끔한 N월": [f"{m}월" for m in range(1, 13)],
    "YYYYMM": [f"2026{m:02d}" for m in range(1, 13)],
    "영문 3글자": ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"],
}


@pytest.mark.parametrize("style", list(HEADER_STYLES))
def test_열두_달을_모두_월로_읽고_머리글을_그대로_들고_온다(style):
    months = HEADER_STYLES[style]
    hdr = run_js(months)
    # 12개월 + 1Q~4Q + 연간 = 17열이 모두 자리를 찾아야 한다. 하나라도 빠지면 그 달 금액이
    # 엉뚱한 자리(추가 열)로 가고, 표 머리글도 코드 글자로 돌아간다.
    assert [hdr.get(f"m{m}") for m in range(1, 13)] == months
    assert [hdr.get(k) for k in ("q1", "q2", "q3", "q4", "y")] == ["1Q", "2Q", "3Q", "4Q", "연간"]


def test_프런트가_읽은_머리글을_서버가_그대로_저장한다(db):
    """프런트의 열 자리 키를 서버가 받아주는가 — 거절하면 머리글이 화면에서 조용히 사라진다."""
    hdr = run_js(HEADER_STYLES["꾸밈말 붙은 한글"])
    lab = next(lab.pk for lab in all_labs(db) if lab.pk != "_ETC")
    out = upsert_cost_headers(
        CostHeaderIn(
            lab_pk=lab, version="forecast", yyyymm=YM, labels=hdr, source_file="해외연구소_SIRC_M2509.xlsx"
        ),
        db=db,
    )
    assert out.labels == hdr
    back = list_cost_headers(yyyymm=YM, version="forecast", lab_pk=lab, db=db)
    assert back[0].labels == hdr


# ---------- 열 누락 검증 대조 ----------
# 프런트가 보내는 열 목록(cols)과 서버가 기대하는 열 자리 키(anomaly.FORM_COLUMNS)가 같은 규칙을
# 써야 "열이 빠졌다"를 알릴 수 있다. 키가 어긋나면 **멀쩡한 파일에 17개 열이 다 없다고** 뜨거나
# (경고가 거짓이 된다) 정말 빠진 열을 놓친다. 그래서 프런트를 실제로 실행해 그 payload를 서버
# 판정 함수에 그대로 먹인다.


def _detect(card):
    from app.accounts.overseas.cost_groups import GROUP_CALC, category_label, resolve_leaves
    from app.shared.core import anomaly

    payload = card["payload"]
    assert payload is not None, "이상 감지 payload가 안 만들어졌다"
    return payload, anomaly.detect(
        vals={k: list(v or []) for k, v in (payload["vals"] or {}).items()},
        base_vals=None, sums=payload.get("sums"), reasons=payload.get("reasons"),
        rules=anomaly.merged_rules([], None),
        group_calc=GROUP_CALC, label_of=category_label, leaves_of=resolve_leaves,
        periods=payload.get("periods"), cols=payload.get("cols"),
    )


def test_열이_다_있는_파일은_열_누락_경고가_없다():
    payload, findings = _detect(run_js_card([f"'26.{m}월 실적" for m in range(1, 13)]))
    assert sorted(payload["cols"]) == sorted([f"m{m}" for m in range(1, 13)] + ["q1", "q2", "q3", "q4", "y"])
    assert [f for f in findings if f["rule"] == "missing_column"] == []


def test_9월_열이_없는_파일은_열_누락으로_한_줄_걸린다():
    """프런트가 안 읽은 열은 cols에서 빠지고, 서버가 그걸 양식과 대조해 알린다."""
    months = [f"'26.{m}월 실적" for m in range(1, 13) if m != 9]
    payload, findings = _detect(run_js_card(months))
    assert "m9" not in payload["cols"]
    col = [f for f in findings if f["rule"] == "missing_column"]
    assert len(col) == 1 and col[0]["months"] == [9] and "9월" in col[0]["message"]
    # 같은 사건이 빈칸으로 또 세어지지 않는다(계정 수만큼 쌓이면 아무도 안 읽는다).
    assert [f for f in findings if f["rule"] == "missing_cell" and 9 in f["months"]] == []


def test_분기_연간_열을_안_보낸_파일도_걸린다():
    payload, findings = _detect(run_js_card([f"{m}월" for m in range(1, 13)], quarters=()))
    assert sorted(payload["cols"]) == sorted([f"m{m}" for m in range(1, 13)])
    col = [f for f in findings if f["rule"] == "missing_column"]
    assert len(col) == 1 and col[0]["months"] == [13, 14, 15, 16, 17]


def test_양식_밖의_열은_값검증을_타지_않는다():
    """연구소가 더 보낸 열(주간 W1 · W2)은 그리드에 보여주고 저장만 한다 — 검증 대상이 아니다.
    검증 payload(vals · periods · cols)에 그 열이 섞여 들어가면 "양식에 없는 열"이 양식 검사를
    받게 된다."""
    card = run_js_card([f"{m}월" for m in range(1, 13)] + ["W1", "W2"])
    payload, findings = _detect(card)
    assert "W1" not in payload["cols"] and "W2" not in payload["cols"]
    assert [f for f in findings if f["rule"] == "missing_column"] == []
    # 머리글에도 안 들어간다 — 양식의 기간 열이 아니라 추가 열이다(추가 열은 extra-cols가 맡는다).
    assert "W1" not in card["hdr"].values() and "W2" not in card["hdr"].values()
