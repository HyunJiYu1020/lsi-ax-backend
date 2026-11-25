"""외주용역 상세 저장 — 연구소×버전 단위 전체 교체.

전용 저장 API와 `PUT /overseas/state`가 **같은 함수를 부른다** — 쓰기 규칙이 두 벌이 되면
"같은 데이터, 두 가지 쓰기 규칙"이 된다.

왜 upsert가 아니라 전체 교체인가: 업로드마다 행 수가 달라진다. upsert만 하면 지워진 행이 남아서
"연구소가 뺀 항목"이 화면에 계속 보인다. 보내지 않은 연구소·버전은 건드리지 않는다 — 한 연구소
시트를 올렸을 때 다른 연구소 자료가 사라지면 안 되기 때문이다.
"""

from fastapi import HTTPException
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.accounts.overseas.models import OverseasVendorRow, OverseasVendorRowDetail
from app.accounts.overseas.schemas import VendorByProjectResult, VendorProjectRow, VendorRow


def replace_vendor_rows(
    db: Session, basis_year: int, rows: list[VendorRow], known_lab_codes: set[str],
    default_yyyymm: int = 0,
) -> int:
    """rows에 등장하는 (연구소, 버전, 기준월) 조합을 통째로 바꿔 넣는다. 바뀐 조합 수를 돌려준다.

    기준월까지 조합에 넣는 이유: 넣지 않으면 11월 회신을 저장할 때 9월 회신이 같은 (연구소, 버전)
    이라는 이유로 **지워진다**. 월이 다르면 다른 자료다.

    default_yyyymm은 행에 기준월이 없을 때 채울 값이다 — 둘 다 없으면 400.

    commit은 하지 않는다 — 호출부가 다른 쓰기와 한 트랜잭션으로 묶을 수 있어야 한다
    (`PUT /overseas/state`는 commit 1회로 원자성을 보장한다).
    """
    unknown = {r.lab_code for r in rows if r.lab_code not in known_lab_codes}
    if unknown:
        raise HTTPException(status_code=400, detail=f"알 수 없는 연구소 코드(외주용역): {sorted(unknown)}")

    ym_of = {id(r): (r.yyyymm or default_yyyymm) for r in rows}
    if rows and not all(ym_of.values()):
        raise HTTPException(status_code=400, detail="외주용역 행에 기준월(yyyymm)이 없다.")

    # 교체 단위는 (연구소, 기준월)이다 — version은 키가 아니다(모델 주석 참고).
    # 같은 자료가 두 자리(cur·prev)로 내려올 수 있어(보는 달에 따라) 키가 겹치면 뒤엣것만 남긴다 —
    # 안 그러면 한 INSERT 안에 같은 키가 두 번 들어가 유니크 키 충돌로 저장 전체가 깨진다.
    rows = list({(r.lab_code, ym_of[id(r)], r.row_no): r for r in rows}.values())
    units = {(r.lab_code, ym_of[id(r)]) for r in rows}
    for lab_code, ym in units:
        db.execute(
            delete(OverseasVendorRow).where(
                OverseasVendorRow.lab_code == lab_code,
                OverseasVendorRow.yyyymm == ym,
            )
        )
    if not rows:
        return 0

    new_ids = (
        db.execute(
            insert(OverseasVendorRow).returning(OverseasVendorRow.id, sort_by_parameter_order=True),
            [
                {
                    "lab_code": r.lab_code, "version": r.version, "yyyymm": ym_of[id(r)],
                    "row_no": r.row_no, "category": r.category,
                    "project": r.project, "company": r.company, "description": r.description,
                    "source_file": r.source_file,
                }
                for r in rows
            ],
        )
        .scalars()
        .all()
    )
    details = [
        {"vendor_row_id": new_id, "year": basis_year, "month": month, "amount": float(amount)}
        for new_id, r in zip(new_ids, rows)
        for month, amount in enumerate(r.m[:12], start=1)
    ]
    if details:
        db.execute(insert(OverseasVendorRowDetail), details)
    return len(units)


