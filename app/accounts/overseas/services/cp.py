"""CP 계약 읽기·쓰기 — 연도 × 적용 시점 단위의 스냅샷.

왜 서비스로 뺐나 (PLAN.md Phase 5): 외주용역과 같은 이유다. 저장이 `PUT /overseas/state` 하나에
뭉쳐 있었고, CP도 이미 "전체 교체" 의미라 떼어내기 쉬운 자리다. 전용 API와 일괄 저장이 **같은
함수**를 부르게 둔다 — 쓰기 규칙이 두 벌이 되면 PLAN P1-3의 "같은 데이터, 두 가지 쓰기 규칙"을
다시 만든다.

왜 연도 단위 전체 교체인가: 연 1회 올리는 파일이라 사라진 POC·직급은 지워야 한다. upsert만 하면
작년 양식에 있던 POC가 계속 남아서 집계에 섞인다. 보내지 않은 연도는 건드리지 않는다.

왜 적용 시점(effective_from)이 붙었나: 분기 정산액은 저장된 값이 아니라 **조회할 때마다 다시
계산되는** 값이다(cp_quarter_amounts가 연 금액 ÷ 4를 3·6·9·12월에 넣는다). 그래서 "지금 값" 한
세트만 두면 계약액을 고치는 순간 이미 마감한 과거 버전의 분기 정산액까지 소급해서 바뀐다.
연구소 기준정보(lab_views)와 같은 규칙으로, 버전이 보는 시점의 세트를 읽는다 — 9월에 바꾸면
9월 이후 버전만 새 계약액으로 계산되고 8월 이하 버전은 바뀌기 전 값 그대로다.

읽는 입구는 cp_rows_at() 하나이고 as_of가 **필수**다. 기본값을 "지금 값"으로 두면 호출부 하나만
빼먹어도 그 화면만 조용히 소급되는데, 소급은 숫자가 틀리는 게 아니라 "과거 보고서가 달라지는"
형태로 나타나서 아무도 못 알아챈다. 빼먹으면 에러가 나는 쪽이 낫다.
"""

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.accounts.overseas.models import CP_EPOCH, OverseasCpRow
from app.accounts.overseas.schemas import CpContractOut, CpContractPoc, CpRow

# 저장한 세트 안에서의 행 순서. 연도 단위로 전체를 지우고 다시 넣는 경로라, 순서를 고정하지 않으면
# POC·직급 표가 저장할 때마다 섞인다(증감사유와 같은 이유).
_ROW_ORDER = (OverseasCpRow.year, OverseasCpRow.poc_type, OverseasCpRow.poc_proj, OverseasCpRow.title)


def effective_set_at(db: Session, year: str, as_of: str) -> str | None:
    """그 버전 시점에 유효한 세트의 effective_from. 그 시점 이전에 올라온 계약이 없으면 None."""
    return db.scalar(
        select(func.max(OverseasCpRow.effective_from)).where(
            OverseasCpRow.year == year, OverseasCpRow.effective_from <= as_of
        )
    )


def cp_rows_at(db: Session, year: int | str, as_of: str) -> list[OverseasCpRow]:
    """as_of("YYYY-MM") 시점의 CP 계약 행 집합. 그 시점 이하 가장 최근 세트를 통째로 돌려준다.

    as_of는 버전의 기준연월이다 — 그 버전을 열었을 때 보여야 하는 계약이 무엇인지를 정한다.
    해당 시점에 올라온 계약이 없으면 빈 목록이다(= 그 버전에서는 CP 정산 칸이 빈칸)."""
    year_key = f"{int(year):04d}"
    effective_from = effective_set_at(db, year_key, as_of)
    if effective_from is None:
        return []
    return list(
        db.scalars(
            select(OverseasCpRow)
            .where(OverseasCpRow.year == year_key, OverseasCpRow.effective_from == effective_from)
            .order_by(*_ROW_ORDER)
        )
    )


def replace_cp_rows(db: Session, rows: list[CpRow], effective_from: str) -> int:
    """rows에 등장하는 연도를, 그 적용 시점(effective_from)의 세트로 통째로 바꿔 넣는다.
    바뀐 연도 수를 돌려준다.

    같은 시점의 세트만 지우고 다시 넣는다 — 다른 시점 세트(과거 버전이 보는 값)는 건드리지 않는다.
    그래서 같은 달에 두 번 올리면 그 달 세트만 덮이고, 지난 버전은 계속 옛 값을 본다.

    commit은 하지 않는다 — 호출부가 다른 쓰기와 한 트랜잭션으로 묶을 수 있어야 한다.
    """
    years = {r.year for r in rows}
    if years:
        db.execute(
            delete(OverseasCpRow).where(
                OverseasCpRow.year.in_(years), OverseasCpRow.effective_from == effective_from
            )
        )
    if rows:
        db.execute(
            insert(OverseasCpRow),
            [
                {
                    "year": r.year, "effective_from": effective_from,
                    "poc_type": r.poc_type, "poc_proj": r.poc_proj, "poc_desc": r.poc_desc,
                    "title": r.title, "dedicated": r.dedicated, "common": r.common, "man_month": r.man_month,
                    "cost_year": r.cost_year, "poc_cost_sum": r.poc_cost_sum, "poc_mm_sum": r.poc_mm_sum,
                    "poc_direct": r.poc_direct, "poc_ovh": r.poc_ovh, "poc_total": r.poc_total,
                    "source_file": r.source_file,
                }
                for r in rows
            ],
        )
    return len(years)


