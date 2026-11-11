"""해외연구소 CRUD API(app/api/v1/endpoints/overseas.py)를 MCP 도구로 감싸는 로컬(stdio) 서버.

백엔드는 CRUD만 맡고, 이 서버는 그 HTTP API를 호출하는 얇은 어댑터다.
실행: `uv run python -m app.mcp_server` (FastAPI 서버가 먼저 떠 있어야 한다).
"""

import asyncio
import os
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer

from app.core.cost_groups import (  # noqa: F401  (CATEGORY_LABELS는 chat.py가 여기서 import해 쓴다)
    CATEGORY_LABELS,
    COST_GROUP_DEFS,
    GROUP_CALC,
    resolve_leaves,
)

API_BASE = os.environ.get("SLSI_API_BASE", "http://localhost:8000/api/v1")

mcp = MCPServer("slsi-overseas", instructions="S.LSI 해외연구소 예산 데이터를 조회·수정하는 도구 모음.")

# 도구 호출마다 새 커넥션을 맺지 않도록 프로세스 생애 동안 하나만 재사용한다.
_client = httpx.AsyncClient(base_url=API_BASE, timeout=10.0)

# overseas.html의 OV_EL과 1:1로 맞춰야 한다 — 늘어나면 여기도 같이 고칠 것.
# hc(인력)만 단위가 명(名)이고 나머지는 전부 K$(천 달러)다.
COST_CATEGORY_KEYS = (
    "hc(인력, 단위 명) · sal(급여) · bonus(상여) · welf(복리후생) · edu(교육훈련비) · travel(출장비) · "
    "dep(감가상각비) · amort(무형자산상각) · rent(임차료) · repair(수선비) · ins(보험료) · "
    "supp(개발소모품비) · test(인증테스트비) · ord(경상연구비) · comm(지급수수료) · it(전산비) · "
    "cons(일반소모품) · util(수도광열비) · car(차량유지비) · tel(통신비) · pub(도서인쇄비) · oth(잡비) · "
    "conv(회의비) · event(행사비) · social(교제비) · misc(기타경비) · tax(조세공과) · nonop(영업외수지)"
)

# upsert_extra_row의 anchor_key 유효값 — core.js OV_EL의 키. COST_CATEGORY_KEYS(위)의 리프 계정
# 전부 + 그룹 헤더(hr·asset·direct·gen·mgr·itx, 그룹 전체 맨 뒤에 붙음) + IT/외주 전용 계정.
ANCHOR_KEYS = (
    COST_CATEGORY_KEYS + " · hr(인원성경비, 그룹) · asset(자산성경비, 그룹) · direct(직접개발비, 그룹) · "
    "gen(일반운영비, 그룹) · mgr(통제성경비, 그룹) · itx(IT 관련 비용, 그룹) · eda(EDA Tool) · "
    "emu(Emulator) · swl(SW/License구매) · svr(SVR/Storage임대) · itoth(IT 기타) · outs(외주용역비)"
)

# 계정 한글명·집계 그룹 정의는 app/core/cost_groups.py가 단일 원본이다(API의 파생 행/열 계산식
# 평가에서도 같은 정의가 필요해 공용 모듈로 옮겼다). chat.py가 CATEGORY_LABELS를 여기서 import해
# 쓰고 있어 이름을 그대로 재노출한다.
_resolve_leaves = resolve_leaves


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    r = await _client.get(path, params={k: v for k, v in (params or {}).items() if v is not None})
    r.raise_for_status()
    return r.json()


async def _put(path: str, body: dict[str, Any]) -> Any:
    # None은 "안 보냄"으로 취급 — 생략 가능한 파라미터(예: actual_thru_month)를 null로 그대로 보내면
    # 선택적이지 않은 스키마 필드(int) 검증에서 막힌다.
    r = await _client.put(path, json={k: v for k, v in body.items() if v is not None})
    r.raise_for_status()
    return r.json()


async def _post(path: str, body: dict[str, Any]) -> Any:
    r = await _client.post(path, json=body)
    r.raise_for_status()
    return r.json()


async def _delete(path: str, params: dict[str, Any]) -> Any:
    r = await _client.delete(path, params=params)
    r.raise_for_status()
    return r.json()


@mcp.tool()
async def list_labs() -> Any:
    """해외연구소(연구소·파트) 기준정보 목록을 반환한다. 연구소가 늘어나면 이 목록도 같이 늘어난다."""
    return await _get("/overseas/labs")


@mcp.tool(
    description=(
        "연구소 하나를 추가하거나 수정한다 — 다른 연구소는 전혀 건드리지 않는다(새 lab_code면 추가, "
        "이미 있으면 그 연구소만 수정). name·currency·is_active를 넣어라. lab_code가 이미 있는지 "
        "모르겠으면 먼저 list_labs로 확인해라(있으면 수정, 없으면 추가로 동작하니 미리 몰라도 안전하다).\n"
        "(헷갈리기 쉬움) 사용자가 '연구소'를 추가·수정해달라고 하면 표의 행(upsert_extra_row)이 아니라 "
        "이 도구다 — '베트남 연구소 추가해줘. 통화는 VND'처럼 통화나 사용 여부를 같이 말하면 확실히 "
        "이 도구다. 통화만 바꿔달라고 해도 이 도구를 쓴다(이름은 list_labs로 확인해서 그대로 넣어라). "
        "새 연구소인데 사용자가 코드를 말하지 않았으면 되묻지 말고 말한 이름에서 lab_code를 정해 "
        "제안해라(단, list_labs로 같은 연구소가 이미 있는지는 반드시 먼저 확인해라). "
        "그 연구소가 어떤 시트(비용·증감사유·외주용역)를 내는지 바꾸는 건 이 도구가 아니라 "
        "upsert_lab_sheet/delete_lab_sheet다."
    )
)
async def upsert_lab(lab_code: str, name: str, currency: str, is_active: bool = True) -> Any:
    return await _put(
        "/overseas/labs/one", {"lab_code": lab_code, "name": name, "currency": currency, "is_active": is_active}
    )


@mcp.tool()
async def delete_lab(lab_code: str) -> Any:
    """연구소 하나를 삭제한다(다른 연구소는 안 건드림). 그 연구소의 비용 데이터는 안 지워지고 집계에서만 빠진다."""
    return await _delete("/overseas/labs/one", {"lab_code": lab_code})


@mcp.tool()
async def list_lab_sheets(lab_code: str | None = None) -> Any:
    """연구소(lab_code)별 워크북 시트 구성(비용|증감분석|외주용역업체)을 조회한다.
    대부분 3개 시트지만 SARC(+ACL)는 비용 시트만 있다. lab_code 생략 시 전체."""
    return await _get("/overseas/lab-sheets", {"lab_code": lab_code})


@mcp.tool(
    description=(
        "연구소(lab_code)에 시트 하나를 배정한다(업로드 양식에 새 시트 종류를 추가할 때 씀). "
        "sheet_key가 아직 없는 새 종류(예: '인건비')면 먼저 upsert_common_code"
        "(group_code='OVERSEAS_SHEET_TYPE', code=sheet_key, name=표시할 한글 이름)로 그 시트 종류를 "
        "등록한 다음 이 도구를 불러야 한다 — 등록 안 된 sheet_key를 넣으면 거부된다. "
        "이 도구는 '그 연구소가 이 시트를 쓴다'는 배정만 하는 것이고, 실제 시트 내용(행)은 "
        "list_sheet_rows/upsert_sheet_row로 따로 채운다."
    )
)
async def upsert_lab_sheet(lab_code: str, sheet_key: str, sheet_order: int = 0) -> Any:
    return await _put("/overseas/lab-sheets", {"lab_code": lab_code, "sheet_key": sheet_key, "sheet_order": sheet_order})


@mcp.tool()
async def delete_lab_sheet(lab_code: str, sheet_key: str) -> Any:
    """연구소에서 시트 배정을 뗀다(그 시트에 이미 채운 행 데이터는 지워지지 않고 남는다)."""
    return await _delete("/overseas/lab-sheets", {"lab_code": lab_code, "sheet_key": sheet_key})


