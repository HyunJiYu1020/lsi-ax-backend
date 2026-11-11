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
import json
from typing import Any

import anthropic
import httpx
from fastapi import APIRouter, HTTPException
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel

from app.core.chat_prompts import build_system_prompt
from app.core.config import settings
from app.mcp_server import CATEGORY_LABELS, mcp

router = APIRouter()

READ_PREFIXES = ("list_", "get_")
# list_*/get_* 패턴을 안 따르지만 부작용이 없는(데이터를 바꾸지 않는) 도구 — calculate는 순수 산술이라
# list_cost_entries 조회 결과를 넘겨서 바로 계산해야 하는데, 승인 팝업이 뜨면 그 흐름이 끊긴다.
# set_display_unit·set_labor_split은 이름이 set_*이지만 저장된 데이터를 전혀 건드리지 않는다 —
# 화면의 표시 단위(K$ vs $)나 인건비 분해 줄의 펼침 여부만 바꾸는 도구라 승인 팝업 없이 바로
# 실행한다(보는 방식을 바꾸는 데 매번 승인을 받으면, 숫자를 눈으로 비교하려는 흐름이 그때마다 끊긴다).
SAFE_NON_PREFIXED_TOOLS = {"calculate", "combine_series", "set_display_unit", "set_labor_split"}
# 데이터를 지우는 도구 — 쓰기 승인(한 번) 위에 "정말 지울까요?"를 한 번 더 받게 하려고 따로 표시한다.
# 값을 덮어쓰는 upsert_*는 잘못돼도 다시 덮어쓰면 되지만, delete_*는 되돌릴 방법이 없어서 구분한다.
DESTRUCTIVE_PREFIXES = ("delete_",)
MAX_TURNS = 10  # 도구 호출이 연쇄될 때 무한 루프 방지용 상한. list_cost_across_labs 덕분에 "연구소
# 수만큼 호출"은 더 이상 이 한도에 안 걸리지만(1번으로 끝남), list+combine_series+upsert처럼 여러
# 단계가 겹치는 요청엔 여전히 여유가 필요해 6에서 10으로 올렸다 — 그래도 모자라면(값을 못 구하면)
# 지어내지 말고 못 구했다고 답하게 시스템 프롬프트(규칙 7)로 막는다.


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant" | "tool"(ollama만)
    content: Any  # 공급자별 메시지 포맷 그대로 왕복


class PendingTool(BaseModel):
    tool_use_id: str
    name: str
    input: dict[str, Any]
    # True면 프런트가 승인 버튼을 눌렀을 때 "정말 삭제하시겠습니까?"를 한 번 더 묻는다.
    # 어느 도구가 파괴적인지는 서버가 정한다 — 화면마다 따로 판단하면 새 삭제 도구가 생겼을 때
    # 한쪽만 고치고 다른 쪽을 빼먹기 쉽다.
    destructive: bool = False


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
    return not name.startswith(READ_PREFIXES) and name not in SAFE_NON_PREFIXED_TOOLS


def _is_destructive_tool(name: str) -> bool:
    return name.startswith(DESTRUCTIVE_PREFIXES)


_tools_cache: list[Any] | None = None


async def _mcp_tools() -> list[Any]:
    # 도구 목록은 프로세스 생애 동안 안 바뀌는데, 매 턴마다 다시 묻는 건 낭비라 한 번만 캐시한다.
    global _tools_cache
    if _tools_cache is None:
        _tools_cache = await mcp.list_tools()
    return _tools_cache


# 프론트 화면 상태는 cur/prev/prior/plan이지만 MCP의 세분화 도구(list_cost_entries 등)는
# forecast/actual만 받는다. 고정 매핑이라 모델에게 맡기지 않고 프론트(agChatCtx)가 미리 계산해
# context의 version_api로 넣어준다. plan(경영계획)은 MCP 도구가 다루지 못해 매핑하지 않는다.
#
# 시스템 프롬프트(공통 규칙 + 계정 전용 규칙)는 app/core/chat_prompts.py가 갖고 있다 —
# 계정이 늘어날 때 이 파일을 건드리지 않고 그쪽 ACCOUNT_RULES에 키만 추가하면 된다.