# ---------- 계약 요약(POC 단위로 접기) ----------
# POC 헤더값(poc_total · poc_cost_sum)은 직급 행마다 반복 저장돼 있어서, 행마다 더하면 직급 수만큼
# 부풀려진다. 읽는 쪽이 전부 이 규칙을 다시 구현하면 한 군데만 고쳐지고 숫자가 갈린다.


def contract_at(db: Session, year: int, as_of: str) -> CpContractOut:
    """그 버전 시점의 CP 계약 요약 — POC별 금액 + 연 합계 + 분기 하나치(연 ÷ 4).

    계약이 없으면 비어 있는 요약을 registered=False로 돌려준다(예외를 던지지 않는다) — 집계 화면은
    "계약이 없다"는 사실을 알고 CP 정산 칸을 빈칸으로 둬야 하는데, 오류로 주면 "서버가 죽었다"와
    구분하지 못해 업로드된 CP PL로 그려 버린다(= 계약 없이 숫자가 서는 사고)."""
    rows = cp_rows_at(db, year, as_of)
    pocs: dict[tuple[str, str], CpContractPoc] = {}
    source_file, uploaded_at = "", ""
    for r in rows:
        key = (r.poc_type, r.poc_proj)
        if key not in pocs:
            pocs[key] = CpContractPoc(
                poc_type=r.poc_type, poc_proj=r.poc_proj, total=float(r.poc_total), cost_sum=float(r.poc_cost_sum)
            )
        source_file = source_file or (r.source_file or "")
        if r.uploaded_at and not uploaded_at:
            uploaded_at = r.uploaded_at.strftime("%m.%d %H:%M")
    year_total = sum(p.total for p in pocs.values())
    year_labor = sum(p.cost_sum for p in pocs.values())
    registered = bool(pocs)
    return CpContractOut(
        year=year,
        registered=registered,
        poc_count=len(pocs),
        year_total=year_total,
        year_labor=year_labor,
        quarter_total=year_total / 4,
        quarter_labor=year_labor / 4,
        source_file=source_file,
        uploaded_at=uploaded_at,
        pocs=list(pocs.values()),
        note=(
            f"{year}년 CP 계약 POC {len(pocs)}개({as_of} 버전 기준). 분기 정산 계약이라 연 금액 ÷ 4를 "
            f"3 · 6 · 9 · 12월에 넣는다."
            if registered
            else f"{year}년 CP 계약이 {as_of} 버전 시점에 등록되지 않았다. 집계의 CP 정산 칸은 0이 아니라 빈칸이고 합계에서 빠진다."
        ),
    )


# ---------- 계약액만 바꾸기 ----------
# 금액 항목만 같은 비율로 조정한다. 인원(dedicated · common)과 맨먼스(man_month · poc_mm_sum)는
# 건드리지 않는다 — "계약액이 얼마로 바뀌었다"는 말은 돈 이야기지 인력 구성 이야기가 아니다.
_MONEY_POC_FIELDS = ("poc_cost_sum", "poc_direct", "poc_ovh", "poc_total")


def rescale_rows(rows: list[OverseasCpRow], factors: dict[tuple[str, str], float]) -> list[CpRow]:
    """POC별 배율을 금액 항목에만 곱한 새 행 집합을 만든다(저장하지는 않는다).

    왜 POC 헤더값만이 아니라 직급 행의 cost_year까지 같이 조정하나: CP 계약 화면은 직급 행과 POC
    헤더값을 한 표에 같이 보여주고 맨 아래에서 둘 다 합산한다. 헤더값만 바꾸면 그 표 안에서
    합이 안 맞는 숫자가 보이고, 보는 사람은 어느 쪽이 맞는지 알 수 없다.

    Direct·Overhead도 같은 비율로 움직여서 합계 관계(direct + ovh = total)가 원래대로 유지된다 —
    배율 하나를 전부에 곱하므로 저장돼 있던 관계가 무엇이든 그대로 보존된다."""
    out: list[CpRow] = []
    for r in rows:
        f = factors.get((r.poc_type, r.poc_proj), 1.0)
        out.append(
            CpRow(
                year=r.year, poc_type=r.poc_type, poc_proj=r.poc_proj, poc_desc=r.poc_desc, title=r.title,
                dedicated=float(r.dedicated), common=float(r.common), man_month=float(r.man_month),
                cost_year=float(r.cost_year) * f,
                poc_mm_sum=float(r.poc_mm_sum),
                source_file=r.source_file,
                **{name: float(getattr(r, name)) * f for name in _MONEY_POC_FIELDS},
            )
        )
    return out


__all__ = [
    "CP_EPOCH", "contract_at", "cp_rows_at", "effective_set_at", "replace_cp_rows", "rescale_rows",
]