def _compute_stats(months: dict[str, float]) -> dict[str, Any]:
    """월별 금액에서 sum/avg/max/min/전월대비 증감률을 직접 계산해 넣는다 — LLM이 월별 숫자를 직접
    더하거나 어느 게 최댓값인지 훑다가 생기는 오차를 원천적으로 없애려는 목적."""
    periods = sorted(int(p) for p in months)
    vals = [months[str(p)] for p in periods]
    stats: dict[str, Any] = {
        "sum": round(sum(vals), 2),
        "avg": round(sum(vals) / len(vals), 2) if vals else 0.0,
    }
    if periods:
        max_p = max(periods, key=lambda p: months[str(p)])
        min_p = min(periods, key=lambda p: months[str(p)])
        stats["max"] = {"month": max_p, "amount": months[str(max_p)]}
        stats["min"] = {"month": min_p, "amount": months[str(min_p)]}
    mom: dict[str, float] = {}
    for prev_p, cur_p in zip(periods, periods[1:]):
        prev_v, cur_v = months[str(prev_p)], months[str(cur_p)]
        if prev_v != 0:
            mom[str(cur_p)] = round((cur_v - prev_v) / abs(prev_v) * 100, 2)
    stats["mom_pct_change"] = mom  # 그 달이 "전월 대비 몇 % 증감"인지, period(월) -> % 로
    return stats


def _summarize_cost_entries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """월별 12행을 (lab_pk, version, category_key, yyyymm) 묶음 1행으로 접어 통계를 계산한다. 이 한
    헤더(연구소×버전×계정×연월) 안에서 끝나는 통계는 여기서 다 계산해주고, 헤더를 넘나드는 비교(다른
    연구소·다른 연월과 비교 등)만 calculate 도구로 넘기면 된다."""
    groups: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for r in rows:
        key = (r["lab_pk"], r["version"], r["category_key"], r["yyyymm"])
        g = groups.setdefault(
            key,
            {
                "lab_pk": r["lab_pk"], "version": r["version"], "category_key": r["category_key"],
                "yyyymm": r["yyyymm"], "year": r["year"], "months": {},
            },
        )
        g["months"][str(r["month"])] = r["amount"]
    out = []
    for g in groups.values():
        g.update(_compute_stats(g["months"]))
        # 실제 실행되는 ORM 쿼리(overseas.py list_cost_entries)를 사람이 읽을 수 있는 SQL로 그대로
        # 보여준다 — 근거 투명성 요청에 따른 것. 이 그룹의 구체적인 scope/category/yyyymm/version 값을
        # 그대로 꽂아 넣는다(바인드 변수 대신 리터럴로 보여줘야 "무슨 값으로 조회했는지"가 한눈에 보인다).
        g["query"] = (
            "SELECT ced.period, ced.amount\n"
            "FROM cost_entries ce JOIN cost_entry_details ced ON ced.cost_entry_id = ce.id\n"
            f"WHERE ce.account_code = 'overseas' AND ce.scope_code = '{g['lab_pk']}'\n"
            f"  AND ce.version = '{g['version']}' AND ce.category_code = '{g['category_key']}'\n"
            f"  AND ce.yyyymm = '{g['yyyymm']}' AND ced.period_type = 'MONTH'\n"
            "ORDER BY ced.period;"
        )
        out.append(g)
    return out


@mcp.tool(
    description=(
        "비용 데이터를 조회한다. version: forecast(속보)|actual(실적). "
        'yyyymm: 이 비용 헤더가 편성된 기준 연월 "YYYYMM"(필수, 예: "202609"). '
        f'lab_pk 예: "SARC:CPU", "SSIR". category_key(전체 목록, 헷갈리지 말 것): {COST_CATEGORY_KEYS}. '
        "주의: forecast와 actual은 서로 다른 헤더라 같은 yyyymm이라도 따로 조회해야 한다 — forecast는 "
        "그 시점에 전망한 1~12월 전체, actual은 그때까지 마감 확정된 달만 있다. "
        "결과의 'sum'(합계)·'avg'(월평균)·'max'/'min'({month, amount})·'mom_pct_change'(월:전월대비 "
        "%, 그 헤더 안의 통계)는 서버가 이미 계산해서 넣은 값이다 — months 안의 숫자들을 직접 다시 "
        "더하거나, 어느 게 최댓값인지 비교하거나, 증감률을 계산하지 말고 이 필드들을 그대로 써라(직접 "
        "계산하면 틀리기 쉽다). 이 헤더를 넘어서는 비교(다른 연구소·다른 연월·다른 계정과 비교)만 "
        "calculate 도구로 계산해라. "
        "주의: '개발비'·'인건비' 등 그리드 '구분'에 보이는 집계 행은 category_key 하나로 바로 조회되지 "
        "않는다 — 여러 리프 계정의 합이다. 이런 집계는 여기서 직접 더하지 말고 반드시 get_cost_group "
        "도구를 써라(리프를 빠뜨리거나 잘못 더할 위험이 없게 서버가 계산해서 돌려준다)."
    )
)
async def list_cost_entries(
    yyyymm: str,
    version: str,
    lab_pk: str | None = None,
    category_key: str | None = None,
) -> Any:
    rows = await _get(
        "/overseas/cost-entries",
        {"yyyymm": yyyymm, "version": version, "lab_pk": lab_pk, "category_key": category_key},
    )
    return _summarize_cost_entries(rows)


@mcp.tool(
    description=(
        "'개발비'·'인건비' 등 그리드 '구분'에 보이는 집계 행(여러 리프 계정의 합) 하나를 조회한다. "
        f"group_key 유효값과 정의: {COST_GROUP_DEFS}. 리프 계정을 직접 list_cost_entries로 일일이 "
        "조회해서 손으로 더하지 마라(리프를 빠뜨리거나 계산이 틀리기 쉽다) — 이 도구가 내부적으로 "
        "모든 리프를 가져와 정확히 더해서 돌려준다. 반환값은 list_cost_entries와 같은 모양"
        "(months·sum·avg·max·min·mom_pct_change)이며 이 필드들도 서버가 이미 계산한 값이니 그대로 "
        "써라. '인당 개발비'처럼 다른 집계(예: hc)로 나눠야 하면, 이 도구로 분자를 구하고 "
        "list_cost_entries(category_key='hc')로 분모를 구한 뒤 combine_series(op=div_elementwise)로 "
        "나눠라(직접 나누지 마라)."
    )
)
async def get_cost_group(group_key: str, yyyymm: str, version: str, lab_pk: str | None = None) -> Any:
    if group_key not in GROUP_CALC:
        return {"error": f"알 수 없는 group_key: {group_key!r} (유효값: {', '.join(GROUP_CALC)})"}
    leaves = set(_resolve_leaves(group_key))
    rows = await _get("/overseas/cost-entries", {"yyyymm": yyyymm, "version": version, "lab_pk": lab_pk})
    months: dict[str, float] = {}
    for r in rows:
        if r["category_key"] not in leaves:
            continue
        key = str(r["month"])
        months[key] = round(months.get(key, 0.0) + r["amount"], 2)
    result: dict[str, Any] = {
        "lab_pk": lab_pk, "version": version, "group_key": group_key, "yyyymm": yyyymm, "months": months,
        "leaves_summed": sorted(leaves),
    }
    result.update(_compute_stats(months))
    return result


async def _lab_series(yyyymm: str, version: str, lab_pk: str, category_key: str | None, group_key: str | None) -> dict[str, Any]:
    if group_key:
        leaves = set(_resolve_leaves(group_key))
        rows = await _get("/overseas/cost-entries", {"yyyymm": yyyymm, "version": version, "lab_pk": lab_pk})
        months: dict[str, float] = {}
        for r in rows:
            if r["category_key"] not in leaves:
                continue
            key = str(r["month"])
            months[key] = round(months.get(key, 0.0) + r["amount"], 2)
    else:
        rows = await _get(
            "/overseas/cost-entries",
            {"yyyymm": yyyymm, "version": version, "lab_pk": lab_pk, "category_key": category_key},
        )
        months = {str(r["month"]): r["amount"] for r in rows}
    result: dict[str, Any] = {"lab_pk": lab_pk, "months": months}
    result.update(_compute_stats(months))
    return result