def _parse_concat_json(text: str) -> list[Any]:
    """도구 결과는 리스트 항목 하나당 content 파트가 하나씩 와서, 이어붙이면 "{...}{...}{...}"처럼
    구분자 없는 JSON 객체 여러 개가 된다(배열이 아니다) — json.loads 한 번으로는 못 읽는다."""
    decoder, items, idx, text = json.JSONDecoder(), [], 0, text.strip()
    while idx < len(text):
        obj, idx = decoder.raw_decode(text, idx)
        items.append(obj)
        while idx < len(text) and text[idx] in " \t\n\r":
            idx += 1
    return items


async def _lab_mismatch_hint(args: dict[str, Any], context: dict[str, Any]) -> str | None:
    """lab_pk로 조회했는데 결과가 비었고, 그 lab_pk가 화면에 떠 있는 연구소(lab_code)와 완전히 다른
    실제 연구소를 가리킬 때(SIRC vs SARC처럼 이름이 비슷해 착각하기 쉬운 경우) 참고 문구를 만든다.
    이 판정은 LLM의 추측이 아니라 list_labs로 확인한 사실이라, 프롬프트 설명 대신 코드로 고정한다."""
    lab_pk = args.get("lab_pk")
    ctx_lab_pk = context.get("lab_pk")
    if not lab_pk or not ctx_lab_pk or lab_pk == ctx_lab_pk:
        return None
    lab_code, ctx_lab_code = str(lab_pk).split(":")[0], str(ctx_lab_pk).split(":")[0]
    if lab_code == ctx_lab_code:
        return None  # 같은 연구소의 다른 파트(SARC:CPU vs SARC:GPU)는 사용자가 명시적으로 고른 것 — 정상
    try:
        labs_result = await mcp.call_tool("list_labs", {})
        labs = _parse_concat_json("".join(c.text for c in labs_result.content if hasattr(c, "text")))
    except Exception:
        return None
    if not any(lab.get("lab_code") == lab_code for lab in labs):
        return None  # 존재하지 않는 코드는 _check_lab_pk가 이미 400으로 걸러낸다
    ctx_candidates = sorted({lab["pk"] for lab in labs if lab.get("lab_code") == ctx_lab_code})
    return (
        f"[시스템 참고] 화면은 '{ctx_lab_pk}'인데, 방금 조회한 '{lab_pk}'는 전혀 다른 연구소이고 "
        f"결과가 비어 있다. 비슷한 이름의 다른 연구소를 착각했을 가능성이 높으니, 그냥 '없다'고만 "
        f"답하지 말고 화면과 다르다는 점을 짚어서 되물어라. 화면 연구소('{ctx_lab_code}')의 실제 "
        f"하위 후보: {', '.join(ctx_candidates)}."
    )


# 파생 행/열(다른 항목을 계산해 만든 값)을 추가하는 도구들 — '집계' 탭에서만 허용한다.
_DERIVED_ADD_TOOLS = {"upsert_extra_row": "행", "upsert_extra_col": "열"}
# 행·열을 지우는 도구들 — 서버가 "원시는 취합에서만, 파생은 집계에서만"을 검사하는데, 그 판단에
# 필요한 현재 단계는 모델이 아니라 프런트가 보내준 context에 있다(아래 _inject_step 참고).
_STEP_SCOPED_DELETE_TOOLS = {"delete_extra_row", "delete_extra_col"}


