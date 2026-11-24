"""eval 공통 — LLM 호출을 "인프라 오류"와 "판정 실패"로 가른다.

이게 이 세트의 수명을 좌우한다. 429(한도 초과)·503(과부하)을 실패로 두면 빨간색이 품질
이야기를 안 하게 되고, 그러면 아무도 빨간색을 안 믿는다(README의 전제와 같은 이유). 그래서
인프라 오류는 **skip**으로 내린다 — "판정하지 못했다"와 "판정했고 틀렸다"는 다른 말이다.

무료 한도는 **분당 입력 토큰**(250k)에 걸린다. 대화 시나리오는 매 호출에 시스템 프롬프트 +
MCP 도구 설명 전체가 실려서, 열 개를 연달아 돌리면 한도에 닿는다. 그래서 한 번은 기다렸다
다시 해 보고, 그래도 막히면 넘긴다. 전체를 한 번에 돌리는 것보다 `-k`로 갈라 돌리는 게 빠르다.
"""

import asyncio
import time

import pytest
from fastapi import HTTPException

# 모델의 판단과 무관한 오류 문자열. config.py의 공급자 오류 문구가 바뀌면 여기도 따라가야 한다.
INFRA_SIGNS = ("429", "503", "500", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE", "timeout")
RETRY_WAIT_SECONDS = 25


@pytest.fixture
def run_llm():
    """`run_llm(lambda: 코루틴())` — 인프라 오류면 한 번 기다렸다 재시도하고, 그래도면 skip.

    코루틴이 아니라 **만드는 함수**를 받는 이유: 코루틴은 한 번만 await할 수 있어서 재시도에
    쓸 수 없다.
    """

    def run(factory, retries: int = 1):
        for attempt in range(retries + 1):
            try:
                return asyncio.run(factory())
            except HTTPException as exc:
                detail = str(exc.detail)
                if not any(sign in detail for sign in INFRA_SIGNS):
                    raise  # 400(키 없음) 등은 진짜 문제다 — 숨기면 안 된다
                if attempt < retries:
                    time.sleep(RETRY_WAIT_SECONDS)
                    continue
                pytest.skip(f"Gemini 한도/과부하로 판정하지 못했다: {detail[:200]}")

    return run