@mcp.tool(
    description=(
        "여러 연구소의 같은 계정(category_key)·집계(group_key)를 한 번에 비교한다. "
        "'연구소별로', '전체 연구소', '연구소마다' 같은 요청은 list_cost_entries/get_cost_group을 "
        "연구소 수만큼 따로따로 부르지 말고 반드시 이 도구 하나로 받아라 — 연구소가 10곳 넘는 상태에서 "
        "하나씩 따로 부르면 도구 호출 한도에 걸려 응답이 중간에 끊기거나, 모델이 나머지 연구소 값을 "
        "실제로 조회하지 않고 지어내서(예: 전부 0으로) 답하는 사고로 이어진다(실제로 발생한 사례). "
        "category_key(리프 계정)·group_key(집계 행, 예: outs처럼 하위가 없으면 category_key) 중 "
        f"정확히 하나만 넣어라. group_key 유효값: {', '.join(GROUP_CALC)}. lab_pks를 생략하면 "
        "list_labs의 활성 연구소 전체를 대상으로 한다. 반환은 연구소당 1개씩 담긴 리스트고, 각 항목은 "
        "list_cost_entries와 같은 모양(months·sum·avg·max·min·mom_pct_change, 서버가 이미 계산한 값)"
        "이다 — 숫자를 다시 더하거나 비교하지 말고 그대로 써라. 어떤 연구소에 그 yyyymm·version 데이터가 "
        "아예 없으면 months가 빈 배열(sum=avg=0)로 돌아오는데, 이건 '그 연구소는 데이터가 없다'는 "
        "뜻이지 조회를 안 한 게 아니다 — 돌려받지 못한 연구소가 있을 수는 없으니(이 도구는 넣은 "
        "lab_pks 전부를 항상 다 조회해서 돌려준다), 반환된 리스트에 없는 연구소를 네가 따로 0으로 "
        "지어내 추가하지 마라."
    )
)
async def list_cost_across_labs(
    yyyymm: str, version: str, category_key: str | None = None, group_key: str | None = None,
    lab_pks: list[str] | None = None,
) -> Any:
    if bool(category_key) == bool(group_key):
        return {"error": "category_key와 group_key 중 정확히 하나만 넣어라"}
    if group_key and group_key not in GROUP_CALC:
        return {"error": f"알 수 없는 group_key: {group_key!r} (유효값: {', '.join(GROUP_CALC)})"}
    if not lab_pks:
        labs = await _get("/overseas/labs")
        # 합계 행(part_code="TOTAL", 예: SARC:TOTAL)은 제외한다 — 파트(SARC:CPU·SARC:GPU)와 같이
        # 돌려주면 "연구소별 합계"를 낼 때 그 연구소가 두 번 세어진다(SARC가 CPU+GPU+TOTAL로 3중 집계).
        # 특정 연구소를 보려고 lab_pks를 직접 넣은 경우에는 넣은 그대로 조회한다.
        lab_pks = [
            lab["pk"] for lab in labs
            if lab.get("is_active", True) and (lab.get("part_code") or "").upper() != "TOTAL"
        ]
    return await asyncio.gather(*(_lab_series(yyyymm, version, pk, category_key, group_key) for pk in lab_pks))


@mcp.tool(
    description=(
        "비용 데이터 한 칸(연구소×버전×계정×연월×월)을 넣거나 덮어쓴다. amount 단위는 hc(인력)만 명(名)이고 "
        f"나머지는 전부 K$(천 달러)다. category_key(전체 목록, 헷갈리지 말 것): {COST_CATEGORY_KEYS}. "
        'yyyymm은 이 헤더의 편성 기준 연월 "YYYYMM"(예: "202609"). month는 보통 1~12(그 연도 1~12월)이지만, '
        "기간을 12개월 너머로 늘려 받는 경우엔 13부터 그다음 연도 1월(13=다음해 1월, 24=다음해 12월), "
        "25부터 또 그다음 연도로 — 12개씩 다음 해로 이어진다."
    )
)
async def upsert_cost_entry(
    lab_pk: str, version: str, category_key: str, yyyymm: str, month: int, amount: float
) -> Any:
    return await _put(
        "/overseas/cost-entries",
        {
            "lab_pk": lab_pk,
            "version": version,
            "category_key": category_key,
            "yyyymm": yyyymm,
            "month": month,
            "amount": amount,
        },
    )


@mcp.tool()
async def list_uploads(version: str | None = None, lab_pk: str | None = None) -> Any:
    """연구소별 파일 업로드 이력을 조회한다(최신순). version: cur(당월 속보)|prev(전실행)|prior(전년 실적)|
    plan(경영계획) — list_cost_entries 등과 달리 forecast/actual이 아니라 화면이 보는 그대로의 코드를
    쓴다(list_memo_cols와 동일). version을 생략하면 전체 버전을 다 가져온다."""
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


@mcp.tool(
    description=(
        "연구소가 편성 화면에서 직접 추가한 행(계정에 없는 비용 항목)을 조회한다. "
        'yyyymm: v(12개월치 값, K$)가 속한 편성 기준 연월 "YYYYMM"(필수, 예: "202609"). '
        "version: forecast(속보)|actual(실적) — '현재 화면' 정보에 나온 값을 그대로 써라."
    )
)
async def list_extra_rows(
    yyyymm: str, version: str | None = None, lab_pk: str | None = None
) -> Any:
    rows = await _get(
        "/overseas/extra-rows",
        {"yyyymm": yyyymm, "version": version, "lab_pk": lab_pk},
    )
    # label_en은 커스텀 행에 영문명을 안 붙이면 항상 빈 문자열이라 LLM에 보여줄 필요가 없다.
    return [{k: v for k, v in row.items() if k != "label_en" or v} for row in rows]


@mcp.tool(
    description=(
        "추가 행 하나를 넣거나 덮어쓴다. 행은 두 종류이고, 어느 쪽이냐에 따라 넣는 값이 다르다.\n"
        "① 원시 행(is_derived=False, 기본): 연구소가 실제로 보고한 비용. v에 월별 값을 넣는다"
        "(보통 12개월, 기간을 12개월 너머로 늘려 받는 경우엔 12의 배수(24·36…)도 된다). "
        "이 값은 '개발비(A)' 합계에 자동으로 더해진다.\n"
        "② 파생 행(is_derived=True): 다른 계정을 합산·비율 계산해서 만든 값(예: '인당인건비' = "
        "인건비 ÷ 인력). v를 넣지 말고 formula(계산식)를 넣어라 — 서버가 조회할 때마다 그 계산식을 "
        "지금 저장된 원본 값으로 다시 계산한다. 그래서 나중에 인건비가 업로드로 바뀌면 이 행도 "
        "자동으로 따라 바뀐다(네가 숫자를 미리 계산해서 v에 박아 넣으면 그 숫자는 곧 낡아버리니 "
        "그렇게 하지 마라 — combine_series로 값을 구해서 v에 넣는 방식은 파생 행에는 쓰지 않는다). "
        'formula 모양: {"op":"div_elementwise","operands":["labor","hc"]} — operands는 계정 키 목록이고 '
        "월별로(같은 달끼리) 계산한다. op: sum_elementwise(2개 이상을 더함) | div_elementwise"
        "(정확히 2개, [분자, 분모]). operands에는 집계 그룹 키(labor=인건비, A=개발비 등)도 쓸 수 있고 "
        "서버가 리프까지 알아서 전개한다. 파생 행은 '개발비(A)' 합계에 더해지지 않는다. "
        "is_derived=True는 사용자가 지금 '집계' 탭을 보고 있을 때만 받아준다 — 다른 탭이면 거부되니"
        "(단계를 네가 대신 바꾸지 마라) 거부 사유를 그대로 사용자에게 설명해라.\n"
        "저장하면 응답의 basis에 '인건비 1,000 ÷ 인력 10 = 100' 식의 근거가 월별·연간으로 들어온다 — "
        "사용자에게 이 행을 설명할 때 그 값을 근거로 그대로 보여줘라.\n"
        "(헷갈리기 쉬움) 이 도구는 표 안의 '행'을 만드는 것이다 — 사용자가 '연구소'를 추가·수정· "
        "삭제해달라고 한 거면 이 도구가 아니라 upsert_lab/delete_lab이다.\n"
        "row_key는 처음 만들 때는 새 id를 하나 지어서 쓰면 된다. "
        'yyyymm: "현재 화면" 정보의 편성 기준 연월 "YYYYMM"을 그대로 써라. '
        f"anchor_key: 사용자가 '~계정 아래에'라고 말하면 표에서 그 계정 바로 뒤에 끼워 넣는다 — 유효값: {ANCHOR_KEYS}. "
        "생략하면(기본값) 표 맨 끝에 붙는다."
    )
)
async def upsert_extra_row(
    lab_pk: str, version: str, row_key: str, label: str, yyyymm: str, v: list[float] | None = None,
    anchor_key: str | None = None, is_derived: bool = False, formula: dict[str, Any] | None = None,
) -> Any:
    return await _put(
        "/overseas/extra-rows",
        {
            "lab_pk": lab_pk, "version": version, "row_key": row_key, "label": label, "yyyymm": yyyymm,
            "v": v if v is not None else [], "anchor_key": anchor_key, "is_derived": is_derived, "formula": formula,
        },
    )


