import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import engine


@pytest.fixture
def db():
    """실제 로컬 DB에 붙여서 테스트하고, 끝나면 롤백해서 변경사항을 남기지 않는다.
    DB에 연결할 수 없으면(로컬 Postgres가 안 떠 있으면) 테스트를 스킵한다.

    세션을 그냥 만들어 쓰면 안 된다 — 엔드포인트 함수는 대부분 내부에서 db.commit()을 하는데,
    그게 진짜 COMMIT이면 테스트가 끝나고 롤백해도 이미 늦어서 개발용 DB에 테스트 데이터가 그대로
    남는다(실제로 계정 메모·진행 단계가 덮어써진 적이 있다). 그래서 바깥 트랜잭션을 직접 열고
    세션을 거기에 SAVEPOINT로 붙인다 — 이러면 엔드포인트의 commit()은 SAVEPOINT 해제로만 끝나고,
    마지막에 바깥 트랜잭션을 롤백하면 테스트가 만든 모든 변경이 사라진다.
    """
    try:
        connection = engine.connect()
    except Exception as e:
        pytest.skip(f"DB에 연결할 수 없어 스킵합니다: {e}")

    # SELECT 1을 먼저 쏘면 커넥션이 알아서 트랜잭션을 열어버려서(autobegin) 아래 begin()이 실패한다 —
    # 그래서 바깥 트랜잭션을 먼저 열고 그 안에서 연결을 확인한다.
    transaction = connection.begin()
    try:
        connection.execute(text("SELECT 1"))
    except Exception as e:
        transaction.rollback()
        connection.close()
        pytest.skip(f"DB에 연결할 수 없어 스킵합니다: {e}")

    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
