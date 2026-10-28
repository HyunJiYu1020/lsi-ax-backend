"""해외연구소 CRUD API(app/api/v1/endpoints/overseas.py)를 MCP 도구로 감싸는 로컬(stdio) 서버.

백엔드는 CRUD만 맡고, 이 서버는 그 HTTP API를 호출하는 얇은 어댑터다.
실행: `uv run python -m app.mcp_server` (FastAPI 서버가 먼저 떠 있어야 한다).
"""

import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

API_BASE = os.environ.get("SLSI_API_BASE", "http://localhost:8000/api/v1")

mcp = MCPServer("slsi-overseas", instructions="S.LSI 해외연구소 예산 데이터를 조회·수정하는 도구 모음.")

# overseas.html의 OV_EL과 1:1로 맞춰야 한다 — 늘어나면 여기도 같이 고칠 것.
# hc(인력)만 단위가 명(名)이고 나머지는 전부 K$(천 달러)다.
COST_CATEGORY_KEYS = (
    "hc(인력, 단위 명) · sal(급여) · bonus(상여) · welf(복리후생) · edu(교육훈련비) · travel(출장비) · "
    "dep(감가상각비) · amort(무형자산상각) · rent(임차료) · repair(수선비) · ins(보험료) · "
    "supp(개발소모품비) · test(인증테스트비) · ord(경상연구비) · comm(지급수수료) · it(전산비) · "
    "cons(일반소모품) · util(수도광열비) · car(차량유지비) · tel(통신비) · pub(도서인쇄비) · oth(잡비) · "
    "conv(회의비) · event(행사비) · social(교제비) · misc(기타경비) · tax(조세공과) · nonop(영업외수지)"
)


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
        r = await client.get(path, params={k: v for k, v in (params or {}).items() if v is not None})
        r.raise_for_status()
        return r.json()


