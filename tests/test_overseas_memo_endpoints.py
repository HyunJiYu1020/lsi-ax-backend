import pytest
from sqlalchemy import delete

from app.api.v1.endpoints.overseas import (
    delete_memo_col,
    list_memo_cols,
    list_memos,
    upsert_memo,
    upsert_memo_col,
)
from app.models import Memo, MemoCol
from app.schemas.overseas import MemoColIn, MemoIn

# 실제 데이터와 안 겹치는 가짜 열 키로 테스트한다.
# 이 엔드포인트들은 (화면 라우트와 동일하게) 내부에서 db.commit()을 호출하므로 conftest의 db
# fixture(테스트 끝나고 rollback)로는 정리가 안 된다 — 그래서 여기서 직접 지우고 커밋한다.
TEST_COL_KEY = "c_test_9999"


@pytest.fixture(autouse=True)
def _cleanup(db):
    yield
    db.execute(delete(Memo).where(Memo.col_key == TEST_COL_KEY))
    db.execute(delete(MemoCol).where(MemoCol.col_key == TEST_COL_KEY))
    db.commit()


def test_memo_col_and_memo_round_trip(db):
    upsert_memo_col(MemoColIn(lab_pk="SARC:CPU", version="cur", col_key=TEST_COL_KEY, label="테스트메모열"), db)

    cols = list_memo_cols(version="cur", lab_pk="SARC:CPU", db=db)
    assert any(c.col_key == TEST_COL_KEY and c.label == "테스트메모열" for c in cols)

    upsert_memo(MemoIn(lab_pk="SARC:CPU", version="cur", row_key="hc", col_key=TEST_COL_KEY, text="메모내용"), db)

    memos = list_memos(version="cur", lab_pk="SARC:CPU", db=db)
    assert any(m.row_key == "hc" and m.col_key == TEST_COL_KEY and m.text == "메모내용" for m in memos)


def test_delete_memo_col_cascades_memos(db):
    upsert_memo_col(MemoColIn(lab_pk="SARC:CPU", version="cur", col_key=TEST_COL_KEY, label="테스트메모열"), db)
    upsert_memo(MemoIn(lab_pk="SARC:CPU", version="cur", row_key="hc", col_key=TEST_COL_KEY, text="메모내용"), db)

    delete_memo_col(lab_pk="SARC:CPU", version="cur", col_key=TEST_COL_KEY, db=db)

    # 이 테스트가 보는 건 "지운 열과 그 열의 메모가 같이 사라졌는가"지 "DB가 비었는가"가 아니다 —
    # 목록 전체를 []와 비교하면 같은 연구소·버전에 다른 메모 열이 하나라도 있을 때(실제 운영
    # 데이터든 다른 테스트가 남긴 것이든) 엉뚱하게 실패한다. 그래서 이 테스트가 만든 키만 확인한다.
    assert all(c.col_key != TEST_COL_KEY for c in list_memo_cols(version="cur", lab_pk="SARC:CPU", db=db))
    assert all(m.col_key != TEST_COL_KEY for m in list_memos(version="cur", lab_pk="SARC:CPU", db=db))
