"""증감사유 초안이 지침을 지키나 · 숫자를 지어내지 않나 (scenarios/reason_draft.yaml).

`draft_reason`은 번역된 회신(source_ko)과 서버가 계산한 증감(diff·diff_pct)만 보고 문장을 쓴다.
여기서 재는 건 둘이다 — 담당자가 말로 정한 지침을 지키나, 받지 않은 숫자·원인을 끌어오지 않나.
뒤쪽이 더 중요하다: 지어낸 숫자는 보고서에 올라간 뒤에는 되돌릴 수 없다.
"""

import os
import re
from pathlib import Path

import pytest
import yaml

from app.shared.core.config import settings
from app.shared.core.guidelines import guidelines_text
from app.shared.core.reason_translate import draft_reason
from app.shared.endpoints.guideline import upsert_guideline
from app.shared.schemas.guideline import GuidelineIn

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        os.getenv("RUN_EVAL") != "1",
        reason="RUN_EVAL=1로만 돈다 — 실제 LLM을 부른다(tests/eval/README.md)",
    ),
    pytest.mark.skipif(not settings.gemini_api_key, reason="GEMINI_API_KEY가 없다"),
]

ACCT = "overseas"
SCENARIOS = yaml.safe_load((Path(__file__).parent / "scenarios" / "reason_draft.yaml").read_text())
# 금액·비율 토큰. 쉼표가 섞인 표기(120,000,000)를 한 덩어리로 잡아야 "120"과 "000"으로 쪼개지지 않는다.
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


@pytest.mark.parametrize("sc", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_reason_draft(db, sc, run_llm):
    for g in (sc.get("given") or {}).get("guidelines") or []:
        upsert_guideline(GuidelineIn(confirm_new_topic=True, **g), db)

    inp, expect = sc["input"], sc["expect"]
    draft = run_llm(
        lambda: draft_reason(
            inp["source_ko"],
            inp.get("source_text", ""),
            inp.get("diff"),
            inp.get("diff_pct"),
            guidelines=guidelines_text(db, ACCT, "variance_reason"),
        )
    )
    where = f"[{sc['id']}] {sc.get('why', '')}\n초안: {draft!r}"
    assert draft, f"{where}\n→ 초안이 비었다"

    if "max_lines" in expect:
        lines = [ln for ln in draft.splitlines() if ln.strip()]
        assert len(lines) <= expect["max_lines"], f"{where}\n→ {len(lines)}줄"
    for s in expect.get("reply_has") or []:
        assert s in draft, f"{where}\n→ '{s}'가 없다"
    for s in expect.get("reply_lacks") or []:
        assert s not in draft, f"{where}\n→ 받지 않은 '{s}'를 끌어왔다"
    if "only_numbers" in expect:
        allowed = {n.replace(",", "") for n in expect["only_numbers"]}
        found = {m.group().replace(",", "") for m in NUMBER.finditer(draft)}
        extra = {n for n in found if n not in allowed}
        assert not extra, f"{where}\n→ 입력에 없는 숫자: {sorted(extra)}"