async def _put(path: str, body: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
        r = await client.put(path, json=body)
        r.raise_for_status()
        return r.json()


async def _post(path: str, body: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
        r = await client.post(path, json=body)
        r.raise_for_status()
        return r.json()


async def _delete(path: str, params: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(base_url=API_BASE, timeout=10.0) as client:
        r = await client.delete(path, params=params)
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def list_labs() -> Any:
    """해외연구소 13개(연구소·파트) 기준정보 목록을 반환한다."""
    return await _get("/overseas/labs")


@mcp.tool(
    description=(
        "비용 데이터를 조회한다. version: cur(당월 속보)|prev(전실행)|prior(전년 실적)|plan(경영계획). "
        f'lab_pk 예: "SARC:CPU", "SSIR". category_key(전체 목록, 헷갈리지 말 것): {COST_CATEGORY_KEYS}'
    )
)
async def list_cost_entries(
    version: str, lab_pk: str | None = None, category_key: str | None = None
) -> Any:
    return await _get(
        "/overseas/cost-entries", {"version": version, "lab_pk": lab_pk, "category_key": category_key}
    )


@mcp.tool(
    description=(
        "비용 데이터 한 칸(연구소×버전×계정×월)을 넣거나 덮어쓴다. amount 단위는 hc(인력)만 명(名)이고 "
        f"나머지는 전부 K$(천 달러)다. category_key(전체 목록, 헷갈리지 말 것): {COST_CATEGORY_KEYS}"
    )
)
async def upsert_cost_entry(
    lab_pk: str, version: str, category_key: str, month: int, amount: float
) -> Any:
    return await _put(
        "/overseas/cost-entries",
        {"lab_pk": lab_pk, "version": version, "category_key": category_key, "month": month, "amount": amount},
    )


@mcp.tool()
async def list_uploads(version: str | None = None, lab_pk: str | None = None) -> Any:
    """연구소별 파일 업로드 이력을 조회한다(최신순)."""
    return await _get("/overseas/uploads", {"version": version, "lab_pk": lab_pk})


@mcp.tool()
async def record_upload(lab_pk: str, version: str, filename: str) -> Any:
    """새 업로드 이력을 기록한다(이력은 추가만 되고 수정·삭제되지 않는다)."""
    return await _post("/overseas/uploads", {"lab_pk": lab_pk, "version": version, "filename": filename})


@mcp.tool()
async def list_notes(
    base_version: str | None = None, period: str | None = None, lab_scope: str | None = None
) -> Any:
    """증감분석 비고(사유)를 조회한다. base_version: prev|prior|plan. period: y(연간)|q1~q4|m1~m12.
    lab_scope: "ALL" 또는 특정 연구소 pk."""
    return await _get(
        "/overseas/notes", {"base_version": base_version, "period": period, "lab_scope": lab_scope}
    )


@mcp.tool()
async def upsert_note(
    base_version: str, period: str, lab_scope: str, category_key: str, note_text: str
) -> Any:
    """증감분석 비고(사유) 한 칸을 넣거나 덮어쓴다."""
    return await _put(
        "/overseas/notes",
        {
            "base_version": base_version,
            "period": period,
            "lab_scope": lab_scope,
            "category_key": category_key,
            "note_text": note_text,
        },
    )


@mcp.tool()
async def list_fx_rates(version: str | None = None) -> Any:
    """환율 데이터를 조회한다. currency: USD|ILS|INR. rate_type: end(월말)|act(실적)|cp(CP)."""
    return await _get("/overseas/fx-rates", {"version": version})


@mcp.tool()
async def upsert_fx_rate(version: str, currency: str, rate_type: str, month: int, value: float) -> Any:
    """환율 한 칸을 넣거나 덮어쓴다."""
    return await _put(
        "/overseas/fx-rates",
        {"version": version, "currency": currency, "rate_type": rate_type, "month": month, "value": value},
    )


@mcp.tool()
async def list_custom_rows(version: str | None = None, lab_pk: str | None = None) -> Any:
    """연구소가 편성 화면에서 직접 추가한 행(계정에 없는 비용 항목)을 조회한다. v는 12개월치 값(K$)."""
    return await _get("/overseas/custom-rows", {"version": version, "lab_pk": lab_pk})


@mcp.tool()
async def upsert_custom_row(lab_pk: str, version: str, row_key: str, label: str, v: list[float]) -> Any:
    """추가 행 하나를 넣거나 덮어쓴다(이름 + 12개월 값). row_key는 처음 만들 때는 새 id를 하나 지어서 쓰면 된다.
    이 값은 '개발비(A)' 합계에 자동으로 더해진다."""
    return await _put(
        "/overseas/custom-rows", {"lab_pk": lab_pk, "version": version, "row_key": row_key, "label": label, "v": v}
    )


@mcp.tool()
async def delete_custom_row(lab_pk: str, version: str, row_key: str) -> Any:
    """추가 행을 삭제한다(그 행에 달린 메모도 같이 지워진다)."""
    return await _delete("/overseas/custom-rows", {"lab_pk": lab_pk, "version": version, "row_key": row_key})


@mcp.tool()
async def list_memo_cols(version: str | None = None, lab_pk: str | None = None) -> Any:
    """편성 화면에 추가한 메모 전용 열 목록을 조회한다(집계·증감분석 계산에는 들어가지 않음)."""
    return await _get("/overseas/memo-cols", {"version": version, "lab_pk": lab_pk})


@mcp.tool()
async def upsert_memo_col(lab_pk: str, version: str, col_key: str, label: str) -> Any:
    """메모 열 하나를 넣거나 덮어쓴다. col_key는 처음 만들 때는 새 id를 하나 지어서 쓰면 된다."""
    return await _put("/overseas/memo-cols", {"lab_pk": lab_pk, "version": version, "col_key": col_key, "label": label})


@mcp.tool()
async def delete_memo_col(lab_pk: str, version: str, col_key: str) -> Any:
    """메모 열을 삭제한다(그 열의 메모 셀도 같이 지워진다)."""
    return await _delete("/overseas/memo-cols", {"lab_pk": lab_pk, "version": version, "col_key": col_key})


@mcp.tool()
async def list_memos(version: str | None = None, lab_pk: str | None = None) -> Any:
    """메모 셀(행×열 교차점의 자유 텍스트)을 조회한다. row_key는 고정 계정 key 또는 추가 행의 row_key."""
    return await _get("/overseas/memos", {"version": version, "lab_pk": lab_pk})


@mcp.tool()
async def upsert_memo(lab_pk: str, version: str, row_key: str, col_key: str, text: str) -> Any:
    """메모 셀 하나를 넣거나 덮어쓴다."""
    return await _put(
        "/overseas/memos", {"lab_pk": lab_pk, "version": version, "row_key": row_key, "col_key": col_key, "text": text}
    )


@mcp.tool()
async def get_step() -> Any:
    """해외연구소 편성 현재 진행 단계(0=취합, 1=집계, 2=증감분석)를 조회한다."""
    return await _get("/overseas/step")


@mcp.tool()
async def update_step(step: int) -> Any:
    """해외연구소 편성 진행 단계를 변경한다(0=취합, 1=집계, 2=증감분석)."""
    return await _put("/overseas/step", {"step": step})


@mcp.tool()
async def list_common_codes(group_code: str | None = None, is_active: bool | None = None) -> Any:
    """전사 공통코드를 조회한다. group_code 예: VERSION, FX_RATE_TYPE, CURRENCY. group_code 생략 시 전체."""
    return await _get("/common-codes", {"group_code": group_code, "is_active": is_active})


@mcp.tool()
async def upsert_common_code(
    group_code: str,
    code: str,
    name: str,
    parent_code: str | None = None,
    sort_order: int = 0,
    is_active: bool = True,
    description: str | None = None,
) -> Any:
    """공통코드 하나를 넣거나 덮어쓴다."""
    return await _put(
        "/common-codes",
        {
            "group_code": group_code,
            "code": code,
            "name": name,
            "parent_code": parent_code,
            "sort_order": sort_order,
            "is_active": is_active,
            "description": description,
        },
    )


@mcp.tool()
async def delete_common_code(group_code: str, code: str) -> Any:
    """공통코드 하나를 삭제한다."""
    return await _delete("/common-codes", {"group_code": group_code, "code": code})


@mcp.tool()
async def list_errors(status_code: int | None = None, path: str | None = None, limit: int = 100) -> Any:
    """API 서버에서 발생한 처리되지 않은 예외(500) 로그를 최신순으로 조회한다."""
    return await _get("/errors", {"status_code": status_code, "path": path, "limit": limit})


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
