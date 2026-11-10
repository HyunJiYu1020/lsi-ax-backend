"""담당자용 플로팅 채팅창이 쓰는 엔드포인트. mcp_server.py의 MCPServer 인스턴스를
그대로 in-process로 재사용한다(스키마 재정의·서브프로세스 없이 mcp.list_tools()/call_tool() 직접 호출).
읽기 도구(list_*, get_*)는 바로 실행하고, 쓰기 도구(upsert_*, record_*, update_*, delete_*)는
/chat/confirm으로 사람이 승인해야 실행된다.

공급자는 요청 바디의 provider 필드로 매 요청마다 고를 수 있다(생략 시 settings.chat_provider가 기본값):
- "anthropic": Claude API (유료, ANTHROPIC_API_KEY 필요)
- "ollama"   : 로컬 Ollama (무료, 키 불필요) — 메시지/도구 왕복 포맷이 Anthropic과 달라서
               대화 중엔 한 공급자로 고정해서 써야 한다(공급자를 바꾸면 그 전 대화 기록은 재사용 불가,
               프런트는 공급자 전환 시 대화를 초기화한다)."""

import base64
from typing import Any

import anthropic
import httpx
from fastapi import APIRouter, HTTPException
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel

from app.core.config import settings
from app.mcp_server import mcp

router = APIRouter()

READ_PREFIXES = ("list_", "get_")
MAX_TURNS = 6  # 도구 호출이 연쇄될 때 무한 루프 방지용 상한


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant" | "tool"(ollama만)
    content: Any  # 공급자별 메시지 포맷 그대로 왕복


class PendingTool(BaseModel):
    tool_use_id: str
    name: str
    input: dict[str, Any]


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    context: dict[str, Any] = {}
    provider: str | None = None  # "anthropic" | "ollama" — 생략 시 settings.chat_provider


class ChatResponse(BaseModel):
    messages: list[ChatMessage]  # 누적 대화 — 프런트가 다음 요청에 그대로 되돌려준다
    reply: str | None = None
    pending: PendingTool | None = None


class ConfirmRequest(BaseModel):
    messages: list[ChatMessage]
    pending: PendingTool
    approved: bool
    context: dict[str, Any] = {}
    provider: str | None = None


class ProviderInfo(BaseModel):
    default: str
    anthropic_available: bool
    ollama_available: bool
    gemini_available: bool


def _is_write_tool(name: str) -> bool:
    return not name.startswith(READ_PREFIXES)


_tools_cache: list[Any] | None = None


async def _mcp_tools() -> list[Any]:
    # 도구 목록은 프로세스 생애 동안 안 바뀌는데, 매 턴마다 다시 묻는 건 낭비라 한 번만 캐시한다.
    global _tools_cache
    if _tools_cache is None:
        _tools_cache = await mcp.list_tools()
    return _tools_cache


