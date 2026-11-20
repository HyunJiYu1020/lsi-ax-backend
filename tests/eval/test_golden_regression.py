"""담당자가 AI를 고친 기록을 다시 먹여서 "지금 코드로도 같은 실수를 하나"를 본다.

사람이 손으로 만든 정답지는 금방 낡는다. 이 저장소에는 정답지가 이미 쌓여 있다:
- `close_voucher_maps.decided_by='user'` — AI 추천이 틀려서 사람이 바로잡은 전표
- `variance_reasons`의 `ai_draft` ≠ `reason_text` — 담당자가 실제로 고친 초안

데이터가 없으면 skip한다. 빈 DB에서 초록불이 뜨면 "검증했다"로 읽혀서 더 위험하다.
"""

import os

import pytest
from sqlalchemy import select

from app.accounts.overseas.services.close_mapping import hints as _hints, lab_list as _lab_list
from app.shared.core.voucher_match import LOW, suggest_lab
from app.shared.models.close import CloseVoucher, CloseVoucherMap

pytestmark = pytest.mark.eval

# 전표 매칭은 규칙 기반(LLM 아님)이라 RUN_EVAL·API 키 없이도 돈다 — 돈도 네트워크도 안 든다.
# 그래도 eval에 두는 이유: 통과 기준이 "맞췄나"가 아니라 품질 지표(얼마나 조용히 틀리나)다.
MAX_SILENT_WRONG = 0.20


def _vouchers_user_fixed(db, limit: int = 200) -> list[tuple[CloseVoucher, CloseVoucherMap]]:
    """사람이 바로잡은 전표 + 그 최종 판단. 제외·이관 건은 뺀다 — 연구소 추천의 정답이 아니다."""
    maps = db.scalars(
        select(CloseVoucherMap)
        .where(
            CloseVoucherMap.decided_by == "user",
            CloseVoucherMap.scope_key != "",
            CloseVoucherMap.excluded.is_(False),
        )
        .limit(limit)
    ).all()
    out = []
    for m in maps:
        vou = db.scalar(
            select(CloseVoucher).where(
                CloseVoucher.basis_year == m.basis_year,
                CloseVoucher.basis_month == m.basis_month,
                CloseVoucher.voucher_no == m.voucher_no,
                CloseVoucher.line_no == m.line_no,
            )
        )
        if vou is not None:
            out.append((vou, m))
    return out


def test_voucher_match_does_not_fail_silently(db):
    """사람이 고친 전표를 다시 추천해 보고, **틀렸는데 자신 있게 틀린** 비율을 본다.

    통과 기준이 정확도가 아닌 이유: 전표에는 연구소가 적혀 오지 않는다. 못 맞히는 건 설계상
    당연하고(그래서 담당자가 고른다), 위험한 건 **틀린 추천을 높은 신뢰도로 올려서 담당자가
    그냥 넘기게 되는 것**이다. 그건 사람이 검토할 기회 자체를 없앤다.
    """
    pairs = _vouchers_user_fixed(db)
    if len(pairs) < 10:
        pytest.skip(f"사람이 고친 전표가 {len(pairs)}건뿐이라 지표가 의미 없다")

    silent_wrong, right = 0, 0
    for vou, final in pairs:
        # 연구소 목록·단서표는 그 전표가 속한 달 기준으로 만든다 — 기준정보는 버전부터 적용되고,
        # 단서는 그 달 **이전** 매핑에서만 뽑아야 자기 답을 보고 맞히는 꼴이 안 된다.
        labs = _lab_list(db, vou.basis_year, vou.basis_month)
        hints = _hints(db, vou.basis_year, vou.basis_month, "overseas")
        got = suggest_lab(
            {
                "vendor": vou.vendor, "text": vou.text,
                "cost_center": vou.cost_center, "project": vou.project,
            },
            labs,
            hints,
        )
        if got["scope_key"] == final.scope_key:
            right += 1
        elif got["scope_key"] and got["confidence"] != LOW:
            silent_wrong += 1  # 틀렸는데 사람에게 안 넘겼다 — 가장 나쁜 경우

    ratio = silent_wrong / len(pairs)
    print(f"\n전표 {len(pairs)}건 — 적중 {right} · 조용히 틀림 {silent_wrong} ({ratio:.0%})")
    assert ratio <= MAX_SILENT_WRONG, (
        f"틀린 추천을 자신 있게 올린 비율이 {ratio:.0%} (상한 {MAX_SILENT_WRONG:.0%}). "
        "담당자가 검토 없이 넘기게 되므로, 못 맞히면 confidence를 낮춰 사람에게 넘겨야 한다."
    )


@pytest.mark.skipif(os.getenv("RUN_EVAL") != "1", reason="RUN_EVAL=1로만 돈다 — 실제 LLM을 부른다")
def test_draft_regression_against_user_edits(db, run_llm):
    """담당자가 고친 초안을 같은 입력으로 다시 생성해, 확정 문장과 얼마나 닮았는지 **보고한다**.

    assert를 느슨하게 두는 이유: 담당자 문장과 안 닮은 게 더 나은 문장일 수도 있다. 여기서
    잡으려는 건 품질 순위가 아니라 **크게 망가진 것**이다(빈 문장·원문 그대로·엉뚱한 언어).
    """
    from difflib import SequenceMatcher

    from app.accounts.overseas.endpoints.variance import past_draft_edits

    pairs = past_draft_edits(db, "overseas", limit=10)
    if len(pairs) < 3:
        pytest.skip(f"담당자가 고친 초안이 {len(pairs)}건뿐이다")

    from app.shared.core.guidelines import guidelines_text
    from app.shared.core.reason_translate import draft_reason

    guides = guidelines_text(db, "overseas", "variance_reason")
    scores = []
    for old_draft, final in pairs:
        new_draft = run_llm(lambda: draft_reason(final, "", None, None, guidelines=guides))
        assert new_draft, f"초안이 비었다 (입력: {final[:60]!r})"
        scores.append(SequenceMatcher(None, new_draft, final).ratio())
    avg = sum(scores) / len(scores)
    print(f"\n초안 {len(scores)}건 — 담당자 확정문과 평균 유사도 {avg:.2f}")
    assert avg >= 0.20, f"평균 유사도 {avg:.2f} — 초안이 확정문과 전혀 다른 방향이다"
