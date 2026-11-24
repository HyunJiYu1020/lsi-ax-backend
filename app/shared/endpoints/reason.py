"""증감사유 조회·저장 — 전 계정 공용(`variance_reasons`).

여태 이 테이블을 쓰는 경로가 `/overseas/variance-reasons` 하나였고 계정이 "overseas"로 박혀
있었다. 그래서 감가상각비·위탁용역비·생소비 화면의 사유는 저장할 곳이 없어 브라우저에만 남았다 —
보고서에 그대로 실리는 문장인데, 브라우저를 비우면 사라지고 옆자리에서는 안 보였다.

해외연구소 경로를 그대로 두고 공용 경로를 따로 연 이유: 그쪽은 회신 원문 번역·AI 초안·서버가
계산한 지금 증감액까지 한 응답에 묶어 주고, 1차 고정(보고까지 끝난 값) 검증이 붙어 있다. 그
무게를 전 계정 공용 경로에 넣으면 아직 수식이 서버에 없는 계정이 "증감액 0"을 받아 재확인 표시가
엉뚱하게 켜진다. 공용 경로는 **사유 문장을 잃지 않는 것**만 책임진다.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.shared.models.account import Account
from app.shared.models.generic import VarianceReason
from app.shared.schemas.reason import ReasonOut, ReasonsIn, ReasonsOut

router = APIRouter()

KINDS = ("forecast", "actual")
STAGES = ("", "first", "second")
BASES = ("prev", "prior", "plan")


def _check(db: Session, account_key: str, kind: str, stage: str, base_version: str, period: str) -> None:
    if not db.scalar(select(Account.key).where(Account.key == account_key)):
        raise HTTPException(404, f"그런 계정이 없습니다: {account_key!r}")
    if kind not in KINDS:
        raise HTTPException(400, f"kind는 {' | '.join(KINDS)}만 가능합니다: {kind!r}")
    if stage not in STAGES:
        raise HTTPException(400, "stage는 빈 문자열(속보) · first · second만 가능합니다.")
    if base_version not in BASES:
        raise HTTPException(400, f"base_version은 {' | '.join(BASES)}만 가능합니다: {base_version!r}")
    # 기간은 연간 · 분기 · 월만. 자유 문자열로 두면 같은 칸이 "y"와 "Y"로 두 줄 생긴다.
    if not (period == "y" or period in {f"q{i}" for i in range(1, 5)} or period in {f"m{i}" for i in range(1, 13)}):
        raise HTTPException(400, f'period는 "y" · q1~q4 · m1~m12만 가능합니다: {period!r}')


def _where(body, *extra):
    return (
        VarianceReason.account_key == body.account_key,
        VarianceReason.basis_year == body.basis_year,
        VarianceReason.basis_month == body.basis_month,
        VarianceReason.kind == body.kind,
        VarianceReason.stage == body.stage,
        VarianceReason.base_version == body.base_version,
        VarianceReason.period == body.period,
        VarianceReason.lab_scope == body.scope,
        *extra,
    )


def _out(r: VarianceReason) -> ReasonOut:
    return ReasonOut(
        category_key=r.category_key, reason_text=r.reason_text or "", source_text=r.source_text or "",
        ai_draft=r.ai_draft or "", diff=float(r.saved_diff) if r.saved_diff is not None else None,
        saved_diff=float(r.saved_diff) if r.saved_diff is not None else None,
        needs_recheck=bool(r.needs_recheck), saved_at=r.saved_at.isoformat() if r.saved_at else "",
    )


@router.get("/variance-reasons", response_model=ReasonsOut)
def list_reasons(
    account_key: str,
    basis_year: int,
    basis_month: int,
    kind: str = "forecast",
    stage: str = "",
    base_version: str = "prev",
    period: str = "y",
    scope: str = "ALL",  # 계정 안의 범위. 구분이 없는 계정은 "ALL"
    db: Session = Depends(get_db),
) -> ReasonsOut:
    """그 자리(계정 · 버전 · 비교 기준 · 기간 · 범위)의 사유 칸 전부.

    지금 증감액은 내려주지 않는다 — 그 수식은 계정마다 다르고 아직 화면에 있다. 화면이 자기
    증감액과 `saved_diff`를 맞대어 "재확인 필요"를 판단한다(그 둘이 다른 게 곧 재확인 사유다)."""
    key = ReasonsIn(
        account_key=account_key, basis_year=basis_year, basis_month=basis_month, kind=kind,
        stage=stage, base_version=base_version, period=period, scope=scope,
    )
    _check(db, account_key, kind, stage, base_version, period)
    rows = db.scalars(select(VarianceReason).where(*_where(key)).order_by(VarianceReason.category_key))
    return ReasonsOut(**key.model_dump(exclude={"rows"}), rows=[_out(r) for r in rows])


@router.put("/variance-reasons", response_model=ReasonsOut)
def put_reasons(body: ReasonsIn, db: Session = Depends(get_db)) -> ReasonsOut:
    """사유 칸들을 한 번에 저장한다. 빈 문장으로 보낸 칸은 지운다.

    왜 지우는가: 담당자가 지운 문장을 서버가 들고 있으면 다음 조회에 되살아나고, 그게 보고서에
    다시 실린다. 반대로 **안 보낸 칸은 건드리지 않는다** — 화면이 한 번에 보는 칸만 보내기 때문에,
    안 보낸 칸까지 지우면 다른 기간·다른 범위의 사유가 조용히 사라진다."""
    _check(db, body.account_key, body.kind, body.stage, body.base_version, body.period)
    for r in body.rows:
        if not r.category_key:
            raise HTTPException(400, "category_key는 비울 수 없습니다(사유 칸의 이름입니다).")

    for r in body.rows:
        text = (r.reason_text or "").strip()
        row = db.scalar(select(VarianceReason).where(*_where(body, VarianceReason.category_key == r.category_key)))
        if not text and not (r.source_text or r.ai_draft):
            if row is not None:
                db.delete(row)
            continue
        if row is None:
            row = VarianceReason(
                account_key=body.account_key, basis_year=body.basis_year, basis_month=body.basis_month,
                kind=body.kind, stage=body.stage, base_version=body.base_version, period=body.period,
                lab_scope=body.scope, category_key=r.category_key,
            )
            db.add(row)
        row.reason_text = text
        row.source_text = r.source_text or ""
        row.ai_draft = r.ai_draft or ""
        # 저장 시점 증감액을 새로 박고 재확인 표시를 지운다 — 담당자가 지금 금액을 보고 쓴 문장이다.
        if r.diff is not None:
            row.saved_diff = r.diff
        row.needs_recheck = False
        row.saved_at = func.now()
    db.commit()
    return list_reasons(
        account_key=body.account_key, basis_year=body.basis_year, basis_month=body.basis_month,
        kind=body.kind, stage=body.stage, base_version=body.base_version, period=body.period,
        scope=body.scope, db=db,
    )


@router.post("/variance-reasons/recheck")
def mark_recheck(
    account_key: str,
    basis_year: int,
    basis_month: int,
    kind: str = "forecast",
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    """그 버전 사유 전부에 "재확인 필요"를 켠다 — 편성값을 다시 올려 금액이 바뀐 직후에 쓴다.

    사유 문장은 지우지 않는다(담당자가 쓴 글을 서버가 버리면 안 된다). 금액이 바뀌었다는 사실만
    표시해 두고, 다시 확인하는 건 사람이 한다."""
    if kind not in KINDS:
        raise HTTPException(400, f"kind는 {' | '.join(KINDS)}만 가능합니다: {kind!r}")
    if not db.scalar(select(Account.key).where(Account.key == account_key)):
        raise HTTPException(404, f"그런 계정이 없습니다: {account_key!r}")
    n = (
        db.query(VarianceReason)
        .filter(
            VarianceReason.account_key == account_key,
            VarianceReason.basis_year == basis_year,
            VarianceReason.basis_month == basis_month,
            VarianceReason.kind == kind,
        )
        .update({VarianceReason.needs_recheck: True}, synchronize_session=False)
    )
    db.commit()
    return {"marked": n, "account_key": account_key}


@router.delete("/variance-reasons")
def delete_reasons(
    account_key: str,
    basis_year: int,
    basis_month: int,
    kind: str = "forecast",
    stage: str = "",
    base_version: str = "prev",
    period: str = "y",
    scope: str = "ALL",
    category_key: str | None = None,  # 비우면 그 자리의 사유 전부
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    """사유를 지운다(칸 하나 또는 그 자리 전부)."""
    key = ReasonsIn(
        account_key=account_key, basis_year=basis_year, basis_month=basis_month, kind=kind,
        stage=stage, base_version=base_version, period=period, scope=scope,
    )
    _check(db, account_key, kind, stage, base_version, period)
    extra = (VarianceReason.category_key == category_key,) if category_key else ()
    n = db.execute(delete(VarianceReason).where(*_where(key, *extra))).rowcount
    db.commit()
    return {"deleted": n, "account_key": account_key}
