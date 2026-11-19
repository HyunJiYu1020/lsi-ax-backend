"""이미 적재된 행 합치기 (TC-008).

별칭(row_alias)과 짝이지만 하는 일이 다르다: 별칭은 **앞으로 올라올** 파일의 이름을 맞추고,
이건 **이미 들어간** 금액을 옮긴다. 담당자가 "Other랑 Misc 기타경비로 합쳐줘" 한 마디로 둘 다
원하므로 보통 같이 호출되지만, 별칭은 DELETE로 되돌릴 수 있고 합치기는 되돌릴 수 없어서(원본
행이 지워진다) 따로 둔다 — 담당자가 "앞으로만 적용하고 이번 달은 그대로 둬"를 고를 수 있어야 한다.
"""

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.accounts.overseas.cost_groups import CATEGORY_LABELS, GROUP_CALC, category_label
from app.shared.core import row_match
from app.shared.core.confirm_state import unconfirm_account
from app.shared.core.periods import ym_year
from app.shared.core.values import n
from app.shared.models.common import CostEntry
from app.accounts.overseas.schemas import RowMergeIn, RowMergePreview, RowMergeResult
from app.accounts.overseas.services.entries import (
    current_basis_yyyymm,
    entry_months_bulk,
    get_or_create_entry,
    resync_summaries,
    upsert_entry_months,
)
from app.accounts.overseas.services.extra_rows import check_lab_pk, check_version
from app.accounts.overseas.services.labs import ACCOUNT_KEY


def merge_target(db: Session, body: RowMergeIn) -> tuple[str, str, str]:
    """합쳐 넣을 곳을 검증하고 (category_code, label, label_en)을 돌려준다.

    집계 그룹 행(개발비(A)·일반운영비 등)을 막는 이유: 그 줄은 하위 항목의 합으로 계산된다. 거기에
    값을 꽂으면 리프 합과 안 맞아서, 표 안에서 숫자가 서로 어긋난다(row-match가 후보에서 그룹 행을
    빼는 것과 같은 이유)."""
    if body.target_kind == "category":
        key = (body.target_key or "").strip()
        if key not in CATEGORY_LABELS:
            raise HTTPException(400, f"없는 비용항목입니다: {key!r}")
        if key in GROUP_CALC or key == "pct":
            raise HTTPException(
                400,
                f"'{category_label(key)}'은 하위 항목의 합으로 계산되는 행이라 값을 넣을 수 없습니다. "
                "합칠 곳으로 리프 항목을 지정하세요.",
            )
        # 고정 계정 행은 label이 NULL이다(업로드로 들어온 행). 이름은 코드가 갖고 있다.
        return key, "", ""
    if body.target_kind == "extra_row":
        label = (body.target_label or "").strip()
        if not label:
            raise HTTPException(400, "target_label(합쳐 넣을 추가 행의 정식 이름)이 비어 있습니다.")
        return row_match.normalize(label), label, ""
    raise HTTPException(400, "target_kind는 category 또는 extra_row만 가능합니다.")