@mcp.tool(
    description=(
        "추가 행을 삭제한다(그 행에 달린 메모도 같이 지워진다). 원시 행은 '취합' 탭에서만, 파생 행은 "
        "'집계' 탭에서만 지울 수 있다 — 지금 탭이 맞지 않으면 거부되니, 거부 사유를 그대로 사용자에게 "
        "설명하고 해당 탭으로 이동한 뒤 다시 요청해달라고 해라(단계를 네가 대신 바꾸지 마라)."
    )
)
async def delete_extra_row(
    lab_pk: str, version: str, row_key: str, yyyymm: str, step: str | None = None
) -> Any:
    # step(지금 보고 있는 탭)은 네가 채우지 않아도 된다 — 서버가 화면 정보에서 알아서 채운다.
    return await _delete(
        "/overseas/extra-rows",
        {"lab_pk": lab_pk, "version": version, "row_key": row_key, "yyyymm": yyyymm, "step": step},
    )


@mcp.tool(
    description=(
        "연구소가 편성 화면 그리드에 직접 추가한 열(행×월이 아니라 행×열 구조의 숫자 값. 화면의 "
        "'+ 열 추가' 버튼과 같은 기능)을 조회한다. label 자체가 열 식별자다(별도 id 없음). "
        'yyyymm: "YYYYMM" 필수. version: forecast(속보)|actual(실적) — "현재 화면" 정보의 '
        "version_api 값을 그대로 써라."
    )
)
async def list_extra_cols(yyyymm: str, version: str | None = None, lab_pk: str | None = None) -> Any:
    return await _get("/overseas/extra-cols", {"yyyymm": yyyymm, "version": version, "lab_pk": lab_pk})


@mcp.tool(
    description=(
        "추가 열의 칸(행×열) 값을 조회한다. row_key가 그 칸이 속한 행(고정 계정 key 또는 "
        "upsert_extra_row로 만든 추가 행의 row_key), label이 열 이름이다. yyyymm/version은 "
        "list_extra_cols와 동일."
    )
)
async def list_cost_extras(yyyymm: str, version: str | None = None, lab_pk: str | None = None) -> Any:
    return await _get("/overseas/cost-extras", {"yyyymm": yyyymm, "version": version, "lab_pk": lab_pk})


@mcp.tool(
    description=(
        "그리드에 열 하나를 만들거나 고친다 — 화면의 '+ 열 추가' 버튼과 같은 결과를 만드는 도구다"
        "(이걸로 만든 열만 그리드에 실제로 보인다 — upsert_memo_col은 메모 전용 열이라 그리드에는 "
        "안 보이니, '표에 열을 추가해달라'는 요청에는 이 도구를 써라). 열도 두 종류다.\n"
        "① 원시 열(is_derived=False, 기본): 사람이 칸마다 값을 직접 넣는 열. 이 도구로 열만 만들고, "
        "칸 값은 upsert_cost_extra로 따로 넣는다.\n"
        "② 파생 열(is_derived=True): 월을 가로로 묶어 계산하는 열(예: '3분기 합계' = 7+8+9월). "
        'formula를 넣어라 — {"op":"sum_months","months":[7,8,9]}. op: sum_months(그 달들을 더함) | '
        "avg_months(그 달들의 평균). 칸 값은 저장하지 않고 각 행의 월별 값에서 조회할 때마다 다시 "
        "계산하므로, 원본이 업로드로 바뀌면 자동으로 따라 바뀐다(네가 미리 더한 숫자를 넣지 마라). "
        "파생 열에는 upsert_cost_extra로 칸 값을 직접 넣을 수 없다(거부된다). is_derived=True는 "
        "사용자가 '집계' 탭을 보고 있을 때만 받아준다.\n"
        "label이 열 이름이자 식별자라 이름 자체를 바꾸는 기능은 없다(바꾸려면 delete_extra_col로 옛 "
        '이름을 지우고 새 이름으로 다시 추가). yyyymm: "현재 화면" 정보의 yyyymm_api 값을 그대로 써라.'
    )
)
async def upsert_extra_col(
    lab_pk: str, version: str, yyyymm: str, label: str,
    is_derived: bool = False, formula: dict[str, Any] | None = None,
) -> Any:
    return await _put(
        "/overseas/extra-cols",
        {
            "lab_pk": lab_pk, "version": version, "yyyymm": yyyymm, "label": label,
            "is_derived": is_derived, "formula": formula,
        },
    )


@mcp.tool(
    description=(
        "원시 열의 칸(행×열) 하나에 값을 넣거나 덮어쓴다. 열이 아직 없으면 원시 열로 자동 등록된다"
        "(그래서 '빈 열 하나만 만들어줘'도 이 도구로 되지만, 어느 행에 넣을지 정해지지 않았으면 "
        "upsert_extra_col로 열만 만드는 게 더 깔끔하다). "
        f"row_key: 값을 넣을 행(고정 계정 key — 유효값: {ANCHOR_KEYS} — 또는 custom row_key). "
        "생략하면 'hc'(인력) 행에 들어간다. 파생 열에는 쓸 수 없다(계산식 결과와 수동 입력이 섞이면 "
        '어느 쪽이 참인지 알 수 없어져서 거부된다). yyyymm: "현재 화면" 정보의 yyyymm_api 값을 쓴다.'
    )
)
async def upsert_cost_extra(
    lab_pk: str, version: str, yyyymm: str, label: str, row_key: str = "hc", value: float = 0
) -> Any:
    return await _put(
        "/overseas/cost-extras",
        {"lab_pk": lab_pk, "version": version, "row_key": row_key, "yyyymm": yyyymm, "label": label, "value": value},
    )


@mcp.tool(
    description=(
        "추가 열을 삭제한다 — 그 열(label)이 달린 모든 행의 칸을 한꺼번에 지운다(열 자체가 없어진다). "
        "원시 열은 '취합' 탭에서만, 파생 열은 '집계' 탭에서만 지울 수 있다 — 지금 탭이 맞지 않으면 "
        "거부되니 거부 사유를 그대로 사용자에게 설명해라(단계를 네가 대신 바꾸지 마라)."
    )
)
async def delete_extra_col(
    lab_pk: str, version: str, label: str, yyyymm: str, step: str | None = None
) -> Any:
    # step(지금 보고 있는 탭)은 네가 채우지 않아도 된다 — 서버가 화면 정보에서 알아서 채운다.
    return await _delete(
        "/overseas/extra-cols",
        {"lab_pk": lab_pk, "version": version, "label": label, "yyyymm": yyyymm, "step": step},
    )


@mcp.tool()
async def list_memo_cols(version: str | None = None, lab_pk: str | None = None) -> Any:
    """편성 화면에 추가한 메모 전용 열 목록을 조회한다(집계·증감분석 계산에는 들어가지 않음).
    version: cur(당월 속보)|prev(전실행)|prior(전년 실적)|plan(경영계획) — list_cost_entries 등과
    달리 forecast/actual이 아니라 화면이 보는 그대로의 코드를 쓴다."""
    return await _get("/overseas/memo-cols", {"version": version, "lab_pk": lab_pk})