def _system_prompt(context: dict[str, Any]) -> str:
    ctx_lines = "\n".join(f"- {k}: {v}" for k, v in context.items() if v not in (None, ""))
    return (
        "너는 S.LSI 예산관리 플랫폼의 데이터 비서다. 숫자나 사실을 지어내지 말고, "
        "반드시 제공된 도구로 조회한 값만 근거로 답해라. 모르면 모른다고 답해라.\n\n"
        f"사용자가 지금 보고 있는 화면:\n{ctx_lines or '(알 수 없음)'}\n\n"
        "연구소/계정 관련 도구를 쓸 때 lab_pk 파라미터 규칙(중요, 반드시 지켜라):\n"
        "1. 위 '현재 화면' 정보에 'lab_pk'로 시작하는 키가 있으면, 사용자가 다른 연구소 이름을 "
        "명시적으로 말하지 않는 한 그 값을 lab_pk로 그대로 사용해라. list_labs로 다시 찾거나 "
        "사용자에게 되묻지 마라 — 예를 들어 그 값이 'SARC:CPU'면 사용자가 'SARC'나 'SARC+AC'라고만 "
        "말해도 바로 'SARC:CPU'를 써라.\n"
        "2. lab_pk 값은 절대 네가 지어내지 마라(예: 'SARC+AC'를 'SARC:AC'로 바꿔치기 금지). "
        "'현재 화면의 lab_pk'에도 없고 사용자가 말한 이름과도 안 맞으면 list_labs로 정확한 pk 목록을 "
        "확인한 뒤 그 목록에 있는 값만 써라.\n"
        "3. 도구의 version 파라미터(cur|prev|prior|plan)에는 '현재 화면' 정보의 'version' 키 값만 써라. "
        "'ver' 키(예: '26.09 속보', '26.08 실적')는 사람이 화면에서 보는 라벨일 뿐 도구가 받는 값이 "
        "아니다 — 'ver'를 version 파라미터에 그대로 넣지 마라. 'version' 키가 없으면 다른 값으로 "
        "지어내지 말고 list_labs/list_lab_sheets 등으로 확인하거나 사용자에게 되물어라.\n"
        "4. 반대로 사용자에게 결과를 말할 때는 cur/prev/prior/plan 같은 내부 코드를 그대로 노출하지 "
        "마라 — 'cur'는 '현재 화면'의 'ver' 라벨(예: '26.09 속보')로, 'prev'는 '전실행'으로, "
        "'prior'는 '전년 실적'으로, 'plan'은 '경영계획'으로 풀어서 말해라.\n"
        "5. 도구의 year 파라미터에는 '현재 화면' 정보의 'year' 키 값을 써라(예: 2026). 'ver' 라벨에서 "
        "연도를 직접 유추하지 말고, 'year' 키가 있으면 그 값을 그대로 써라. 'year' 키가 없는데 "
        "연도가 필요하면 사용자에게 되물어라.\n"
        "6. 추가 행/추가 열 도구(upsert_custom_row, list_custom_rows, delete_custom_row 등)의 "
        "actual_thru_month 파라미터에는 '현재 화면' 정보의 'actual_thru_month' 키 값을 그대로 써라 — "
        "생략하거나 추측하지 마라. 생략하면 화면이 실제 보고 있는 달과 다른 자리에 저장되어, 방금 "
        "추가한 행이 사용자 화면에는 안 보이게 된다.\n"
        "그 외(연구소를 특정할 필요 없는 값)도 위 '현재 화면' 정보를 기본값으로 쓰고, "
        "거기에도 없는 값만 사용자에게 되물어라.\n\n"
        "읽기 vs 쓰기 판단(중요, 반드시 지켜라):\n"
        "'바꿔줘'·'수정해줘'·'넣어줘'·'적어줘'·'올려줘'·'지워줘' 같은 동사가 있으면 조회가 아니라 "
        "변경 요청이다. 이때:\n"
        "- 변경에 필요한 값(대상 월·금액·항목 등)이 문장에 다 있으면 반드시 해당 쓰기 도구"
        "(upsert_*, record_*, delete_* 등)를 호출해라. list_*/get_*로 비슷한 값을 조회해서 "
        "보여주는 것으로 때우지 마라 — 그건 사용자가 원한 변경을 안 한 것이다.\n"
        "- 필요한 값이 하나라도 빠졌으면 절대 추측하지 말고, 아무 도구도 쓰지 말고 바로 "
        "사용자에게 무엇이 부족한지 되물어라. 빠진 값 대신 비슷한 걸 조회해서 보여주고 끝내지 마라.\n"
        "- 예외: upsert_custom_row로 '행 추가'만 할 때는 금액을 말 안 해도 되물지 말고 v를 전부 0으로 "
        "비워서 바로 추가해라 — 화면의 '+행 추가' 버튼도 빈 행(0)부터 만들고 나중에 칸을 채우는 "
        "방식이라, 행 추가 자체에는 금액이 필수가 아니다. 단, 이미 있는 값(기존 셀의 금액)을 "
        "'바꿔달라'는 요청이면 이 예외가 아니라 위 규칙대로 금액을 반드시 되물어라.\n"
        "- upsert_custom_row로 행을 추가할 때 사용자가 '~계정/~비용 아래에'처럼 위치를 지정하면, "
        "그 계정에 맞는 anchor_key를 반드시 같이 넣어라(도구 설명의 유효값 목록 참고). 위치를 "
        "말하지 않았으면 anchor_key는 생략해라(표 맨 끝에 붙는다).\n"
        "- 쓰기 도구(upsert_*, record_*, delete_* 등)를 호출할 때는 그 호출과 같은 턴에 반드시 "
        "짧은 한국어 문장 하나를 같이 말해라 — 무엇을 어디에 어떻게 바꾸려는지 사용자가 읽고 바로 "
        "이해할 수 있게(예: \"SIRC 연구소 IT 관련 비용 아래에 '클라우드 사용료' 행을 2026년 "
        "속보(9월 기준)로 추가하겠습니다. 금액은 모두 0으로 시작합니다.\"). anchor_key·"
        "actual_thru_month·version 같은 내부 코드/파라미터 이름을 이 문장에 그대로 쓰지 마라 — "
        "이 문장이 사용자가 승인 전에 보는 유일한 설명이다."
    )


