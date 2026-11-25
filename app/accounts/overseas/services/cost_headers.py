"""비용 시트 기간 열 머리글 원문 — 연구소×버전×기준월 단위 전체 교체.

연구소가 보낸 파일의 머리글("'26.1월 실적" · "M2601A" · "202601")을 글자 그대로 들고 있다가,
취합 › 비용 표가 코드가 만든 "1월" 대신 그 글자를 보여 주게 하는 자리다. 집계·증감분석 표는
쓰지 않는다 — 왜인지는 models.OverseasCostHeader 주석에 적어 뒀다.

왜 upsert가 아니라 전체 교체인가: 파일마다 열 구성이 달라진다(분기 칸을 빼고 보내는 달, 기간을
24개월로 늘려 보내는 달). 열별 덮어쓰기만 하면 지난 업로드에만 있던 열이 남아서, 연구소가 뺀
열이 화면에 계속 보인다 — 외주용역 행(services/vendor.py)과 같은 이유로 같은 방식을 쓴다.
보내지 않은 (연구소 · 버전 · 기준월)은 건드리지 않는다.
"""

import re

from fastapi import HTTPException
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.accounts.overseas.models import OverseasCostHeader
from app.accounts.overseas.schemas import CostHeaderOut

# 열 자리 키. "m1".."m99"는 월(기간을 12개월 너머로 받으면 13월·14월…이 생긴다), "q1".."q4"는
# 분기, "y"는 연간. DB의 ck_overseas_cost_headers_col_key와 같은 규칙이라 둘을 같이 고쳐야 한다.
COL_KEY_RX = re.compile(r"^(m[1-9][0-9]?|q[1-4]|y)$")
# 머리글 글자 길이 상한(models.OverseasCostHeader.label). 넘치면 자르지 않고 거절한다 — 잘라서
# 저장하면 "그대로 가져온다"는 약속이 조용히 깨진 채 표에 그려진다.
MAX_LABEL = 100


def col_sort_key(col_key: str) -> tuple[int, int]:
    """표에 그리는 순서(월 → 분기 → 연간). 응답 순서를 고정해 두려고 둔다 —
    정렬하지 않으면 같은 요청이 매번 다른 순서를 돌려준다(증감사유와 같은 이유)."""
    if col_key == "y":
        return (2, 0)
    return (1, int(col_key[1:])) if col_key[0] == "q" else (0, int(col_key[1:]))


def check_labels(labels: dict[str, str]) -> dict[str, str]:
    """열 자리 키와 글자 길이를 검증하고, 빈 글자는 떨어낸 한 벌을 돌려준다.

    빈 글자를 저장하지 않는 이유: 빈 머리글은 "파일에 그 열이 없었다"와 같은 사건이고, 화면은
    그때 코드 머리글("1월")을 쓴다 — 빈 문자열을 넣어 두면 그 열만 이름 없는 칸이 된다."""
    bad = sorted(k for k in labels if not COL_KEY_RX.match(k)) if labels else []
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f'열 자리 키가 올바르지 않습니다: {bad} — "m1".."m12"(월) · "q1".."q4"(분기) · "y"(연간)만 받습니다.',
        )
    out = {k: v.strip() for k, v in (labels or {}).items() if v and v.strip()}
    long = sorted(k for k, v in out.items() if len(v) > MAX_LABEL)
    if long:
        raise HTTPException(status_code=400, detail=f"머리글이 너무 깁니다({MAX_LABEL}자 초과): {long}")
    return out


def replace_cost_headers(
    db: Session, lab_pk: str, version: str, yyyymm: int, labels: dict[str, str], source_file: str = ""
) -> int:
    """(연구소 · 버전 · 기준월) 하나의 머리글을 통째로 바꿔 넣는다. 넣은 열 수를 돌려준다.

    commit은 하지 않는다 — 호출부가 다른 쓰기와 한 트랜잭션으로 묶을 수 있어야 한다
    (services/vendor.replace_vendor_rows와 같은 약속이다)."""
    clean = check_labels(labels)
    db.execute(
        delete(OverseasCostHeader).where(
            OverseasCostHeader.lab_pk == lab_pk,
            OverseasCostHeader.version == version,
            OverseasCostHeader.yyyymm == yyyymm,
        )
    )
    if not clean:
        return 0
    db.execute(
        insert(OverseasCostHeader),
        [
            {
                "lab_pk": lab_pk, "version": version, "yyyymm": yyyymm,
                "col_key": col_key, "label": label, "source_file": source_file or "",
            }
            for col_key, label in sorted(clean.items(), key=lambda kv: col_sort_key(kv[0]))
        ],
    )
    return len(clean)


def read_cost_headers(
    db: Session, yyyymm: int, version: str | None = None, lab_pk: str | None = None
) -> list[CostHeaderOut]:
    """연구소별로 묶은 머리글 한 벌 목록. 열이 하나도 없는 연구소는 아예 나오지 않는다 —
    "머리글이 없다"와 "빈 머리글이 있다"를 화면에서 구별할 수 있어야 한다."""
    stmt = select(OverseasCostHeader).where(OverseasCostHeader.yyyymm == yyyymm)
    if version:
        stmt = stmt.where(OverseasCostHeader.version == version)
    if lab_pk:
        stmt = stmt.where(OverseasCostHeader.lab_pk == lab_pk)
    by_sheet: dict[tuple[str, str], CostHeaderOut] = {}
    for r in db.scalars(stmt.order_by(OverseasCostHeader.lab_pk, OverseasCostHeader.id)):
        sheet = by_sheet.get((r.lab_pk, r.version))
        if sheet is None:
            sheet = by_sheet[(r.lab_pk, r.version)] = CostHeaderOut(
                lab_pk=r.lab_pk, version=r.version, yyyymm=r.yyyymm, labels={}, source_file=r.source_file
            )
        sheet.labels[r.col_key] = r.label
    return [by_sheet[k] for k in sorted(by_sheet)]
