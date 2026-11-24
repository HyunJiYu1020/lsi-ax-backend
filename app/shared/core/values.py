"""값 처리 규칙 — 전 계정 공용 순수 함수. DB도 HTTP도 모른다."""

import hashlib


def n(v: float | None) -> float:
    """미등록(None)을 0으로 바꿔 "계산에만" 쓴다. 응답으로 내려줄 때는 None을 그대로 둔다 —
    공통 RULE: 미등록/미업로드 칸은 0이 아니라 빈칸이고, 합계는 있는 값만 더해 진행한다.
    (None을 0으로 더하는 것과 "있는 값만 더하는 것"은 합계가 같으므로, 합계는 이 헬퍼로 충분하다.)"""
    return 0.0 if v is None else float(v)


def source_hash(text: str) -> str:
    """번역 캐시 키 — 같은 문장을 두 번 번역하지 않게 원문으로 만드는 지문."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