@mcp.tool()
async def upsert_memo_col(lab_pk: str, version: str, col_key: str, label: str) -> Any:
    """메모 열 하나를 넣거나 덮어쓴다. col_key는 처음 만들 때는 새 id를 하나 지어서 쓰면 된다.
    version: cur|prev|prior|plan(list_memo_cols와 동일)."""
    return await _put("/overseas/memo-cols", {"lab_pk": lab_pk, "version": version, "col_key": col_key, "label": label})


@mcp.tool()
async def delete_memo_col(lab_pk: str, version: str, col_key: str) -> Any:
    """메모 열을 삭제한다(그 열의 메모 셀도 같이 지워진다). version: cur|prev|prior|plan."""
    return await _delete("/overseas/memo-cols", {"lab_pk": lab_pk, "version": version, "col_key": col_key})


@mcp.tool()
async def list_memos(version: str | None = None, lab_pk: str | None = None) -> Any:
    """메모 셀(행×열 교차점의 자유 텍스트)을 조회한다. row_key는 고정 계정 key 또는 추가 행의 row_key.
    version: cur|prev|prior|plan(list_memo_cols와 동일)."""
    return await _get("/overseas/memos", {"version": version, "lab_pk": lab_pk})


@mcp.tool()
async def upsert_memo(lab_pk: str, version: str, row_key: str, col_key: str, text: str) -> Any:
    """메모 셀 하나를 넣거나 덮어쓴다. version: cur|prev|prior|plan(list_memo_cols와 동일)."""
    return await _put(
        "/overseas/memos", {"lab_pk": lab_pk, "version": version, "row_key": row_key, "col_key": col_key, "text": text}
    )


@mcp.tool()
async def list_vendor_rows(lab_code: str | None = None, version: str = "cur") -> Any:
    """외주용역 상세(업체·과제별 월 금액)를 조회한다. 연구소 단위 시트라 lab_code는 파트 없는
    연구소 코드("SARC")를 쓴다. m은 1~12월 금액 배열이고, 비용 시트의 외주용역비(outs)와 합계가
    맞아야 한다(화면의 대사). version: cur|prev|prior|plan."""
    return await _get("/overseas/vendor-rows", {"lab_code": lab_code, "version": version})


@mcp.tool()
async def list_sheet_rows(version: str | None = None, lab_pk: str | None = None, sheet_key: str | None = None) -> Any:
    """구조가 아직 확정되지 않은 새 시트의 원본 행을 조회한다. data는 그 시트의 헤더열 이름 -> 값으로
    이루어진 자유 형식 JSON이다. 외주용역은 열이 확정돼 전용 테이블로 옮겼으니 list_vendor_rows를 쓸 것."""
    return await _get("/overseas/sheet-rows", {"version": version, "lab_pk": lab_pk, "sheet_key": sheet_key})


@mcp.tool()
async def upsert_sheet_row(
    lab_pk: str, version: str, sheet_key: str, row_key: str, data: dict[str, Any], sort_order: int = 0
) -> Any:
    """구조 미정 시트의 행 하나를 넣거나 덮어쓴다.
    row_key는 처음 만들 때는 새 id(업로드 시 행 순서 등)를 지어서 쓰면 된다."""
    return await _put(
        "/overseas/sheet-rows",
        {"lab_pk": lab_pk, "version": version, "sheet_key": sheet_key, "row_key": row_key, "sort_order": sort_order, "data": data},
    )


@mcp.tool()
async def delete_sheet_row(lab_pk: str, version: str, sheet_key: str, row_key: str) -> Any:
    """구조 미정 시트의 행 하나를 삭제한다."""
    return await _delete(
        "/overseas/sheet-rows", {"lab_pk": lab_pk, "version": version, "sheet_key": sheet_key, "row_key": row_key}
    )


@mcp.tool()
async def get_step() -> Any:
    """해외연구소 편성 현재 진행 단계(1=취합, 2=집계, 3=증감분석)를 조회한다."""
    return await _get("/overseas/step")


@mcp.tool()
async def update_step(step: int) -> Any:
    """해외연구소 편성 진행 단계를 변경한다(1=취합, 2=집계, 3=증감분석)."""
    return await _put("/overseas/step", {"step": step})


@mcp.tool()
async def list_common_codes(group_code: str | None = None, is_active: bool | None = None) -> Any:
    """전사 공통코드를 조회한다. group_code 예: ACCOUNT_STATUS, VERSION_STATUS, OVERSEAS_CATEGORY,
    OVERSEAS_SHEET_TYPE, OVERSEAS_FX_RULE. group_code 생략 시 전체."""
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


@mcp.tool(
    description=(
        "특정 기준(basis_year·basis_month·basis_kind·rule)의 월별(1~12) 적용 환율을 조회한다. "
        "rule: avg(월평균 환율 적용, 기본값) | last(말일 환율 적용). basis_kind: forecast(속보)|actual(실적)."
    )
)
async def get_applied_fx_rates(basis_year: int, basis_month: int, basis_kind: str = "forecast", rule: str = "avg") -> Any:
    return await _get(
        "/fx/applied",
        {"basis_year": basis_year, "basis_month": basis_month, "basis_kind": basis_kind, "rule": rule},
    )


@mcp.tool(
    description=(
        "해외연구소 실적 구간 환산 기준(말일 vs 월평균)의 현재 상태를 조회한다. 'default'는 예외가 "
        "없는 모든 달이 쓰는 기본값(avg|last)이고, 'overrides'는 그 달만 기본값과 다르게 쓰기로 한 "
        "월 단위 예외 목록(year_month·rule)이다 — 어떤 달의 실제 기준은 '그 달 예외가 있으면 그 rule, "
        "없으면 default'다. '지금 무슨 기준이야?', '9월엔 뭘로 봤어?' 같은 질문에 이 도구로 답해라."
    )
)
async def get_overseas_fx_rule() -> Any:
    return await _get("/fx/overseas-rule", {})


@mcp.tool(
    description=(
        "특정 기준(basis_year·basis_month·basis_kind·rule)의 적용 환율을 실적·속보 환율 원본에서 다시 "
        "계산해본다 — 저장하지 않는다(dry-run). '말일 대신 월평균 환율을 쓰면 어떻게 되는지 보여줘' "
        "처럼 실제로 바꾸기 전에 먼저 숫자를 보여줄 때 반드시 이 도구부터 써라(바로 "
        "recompute_applied_fx_rates로 저장하지 마라 — 사용자가 전후 숫자를 보고 승인할 기회가 "
        "없어진다). rule: avg(월평균) | last(말일)."
    )
)
async def get_fx_rate_preview(basis_year: int, basis_month: int, basis_kind: str = "forecast", rule: str = "avg") -> Any:
    return await _post(
        "/fx/applied/recompute",
        {"basis_year": basis_year, "basis_month": basis_month, "basis_kind": basis_kind, "rule": rule, "dry_run": True},
    )


@mcp.tool(
    description=(
        "특정 기준(basis_year·basis_month·basis_kind·rule)의 적용 환율을 실적·속보 환율 원본에서 다시 "
        "계산해 저장하고, 해외연구소 화면이 그 기준을 실제로 쓰도록 설정도 같이 저장한다. 어느 쪽이든 "
        "새로고침해도 유지되는 진짜 저장이다(미리보기가 아니다) — 차이는 '다음 달'에만 있다:\n"
        "- set_as_default=False('이번 한 번만'): basis_month월에만 이 rule을 쓰는 예외를 넣는다. "
        "기본값은 그대로라 다음 달부터는 원래 쓰던 기준이 그대로 적용된다.\n"
        "- set_as_default=True('앞으로 계속'): 기본값 자체를 이 rule로 바꾼다 — 다음 달부터도 "
        "자동으로 이 기준이 적용된다. basis_month보다 이전 달들은 바뀌기 전 기준으로 고정되므로 "
        "이미 마감된 과거 숫자는 소급해서 바뀌지 않는다.\n"
        "사용자가 '이번만'인지 '앞으로 계속'인지로 이 값을 결정해라. 어느 쪽인지 불분명하면 호출하지 "
        "말고 먼저 확인해라 — 한번 승인받았다고 영구 전환까지 동의한 건 아니다. 지금 기준이 뭔지, "
        "어떤 달에 예외가 걸려 있는지는 get_overseas_fx_rule로 조회할 수 있다. 이 도구를 호출하기 "
        "전에 반드시 get_fx_rate_preview로 먼저 전후 숫자를 보여주고 승인을 받은 뒤 호출해라."
    )
)
async def recompute_applied_fx_rates(
    basis_year: int, basis_month: int, basis_kind: str = "forecast", rule: str = "avg", set_as_default: bool = False
) -> Any:
    result = await _post(
        "/fx/applied/recompute",
        {"basis_year": basis_year, "basis_month": basis_month, "basis_kind": basis_kind, "rule": rule},
    )
    basis_ym = f"{basis_year:04d}-{basis_month:02d}"
    if set_as_default:
        await _put("/fx/overseas-rule/default", {"rule": rule, "effective_from": basis_ym})
    else:
        await _put("/fx/overseas-rule/override", {"year_month": basis_ym, "rule": rule})
    return result


