"""19개 비용계정 레지스트리(대시보드 카드) 조회·수정.

accounts·account_steps·account_progress 테이블은 처음부터 있었지만 이 엔드포인트가 없어서,
대시보드의 계정 상태·비고·진행 단계를 읽거나 저장할 방법이 아예 없었다(해외연구소 단계만
/overseas/step으로 따로 열려 있었다). 그래서 "어느 계정이 아직 미제출이야?" 같은 질문에 챗봇이
답할 수 없었고, 화면이 상태를 고쳐도 서버에 남지 않았다.

단계(account_steps)는 계정마다 개수·이름이 다르고 account_progress.current_step_order가
복합 FK로 "그 계정에 실제로 있는 단계"만 가리키도록 강제돼 있다 — 그래서 단계 이동은 그 계정에
정의된 범위를 넘으면 DB가 막아주고, 여기서는 그 위반을 400으로 바꿔 돌려준다.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.shared.core.enums import CodeGroup
from app.db.session import get_db
from app.shared.models.account import Account, AccountProgress, AccountStep
from app.shared.models.common import CostEntry, CostEntryDetail
from app.shared.models.system import CommonCode
from app.shared.schemas.account import (
    AccountOut,
    AccountStepOut,
    AccountStepUpdate,
    AccountSummaryIn,
    AccountSummaryOut,
    AccountUpdate,
)

router = APIRouter()


def _check_yyyymm(yyyymm: str) -> None:
    """"202609" 모양만 받는다 — 형식이 틀리면 조용히 다른 버전 칸에 쓰게 된다."""
    if not (len(yyyymm) == 6 and yyyymm.isdigit() and 1 <= int(yyyymm[4:]) <= 12):
        raise HTTPException(status_code=400, detail=f'yyyymm은 "YYYYMM" 형식이어야 합니다: {yyyymm!r}')


def _status_labels(db: Session) -> dict[str, str]:
    """ACCOUNT_STATUS 코드 -> 한글명. 화면·챗봇이 'not_submitted' 같은 내부 코드를 그대로
    보여주지 않도록 서버가 미리 풀어서 내려준다."""
    return {
        c.code: c.name
        for c in db.scalars(
            select(CommonCode).where(CommonCode.group_code == CodeGroup.ACCOUNT_STATUS.value)
        )
    }


def _build_out(
    accounts: list[Account],
    steps_by_account: dict[int, list[AccountStep]],
    step_by_account: dict[int, int],
    labels: dict[str, str],
) -> list[AccountOut]:
    return [
        AccountOut(
            key=a.key,
            name=a.name,
            data_type=a.data_type,
            color=a.color,
            status=a.status,
            status_label=labels.get(a.status, a.status),
            owner=a.owner or "",
            memo=a.memo,
            current_step_order=step_by_account.get(a.id),
            steps=[
                AccountStepOut(step_order=s.step_order, tag=s.tag, title=s.title)
                for s in sorted(steps_by_account.get(a.id, []), key=lambda s: s.step_order)
            ],
            updated_at=a.updated_at,
        )
        for a in accounts
    ]


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)) -> list[AccountOut]:
    """19개 계정 전체를 단계·진행상황까지 묶어서 돌려준다.

    단계와 진행상황은 계정마다 따로 조회하지 않고 한 번씩만 읽어서 메모리에서 묶는다 —
    계정 수만큼 쿼리가 늘면(19배) 대시보드 첫 화면이 그만큼 느려진다."""
    accounts = list(db.scalars(select(Account).order_by(Account.id)))
    steps_by_account: dict[int, list[AccountStep]] = {}
    for s in db.scalars(select(AccountStep)):
        steps_by_account.setdefault(s.account_id, []).append(s)
    step_by_account = {
        p.account_id: p.current_step_order for p in db.scalars(select(AccountProgress))
    }
    return _build_out(accounts, steps_by_account, step_by_account, _status_labels(db))


# ---------- 전체 현황판 숫자 ----------
# 아래 두 경로는 /accounts/{key}보다 **먼저** 선언해야 한다 — 경로 매칭은 선언 순서라서,
# 뒤에 두면 "summaries"가 계정 key로 잡혀 404가 된다.
# 전체 현황판 요약이 들어가는 자리. 계정 전용 화면이 쓰는 잘게 쪼갠 행들과 섞이지 않도록
# scope·category를 고정해 둔다 — 해외연구소의 실제 값은 scope_code가 연구소 pk라서 겹치지 않는다.
SUMMARY_SCOPE = "ALL"
SUMMARY_CATEGORY = "total"
PRIOR_CATEGORY = "prior"


def _summary_header(db: Session, key: str, yyyymm: str, version: str, category: str) -> CostEntry | None:
    return db.scalar(
        select(CostEntry).where(
            CostEntry.account_code == key,
            CostEntry.scope_code == SUMMARY_SCOPE,
            CostEntry.category_code == category,
            CostEntry.yyyymm == yyyymm,
            CostEntry.version == version,
        )
    )


def _summary_of(db: Session, a: Account, yyyymm: str, version: str) -> AccountSummaryOut:
    year = int(yyyymm[:4])
    monthly = [0.0] * 12
    head = _summary_header(db, a.key, yyyymm, version, SUMMARY_CATEGORY)
    if head is not None:
        for d in db.scalars(
            select(CostEntryDetail).where(
                CostEntryDetail.cost_entry_id == head.id,
                CostEntryDetail.period_type == "MONTH",
                CostEntryDetail.year == year,
            )
        ):
            if 1 <= d.period <= 12:
                monthly[d.period - 1] = float(d.amount or 0)
    prior = 0.0
    ph = _summary_header(db, a.key, yyyymm, version, PRIOR_CATEGORY)
    if ph is not None:
        row = db.scalar(
            select(CostEntryDetail).where(
                CostEntryDetail.cost_entry_id == ph.id,
                CostEntryDetail.period_type == "YEAR",
                CostEntryDetail.year == year - 1,
            )
        )
        prior = float(row.amount or 0) if row is not None else 0.0
    return AccountSummaryOut(
        key=a.key, name=a.name, monthly=monthly, total=sum(monthly), prior_amount=prior
    )


@router.get("/accounts/summaries", response_model=list[AccountSummaryOut])
def list_account_summaries(
    yyyymm: str, version: str = "forecast", db: Session = Depends(get_db)
) -> list[AccountSummaryOut]:
    """19개 계정의 그 버전 월별 편성액·전년 실적 총액 — 전체 현황판이 한 번에 받는다.

    계정마다 따로 부르지 않는 이유는 list_accounts와 같다(19배 쿼리면 첫 화면이 그만큼 느려진다).
    값이 없는 계정은 0으로 내려간다 — 행이 없는 것과 0원을 화면에서 구분할 필요가 없고(둘 다
    "아직 편성 안 됨"이다), 구분하려 들면 현황판이 빈칸과 0을 섞어 보여준다."""
    _check_yyyymm(yyyymm)
    return [_summary_of(db, a, yyyymm, version) for a in db.scalars(select(Account).order_by(Account.id))]


@router.put("/accounts/{key}/summary", response_model=AccountSummaryOut)
def put_account_summary(
    key: str, body: AccountSummaryIn, db: Session = Depends(get_db)
) -> AccountSummaryOut:
    """계정 하나의 현황판 숫자를 저장한다(월 12칸 + 전년 총액을 한 번에 교체).

    칸 단위 `/cost-entries`로도 쓸 수 있지만, 화면이 12번 + 1번을 따로 보내면 중간에 실패했을 때
    현황판 총액이 반만 바뀐 상태로 남는다. 현황판 숫자는 한 덩어리로 봐야 하는 값이라 한 번에 받는다."""
    _check_yyyymm(body.yyyymm)
    if body.version not in ("forecast", "actual"):
        raise HTTPException(status_code=400, detail="version은 forecast 또는 actual이어야 합니다.")
    account = db.scalar(select(Account).where(Account.key == key))
    if account is None:
        raise HTTPException(status_code=404, detail=f"그런 계정이 없습니다: {key}")
    year = int(body.yyyymm[:4])
    months = [float(v or 0) for v in (body.monthly or [])][:12]
    months += [0.0] * (12 - len(months))

    for category, rows in (
        (SUMMARY_CATEGORY, [("MONTH", year, m + 1, months[m]) for m in range(12)]),
        (PRIOR_CATEGORY, [("YEAR", year - 1, 0, float(body.prior_amount or 0))]),
    ):
        head = _summary_header(db, key, body.yyyymm, body.version, category)
        if head is None:
            head = CostEntry(
                account_code=key, scope_code=SUMMARY_SCOPE, category_code=category,
                yyyymm=body.yyyymm, version=body.version,
            )
            db.add(head)
            db.flush()
        for period_type, y, period, amount in rows:
            row = db.scalar(
                select(CostEntryDetail).where(
                    CostEntryDetail.cost_entry_id == head.id,
                    CostEntryDetail.year == y,
                    CostEntryDetail.period_type == period_type,
                    CostEntryDetail.period == period,
                    CostEntryDetail.col_name == "",
                )
            )
            if row is None:
                db.add(
                    CostEntryDetail(
                        cost_entry_id=head.id, year=y, period_type=period_type, period=period,
                        col_name="", amount=amount,
                    )
                )
            else:
                row.amount = amount
    db.commit()
    return _summary_of(db, account, body.yyyymm, body.version)


@router.get("/accounts/{key}", response_model=AccountOut)
def get_account(key: str, db: Session = Depends(get_db)) -> AccountOut:
    account = db.scalar(select(Account).where(Account.key == key))
    if account is None:
        raise HTTPException(status_code=404, detail=f"그런 계정이 없습니다: {key}")
    steps = list(db.scalars(select(AccountStep).where(AccountStep.account_id == account.id)))
    progress = db.scalar(select(AccountProgress).where(AccountProgress.account_id == account.id))
    return _build_out(
        [account],
        {account.id: steps},
        {account.id: progress.current_step_order} if progress else {},
        _status_labels(db),
    )[0]


@router.put("/accounts/{key}", response_model=AccountOut)
def update_account(key: str, body: AccountUpdate, db: Session = Depends(get_db)) -> AccountOut:
    """계정 카드의 상태·편성 주체·비고를 고친다. status는 common_codes(ACCOUNT_STATUS)에 등록된 코드만
    받는다 — 오타가 들어가면 화면의 상태 필터·집계가 조용히 어긋나기 때문이다."""
    account = db.scalar(select(Account).where(Account.key == key))
    if account is None:
        raise HTTPException(status_code=404, detail=f"그런 계정이 없습니다: {key}")
    labels = _status_labels(db)
    if body.status is not None:
        if body.status not in labels:
            raise HTTPException(
                status_code=400,
                detail=f"알 수 없는 상태 코드: {body.status!r} (가능한 값: {', '.join(sorted(labels))})",
            )
        account.status = body.status
    if body.owner is not None:
        account.owner = body.owner.strip()
    if body.memo is not None:
        account.memo = body.memo
    db.commit()
    return get_account(key, db)


@router.put("/accounts/{key}/step", response_model=AccountOut)
def update_account_step(key: str, body: AccountStepUpdate, db: Session = Depends(get_db)) -> AccountOut:
    """계정의 현재 진행 단계를 옮긴다. 그 계정의 account_steps에 실제로 있는 단계만 받는다
    (복합 FK로 DB가 강제한다 — 여기서는 그 위반을 읽을 수 있는 400으로 바꿔준다).

    해외연구소는 기존 /overseas/step과 같은 행(account_progress)을 가리키므로 어느 쪽으로
    바꿔도 결과가 같다 — 화면이 쓰던 경로를 그대로 둔 채 다른 계정만 이걸 쓰면 된다."""
    account = db.scalar(select(Account).where(Account.key == key))
    if account is None:
        raise HTTPException(status_code=404, detail=f"그런 계정이 없습니다: {key}")
    progress = db.scalar(select(AccountProgress).where(AccountProgress.account_id == account.id))
    if progress is None:
        db.add(AccountProgress(account_id=account.id, current_step_order=body.step))
    else:
        progress.current_step_order = body.step
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        defined = sorted(
            db.scalars(select(AccountStep.step_order).where(AccountStep.account_id == account.id))
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{key}' 계정에 정의되지 않은 단계입니다: {body.step}. "
                + (f"가능한 단계: {defined}" if defined else "이 계정은 아직 편성 단계가 정의되지 않았습니다.")
            ),
        ) from e
    return get_account(key, db)