def merge_rows(db: Session, body: RowMergeIn) -> RowMergeResult:
    """이미 적재된 추가 행의 금액을 다른 행으로 합쳐 옮긴다. 원본 행은 지운다.

    **지나간 칸은 건드리지 않는다.** yyyymm이 지금 편성 기준월보다 이전이면 거절한다 — 이미 그
    숫자로 보고가 나간 칸을 뒤에서 바꾸면 총액은 그대로인데 계정별 줄이 달라져서, 그 달 보고서를
    다시 뽑으면 기타경비가 늘어난 걸로 나온다. 지나간 달을 고쳐야 하면 그 달 파일을 다시
    업로드하는 게 맞다(버전 반영 규칙을 정상적으로 타니까).

    되돌릴 수 없는 작업이라(원본 행이 cascade로 디테일까지 지워진다) dry_run=True가 기본이다.
    호출부가 먼저 미리보기로 보여주고 사람 확인을 받은 뒤 dry_run=False로 다시 부른다.

    합친 뒤 분기·연간을 반드시 재계산한다(resync_summaries) — upsert_entry_months는 MONTH 행만
    쓰기 때문에, 이걸 빼면 월은 맞고 분기·연간은 합치기 전 값에 멈춘다.
    """
    version = check_version(body.version)
    if len(body.yyyymm) != 6 or not body.yyyymm.isdigit():
        raise HTTPException(400, 'yyyymm은 "YYYYMM" 6자리여야 합니다.')
    basis = current_basis_yyyymm(db)
    if basis and body.yyyymm < basis:
        raise HTTPException(
            400,
            f"지나간 칸({body.yyyymm})은 합칠 수 없습니다 — 지금 편성 기준월은 {basis}입니다. "
            "이미 보고된 숫자를 뒤에서 바꾸게 됩니다. 그 달을 고쳐야 하면 파일을 다시 업로드하세요.",
        )
    norms = {row_match.normalize(x) for x in body.source_labels if row_match.normalize(x)}
    if not norms:
        raise HTTPException(400, "source_labels(합칠 행 이름)가 비어 있습니다.")
    target_key, target_label, target_label_en = merge_target(db, body)
    if target_key in norms:
        raise HTTPException(400, "합칠 행과 합쳐 넣을 행이 같습니다.")
    if body.lab_pk:
        check_lab_pk(db, body.lab_pk)

    # source는 "연구소가 추가한 원시 행"만이다. label IS NOT NULL이 그 조건이고(고정 계정은 NULL),
    # 파생 행은 월값을 저장하지 않아서 옮길 금액이 없다.
    stmt = select(CostEntry).where(
        CostEntry.account_code == ACCOUNT_KEY,
        CostEntry.version == version,
        CostEntry.yyyymm == body.yyyymm,
        CostEntry.label.is_not(None),
        CostEntry.is_derived.is_(False),
    )
    if body.lab_pk:
        stmt = stmt.where(CostEntry.scope_code == body.lab_pk)
    sources = [
        e
        for e in db.scalars(stmt)
        if row_match.normalize(e.label or "") in norms
        or row_match.normalize(e.label_en or "") in norms
    ]

    target_name = target_label or category_label(target_key)
    if not sources:
        return RowMergeResult(
            dry_run=body.dry_run, yyyymm=body.yyyymm, version=version, target_label=target_name,
            note=f"합칠 행이 없습니다 — {body.yyyymm} {version}에 "
                 f"{' · '.join(sorted(norms))} 이름으로 적재된 추가 행이 없습니다.",
        )

    # 연구소별로 따로 합친다. 같은 이름이 12개 연구소에 흩어져 있는 게 애초의 문제이고, 연구소를
    # 섞어 더하면 그 연구소 실적이 다른 연구소로 옮겨간다.
    by_scope: dict[str, list[CostEntry]] = {}
    for e in sources:
        by_scope.setdefault(e.scope_code, []).append(e)

    preview: list[RowMergePreview] = []
    base_year = ym_year(body.yyyymm)
    touched: set[tuple[int, int]] = set()
    removed = 0

    for scope, rows in by_scope.items():
        target_id = get_or_create_entry(db, scope, version, target_key, body.yyyymm)
        target = db.get(CostEntry, target_id)
        months = entry_months_bulk(db, [target] + rows)
        before = months.get(target_id) or []
        width = max([len(before)] + [len(months.get(e.id) or []) for e in rows] + [12])

        def at(vals: list[float | None], i: int) -> float:
            return n(vals[i]) if i < len(vals) else 0.0

        merged = [
            at(before, i) + sum(at(months.get(e.id) or [], i) for e in rows) for i in range(width)
        ]
        before_total = sum(at(before, i) for i in range(width))
        for e in rows:
            vals = months.get(e.id) or []
            preview.append(
                RowMergePreview(
                    lab_pk=scope,
                    source_label=e.label or e.label_en or "",
                    source_total=round(sum(at(vals, i) for i in range(width)), 3),
                    target_before=round(before_total, 3),
                    target_after=round(sum(merged), 3),
                )
            )
        if body.dry_run:
            continue

        # 추가 행으로 합치는 경우 대상 행의 이름·영문명을 정식 이름으로 맞춰 둔다 — 집계표는 행
        # 이름으로 묶기 때문에(ovPLCustomRows) 이름이 어긋나면 합쳤는데 두 줄로 보인다.
        if body.target_kind == "extra_row":
            target.label = target_label
            if target_label_en and not target.label_en:
                target.label_en = target_label_en
            if target.anchor_key is None:
                target.anchor_key = next((e.anchor_key for e in rows if e.anchor_key), None)
        upsert_entry_months(db, target_id, base_year, merged)
        touched.add((target_id, base_year))
        for i in range(1, (width // 12)):
            touched.add((target_id, base_year + i))
        db.execute(delete(CostEntry).where(CostEntry.id.in_([e.id for e in rows])))
        removed += len(rows)

    result = RowMergeResult(
        dry_run=body.dry_run, yyyymm=body.yyyymm, version=version,
        target_label=target_name, merged=preview, removed_rows=removed,
    )
    if body.dry_run:
        result.note = (
            f"{len(preview)}건을 '{target_name}'(으)로 합칩니다 — 되돌릴 수 없습니다. "
            "확인한 뒤 dry_run=false로 다시 호출하세요."
        )
        return result

    # 분기·연간을 다시 계산한다. 빼먹으면 월은 맞는데 분기·연간이 합치기 전 값에 멈춘다.
    resync_summaries(db, touched)
    # 값이 바뀌었으니 확정은 거짓이 된다 — 화면의 ovUnconfirm과 같은 처리를 서버도 한다.
    result.unconfirmed = unconfirm_account(db, body.yyyymm, version, ACCOUNT_KEY)
    db.commit()
    result.note = (
        f"{len(preview)}건을 '{target_name}'(으)로 합쳐 넣고 행 {removed}개를 지웠습니다."
        + (" 확정은 작성 중으로 되돌렸습니다." if result.unconfirmed else "")
    )
    return result