@mcp.tool(
    description=(
        "19개 비용계정 전체의 편성 진행 상태를 조회한다(대시보드 카드와 같은 내용) — '어느 계정이 아직 "
        "미제출이야?'·'지금 몇 개 계정이 작성완료야?'·'감가상각비 지금 몇 단계야?' 같은 질문에 쓴다.\n"
        "각 계정마다: key(계정 코드) · name(계정명) · status(상태 코드)와 status_label(그 한글명 — "
        "답변에는 코드가 아니라 이 라벨을 써라) · memo(비고) · steps(그 계정의 편성 단계 목록) · "
        "current_step_order(지금 머문 단계, 1-based)가 들어 있다.\n"
        "(중요) steps가 빈 배열인 계정은 전용 편성 화면이 아직 없는 '준비 중' 계정이고 "
        "current_step_order도 null이다 — '단계 정보가 없다'거나 오류라고 답하지 말고 '아직 전용 화면이 "
        "준비되지 않은 계정'이라고 설명해라. 지금 단계가 정해진 계정은 해외연구소·감가상각비·"
        "위탁용역비·생소비 네 개뿐이다.\n"
        "status_label을 세어서 답할 때는 네가 직접 세지 말고 개수가 많으면 calculate(op='count')를 써라."
    )
)
async def list_accounts() -> Any:
    return await _get("/accounts")


@mcp.tool(
    description=(
        "계정 하나의 편성 상태(status)나 비고(memo)를 바꾼다 — '감가상각비 제출완료로 바꿔줘'·"
        "'생소비 비고에 ~라고 적어줘' 같은 요청에 쓴다. 바꾸고 싶은 것만 넣어라(안 넣은 쪽은 그대로 "
        "둔다 — 비고만 고치려면 status를 넣지 마라).\n"
        "status 가능한 값: drafting(작성중) · drafted(작성완료) · not_submitted(미제출) · "
        "submitted(제출완료) · reviewed(검토완료) · rejected(반려). 사용자가 한글로 말한 상태를 이 "
        "코드로 바꿔서 넣어라(등록되지 않은 값을 넣으면 400으로 거부된다).\n"
        "key는 계정 코드다 — 한글 계정명으로 들었으면 list_accounts로 그 계정의 key를 먼저 확인해라"
        "(지어내지 마라). 이건 그 계정 담당자 화면에 그대로 보이는 값이니, 사용자가 분명히 바꿔달라고 "
        "한 계정만 건드려라."
    )
)
async def update_account(key: str, status: str | None = None, memo: str | None = None) -> Any:
    return await _put(f"/accounts/{key}", {"status": status, "memo": memo})


@mcp.tool(
    description=(
        "계정의 편성 진행 단계를 옮긴다(예: 감가상각비를 3단계로). 그 계정에 실제로 정의된 단계만 "
        "받는다 — 범위를 넘으면 400에 가능한 단계 목록이 같이 오니, 재시도하지 말고 그 목록을 "
        "사용자에게 그대로 보여주고 확인해라. 단계 번호·이름은 list_accounts의 steps로 확인할 수 있다.\n"
        "해외연구소(key='overseas')는 update_step 도구와 같은 값을 가리키므로 어느 쪽을 써도 결과가 "
        "같다. 사용자가 단계를 옮겨달라고 분명히 말한 경우에만 불러라 — 단계를 네가 대신 바꾸지 마라."
    )
)
async def update_account_step(key: str, step: int) -> Any:
    return await _put(f"/accounts/{key}/step", {"step": step})


@mcp.tool(
    description=(
        "환율 원본(기준정보 › 환율 화면에 올라온 값)을 조회한다 — '9월 실적 환율 얼마야?'·'USD 속보 "
        "환율 보여줘'·'경영계획 환율 올라왔어?' 같은 질문에 쓴다. 네 종류가 같이 돌아온다:\n"
        "- actual: 연월별 실적 환율. avg(월평균)와 eom(말일)이 따로 들어 있다(서로 다른 파일로 "
        "올라오니 한쪽만 있을 수 있다).\n"
        "- forecast: 연월별 속보(=실행) 환율.\n"
        "- plan: 연도별 경영계획 환율(통화당 1~12월 배열).\n"
        "이건 '원본 입력값'이다 — 해외연구소 화면이 실제로 쓰는 '적용 환율'(기준시점에 따라 실적·속보를 "
        "조합한 값)은 get_applied_fx_rates로 따로 조회해라. 둘을 섞어 답하지 마라.\n"
        "year_month를 주면 그 연월만(실적·속보), year를 주면 그 연도만 걸러서 돌려준다 — 생략하면 "
        "전체라서 양이 많으니 되도록 좁혀서 불러라."
    )
)
async def list_fx_rates(year_month: str | None = None, year: str | None = None) -> Any:
    state = await _get("/fx/state")
    if year_month:
        state = {
            "actual": {k: v for k, v in state.get("actual", {}).items() if k == year_month},
            "forecast": {k: v for k, v in state.get("forecast", {}).items() if k == year_month},
            "plan": {k: v for k, v in state.get("plan", {}).items() if k == year_month[:4]},
        }
    elif year:
        state = {
            "actual": {k: v for k, v in state.get("actual", {}).items() if k.startswith(year)},
            "forecast": {k: v for k, v in state.get("forecast", {}).items() if k.startswith(year)},
            "plan": {k: v for k, v in state.get("plan", {}).items() if k == year},
        }
    return state


@mcp.tool(
    description=(
        "속보·실적 버전의 진행 상태(기준정보 › 경영계획 연간 달력)를 조회한다 — '9월 실적 마감됐어?'·"
        "'올해 어디까지 확정됐어?' 같은 질문에 쓴다. kind: forecast(속보) | actual(실적), "
        "status: drafting(작성중) | done(완료)이고 completed면 confirmed_at(확정일)이 들어 있다.\n"
        "(아주 중요) 이 목록에 없는 (연,월,kind)는 '아직 열지 않은 = 예정'이라는 뜻이다 — 행이 없는 게 "
        "정상이니 '데이터가 없다'거나 '오류'라고 답하지 말고 '아직 시작 안 됐다(예정)'로 설명해라."
    )
)
async def list_versions(year: int | None = None) -> Any:
    return await _get("/versions", {"year": year})


@mcp.tool(
    description=(
        "그 연월의 속보·실적 버전을 새로 연다(작성중 상태로 만든다). kind: forecast(속보) | "
        "actual(실적). 이미 열려 있으면 400 에러가 나니, 모르겠으면 먼저 list_versions로 확인해라 "
        "— 에러가 나면 재시도하지 말고 이미 열려 있다고 사용자에게 설명해라.\n"
        "'완료/확정'으로 바꾸는 건 이 도구가 아니라 update_version_status다."
    )
)
async def open_version(year: int, month: int, kind: str) -> Any:
    return await _post("/versions", {"year": year, "month": month, "kind": kind})


