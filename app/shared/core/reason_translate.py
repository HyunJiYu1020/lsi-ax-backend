"""해외연구소 증감사유(연구소가 영문으로 써 보낸 회신) → 보고용 한국어.

원래 이 번역은 프런트(core.js의 OV_KO 사전 + ovTrK 정규식)에만 있었다 — 초기에는 회신 문구를
사전에 미리 박아놨고, 사전에 없는 문장은 "(원문 · 번역 필요)"를 붙여 그대로 내보냈다. 연구소가
매달 새로 쓰는 문장을 사전으로 따라잡을 수는 없어서, 그 사전이 정해놨던 "번역 모양"만 규칙으로
옮겨 LLM에게 맡긴다 — 즉 이 파일의 프롬프트는 그 사전의 명세서다:

- 보고용 명사형 간결체("신규 채용에 따른 기본급 증가"), 서술형 문장("~했습니다")으로 풀지 않는다
- 금액·비율·인원은 원문 표기 그대로($449K는 $449K로, 환산하거나 단위를 바꾸지 않는다)
- 한 칸에 사유가 여러 개면 ① ② ③으로 끊는다(목업의 "mainly due to" 다항목 처리)
- Increase/Decrease는 증가/감소로, 방향을 뒤집지 않는다

번역 결과는 DB(overseas_reason_translations)에 원문과 짝지어 캐시한다. 같은 회신 문장이 화면을
그릴 때마다 다시 올라오는데 그때마다 LLM을 부르면 느리고 비싸고, 무엇보다 같은 원문이 어제와 오늘
다르게 번역되면 보고서 숫자 설명이 흔들린다 — 한 번 번역한 문장은 계속 같은 한국어로 보이게 한다.
원문을 지우지 않고 같이 남기는 것도 그래서다(공통 RULE: 원문 보관).
"""

import json
import re
from typing import Any

# Anthropic 주석 처리로 안 쓴다 — 되살릴 때 같이 푼다.
# import anthropic
import httpx
from fastapi import HTTPException
from google import genai
from google.genai import types as genai_types

from app.shared.core.config import settings

# 한 번에 번역할 문장 수 상한. 증감사유 화면 한 장이 연구소 12개 × 계정 20여 개라 캐시가 빈
# 상태에서 한 번에 다 보내면 응답이 토큰 한도에 걸려 잘린다 — 프런트가 나눠 보내게 막아둔다.
MAX_TEXTS = 40
# 원문 1건 길이 상한. overseas_reason_rows.remark_* 가 String(500)이라 그보다 긴 원문은 없다.
MAX_LEN = 500

# 용어 고정. 번역 자체는 모델이 하고, 이 표는 "같은 뜻을 매달 같은 단어로 쓰게" 하는 데만 쓴다 —
# 사전처럼 전체 문장을 다 담지 않는다(그 방식이 사전 번역의 한계였다). 보고서에 그대로 실리는 말이라
# 담당자가 쓰는 표현으로 적는다.
GLOSSARY: list[tuple[str, str]] = [
    ("basic salary", "기본급"),
    ("salary others", "기타 급여"),
    ("merit increase", "정기 인상분"),
    ("annual salary increase", "연봉 인상"),
    ("MBO accrual / bonus accrual", "MBO 충당금 / 상여 충당금"),
    ("retention bonus", "리텐션 보너스"),
    ("social security expense", "사회보험료"),
    ("health insurance premium", "건강보험료"),
    ("401K matching", "401K 매칭"),
    ("hire / new hires", "채용 / 신규 채용"),
    ("resignation", "퇴직"),
    ("headcount", "인원"),
    ("training / conference fee", "교육 / 컨퍼런스 참가비"),
    ("business trip", "출장"),
    ("depreciation", "상각비"),
    ("capex", "투자 집행"),
    ("office lease / rent", "임차료"),
    ("tape-out", "Tape-out"),
    ("evaluation board / sample", "평가 보드 / 샘플"),
    ("consulting / legal service", "컨설팅 / 법무 용역"),
    ("recruiting agency fee", "채용 대행 수수료"),
    ("SW license (EDA)", "SW 라이선스(EDA)"),
    ("cloud usage", "클라우드 사용량"),
    ("outsourcing", "외주용역"),
    ("design service", "설계 용역"),
    ("verification", "검증"),
    ("true-up", "정산"),
    ("refund", "환급"),
    ("reversal", "환입"),
    ("postponed / delayed to next year", "차년도 이연"),
]