# ---------- 외주용역비 과제별 집계 ----------
# "과제 기준으로 분석해줘"는 비용 본체로는 할 수 없다 — cost_entries의 키는 연월·계정과목·연구소·
# 버전·계정이고 과제 칸이 없다. 과제명이 붙어 있는 건 외주용역 시트(overseas_vendor_rows.project)와
# CP 계약뿐이라, 과제 기준 분석은 외주용역비 범위에서만 가능하다.
def vendor_period_sums(
    db: Session, version: str, lab_code: str | None, ix: tuple[int, ...]
) -> dict[int, tuple[OverseasVendorRow, float]]:
    """그 버전의 외주용역 행별 (행, 조회 기간 합계)."""
    q = select(OverseasVendorRow).where(OverseasVendorRow.version == version)
    if lab_code:
        q = q.where(OverseasVendorRow.lab_code == lab_code)
    rows = list(db.scalars(q))
    if not rows:
        return {}
    months: dict[int, list[float]] = {}
    for d in db.scalars(
        select(OverseasVendorRowDetail).where(
            OverseasVendorRowDetail.vendor_row_id.in_([r.id for r in rows])
        )
    ):
        months.setdefault(d.vendor_row_id, [0.0] * 12)[d.month - 1] = float(d.amount)
    return {
        r.id: (r, sum(months.get(r.id, [0.0] * 12)[j] for j in ix))
        for r in rows
    }


def vendor_by_project(
    db: Session, version: str, base_version: str | None, lab_code: str | None,
    period: str, ix: tuple[int, ...],
) -> VendorByProjectResult:
    """과제별 외주용역 금액(+base_version을 주면 증감·상태까지)."""
    cur = vendor_period_sums(db, version, lab_code, ix)
    if not cur:
        where = f"{lab_code} 연구소의 " if lab_code else ""
        raise HTTPException(404, f"{where}{version} 외주용역 데이터가 없다.")
    base = vendor_period_sums(db, base_version, lab_code, ix) if base_version else {}
    compared = bool(base_version) and bool(base)

    # 과제명이 빈 행은 "(과제 미지정)"으로 묶는다 — 조용히 빼면 합계가 화면과 안 맞는다.
    def group(src: dict[int, tuple[OverseasVendorRow, float]]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for row, amount in src.values():
            key = (row.project or "").strip() or "(과제 미지정)"
            g = out.setdefault(key, {"amount": 0.0, "count": 0, "labs": set(), "companies": set()})
            g["amount"] += amount
            g["count"] += 1
            g["labs"].add(row.lab_code)
            if row.company:
                g["companies"].add(row.company)
        return out

    g_cur, g_base = group(cur), group(base)
    rows: list[VendorProjectRow] = []
    for key in sorted(set(g_cur) | set(g_base)):
        c = g_cur.get(key, {"amount": 0.0, "count": 0, "labs": set(), "companies": set()})
        b = g_base.get(key)
        diff = c["amount"] - (b["amount"] if b else 0.0)
        if not compared:
            status = None
        elif not b or not b["amount"]:
            status = "신규"
        elif not c["amount"]:
            status = "종료"
        elif abs(diff) < 0.005:
            status = "동일"
        else:
            status = "변동"
        rows.append(VendorProjectRow(
            project=key, amount=c["amount"], item_count=c["count"],
            labs=sorted(c["labs"]), company_count=len(c["companies"]),
            # 비교하지 못했으면 0이 아니라 비워서 내려보낸다 — 0은 "증감 없음"으로 읽힌다.
            base_amount=(b["amount"] if b else 0.0) if compared else None,
            diff=diff if compared else None, status=status,
        ))
    rows.sort(key=lambda r: -abs(r.diff or 0.0) if compared else -r.amount)

    total = sum(a for _, a in cur.values())
    base_total = sum(a for _, a in base.values())
    base_label = {"cur": "당월 속보", "prev": "전실행", "prior": "전년 실적", "plan": "경영계획"}
    note = ""
    if base_version and not base:
        note = (
            f"{base_label.get(base_version, base_version)} 외주용역 데이터가 저장돼 있지 않아 증감을 "
            f"구할 수 없다. 그래서 base_amount·diff·status를 0이 아니라 비워서(null) 돌려준다 — "
            f"'증감이 없다'가 아니라 '증감을 알 수 없다'는 뜻이다. 사용자에게는 "
            f"{base_label.get(version, version)} 과제별 금액만 답하고, 증감은 그 데이터가 올라와야 "
            f"볼 수 있다고 설명해라. 0을 증감으로 말하지 마라."
        )
    return VendorByProjectResult(
        version=version, base_version=base_version, period=period, lab_code=lab_code,
        total=total, base_total=base_total if compared else None,
        diff=(total - base_total) if compared else None, compared=compared, projects=rows, note=note,
    )
