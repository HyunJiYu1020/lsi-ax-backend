"""CP 계약 읽기·쓰기 — 연도 × 적용 시점 단위의 스냅샷.

전용 API와 일괄 저장이 **같은 함수**를 부른다 — 쓰기 규칙이 두 벌이 되면 "같은 데이터,
두 가지 쓰기 규칙"이 된다.

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

from fastapi import HTTPException
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


# ---------- 들어온 계약이 "계약처럼 생겼나" ----------
# 왜 저장할 때 막나: CP 저장은 연도 단위 전체 교체라, 깨진 세트 한 벌이 그 해 계약을 통째로 지운다.
# 실제로 그렇게 날아간 적이 있다 — 업로드 파싱이 머리글만 맞추고 본문을 한 줄로 읽어서
# (Type="P" · Projects="J" · Total=$1) POC 3개 11행이 그 한 줄로 바뀌었다. 화면은 "POC 1개를
# 반영했습니다"라는 성공 메시지를 띄웠고, 숫자가 0이 된 자리는 집계 ② 개발비 탭이라 이틀 뒤에야
# 발견됐다. 사고 낸 화면과 증상 보이는 화면이 달라서 아무도 그 사이에 알아채지 못했다.
#
# 금액 크기로는 판단하지 않는다 — "너무 작으면 틀렸다"는 기준은 계약이 진짜 작은 해나 단위를 잘못
# 적은 해를 사람이 못 넣게 만든다. 양식 **안에서 합이 안 맞는 것**만 거부한다:
# Total = Cost Sum POC(인건비) + Direct R&D + Overhead and markup
# 실제 계약서(CP_계약_2026.xlsx)의 POC 3개 모두 이 관계가 반올림 $1 이내로 성립한다.
#
# 이 검사가 잡는 건 "업로드가 깨져서 들어온 세트" 하나다. 자기들끼리 합이 맞는 **다른** 파일을
# 잘못 올리는 건 어떤 검사로도 못 잡는다 — 그건 교체 전에 사람에게 "POC 3개 → 1개로 바뀝니다"를
# 보여주고 묻는 쪽(화면 확인창)의 일이다.
_SUM_TOLERANCE_RATIO = 0.01 # 반올림·양식 차이를 받아 줄 상대 오차. 깨진 세트는 100% 어긋나 이 안에 못 든다


def validate_cp_rows(rows: list[CpRow]) -> None:
    """들어온 계약 세트가 양식 안에서 합이 맞는지 본다. 안 맞으면 400으로 멈춘다(저장하지 않는다).

    POC 단위로 접어서 본다 — 헤더값(poc_total 등)은 직급 행마다 반복 저장돼 있어서 행마다 보면
    같은 값을 직급 수만큼 다시 본다.

    금액이 안 적힌 POC(poc_total <= 0)는 건너뛴다 — 판단할 근거가 없고, 인원·맨먼스만 먼저 올려
    두는 경우를 막을 이유도 없다.
    """
    by_poc: dict[tuple[str, str], list[CpRow]] = {}
    for r in rows:
        by_poc.setdefault((r.poc_type, r.poc_proj), []).append(r)

    for (poc_type, poc_proj), poc_rows in by_poc.items():
        head = poc_rows[0]
        if head.poc_total <= 0:
            continue
        name = f"{poc_type} {poc_proj}".strip() or "(이름 없는 POC)"
        tail = "저장하지 않았다 — 올라와 있던 계약은 그대로다. 파일의 머리글·행이 양식과 같은지 보고 다시 올려라."

        # 1) 합계만 있고 재료가 전부 0 — 금액이 작은 계약이 아니라 파싱이 깨진 세트다.
        materials = (
            head.poc_cost_sum, head.poc_direct, head.poc_ovh,
            *(r.cost_year for r in poc_rows), *(r.man_month for r in poc_rows),
            *(r.dedicated for r in poc_rows), *(r.common for r in poc_rows),
        )
        if not any(materials):
            raise HTTPException(
                400,
                f"CP 계약 '{name}': Total은 {head.poc_total:,.0f}$인데 인건비 · Direct R&D · "
                f"Overhead · 인원 · 맨먼스가 전부 0이다. 계약이 아니라 업로드 파싱이 깨진 세트다. "
                f"{tail}",
            )

        # 2) 합이 안 맞음 — Total = 인건비 + Direct + Overhead
        parts = head.poc_cost_sum + head.poc_direct + head.poc_ovh
        if abs(parts - head.poc_total) > max(2.0, head.poc_total * _SUM_TOLERANCE_RATIO):
            raise HTTPException(
                400,
                f"CP 계약 '{name}': 인건비({head.poc_cost_sum:,.0f}$) + Direct R&D"
                f"({head.poc_direct:,.0f}$) + Overhead({head.poc_ovh:,.0f}$) = {parts:,.0f}$인데 "
                f"Total은 {head.poc_total:,.0f}$다. 양식 안에서 합이 맞지 않는다. {tail}",
            )


def replace_cp_rows(db: Session, rows: list[CpRow], effective_from: str) -> int:
    """rows에 등장하는 연도를, 그 적용 시점(effective_from)의 세트로 통째로 바꿔 넣는다.
    바뀐 연도 수를 돌려준다.

    같은 시점의 세트만 지우고 다시 넣는다 — 다른 시점 세트(과거 버전이 보는 값)는 건드리지 않는다.
    그래서 같은 달에 두 번 올리면 그 달 세트만 덮이고, 지난 버전은 계속 옛 값을 본다.

    넣기 전에 validate_cp_rows로 한 번 본다 — 지우고 나서 걸리면 이미 늦다(전체 교체라서 그 해
    계약이 사라진 뒤다). 깨진 세트는 400으로 멈추고 기존 계약은 그대로 남는다.

    commit은 하지 않는다 — 호출부가 다른 쓰기와 한 트랜잭션으로 묶을 수 있어야 한다.
    """
    validate_cp_rows(rows)
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


# ---------- 교체로 계약이 줄어드는가 ----------
# 가드 2(위)가 "데이터가 깨졌나"를 보는 거라면, 이쪽은 "사람이 의도한 교체인가"를 본다. 둘이 다른
# 일을 하는 이유: 자기들끼리 합이 맞는 **다른** 파일(예: 작년 계약서)을 잘못 올리면 깨진 데이터가
# 아니라서 가드 2는 통과한다. 그런데 CP 저장은 연도 단위 전체 교체라 그대로 11행이 날아간다.
# 그래서 줄어드는 교체는 사람이 "예"라고 한 적이 있어야만 지나간다.
#
# 화면이 묻고 끝내면 안 되는 이유: 확인창은 그 화면에만 있다. 챗봇 · 일괄 저장 · 스크립트로 들어오는
# 같은 교체는 확인창을 지나지 않는다. 그래서 같은 판단을 서버에서 한 번 더 하고, 화면은 사용자에게
# 물어 확인을 받았다는 사실을 confirm_shrink로 실어 보낸다.
#
# "줄어든다"의 기준은 금액 크기가 아니라 **직전 세트와의 비교**다(POC 수 · 직급 행 수 · 총 계약금
# 중 하나라도 줄면). 같거나 늘어나는 교체는 묻지 않는다 — 계약이 늘어난 걸 사람이 확인할 이유는 없고,
# 매 저장마다 확인창이 뜨면 아무도 안 읽는다.


def shrink_note(db: Session, rows: list[CpRow], effective_from: str) -> str | None:
    """이 교체로 계약이 줄어드는 연도가 있으면 사람이 읽을 비교 문장, 없으면 None.

    비교 대상은 "그 시점 버전이 지금 보고 있는 세트"다(cp_rows_at) — 같은 effective_from에
    아직 아무것도 없으면 그 이전 세트를 보게 되고, 그게 이 교체로 가려질 세트다.
    """
    notes = []
    for year in sorted({r.year for r in rows}):
        new_rows = [r for r in rows if r.year == year]
        old_rows = cp_rows_at(db, year, effective_from)
        if not old_rows:
            continue  # 처음 올리는 해 — 줄어들 게 없다
        # POC 수 · 직급 행 수 · 총 계약금. 총 계약금은 POC 단위로 접어서 더한다 — 헤더값이
        # 행마다 반복 저장돼 있어 행마다 더하면 직급 수만큼 부풀려진다.
        def fold(rs) -> tuple[int, int, float]:
            pocs = {(r.poc_type, r.poc_proj): float(r.poc_total) for r in rs}
            return len(pocs), len(rs), sum(pocs.values())

        old_pocs, old_n, old_sum = fold(old_rows)
        new_pocs, new_n, new_sum = fold(new_rows)
        if new_pocs >= old_pocs and new_n >= old_n and new_sum >= old_sum:
            continue
        notes.append(
            f"{year}년: POC {old_pocs}개 → {new_pocs}개 · 직급 행 {old_n}개 → {new_n}개 · "
            f"총 계약금 {old_sum:,.0f}$ → {new_sum:,.0f}$"
        )
    return " / ".join(notes) if notes else None


def guard_cp_shrink(db: Session, rows: list[CpRow], effective_from: str, confirmed: bool) -> None:
    """줄어드는 교체를 확인 없이 지나가지 못하게 막는다(400). 확인을 받았으면 아무것도 하지 않는다."""
    if confirmed or not rows:
        return
    note = shrink_note(db, rows, effective_from)
    if note:
        raise HTTPException(
            400,
            f"이 업로드는 올라와 있는 CP 계약을 줄입니다 — {note}. CP 저장은 연도 단위 전체 교체라 "
            f"지금 계약이 이 내용으로 바뀝니다. 맞으면 화면에서 확인(confirm_shrink)을 받아 다시 "
            f"보내세요. 저장하지 않았습니다.",
        )


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

    Direct·Overhead도 같은 비율로 움직여서 합계 관계(인건비 + direct + ovh = total)가 유지된다 —
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
    "guard_cp_shrink", "shrink_note", "validate_cp_rows",
]