_RULES = (
    "너는 반도체 해외연구소가 본사로 보낸 비용 증감사유(영문 회신)를 한국어 보고 문구로 옮긴다.\n"
    "규칙:\n"
    "1. 보고용 명사형 간결체로 쓴다 — 예: 'higher basic salary from new hires' → '신규 채용에 따른 기본급 증가'. "
    "'~했습니다'·'~입니다' 같은 서술형으로 풀지 마라. 문장 끝에 마침표를 붙이지 마라.\n"
    "2. 금액·비율·인원은 원문 표기를 그대로 유지한다 — '$449K'는 '$449K', 'avg 6%'는 '평균 6%', '2 engineers'는 '2명'. "
    "단위를 환산하거나(K$ → 억원 등) 숫자를 바꾸거나 없는 숫자를 만들지 마라.\n"
    "3. 한 칸에 사유가 둘 이상이면 ① ② ③ ④로 끊어서 한 줄로 붙인다 — "
    "예: '증가 사유: ① 기타 급여 $449K ② 인원 증가에 따른 기본급 $173K'.\n"
    "4. Increase/Decrease(또는 higher/lower, more/less)의 방향은 반드시 보존한다 — 증가를 감소로 옮기면 보고가 틀어진다.\n"
    "5. 월·분기 표기는 '4월'·'3분기'·'하반기'처럼 한국어로 옮긴다. 고유명사(IRS, CA State, ACAS, GPU, HBM4, "
    "401K, MBO, EDA, Tape-out 등)와 과제명·장비명은 원문 그대로 둔다.\n"
    "6. 원문에 없는 원인·금액·추측을 덧붙이지 마라. 요약이 필요하면 빼는 쪽으로 줄이고, 지어내지 마라.\n"
    "7. 영문이 아니라 이미 한국어인 원문은 손대지 말고 그대로 돌려준다.\n"
    "8. 뜻을 알 수 없어 번역할 수 없으면 그 항목만 null로 둔다 — 억지로 번역하지 마라.\n"
    "용어는 아래 표를 따른다(같은 뜻이면 매달 같은 단어로 써야 한다):\n"
    + "\n".join(f"- {en} → {ko}" for en, ko in GLOSSARY)
)

_OUTPUT_RULE = (
    '응답은 JSON 배열 하나만 출력한다 — 설명·머리말·코드펜스 없이 [{"i": 0, "ko": "..."}, ...] 형식. '
    "i는 입력 번호 그대로, ko는 번역문(번역 불가면 null)이다. 입력 개수와 출력 개수가 같아야 한다."
)


