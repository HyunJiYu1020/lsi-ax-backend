from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.shared.core.config import settings

# pool_pre_ping: 꺼내 쓰기 전에 연결이 살아 있는지 한 번 확인한다. 없으면 DB가 연결을 끊은 뒤
# (재시작·유휴 정리·네트워크 끊김) 풀에 남은 죽은 연결을 그대로 건네서, 서버를 다시 띄울 때까지
# 모든 요청이 실패한다 — 개발 중에 실제로 그렇게 멈췄다.
# pool_size/max_overflow: 화면 한 장이 첫 로드에 10여 개 요청을 던진다(기준정보 · 계정 현황 ·
# 편성값 묶음 · 결산 전표). 기본값(5+10)이면 두 사람이 같이 새로고침하는 것만으로 풀이 마른다.
engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
