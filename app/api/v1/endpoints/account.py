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

from app.core.enums import CodeGroup
from app.db.session import get_db
from app.models import Account, AccountProgress, AccountStep, CommonCode
from app.schemas.account import AccountOut, AccountStepOut, AccountStepUpdate, AccountUpdate

router = APIRouter()


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
    """계정 카드의 상태·비고를 고친다. status는 common_codes(ACCOUNT_STATUS)에 등록된 코드만
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