def _derived_row_block_hint(name: str, args: dict[str, Any], context: dict[str, Any]) -> str | None:
    """파생 행/열(is_derived=True, 다른 계정·월을 계산해 만든 값)은 사용자가 지금 '집계'
    탭(화면 상단 진행 단계 표시의 짧은 이름 — 취합·집계·증감분석)을 보고 있을 때만 허용한다. 서버의
    전역 진행 단계(account_progress)는 이 화면의 탭 전환과 동기화되지 않는 별개 개념이라(core.js의
    S.ov.step 주석 참고) 그걸로는 판단할 수 없고, 프런트가 매 요청마다 보내주는 context['step']
    (화면에 지금 보이는 탭 이름)으로만 판단할 수 있다 — 그래서 이 판정은 MCP 서버(REST API)가 아니라
    context를 받는 여기(chat.py)에서 한다."""
    what = _DERIVED_ADD_TOOLS.get(name)
    if what is None or not args.get("is_derived"):
        return None
    if context.get("step") == "집계":
        return None
    return (
        f"도구 실행 실패: 지금 화면 탭은 '{context.get('step') or '알 수 없음'}'이다. 다른 항목을 "
        f"계산해 만든 파생 {what}은 '집계' 탭을 보고 있을 때만 추가할 수 있다. 재시도하지 "
        "말고, '집계' 탭으로 이동한 뒤 다시 요청해달라고 사용자에게 설명해라."
    )


# 집계의 파생 행/열이 붙는 범위 — 연구소 하나가 아니라 "전체 연구소 합산"이다
# (overseas.py의 ALL_SCOPE와 같은 값이어야 한다).
ALL_SCOPE = "ALL"


