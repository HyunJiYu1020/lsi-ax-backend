"""제대로 적은 파일을 올리면 **경고가 하나도 안 뜬다** — 검증의 반대쪽을 고정한다.

경고가 뜨는지는 test_upload_validation_crosscheck.py · test_upload_to_grid.py가 본다. 그 반대
("맞게 올렸는데도 뜨는" 오경보)는 아무도 안 보고 있었는데, 오경보가 흔해지면 담당자가 경고를
전부 무시하게 되므로 사실 더 위험하다. 양쪽을 다 잡아야 기준이 쓸모가 있다.

올리는 파일은 tests/overseas/make_clean_upload.py가 **서버가 들고 있는 전실행 값에서** 만든다
(그래서 급변·신규 계정·단위가 날 수 없다). 그 생성기는 손으로 올려 보며 확인할 때도 쓴다:
    .venv/bin/python -m tests.overseas.make_clean_upload
"""

import json

import pytest

from tests.overseas.make_clean_upload import build
from tests.overseas.test_upload_to_grid import (  # noqa: F401  (backend 픽스처를 그대로 쓴다)
    HARNESS,
    backend,
    pytestmark,
    run_harness,
)

LAB = "SIRC"


def _screen_basis() -> str:
    """헤드리스로 띄운 화면이 **실제로 보고 있는 기준월**. 달을 여기 박아 두면 안 된다 — 테스트가
    고른 달과 화면이 연 달이 다르면 기준 파일은 9월 속보와 맞춰 만들었는데 화면은 10월 속보와
    비교해서 "맞게 적은 파일"에 전월 대조 경고가 뜬다(그 상태로 오경보를 잡는 테스트라 통째로
    빨개진다 — 2026-11-23에 실제로 그랬다).

    화면은 **현재 달력 월**로 부팅한다(core.js verSeed · verNowM: 2026년 목업이라 그 해로 가둔다).
    서버의 최신 속보월(current_basis_month)이 아니다 — 11월에 10월 속보가 최신이어도 화면은 11월을
    열고 전실행으로 10월 속보를 본다. 전에 여기서 current_basis_month를 쓰다가 그 둘이 갈리면서
    한 달 어긋난 파일이 만들어졌다."""
    from datetime import date

    today = date.today()
    return f"2026-{(today.month if today.year == 2026 else 12):02d}"


@pytest.fixture(scope="module")
def clean(backend, tmp_path_factory):
    try:
        book = build(LAB, _screen_basis(), tmp_path_factory.mktemp("clean"))
    except SystemExit as e:  # 전실행 값이 DB에 없으면 "통과했다"고 말할 수 없다
        pytest.skip(str(e))
    return run_harness(backend, book, LAB)


def test_a_correct_file_raises_no_warning(clean):
    """기준 9개 중 하나도 걸리지 않는다. 걸리면 무엇이 걸렸는지 그대로 보여준다."""
    hits = [f"{c['pk']}: {f['tip']}" for c in clean["scan"] for f in c["findings"]]
    assert not hits, "맞게 적은 파일인데 경고가 떴다(오경보):\n" + "\n".join(hits)


def test_all_three_sheets_are_read_as_their_own_tabs(clean):
    """비용 · 증감분석 · 외주용역 세 시트가 다 읽혀서 미리보기 탭 세 개로 뜬다.

    한 시트라도 머리글이 안 맞아 못 읽히면 그 시트의 검사는 조용히 빠진다(skipped) — 경고가
    0인 것과 구분이 안 되므로 탭 수를 같이 본다.
    """
    labels = [t["label"].strip() for t in clean["tabs"]]
    assert len(labels) == 3, f"탭이 세 개가 아니다: {labels}"
    assert any("비용" in x for x in labels) and any("증감" in x for x in labels) and any("외주" in x for x in labels)
    assert all(t["flagged"] == 0 for t in clean["tabs"]), "경고가 없는데 칸에 표시가 붙었다"


def test_it_says_which_checks_it_could_not_run(clean):
    """못 한 검사는 밝힌다 — 경고 0건과 "검사를 안 했다"는 다르다.

    카드가 시트 단위로 갈라지는 구조상(비용은 자리별 · 증감분석 · 외주용역은 연구소별) 한 카드가
    다른 카드의 시트를 검사할 수는 없다. 그 사실이 skipped로 적혀 있어야 한다.
    """
    skipped = {c["pk"]: c["skipped"] for c in clean["scan"]}
    assert any(s for s in skipped.values()), f"못 한 검사를 하나도 안 밝혔다: {json.dumps(skipped, ensure_ascii=False)}"
    for pk, lines in skipped.items():
        for line in lines:
            assert "—" in line, f"{pk}: 이유 없이 '건너뜀'만 적혀 있다: {line}"