@mcp.tool(
    description=(
        "열려 있는 버전을 작성중 ↔ 완료로 바꾼다. status: done(완료 — 확정일이 오늘로 찍힌다) | "
        "drafting(완료 취소 — 확정일이 지워진다). kind: forecast(속보) | actual(실적).\n"
        "아직 열지 않은 연월이면 404가 난다 — 그때는 open_version으로 먼저 열어야 한다고 설명해라. "
        "완료 처리는 '그 달 숫자를 확정한다'는 뜻이라, 사용자가 분명히 완료·확정해달라고 한 경우에만 "
        "status='done'으로 불러라."
    )
)
async def update_version_status(year: int, month: int, kind: str, status: str) -> Any:
    return await _put(f"/versions/{year}/{month}/{kind}", {"status": status})


@mcp.tool(
    description=(
        "경영계획 버전 목록(연도별 P00·P01…)을 조회한다. is_confirmed는 확정 여부, is_final은 그 연도의 "
        "'대비 기준이 되는 공식 버전'인지다(연도당 하나). '올해 경영계획 몇 차까지 있어?'·'확정된 "
        "경영계획 어느 거야?' 같은 질문에 쓴다."
    )
)
async def list_plan_versions(year: int | None = None) -> Any:
    return await _get("/management-plan/versions", {"year": year})


@mcp.tool(
    description=(
        "경영계획(management_plan_entries)에 저장된 월별 금액을 조회한다. 해외연구소 화면의 'plan'"
        "(경영계획) 값이 여기 있다 — list_cost_entries는 forecast/actual만 다루므로 경영계획 값을 "
        "물어보면 이 도구를 써야 한다.\n"
        "account_key는 계정 코드(해외연구소는 'overseas'), scope_key는 그 계정 안의 행 식별자"
        "(해외연구소는 연구소 pk, 예: 'SARC:CPU'), category_key는 비용항목 키다. plan_no를 생략하면 "
        "그 연도의 모든 차수가 섞여 돌아오니, 보통 list_plan_versions로 차수를 확인한 뒤 지정해라.\n"
        "결과는 (항목 × 월)당 1행이고 amount는 그 달 금액이다 — 합계·평균은 서버가 넣어주지 않으니 "
        "직접 더하지 말고 calculate 도구에 숫자를 넘겨 계산해라."
    )
)
async def list_plan_entries(
    year: int | None = None, plan_no: str | None = None, account_key: str | None = None,
    scope_key: str | None = None, category_key: str | None = None,
) -> Any:
    return await _get(
        "/management-plan/entries",
        {"year": year, "plan_no": plan_no, "account_key": account_key, "scope_key": scope_key,
         "category_key": category_key},
    )


@mcp.tool()
async def delete_sheet_rows_all(lab_pk: str, version: str, sheet_key: str) -> Any:
    """구조 미정 시트의 그 연구소·버전 행을 전부 지운다(시트를 다시 업로드하기 전 비울 때 씀).
    행 하나만 지우는 건 delete_sheet_row다 — 이 도구는 그 시트 전체를 비우니 주의해서 써라."""
    return await _delete(
        "/overseas/sheet-rows/all", {"lab_pk": lab_pk, "version": version, "sheet_key": sheet_key}
    )


_DISPLAY_UNITS = {
    "dollar": "dollar", "$": "dollar", "usd": "dollar", "d": "dollar",
    "kdollar": "kdollar", "k$": "kdollar", "k": "kdollar", "kusd": "kdollar", "thousand": "kdollar",
}


@mcp.tool(
    description=(
        "해외연구소 화면의 달러 금액 표시 단위를 바꾼다 — 화면 표시만 바꾸는 도구다. 저장된 데이터는 "
        "건드리지 않는다(DB는 그대로 K$이고, 화면이 1000을 곱해서 보여주는 것뿐이다). "
        "unit: dollar($ 단위) | kdollar(K$ 단위, 원래 값). 'K$ 말고 $로 보여줘'·'달러 단위로 "
        "받을래'·'천 달러 말고 달러로'처럼 단위 표시를 바꿔달라는 요청에 이 도구를 써라.\n"
        "이 호출 자체가 화면 표를 즉시 그 단위로 다시 그린다 — 환산한 숫자를 챗창에 나열하지 마라. "
        "답변에는 (1) 화면 표시만 바뀌었고 저장된 값은 그대로라는 점, (2) 새로고침하면 K$로 "
        "돌아간다는 점을 꼭 같이 알려줘라.\n"
        "단위를 바꿔달라는 요청에 upsert_* 같은 쓰기 도구로 저장된 숫자를 1000배 해서 덮어쓰지 마라 "
        "— 그건 되돌리기 어려운 데이터 손상이다."
    )
)
async def set_display_unit(unit: str = "dollar") -> Any:
    resolved = _DISPLAY_UNITS.get(str(unit or "").strip().lower())
    if resolved is None:
        raise ValueError("unit은 dollar($ 단위) 또는 kdollar(K$ 단위)만 쓸 수 있다.")
    return {
        "display_unit": resolved,
        "scope": "해외연구소 화면의 달러 금액 칸",
        "factor": 1000 if resolved == "dollar" else 1,
        "stored_unit": "K$ (저장된 데이터는 바뀌지 않음)",
        "resets_on_reload": True,
    }


@mcp.tool(
    description=(
        "외주용역비를 과제(Project) 단위로 묶어서 돌려준다 — '과제 기준으로 분석해줘'·'과제별로 "
        "얼마 쓰는지 보여줘'·'어느 과제가 제일 큰지' 같은 요청에 쓴다.\n"
        "(아주 중요) 과제 기준 분석은 외주용역비에서만 된다. 비용 데이터(cost_entries)의 키는 "
        "연월·계정과목·연구소·버전·계정이고 과제 칸이 아예 없어서, 급여·출장비·감가상각비 같은 다른 "
        "계정은 어느 과제 것인지 알 방법이 없다. 그러니 이 도구 결과를 '개발비를 과제별로 나눴다'고 "
        "말하지 마라 — 반드시 '외주용역비 기준'이라고 범위를 밝혀서 답해라(응답의 scope_note 참고). "
        "사용자가 개발비 전체를 과제별로 보고 싶어 하면, 비용 수집 양식에 과제 칸이 없어서 지금은 "
        "외주용역비까지만 가능하다고 설명해라.\n"
        "version: cur(당월 속보, 기본값) | prev(전실행) | prior(전년 실적) | plan(경영계획). "
        "base_version을 같이 주면 그 기준과 비교해서 과제별 증감과 상태(신규·종료·변동·동일)까지 "
        "낸다. 단 비교 기준 데이터가 저장돼 있지 않으면 compared=false로 돌아오고 diff·base_amount·"
        "status가 0이 아니라 null이다 — 이건 '증감이 0'이 아니라 '증감을 알 수 없다'는 뜻이다. "
        "그때는 절대 '증감 없음'이라고 답하지 말고, 금액만 답한 뒤 비교할 데이터가 없어 증감은 못 "
        "구했다고 note 내용대로 설명해라. "
        "lab_code로 연구소 하나만 볼 수 있고(파트 없는 코드, 예: 'SARC'), period는 y(연간, 기본값)|"
        "q1~q4|m1~m12다.\n"
        "금액 단위는 백만원이다(화면 외주용역 표와 같다) — K$나 원으로 바꿔 말하지 마라."
    )
)
async def list_vendor_by_project(
    version: str = "cur", base_version: str | None = None,
    lab_code: str | None = None, period: str = "y",
) -> Any:
    return await _get(
        "/overseas/vendor-by-project",
        {"version": version, "base_version": base_version, "lab_code": lab_code, "period": period},
    )


