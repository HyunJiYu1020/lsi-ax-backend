"""19개 계정 레지스트리 엔드포인트(/accounts) 테스트.

conftest의 db 픽스처가 끝나면 롤백하므로, 엔드포인트 함수에 그 세션을 직접 넘겨서
(TestClient 대신) 실제 DB를 건드리지 않고 돌린다 — 엔드포인트가 내부에서 commit()을 해도
픽스처 쪽 트랜잭션이 롤백되면서 되돌아간다.
"""

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.account import (
    get_account,
    list_accounts,
    update_account,
    update_account_step,
)
from app.schemas.account import AccountStepUpdate, AccountUpdate


def test_list_accounts_returns_all_seeded_accounts(db):
    accounts = list_accounts(db=db)
    assert len(accounts) == 19, "시드된 19개 비용계정이 모두 나와야 한다"
    keys = {a.key for a in accounts}
    assert {"overseas", "dep", "outsource", "consum"} <= keys


def test_status_label_is_resolved_from_common_codes(db):
    """화면·챗봇이 'drafting' 같은 코드를 그대로 보여주지 않도록 서버가 한글명을 붙여준다."""
    overseas = next(a for a in list_accounts(db=db) if a.key == "overseas")
    assert overseas.status == "drafting"
    assert overseas.status_label == "작성중"


def test_steps_only_for_accounts_with_a_real_screen(db):
    """전용 편성 화면이 있는 4개 계정만 단계가 있고, 나머지는 빈 배열 + current_step_order=None."""
    by_key = {a.key: a for a in list_accounts(db=db)}
    assert len(by_key["overseas"].steps) == 3
    assert len(by_key["dep"].steps) == 5
    assert [s.step_order for s in by_key["dep"].steps] == [1, 2, 3, 4, 5]

    stub = by_key["patent"]  # 아직 "준비 중" 스텁인 계정
    assert stub.steps == []
    assert stub.current_step_order is None


def test_update_account_memo_does_not_clear_status(db):
    """memo만 보냈을 때 status가 지워지면 안 된다(보내지 않은 필드는 그대로)."""
    before = get_account("patent", db=db)
    updated = update_account("patent", AccountUpdate(memo="야간 점검 메모"), db=db)
    assert updated.memo == "야간 점검 메모"
    assert updated.status == before.status


def test_update_account_rejects_unknown_status(db):
    with pytest.raises(HTTPException) as e:
        update_account("patent", AccountUpdate(status="존재하지않는상태"), db=db)
    assert e.value.status_code == 400
    assert "알 수 없는 상태 코드" in e.value.detail


def test_update_account_unknown_key_is_404(db):
    with pytest.raises(HTTPException) as e:
        update_account("없는계정", AccountUpdate(memo="x"), db=db)
    assert e.value.status_code == 404


def test_step_move_within_defined_range(db):
    updated = update_account_step("dep", AccountStepUpdate(step=3), db=db)
    assert updated.current_step_order == 3


def test_step_beyond_defined_range_is_rejected_with_the_valid_list(db):
    """account_progress는 복합 FK로 그 계정에 실제 있는 단계만 가리킬 수 있다 —
    범위를 넘으면 DB 위반이 400으로 번역되고, 가능한 단계가 메시지에 들어 있어야 한다."""
    with pytest.raises(HTTPException) as e:
        update_account_step("dep", AccountStepUpdate(step=99), db=db)
    assert e.value.status_code == 400
    assert "정의되지 않은 단계" in e.value.detail
    assert "[1, 2, 3, 4, 5]" in e.value.detail


def test_step_rejected_for_account_without_steps(db):
    """단계가 아예 정의되지 않은 '준비 중' 계정은 어떤 단계도 받을 수 없다."""
    with pytest.raises(HTTPException) as e:
        update_account_step("patent", AccountStepUpdate(step=1), db=db)
    assert e.value.status_code == 400
    assert "편성 단계가 정의되지 않았습니다" in e.value.detail