async def _tool_result_text(name: str, args: dict[str, Any]) -> tuple[str, bool]:
    try:
        result = await mcp.call_tool(name, args)
    except Exception as exc:
        # mcp_server.py의 각 도구는 내부적으로 REST API를 httpx로 호출한다. lab_pk를 잘못 지어내는 등
        # 400을 유발하면 MCP SDK가 이를 UnexpectedToolError로 감싸 올리는데, 여기서 안 잡으면
        # 채팅 요청 전체가 500으로 죽는다(브라우저에선 CORS 에러로 오해하기 쉬움). 모델이 스스로
        # 고쳐 재시도할 수 있도록 일반 tool_result(is_error=True)로 돌려준다.
        cause = exc.__cause__ or exc
        if isinstance(cause, httpx.HTTPStatusError):
            detail = cause.response.text
            try:
                detail = cause.response.json().get("detail", detail)
            except ValueError:
                pass
            return f"도구 실행 실패 ({cause.response.status_code}): {detail}", True
        return f"도구 실행 중 오류: {cause}", True
    text = "".join(c.text for c in result.content if hasattr(c, "text"))
    return text, bool(result.is_error)


def _tool_result_message(provider: str, tool_use_id: str, text: str, is_error: bool) -> dict[str, Any]:
    if provider == "ollama":
        return {"role": "tool", "tool_call_id": tool_use_id, "content": text}
    if provider == "gemini":
        # 이 API는 role="tool"을 안 받는다(400 INVALID_ARGUMENT — 유효한 role에 tool이 없음).
        # Anthropic과 같은 방식으로 user role에 function_response part를 담아 보낸다.
        return {
            "role": "user",
            "content": [{"function_response": {"name": tool_use_id, "response": {"result": text, "is_error": is_error}}}],
        }
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": text, "is_error": is_error}],
    }


def _resolve_provider(provider: str | None) -> str:
    p = provider or settings.chat_provider
    if p not in ("anthropic", "ollama", "gemini"):
        raise HTTPException(status_code=400, detail=f"알 수 없는 provider: {p!r} (anthropic | ollama | gemini만 가능)")
    return p


async def _run_turn(
    messages: list[dict[str, Any]], context: dict[str, Any], provider: str | None = None, depth: int = 0
) -> ChatResponse:
    resolved = _resolve_provider(provider)
    if resolved == "ollama":
        return await _run_turn_ollama(messages, context, depth)
    if resolved == "gemini":
        return await _run_turn_gemini(messages, context, depth)
    return await _run_turn_anthropic(messages, context, depth)


# ---------- Anthropic (Claude API) ----------


def _anthropic_client() -> anthropic.AsyncAnthropic:
    if not settings.anthropic_api_key:
        raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY가 설정되지 않았습니다. backend/.env에 넣어주세요.")
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)


async def _anthropic_tools() -> list[dict[str, Any]]:
    tools = await _mcp_tools()
    return [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema} for t in tools]


async def _run_turn_anthropic(messages: list[dict[str, Any]], context: dict[str, Any], depth: int = 0) -> ChatResponse:
    client = _anthropic_client()
    try:
        resp = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=1024,
            system=_system_prompt(context),
            messages=messages,
            tools=await _anthropic_tools(),
        )
    except anthropic.APIStatusError as e:
        # Anthropic이 돌려준 에러(크레딧 부족, 키 무효, 레이트리밋 등)를 502로 그대로 노출 —
        # 전역 예외 핸들러가 "Internal Server Error"로 뭉개버리면 사용자가 원인을 알 수 없다.
        msg = (e.body or {}).get("error", {}).get("message") if isinstance(e.body, dict) else str(e)
        raise HTTPException(status_code=502, detail=f"Anthropic API 오류: {msg or e}") from e
    messages = messages + [{"role": "assistant", "content": [b.model_dump() for b in resp.content]}]

    tool_use = next((b for b in resp.content if b.type == "tool_use"), None)
    if tool_use is None:
        text = "".join(b.text for b in resp.content if b.type == "text")
        return ChatResponse(messages=[ChatMessage(**m) for m in messages], reply=text)

    if _is_write_tool(tool_use.name):
        note = "".join(b.text for b in resp.content if b.type == "text")
        return ChatResponse(
            messages=[ChatMessage(**m) for m in messages],
            reply=note or None,
            pending=PendingTool(tool_use_id=tool_use.id, name=tool_use.name, input=tool_use.input),
        )

    if depth >= MAX_TURNS:
        text, _ = await _tool_result_text(tool_use.name, tool_use.input)
        return ChatResponse(
            messages=[ChatMessage(**m) for m in messages],
            reply="도구 호출이 너무 길어져서 중단했습니다. 마지막 조회 결과: " + text[:500],
        )

    # 읽기 도구는 사람 확인 없이 바로 실행하고, 결과를 다시 모델에 넣어 다음 턴을 이어간다.
    result_text, is_error = await _tool_result_text(tool_use.name, tool_use.input)
    messages = messages + [_tool_result_message("anthropic", tool_use.id, result_text, is_error)]
    return await _run_turn_anthropic(messages, context, depth + 1)