def normalize(text: str) -> str:
    """캐시 키와 LLM 입력에 같이 쓰는 정규화. 엑셀에서 긁어온 원문은 줄바꿈·연속 공백·앞뒤 하이픈이
    제각각이라(같은 문장이 '- Increase mainly due to  $449K'와 'Increase mainly due to $449K'로 둘 다
    온다) 그대로 두면 같은 문장을 두 번 번역하고 두 결과가 달라진다."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _prompt(texts: list[str]) -> str:
    listed = "\n".join(f"{i}. {t}" for i, t in enumerate(texts))
    return f"{_RULES}\n\n{_OUTPUT_RULE}\n\n번역할 원문 {len(texts)}건:\n{listed}"


def _parse(raw: str, count: int) -> list[str | None]:
    """모델이 JSON 배열만 내놓으라는 말을 안 들을 때가 있어(앞뒤 설명·```json 펜스) 가장 바깥
    대괄호 구간만 떼어내 읽는다. 읽어낸 번호(i)만 채우고 나머지는 None으로 남긴다 — 일부만 돌아와도
    그 부분은 쓰고, 빠진 건 다음 호출에서 다시 물어본다."""
    out: list[str | None] = [None] * count
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < start:
        return out
    try:
        items = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return out
    if not isinstance(items, list):
        return out
    for pos, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        idx = item.get("i")
        idx = pos if not isinstance(idx, int) else idx
        ko = item.get("ko")
        if 0 <= idx < count and isinstance(ko, str) and ko.strip():
            out[idx] = normalize(ko)
    return out


def resolve_provider(provider: str | None) -> str:
    p = provider or settings.chat_provider
    if p != "gemini":
        raise HTTPException(status_code=400, detail=f"알 수 없는 provider: {p!r} (지금은 gemini만 가능)")
    return p


def engine_label(provider: str) -> str:
    """어느 모델이 번역했는지 행마다 남긴다 — 모델을 바꾼 뒤 '예전 번역이 왜 이 말투인지'를
    되짚을 때, 그리고 특정 모델이 낸 번역만 골라 다시 돌릴 때 필요하다."""
    # 2026-11-16: Gemini 하나만 쓴다(PLAN.md 질문 6). 되살릴 때 주석을 같이 푼다.
    model = {
        # "anthropic": settings.anthropic_model,
        "gemini": settings.gemini_model,
        # "ollama": settings.ollama_model,
    }[provider]
    return f"{provider}:{model}"[:40]


# Anthropic 호출 — 2026-11-16 주석 처리(공급자를 Gemini 하나로, PLAN.md 질문 6).
# async def _call_anthropic(prompt: str) -> str:
#     if not settings.anthropic_api_key:
#         raise HTTPException(status_code=400, detail="ANTHROPIC_API_KEY가 설정되지 않았습니다. backend/.env에 넣어주세요.")
#     client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
#     try:
#         resp = await client.messages.create(
#             model=settings.anthropic_model,
#             max_tokens=4096,
#             messages=[{"role": "user", "content": prompt}],
#         )
#     except anthropic.APIStatusError as e:
#         msg = (e.body or {}).get("error", {}).get("message") if isinstance(e.body, dict) else str(e)
#         raise HTTPException(status_code=502, detail=f"Anthropic API 오류: {msg or e}") from e
#     return "".join(b.text for b in resp.content if b.type == "text")
#
#
async def _call_gemini(prompt: str) -> str:
    if not settings.gemini_api_key:
        raise HTTPException(status_code=400, detail="GEMINI_API_KEY가 설정되지 않았습니다. backend/.env에 넣어주세요.")
    client = genai.Client(api_key=settings.gemini_api_key)
    try:
        resp = await client.aio.models.generate_content(
            model=settings.gemini_model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                thinking_config=genai_types.ThinkingConfig(thinking_level="low"),
                max_output_tokens=4096,
            ),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Gemini API 오류: {e}") from e
    parts = (resp.candidates[0].content.parts if resp.candidates else None) or []
    return "".join(p.text for p in parts if p.text and not p.thought)


# Ollama 호출 — 2026-11-16 주석 처리(공급자를 Gemini 하나로, PLAN.md 질문 6).
# async def _call_ollama(prompt: str) -> str:
#     try:
#         async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=180.0) as client:
#             resp = await client.post(
#                 "/api/chat",
#                 json={
#                     "model": settings.ollama_model,
#                     "stream": False,
#                     "messages": [{"role": "user", "content": prompt}],
#                 },
#             )
#             resp.raise_for_status()
#             data: dict[str, Any] = resp.json()
#     except httpx.ConnectError as e:
#         raise HTTPException(
#             status_code=502, detail="Ollama 서버에 연결할 수 없습니다. `ollama serve`가 떠 있는지 확인하세요."
#         ) from e
#     except httpx.HTTPStatusError as e:
#         raise HTTPException(status_code=502, detail=f"Ollama 오류: {e.response.text}") from e
#     return (data.get("message") or {}).get("content") or ""
#
#
async def translate_batch(texts: list[str], provider: str | None = None) -> list[str | None]:
    """정규화된 원문 리스트 → 같은 순서의 번역문 리스트(번역 못 한 건은 None).

    공급자 오류는 HTTPException으로 올려보낸다 — 번역이 안 되는 이유(키 없음·크레딧·연결 실패)를
    화면이 "번역 실패"로 뭉개면 담당자가 손쓸 수가 없다. 대신 호출부(엔드포인트)가 이걸 잡아서
    화면 전체를 죽이지 않고 원문을 그대로 보여주는 쪽으로 처리한다."""
    if not texts:
        return []
    p = resolve_provider(provider)
    prompt = _prompt(texts)
    raw = await _call_gemini(prompt)
    return _parse(raw, len(texts))


# ---------- 보고용 사유 초안 ----------
# 번역과 초안은 하는 일이 다르다. 번역은 연구소가 쓴 문장을 한국어로 옮기기만 하고(숫자를 그대로
# 두는 게 규칙이다), 초안은 거기에 화면이 계산한 증감액·증감률을 붙여 보고서에 실릴 한 줄로 만든다 —
# 연구소는 자기 통화로 "$449K 증가"라고 쓰지만 보고서에 필요한 건 "원화 12.3억원 증가"라서,
# 번역문만으로는 사유 칸이 채워지지 않는다.
#
# 숫자를 서버가 다시 계산하지 않고 받아 쓰는 이유: 화면이 이미 같은 표에 그 금액을 띄워 놨다.
# 여기서 또 계산하면 환율 기준이 조금만 달라도 사유 문장과 바로 옆 칸 숫자가 어긋난다.
_DRAFT_RULES = (
    "너는 반도체 해외연구소 비용 증감사유를 본사 보고용 한 줄로 정리한다.\n"
    "규칙:\n"
    "1. 보고용 명사형 간결체 한 줄로 쓴다 — '~했습니다'로 풀지 말고, 문장 끝에 마침표를 붙이지 마라.\n"
    "2. 주어진 증감액·증감률을 문장에 반드시 넣되, 준 숫자를 그대로 쓰고 다시 계산하거나 바꾸지 마라.\n"
    "3. 원인은 회신 원문에 적힌 것만 쓴다. 원문에 없는 원인·금액·추측을 덧붙이지 마라 — "
    "원문이 비어 있거나 원인을 알 수 없으면 원인 없이 증감 사실만 적어라.\n"
    "4. 원인이 둘 이상이면 ① ② ③으로 끊어 한 줄로 붙인다.\n"
    "5. 회신 원문의 금액 표기($449K 등)는 원인 설명 안에서는 그대로 두고, 전체 증감액은 준 원화 값을 쓴다.\n"
    "6. 고유명사(IRS, 401K, MBO, Tape-out, GPU 등)와 과제명·장비명은 원문 그대로 둔다.\n"
    "7. 한 줄(120자 이내)로 끝내라.\n"
    "용어는 아래 표를 따른다:\n" + "\n".join(f"- {en} → {ko}" for en, ko in GLOSSARY)
)

_DRAFT_OUTPUT_RULE = (
    '응답은 JSON 객체 하나만 출력한다 — 설명·머리말·코드펜스 없이 {"ko": "..."} 형식. '
    "ko는 사유 한 줄이고, 쓸 수 없으면 null이다."
)


def _amount_text(diff: float | None, diff_pct: float | None) -> str:
    """증감액(원)을 보고서가 쓰는 억원 표기로 바꾼 설명 줄. LLM이 단위를 환산하지 않게 미리 환산해서
    문장으로 넘긴다 — 숫자 환산을 모델에 맡기면 틀린다(실제로 K$를 억원으로 잘못 옮긴 적이 있다)."""
    if diff is None:
        return "증감액: 알 수 없음"
    direction = "증가" if diff > 0 else "감소" if diff < 0 else "동일"
    parts = [f"증감액: {abs(diff) / 1e8:,.1f}억원 {direction}"]
    if diff_pct is not None:
        parts.append(f"증감률: {diff_pct:+.1f}%")
    return " · ".join(parts)


def _draft_prompt(source_ko: str, source_text: str, diff: float | None, diff_pct: float | None) -> str:
    body = source_ko or source_text or ""
    return (
        f"{_DRAFT_RULES}\n\n{_DRAFT_OUTPUT_RULE}\n\n"
        f"회신 원문(번역): {body or '(없음)'}\n"
        f"회신 원문(원본): {source_text or '(없음)'}\n"
        f"{_amount_text(diff, diff_pct)}"
    )


def _parse_draft(raw: str) -> str | None:
    """초안은 1건이라 배열이 아니라 객체로 받는다. 모델이 그래도 설명을 붙여 보내는 경우가 있어
    가장 바깥 중괄호 구간만 떼어 읽는다(_parse와 같은 이유)."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        item = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    ko = item.get("ko") if isinstance(item, dict) else None
    return normalize(ko) if isinstance(ko, str) and ko.strip() else None


async def draft_reason(
    source_ko: str,
    source_text: str = "",
    diff: float | None = None,
    diff_pct: float | None = None,
    provider: str | None = None,
) -> str | None:
    """번역문 + 증감 숫자 → 보고용 사유 초안 한 줄(만들 수 없으면 None).

    초안은 캐시하지 않는다 — 같은 회신 원문이어도 증감액이 달라지면 문장이 달라져야 하고(재업로드로
    금액이 바뀌는 게 바로 그 경우다), 애초에 담당자가 고쳐서 쓰는 출발점이라 매번 같을 필요가 없다.
    번역(translate_batch)만 캐시하는 것과 구분해야 한다."""
    if not (source_ko or source_text):
        return None
    p = resolve_provider(provider)
    prompt = _draft_prompt(source_ko, source_text, diff, diff_pct)
    raw = await _call_gemini(prompt)
    return _parse_draft(raw)


# ---------- 회신 사유 묶음 요약 ----------
# 번역·초안과 하는 일이 또 다르다. 번역은 한 문장을 한국어로 옮기고, 초안(draft_reason)은 한 칸짜리
# 보고 문장을 만든다. 요약은 **여러 칸을 가로질러** 읽는다 — 연구소 하나가 보낸 회신은 계정 20여 개에
# 흩어져 있어서, 담당자는 표를 위아래로 훑으며 "그래서 이 연구소는 왜 늘었나"를 머릿속에서 모은다.
# 그 모으는 일을 대신한다(AI-07: 사유 해석 시간 단축).
#
# 요약은 캐시하지 않는다 — 캐시 키가 될 "원문 묶음"이 업로드·필터(기간·연구소)에 따라 매번 달라지고,
# 번역 캐시(원문 1건 = 번역 1개)에 섞으면 회신이 한 줄뿐인 연구소에서 같은 해시가 잡혀 번역 칸에
# 요약이 보이게 된다. 원문과 번역은 그대로 표에 남으니(공통 RULE: 원문 보관) 요약이 틀려도 되짚을 수 있다.
_SUMMARY_RULES = (
    "너는 반도체 해외연구소가 본사로 보낸 비용 증감사유(계정별 회신)를 담당자가 먼저 읽을 요약으로 정리한다.\n"
    "규칙:\n"
    "1. 3~5줄로 끝낸다. 각 줄은 '계정명: 원인(금액)' 꼴의 명사형 간결체로 쓰고, 문장 끝에 마침표를 붙이지 마라.\n"
    "2. 금액이 큰 계정, 또는 여러 계정에 걸쳐 같은 원인이 반복되는 것부터 앞에 놓는다.\n"
    "3. 같은 원인이 여러 계정에 걸쳐 있으면 한 줄로 묶어라 — 예: '인건비·복리후생: 신규 채용 3명 반영'.\n"
    "4. 금액·비율·인원은 원문 표기를 그대로 쓴다($449K는 $449K). 단위를 환산하거나 합산하지 마라 — "
    "여러 계정의 금액을 네가 더하면 환율이 섞여 화면 숫자와 어긋난다.\n"
    "5. 원문에 없는 원인·금액·추측을 덧붙이지 마라. 줄일 때는 빼는 쪽으로 줄여라.\n"
    "6. 증가/감소 방향은 반드시 보존한다.\n"
    "7. 고유명사(IRS, 401K, MBO, Tape-out, GPU 등)와 과제명·장비명은 원문 그대로 둔다.\n"
    "용어는 아래 표를 따른다:\n" + "\n".join(f"- {en} → {ko}" for en, ko in GLOSSARY)
)

_SUMMARY_OUTPUT_RULE = (
    '응답은 JSON 객체 하나만 출력한다 — 설명·머리말·코드펜스 없이 {"lines": ["...", "..."]} 형식. '
    "lines는 요약 줄의 배열(3~5개)이고, 요약할 내용이 없으면 빈 배열이다."
)


def _summary_prompt(items: list[tuple[str, str]], scope_label: str) -> str:
    listed = "\n".join(f"- {label}: {text}" for label, text in items)
    where = f"\n대상: {scope_label}" if scope_label else ""
    return f"{_SUMMARY_RULES}\n\n{_SUMMARY_OUTPUT_RULE}\n{where}\n회신 원문 {len(items)}건:\n{listed}"


def _parse_summary(raw: str) -> list[str]:
    """요약은 줄 배열 하나라 객체로 받는다(_parse_draft와 같은 이유로 바깥 중괄호 구간만 떼어 읽는다)."""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        return []
    try:
        item = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    lines = item.get("lines") if isinstance(item, dict) else None
    if not isinstance(lines, list):
        return []
    return [normalize(x) for x in lines if isinstance(x, str) and x.strip()]


async def summarize_reasons(
    items: list[tuple[str, str]], scope_label: str = "", provider: str | None = None
) -> list[str]:
    """(계정명, 회신 원문) 묶음 → 담당자가 먼저 읽을 요약 3~5줄(만들 수 없으면 빈 배열).

    원문을 그대로 넘긴다(번역문이 아니라) — 번역도 같은 모델이 하고, 중간에 한 번 더 옮기면
    그만큼 원문에서 멀어진다. 용어는 GLOSSARY로 번역과 같은 단어를 쓰게 맞춰 둔다."""
    items = [(normalize(label), normalize(text)) for label, text in items if normalize(text)]
    if not items:
        return []
    p = resolve_provider(provider)
    prompt = _summary_prompt(items, scope_label)
    raw = await _call_gemini(prompt)
    return _parse_summary(raw)