@mcp.tool(
    description=(
        "인건비 증감을 인원 효과 · 단가 효과로 나눈 금액을 조회한다 — '인원 때문이야 단가 때문이야?', "
        "'SARC 인원 효과 얼마야?'처럼 금액을 말로 듣고 싶어 할 때 쓴다(화면에 펼쳐주기만 하면 될 때는 "
        "set_labor_split을 써라; 둘을 같이 써도 된다).\n"
        "base_version: prev(전실행, 기본값) | prior(전년 실적). period: y(연간, 기본값)|q1~q4|m1~m12. "
        "lab_scope: 'ALL'(전체 연구소, 기본값) 또는 연구소 코드('SARC'면 파트 합계) 또는 pk('SARC:CPU'). "
        "basis_year(필수)에는 '현재 화면' 정보의 'year' 키 값을 그대로 넣어라.\n"
        "반환값은 전부 원 단위고 인원만 명이다: hc_effect(인원 효과) · rate_effect(단가 효과) · "
        "unsplit(인력을 안 낸 연구소 몫) · fx_outside(세 값의 합 = 화면 인건비 행의 환차外 금액) · "
        "per_head_base/per_head_cur(인당 인건비, 원/명) · labs(연구소별 같은 항목).\n"
        "이 값들은 환율이 움직여서 변한 몫(환차)을 뺀 뒤 나눈 것이다 — list_cost_entries로 받은 "
        "인건비·인력을 네가 직접 나눠서 답하지 마라(환차가 섞여 화면과 어긋난다). 반드시 이 도구 "
        "결과를 그대로 써라.\n"
        "경영계획 대비(plan)는 환율 기준이 없어 나눌 수 없다. 전년 실적(prior) 데이터가 없는 환경에서는 "
        "404가 나는데, 그때는 지어내지 말고 '그 비교 기준은 저장된 데이터가 없다'고 답해라."
    )
)
async def get_labor_split(
    basis_year: int, base_version: str = "prev", period: str = "y", lab_scope: str = "ALL",
) -> Any:
    return await _get(
        "/overseas/labor-split",
        {"basis_year": basis_year, "base_version": base_version, "period": period, "lab_scope": lab_scope},
    )


@mcp.tool(
    description=(
        "해외연구소 '계정별 증감분석' 화면에서 인건비 증감을 인원 효과 · 단가 효과로 나눈 줄을 "
        "펼치거나 접는다 — 화면 표시만 바꾸는 도구다(저장된 데이터는 건드리지 않는다). "
        "open: True(펼치기, 기본값) | False(접기).\n"
        "'인건비 쪼개줘'·'인건비 왜 늘었는지 인원이랑 단가로 나눠줘'·'인원 때문인지 단가 때문인지 "
        "보여줘'처럼 인건비 증감의 원인을 인원/단가로 갈라서 보고 싶다는 요청에 이 도구를 써라.\n"
        "이 호출 자체가 계정별 상세 표를 펼치고 인건비 아래에 네 줄(인원 효과 · 단가 효과 · 인력 "
        "미제출 연구소 몫 · 참고용 인당 인건비 증감률)을 즉시 그려준다 — 그러니 금액을 네가 계산해서 "
        "챗창에 나열하지 마라(화면 숫자와 어긋날 수 있다). 답변에는 (1) 화면 계정별 상세의 인건비 "
        "아래에 펼쳤다는 것, (2) 인원 효과 + 단가 효과(+ 인력 미제출 몫)를 더하면 그 위 인건비 행의 "
        "환차外 금액과 같다는 것, (3) 환율 때문에 변한 몫은 환차 열에 따로 있어서 이 분해에는 안 "
        "들어간다는 것을 설명해라.\n"
        "이 분해는 해외연구소 계정별 증감분석 화면에만 있다 — 다른 계정 화면에는 없다."
    )
)
async def set_labor_split(open: bool = True) -> Any:
    return {
        "labor_split_open": bool(open),
        "scope": "해외연구소 계정별 증감분석 > 계정별 상세 > 인건비 행",
        "rows": [
            "환차外 · 인원 효과 = (당월 인원 − 비교 인원) × 비교 단가",
            "환차外 · 단가 효과 = 당월 인원 × (당월 단가 − 비교 단가)",
            "환차外 · 인력 미제출 연구소 (CP처럼 인력을 안 낸 곳 몫, 0이면 줄이 안 생김)",
            "(참고) 인당 인건비 증감률 (합계에 들어가지 않음)",
        ],
        "ties_to": "위 세 줄의 합 = 인건비 행의 환차外 금액",
        "fx_excluded": "환율이 움직여서 변한 몫은 환차(현지→$ · $→원) 열에 따로 있어 이 분해에는 포함되지 않는다",
        "resets_on_reload": False,
    }


@mcp.tool(
    description=(
        "숫자 모음에 대한 정확한 산술 계산. list_cost_entries의 sum/avg가 없는 계산(최댓값·최솟값· "
        "증감률 등)은 모델이 직접 더하거나 나누지 말고 반드시 이 도구를 써라 — 숫자가 몇 개든 모델이 "
        "직접 계산하면 오차가 날 수 있다. op: sum(합계) | avg(평균) | max(최댓값) | min(최솟값) | "
        "count(개수) | pct_change(증감률 %, numbers는 [이전값, 이후값] 두 개여야 함) | "
        "div(나누기, numbers는 [분자, 분모] 두 개여야 함 — 예: '인당 개발비' 연간 하나만 필요하면 "
        "[개발비 합계, 인력 합계]). 월별로 12개를 한 번에 나눠야 하면 이 도구 대신 combine_series를 써라."
    )
)
async def calculate(numbers: list[float], op: str) -> Any:
    if not numbers:
        return {"error": "numbers가 비었다"}
    if op == "sum":
        return {"result": round(sum(numbers), 4)}
    if op == "avg":
        return {"result": round(sum(numbers) / len(numbers), 4)}
    if op == "max":
        return {"result": max(numbers)}
    if op == "min":
        return {"result": min(numbers)}
    if op == "count":
        return {"result": len(numbers)}
    if op == "pct_change":
        if len(numbers) != 2:
            return {"error": "pct_change는 numbers가 [이전값, 이후값] 두 개여야 한다"}
        before, after = numbers
        if before == 0:
            return {"error": "이전값이 0이라 증감률을 계산할 수 없다"}
        return {"result": round((after - before) / abs(before) * 100, 2)}
    if op == "div":
        if len(numbers) != 2:
            return {"error": "div는 numbers가 [분자, 분모] 두 개여야 한다"}
        num, den = numbers
        if den == 0:
            return {"error": "분모가 0이라 나눌 수 없다"}
        return {"result": round(num / den, 4)}
    return {"error": f"알 수 없는 op: {op} (sum|avg|max|min|count|pct_change|div 중 하나)"}


@mcp.tool(
    description=(
        "길이가 같은 12개월 숫자 배열(각 list_cost_entries 결과의 'months'를 월(1~12) 순서로 늘어놓은 "
        "것) 여러 개를 월별로(같은 달끼리) 묶어 계산한다. 그리드의 집계 행(예: '개발비(A)' = 여러 "
        "계정의 월별 합)이나 비율 행(예: '인당 개발비' = 개발비 ÷ 인력, 월별로 나눠야 함)을 만들 때 "
        "쓴다 — calculate는 숫자를 한 줄로 뭉쳐서 계산해 월별 구분이 안 되고, 나누기도 없다. "
        "op: sum_elementwise(series 2개 이상을 같은 달끼리 더함) | "
        "div_elementwise(series가 정확히 2개 — series[0] ÷ series[1]을 같은 달끼리 나눔). "
        "결과 배열을 upsert_extra_row의 v에 그대로 넣으면 된다."
    )
)
async def combine_series(series: list[list[float]], op: str) -> Any:
    if len(series) < 1:
        return {"error": "series가 비었다"}
    lengths = {len(s) for s in series}
    if len(lengths) != 1:
        return {"error": "series 안의 배열들 길이가 서로 다르다 — 모두 같은 길이(보통 12개월)여야 한다"}
    n = lengths.pop()
    if op == "sum_elementwise":
        if len(series) < 2:
            return {"error": "sum_elementwise는 series가 2개 이상이어야 한다"}
        return {"result": [round(sum(s[i] for s in series), 4) for i in range(n)]}
    if op == "div_elementwise":
        if len(series) != 2:
            return {"error": "div_elementwise는 series가 정확히 2개([분자 배열, 분모 배열])여야 한다"}
        num, den = series
        return {"result": [round(a / b, 4) if b != 0 else None for a, b in zip(num, den)]}
    return {"error": f"알 수 없는 op: {op} (sum_elementwise|div_elementwise 중 하나)"}


@mcp.tool()
async def list_errors(status_code: int | None = None, path: str | None = None, limit: int = 100) -> Any:
    """API 서버에서 발생한 처리되지 않은 예외(500) 로그를 최신순으로 조회한다."""
    return await _get("/errors", {"status_code": status_code, "path": path, "limit": limit})


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