# ---------- Ollama (로컬 모델) ----------


async def _ollama_tools() -> list[dict[str, Any]]:
    tools = await _mcp_tools()
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description or "", "parameters": t.input_schema}}
        for t in tools
    ]


async def _run_turn_ollama(messages: list[dict[str, Any]], context: dict[str, Any], depth: int = 0) -> ChatResponse:
    try:
        async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=180.0) as client:
            resp = await client.post(
                "/api/chat",
                json={
                    "model": settings.ollama_model,
                    "stream": False,
                    "messages": [{"role": "system", "content": _system_prompt(context)}] + messages,
                    "tools": await _ollama_tools(),
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError as e:
        raise HTTPException(
            status_code=502, detail="Ollama 서버에 연결할 수 없습니다. `ollama serve`가 떠 있는지 확인하세요."
        ) from e
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Ollama 오류: {e.response.text}") from e

    msg = data.get("message", {})
    tool_calls = msg.get("tool_calls") or []
    assistant_entry: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
    if tool_calls:
        assistant_entry["tool_calls"] = tool_calls
    messages = messages + [assistant_entry]

    if not tool_calls:
        return ChatResponse(messages=[ChatMessage(**m) for m in messages], reply=msg.get("content") or "")

    # Anthropic 경로와 동일하게, 한 턴엔 첫 번째 도구 호출만 처리한다.
    call = tool_calls[0]
    name = call["function"]["name"]
    args = call["function"]["arguments"]
    call_id = call.get("id") or name

    if _is_write_tool(name):
        return ChatResponse(
            messages=[ChatMessage(**m) for m in messages],
            reply=msg.get("content") or None,
            pending=PendingTool(tool_use_id=call_id, name=name, input=args),
        )

    if depth >= MAX_TURNS:
        text, _ = await _tool_result_text(name, args)
        return ChatResponse(
            messages=[ChatMessage(**m) for m in messages],
            reply="도구 호출이 너무 길어져서 중단했습니다. 마지막 조회 결과: " + text[:500],
        )

    result_text, is_error = await _tool_result_text(name, args)
    messages = messages + [_tool_result_message("ollama", call_id, result_text, is_error)]
    return await _run_turn_ollama(messages, context, depth + 1)


# ---------- Gemini (Google) ----------
# messages에는 Gemini SDK 객체를 그대로 못 넣으므로(JSON 직렬화 대상) part를 작은 dict로 펼쳐서 담고,
# 호출 직전에만 Content/Part로 다시 조립한다. function_response는 role="tool"로 돌려준다(SDK 예시 그대로).


def _gemini_client() -> genai.Client:
    if not settings.gemini_api_key:
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY가 설정되지 않았습니다. backend/.env에 넣어주세요.")
    return genai.Client(api_key=settings.gemini_api_key)


async def _gemini_tool() -> genai_types.Tool:
    tools = await _mcp_tools()
    return genai_types.Tool(
        function_declarations=[
            genai_types.FunctionDeclaration(name=t.name, description=t.description or "", parameters_json_schema=t.input_schema)
            for t in tools
        ]
    )


def _gemini_part_to_dict(part: genai_types.Part) -> dict[str, Any]:
    # thought_signature(bytes)는 모델이 function_call에 실어 보내는 추론 연속성 토큰 —
    # 다음 턴에 그 function_call part를 그대로 돌려줄 때 같이 안 보내면 400 에러가 난다.
    sig = base64.b64encode(part.thought_signature).decode() if part.thought_signature else None
    if part.function_call is not None:
        d: dict[str, Any] = {"function_call": {"name": part.function_call.name, "args": dict(part.function_call.args or {})}}
    elif part.function_response is not None:
        d = {"function_response": {"name": part.function_response.name, "response": part.function_response.response}}
    else:
        d = {"text": part.text or ""}
    if sig:
        d["thought_signature"] = sig
    return d


def _dict_to_gemini_part(d: dict[str, Any]) -> genai_types.Part:
    kwargs: dict[str, Any] = {}
    if d.get("thought_signature"):
        kwargs["thought_signature"] = base64.b64decode(d["thought_signature"])
    if "function_call" in d:
        return genai_types.Part(function_call=genai_types.FunctionCall(**d["function_call"]), **kwargs)
    if "function_response" in d:
        return genai_types.Part(function_response=genai_types.FunctionResponse(**d["function_response"]), **kwargs)
    return genai_types.Part(text=d.get("text", ""), **kwargs)


def _messages_to_gemini_contents(messages: list[dict[str, Any]]) -> list[genai_types.Content]:
    out = []
    for m in messages:
        content = m["content"]
        parts = content if isinstance(content, list) else [{"text": content}]
        out.append(genai_types.Content(role=m["role"], parts=[_dict_to_gemini_part(p) for p in parts]))
    return out


async def _run_turn_gemini(messages: list[dict[str, Any]], context: dict[str, Any], depth: int = 0) -> ChatResponse:
    client = _gemini_client()
    try:
        resp = await client.aio.models.generate_content(
            model=settings.gemini_model,
            contents=_messages_to_gemini_contents(messages),
            config=genai_types.GenerateContentConfig(
                system_instruction=_system_prompt(context),
                tools=[await _gemini_tool()],
                automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
                thinking_config=genai_types.ThinkingConfig(thinking_level="low"),
                max_output_tokens=1024,
            ),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini API 오류: {e}") from e

    parts = resp.candidates[0].content.parts or []
    messages = messages + [{"role": "model", "content": [_gemini_part_to_dict(p) for p in parts]}]

    fn_call = next((p.function_call for p in parts if p.function_call is not None), None)
    if fn_call is None:
        # thought=True part는 모델의 내부 추론(사고 과정)이다 — 최종 답변이 아니라 그냥 흘려보내면
        # 안 되므로 반드시 제외하고 합친다.
        text = "".join(p.text for p in parts if p.text and not p.thought)
        return ChatResponse(messages=[ChatMessage(**m) for m in messages], reply=text)

    name, args = fn_call.name, dict(fn_call.args or {})
    if _is_write_tool(name):
        note = "".join(p.text for p in parts if p.text and not p.thought)
        return ChatResponse(
            messages=[ChatMessage(**m) for m in messages],
            reply=note or None,
            pending=PendingTool(tool_use_id=name, name=name, input=args),
        )

    if depth >= MAX_TURNS:
        text, _ = await _tool_result_text(name, args)
        return ChatResponse(
            messages=[ChatMessage(**m) for m in messages],
            reply="도구 호출이 너무 길어져서 중단했습니다. 마지막 조회 결과: " + text[:500],
        )

    result_text, is_error = await _tool_result_text(name, args)
    messages = messages + [_tool_result_message("gemini", name, result_text, is_error)]
    return await _run_turn_gemini(messages, context, depth + 1)


# ---------- 엔드포인트 ----------


@router.get("/chat/providers", response_model=ProviderInfo)
async def chat_providers() -> ProviderInfo:
    ollama_available = False
    try:
        async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=2.0) as client:
            ollama_available = (await client.get("/api/tags")).status_code == 200
    except httpx.HTTPError:
        ollama_available = False
    return ProviderInfo(
        default=settings.chat_provider,
        anthropic_available=bool(settings.anthropic_api_key),
        ollama_available=ollama_available,
        gemini_available=bool(settings.gemini_api_key),
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest) -> ChatResponse:
    messages = [m.model_dump() for m in body.messages]
    return await _run_turn(messages, body.context, body.provider)


@router.post("/chat/confirm", response_model=ChatResponse)
async def chat_confirm(body: ConfirmRequest) -> ChatResponse:
    provider = _resolve_provider(body.provider)
    messages = [m.model_dump() for m in body.messages]
    if body.approved:
        result_text, is_error = await _tool_result_text(body.pending.name, body.pending.input)
    else:
        result_text, is_error = "사용자가 이 작업을 취소했습니다.", True
    messages = messages + [_tool_result_message(provider, body.pending.tool_use_id, result_text, is_error)]
    return await _run_turn(messages, body.context, provider)