def _force_all_scope(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """파생 행/열의 lab_pk를 전체 합산 범위로 강제한다.

    모델은 '현재 화면'의 lab_pk(예: SARC:CPU)를 기본값으로 쓰도록 배워서, 그냥 두면 파생 행을
    연구소 하나에 붙인다. 그런데 집계 화면은 전체 합산만 보여주고 취합 화면은 파생 행을 걸러내므로,
    그렇게 저장된 행은 화면 어디에도 안 나타난다 — 저장은 됐는데 안 보이는, 사용자가 가장
    혼란스러워하는 상태가 된다. 비율을 연구소별로 따로 내서 더하면 뜻이 없다는 이유로 범위를
    전체로 정했으니(인당인건비 = 전체 인건비 ÷ 전체 인력), 프롬프트로 부탁하는 대신 여기서 못을 박는다."""
    if name not in _DERIVED_ADD_TOOLS or not args.get("is_derived"):
        return args
    return {**args, "lab_pk": ALL_SCOPE}


def _inject_step(name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """삭제 도구에 현재 화면 단계를 끼워 넣는다. 서버는 "원시 행/열은 취합에서만, 파생은 집계에서만
    지울 수 있다"를 이 값으로 검사하는데, 모델이 단계를 직접 채우게 하면 틀리거나 빼먹을 수 있다 —
    프런트가 매 요청마다 보내주는 context['step']이 화면에 실제로 보이는 탭이라 그걸 그대로 쓴다."""
    if name not in _STEP_SCOPED_DELETE_TOOLS:
        return args
    step = context.get("step")
    return {**args, "step": step} if step else args


def _is_empty_list_result(text: str) -> bool:
    # 빈 리스트를 돌려주는 도구는 content 파트가 하나도 없어(text="") 조인 결과가 빈 문자열이 된다 —
    # "[]" 텍스트가 오는 경우는 실제로 없지만 혹시 바뀌어도 깨지지 않게 둘 다 받아준다.
    if text.strip() == "":
        return True
    try:
        return json.loads(text) == []
    except ValueError:
        return False


# 화면이 $ 단위로 보고 있을 때(context["usd_unit"] == "dollar") 금액을 받는 쓰기 도구들 — 그 금액은
# 사용자가 화면에서 읽은 $ 값이라 1000으로 나눠 K$로 바꿔 저장해야 한다. 어느 인자가 금액인지만
# 적어둔다(upsert_cost_extra의 value는 제외 — 추가 열은 사용자가 이름을 직접 붙이는 자유 칸이라
# 금액이 아닐 수도 있고, 화면 입력칸도 K$ 환산 없이 그대로 쓴다).
_USD_AMOUNT_ARGS = {"upsert_cost_entry": ("amount",), "upsert_extra_row": ("v",)}


def _to_kdollar(name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """$ 단위로 보고 있는 화면에서 들어온 금액을 K$로 환산한다. 이 환산을 모델에게 맡기지 않고
    코드에서 하는 이유: 모델이 나눗셈을 빼먹으면 1000배 큰 금액이 그대로 저장되고(되돌리려면 원래
    값을 알아야 한다), 가벼운 모델일수록 '말한 숫자를 그대로 넘기기'보다 '나눠서 넘기기'를 더 자주
    틀린다. 그래서 시스템 프롬프트는 '사용자가 말한 숫자를 그대로 넣어라'로 고정하고(규칙 9-1),
    실제 환산은 여기서만 한다."""
    if context.get("usd_unit") != "dollar":
        return args
    keys = _USD_AMOUNT_ARGS.get(name)
    if not keys:
        return args
    out = dict(args)
    for k in keys:
        v = out.get(k)
        if isinstance(v, list):
            out[k] = [(x / 1000 if isinstance(x, (int, float)) else x) for x in v]
        elif isinstance(v, (int, float)):
            out[k] = v / 1000
    return out


async def _tool_result_text(name: str, args: dict[str, Any], context: dict[str, Any]) -> tuple[str, bool]:
    args = _force_all_scope(name, _to_kdollar(name, _inject_step(name, args, context), context))
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
    if name.startswith("list_") and "lab_pk" in args and _is_empty_list_result(text):
        hint = await _lab_mismatch_hint(args, context)
        if hint:
            text = f"{text}\n\n{hint}"
    return text, bool(result.is_error)


# list_memo_cols 등 화면 코드(cur/prev/prior/plan)와 list_cost_entries 등 API 코드(forecast/actual)를
# 섞어 쓰는 쓰기 도구들이 있어, 사람이 보는 설명 문장에서는 둘 다 알아듣게 라벨을 붙인다.
_VERSION_LABELS = {"forecast": "속보", "actual": "실적", "cur": "당월 속보", "prev": "전실행", "prior": "전년 실적", "plan": "경영계획"}


def _default_write_note(name: str, args: dict[str, Any]) -> str | None:
    """모델이 쓰기 도구 호출과 같은 턴에 설명 문장을 빼먹었을 때 쓰는 대체 문장. 시스템 프롬프트가
    이 문장을 요구하지만 지키라고 "말"만 하는 거라 모델이(특히 가벼운 모델일수록) 종종 빼먹는다 —
    그때도 사용자가 내용 없이 승인 버튼만 보는 일이 없도록 도구 이름·인자에서 기계적으로 만들어
    채운다. 모델이 이미 문장을 냈으면 이 함수는 호출되지 않는다(아래 호출부에서 note 없을 때만 씀)."""
    g = args.get
    v = _VERSION_LABELS.get(g("version"), g("version"))
    if name == "upsert_cost_entry":
        category = CATEGORY_LABELS.get(g("category_key"), g("category_key"))
        return f"{g('lab_pk')} 연구소의 {v} 기준({g('yyyymm')}) {category} 항목 {g('month')}월 금액을 {g('amount')}로 변경합니다."
    if name == "upsert_extra_row":
        anchor = CATEGORY_LABELS.get(g("anchor_key"), g("anchor_key"))
        where = f" ('{anchor}' 아래)" if g("anchor_key") else ""
        if g("is_derived"):
            # 파생 행은 숫자가 아니라 계산식을 저장한다 — 승인 전 설명도 "무엇으로 계산하는 행인지"를
            # 보여줘야 사용자가 무엇에 동의하는지 안다(operands는 코드가 아니라 한글명으로 풀어서).
            formula = g("formula") or {}
            materials = " · ".join(CATEGORY_LABELS.get(k, k) for k in formula.get("operands", []))
            how = "합계" if formula.get("op") == "sum_elementwise" else "나눈 값"
            return (
                f"전체 연구소 합산 기준 {v}({g('yyyymm')})에 '{g('label')}' 행을 추가합니다{where} — "
                f"{materials}을(를) 월별로 {how}으로 계산합니다(원본이 바뀌면 자동으로 다시 계산됩니다)."
            )
        return f"{g('lab_pk')} 연구소의 {v} 기준({g('yyyymm')})에 '{g('label')}' 행을 추가/수정합니다{where}."
    if name == "upsert_extra_col":
        if g("is_derived"):
            formula = g("formula") or {}
            months = formula.get("months", [])
            how = "합계" if formula.get("op") == "sum_months" else "평균"
            when = "+".join(f"{m}월" for m in months)
            return (
                f"전체 연구소 합산 기준 {v}({g('yyyymm')})에 '{g('label')}' 열을 추가합니다 — "
                f"각 행의 {when}을 {how}으로 계산합니다(원본이 바뀌면 자동으로 다시 계산됩니다)."
            )
        return f"{g('lab_pk')} 연구소의 {v} 기준({g('yyyymm')})에 '{g('label')}' 열을 추가합니다."
    if name == "delete_extra_row":
        return f"{g('lab_pk')} 연구소의 {v} 기준({g('yyyymm')}) 추가 행을 삭제합니다."
    if name == "upsert_cost_extra":
        return f"{g('lab_pk')} 연구소의 {v} 기준({g('yyyymm')})에 '{g('label')}' 열을 추가/수정합니다."
    if name == "delete_extra_col":
        return f"{g('lab_pk')} 연구소의 {v} 기준({g('yyyymm')}) '{g('label')}' 열을 삭제합니다."
    if name == "upsert_note":
        category = CATEGORY_LABELS.get(g("category_key"), g("category_key"))
        return f"{g('lab_scope')}의 {category} 증감분석 비고를 저장합니다: \"{g('note_text')}\""
    if name == "upsert_memo_col":
        return f"{g('lab_pk')} 연구소의 {v} 기준에 메모 열 '{g('label')}'을 추가/수정합니다."
    if name == "delete_memo_col":
        return f"{g('lab_pk')} 연구소의 {v} 기준 메모 열을 삭제합니다."
    if name == "upsert_memo":
        return f"{g('lab_pk')} 연구소의 {v} 기준 메모를 저장합니다: \"{g('text')}\""
    if name == "upsert_sheet_row":
        return f"{g('lab_pk')} 연구소의 {v} 기준 시트 행을 추가/수정합니다."
    if name == "delete_sheet_row":
        return f"{g('lab_pk')} 연구소의 {v} 기준 시트 행을 삭제합니다."
    if name == "update_step":
        return f"편성 진행 단계를 {g('step')}단계로 변경합니다."
    if name == "upsert_common_code":
        return f"공통코드 '{g('group_code')}/{g('code')}'를 추가/수정합니다: {g('name')}"
    if name == "delete_common_code":
        return f"공통코드 '{g('group_code')}/{g('code')}'를 삭제합니다."
    if name == "record_upload":
        return f"{g('lab_pk')} 연구소에 업로드 이력 '{g('filename')}'을 기록합니다."
    if name == "open_version":
        return f"{g('year')}년 {g('month')}월 {_VERSION_LABELS.get(g('kind'), g('kind'))} 버전을 새로 엽니다(작성중)."
    if name == "update_version_status":
        kind = _VERSION_LABELS.get(g("kind"), g("kind"))
        what = "완료(확정)로" if g("status") == "done" else "작성중으로(완료 취소)"
        return f"{g('year')}년 {g('month')}월 {kind} 버전을 {what} 바꿉니다."
    if name == "delete_sheet_rows_all":
        v = _VERSION_LABELS.get(g("version"), g("version"))
        return f"{g('lab_pk')} 연구소의 {v} 기준 '{g('sheet_key')}' 시트 행을 전부 삭제합니다."
    return None  # 알 수 없는 도구(새로 추가된 쓰기 도구 등)는 모델이 낸 설명이 없으면 그냥 없는 채로 둔다


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
            system=build_system_prompt(context),
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
        block_hint = _derived_row_block_hint(tool_use.name, tool_use.input, context)
        if block_hint is not None:
            if depth >= MAX_TURNS:
                return ChatResponse(messages=[ChatMessage(**m) for m in messages], reply=block_hint)
            messages = messages + [_tool_result_message("anthropic", tool_use.id, block_hint, True)]
            return await _run_turn_anthropic(messages, context, depth + 1)
        note = "".join(b.text for b in resp.content if b.type == "text")
        return ChatResponse(
            messages=[ChatMessage(**m) for m in messages],
            reply=note or _default_write_note(tool_use.name, tool_use.input),
            pending=PendingTool(
                tool_use_id=tool_use.id, name=tool_use.name, input=tool_use.input,
                destructive=_is_destructive_tool(tool_use.name),
            ),
        )

    if depth >= MAX_TURNS:
        text, _ = await _tool_result_text(tool_use.name, tool_use.input, context)
        return ChatResponse(
            messages=[ChatMessage(**m) for m in messages],
            reply="도구 호출이 너무 길어져서 중단했습니다. 마지막 조회 결과: " + text[:500],
        )

    # 읽기 도구는 사람 확인 없이 바로 실행하고, 결과를 다시 모델에 넣어 다음 턴을 이어간다.
    result_text, is_error = await _tool_result_text(tool_use.name, tool_use.input, context)
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
                    "messages": [{"role": "system", "content": build_system_prompt(context)}] + messages,
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
        block_hint = _derived_row_block_hint(name, args, context)
        if block_hint is not None:
            if depth >= MAX_TURNS:
                return ChatResponse(messages=[ChatMessage(**m) for m in messages], reply=block_hint)
            messages = messages + [_tool_result_message("ollama", call_id, block_hint, True)]
            return await _run_turn_ollama(messages, context, depth + 1)
        return ChatResponse(
            messages=[ChatMessage(**m) for m in messages],
            reply=msg.get("content") or _default_write_note(name, args),
            pending=PendingTool(
                tool_use_id=call_id, name=name, input=args, destructive=_is_destructive_tool(name)
            ),
        )

    if depth >= MAX_TURNS:
        text, _ = await _tool_result_text(name, args, context)
        return ChatResponse(
            messages=[ChatMessage(**m) for m in messages],
            reply="도구 호출이 너무 길어져서 중단했습니다. 마지막 조회 결과: " + text[:500],
        )

    result_text, is_error = await _tool_result_text(name, args, context)
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
                system_instruction=build_system_prompt(context),
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
        if not text.strip():
            # Gemini가 과부하 등으로 함수 호출도 텍스트도 없는 빈 응답을 줄 때가 있다 — 그걸 그대로
            # reply=""로 돌려주면 사용자 눈에는 "아무 반응도 없는" 것처럼 보인다. 빈 응답 대신 분명한
            # 에러 문구를 돌려줘서 재시도하라는 걸 알 수 있게 한다.
            text = "모델이 빈 응답을 반환했습니다(일시적인 과부하일 수 있습니다). 다시 시도해 주세요."
        return ChatResponse(messages=[ChatMessage(**m) for m in messages], reply=text)

    name, args = fn_call.name, dict(fn_call.args or {})
    if _is_write_tool(name):
        block_hint = _derived_row_block_hint(name, args, context)
        if block_hint is not None:
            if depth >= MAX_TURNS:
                return ChatResponse(messages=[ChatMessage(**m) for m in messages], reply=block_hint)
            messages = messages + [_tool_result_message("gemini", name, block_hint, True)]
            return await _run_turn_gemini(messages, context, depth + 1)
        note = "".join(p.text for p in parts if p.text and not p.thought)
        return ChatResponse(
            messages=[ChatMessage(**m) for m in messages],
            reply=note or _default_write_note(name, args),
            pending=PendingTool(
                tool_use_id=name, name=name, input=args, destructive=_is_destructive_tool(name)
            ),
        )

    if depth >= MAX_TURNS:
        text, _ = await _tool_result_text(name, args, context)
        return ChatResponse(
            messages=[ChatMessage(**m) for m in messages],
            reply="도구 호출이 너무 길어져서 중단했습니다. 마지막 조회 결과: " + text[:500],
        )

    result_text, is_error = await _tool_result_text(name, args, context)
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
        result_text, is_error = await _tool_result_text(body.pending.name, body.pending.input, body.context)
    else:
        result_text, is_error = "사용자가 이 작업을 취소했습니다.", True
    messages = messages + [_tool_result_message(provider, body.pending.tool_use_id, result_text, is_error)]
    return await _run_turn(messages, body.context, provider)
