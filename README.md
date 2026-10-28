# S.LSI 예산관리 플랫폼 · 백엔드

FastAPI 기반 API 서버 뼈대. 아직 인증·비즈니스 로직은 없고, 구조와 DB/Docker 연결만 잡혀있는 상태.

## 로컬 실행

```bash
uv sync                  # 의존성 설치
cp .env.example .env     # 환경변수 (DB 없이도 기본값으로 기동은 됨)
uv run uvicorn app.main:app --reload
```

- http://localhost:8000/docs — Swagger UI (API 문서, 바로 테스트 가능)
- http://localhost:8000/api/v1/health — 헬스체크

## DB 마이그레이션 (Alembic)

실제 DB(Postgres)가 떠있어야 동작합니다 (`docker compose up db` 또는 로컬 Postgres).

```bash
uv run alembic revision --autogenerate -m "메시지"
uv run alembic upgrade head
```

## Docker로 전체(API + Postgres) 띄우기

프로젝트 루트에서:

```bash
docker compose up --build
```

## 구성

- `app/main.py` — FastAPI 앱 생성, CORS, 라우터 등록
- `app/core/config.py` — 환경변수 설정 (pydantic-settings)
- `app/db/session.py` — SQLAlchemy engine/세션, `Base`
- `app/api/v1/` — API 라우터 (버전별로 분리)
- `app/models/` — SQLAlchemy 모델 (아직 비어있음)
- `alembic/` — DB 마이그레이션

## 참고

- 인증(JWT 등)은 아직 미구현 — 로그인/SSO 여부가 정해지면 미들웨어 레이어로 추가 예정. 없어도 되는 구조로 잡혀있어서, 필요 없으면 안 넣고 가도 됨.
- Dockerfile은 `python:3.12-slim`(Debian 기반) 베이스를 씀 — Python 앱 컨테이너의 표준 관례이며, 배포 호스트(Ubuntu)와는 무관하게 Docker 컨테이너는 어떤 베이스 이미지든 Ubuntu 호스트 위에서 동일하게 동작함.
