# S.LSI 예산관리 플랫폼 · 백엔드

FastAPI 기반 API 서버. 해외연구소 편성 1개 계정 + 전사 공통 환율·경영계획 버전이 실제로 DB와
연결돼 있고, 나머지 18개 비용계정은 테이블만 설계해둔 "준비 중" 상태. 프론트가 뭘 실제로 부르는지는
[CONNECTIONS.md](./CONNECTIONS.md), 엔드포인트별 설계 이유는 [API.md](./API.md) 참고.

## 로컬 실행

```bash
uv sync                  # 의존성 설치
cp .env.example .env     # 환경변수 (DB 없이도 기본값으로 기동은 됨)
uv run alembic upgrade head   # DB 마이그레이션 (아래 참고)
uv run python -m app.db.seed  # 19개 계정 · 해외연구소 기준정보 등 초기 데이터
uv run uvicorn app.main:app --reload
```

- http://localhost:8000/docs — Swagger UI (API 문서, 바로 테스트 가능)
- http://localhost:8000/api/v1/health — 헬스체크

## DB 마이그레이션 (Alembic)

실제 DB(Postgres)가 떠있어야 동작합니다 (`docker compose up db` 또는 로컬 Postgres).

```bash
uv run alembic revision -m "메시지"   # 보통 수동으로 작성(이 저장소는 autogenerate보다 직접 작성하는 쪽을 택함)
uv run alembic upgrade head
```

## 시드 데이터 (`app/db/seed.py`)

스키마가 아니라 데이터(행)만 채우는 스크립트라 Alembic과 분리했다. 19개 비용계정 레지스트리, 해외연구소
13개 파트, 공통코드(버전·통화·환율종류 등)를 심는다. 재실행해도 안전(이미 있으면 건너뜀).

```bash
uv run python -m app.db.seed
```

해외연구소 편성의 "증감사유 · 외주용역" 시트는 화면 확인용 예시 데이터를 따로 만든다 — 비용 시트
(`cost_entries`)의 금액에서 사유 문장과 외주용역 상세(업체·과제별 월 금액)를 만들어 채우므로,
비용 데이터가 먼저 들어 있어야 한다. 재실행하면 같은 값으로 다시 채운다(결정적).

```bash
uv run python -m app.db.seed_overseas_sheets
```

## 버전 값 체계 (주의)

같은 "버전"인데 테이블마다 값이 다르다 — 섞어 쓰면 조회가 비게 된다.

- `cost_entries` · `extra_cols`: `forecast`(속보) | `actual`(실적) + `yyyymm`(편성 기준 연월)
- `overseas_reason_rows` · `overseas_vendor_rows` · `uploads` · `memo_cols` · `memos` · `notes` ·
  `sheet_rows`: `cur`(당월 속보) | `prev`(전실행) | `prior`(전년 실적) | `plan`(경영계획)

프론트는 화면 상태로 `cur/prev/prior/plan`을 쓰고, 비용 API를 부를 때만 `ovVerApi`/`ovYyyymmApi`로
`forecast|actual` + `yyyymm`으로 바꿔 보낸다. 챗봇도 같은 규칙을 프롬프트로 지시받는다.

## Docker로 전체(API + Postgres) 띄우기

프로젝트 루트에서:

```bash
docker compose up --build
```

## 구성

- `app/main.py` — FastAPI 앱 생성, CORS, 전역 예외 핸들러(500은 `error_logs`에 기록), 라우터 등록
- `app/core/config.py` — 환경변수 설정 (pydantic-settings)
- `app/db/session.py` — SQLAlchemy engine/세션, `Base`
- `app/db/seed.py` — 초기 데이터 시드 스크립트
- `app/db/seed_overseas_sheets.py` — 해외연구소 증감사유 · 외주용역 시트의 예시 데이터 생성·적재
- `app/api/v1/` — API 라우터: `overseas`(해외연구소), `fx`(전사 공통 환율), `plan`(경영계획 버전),
  `system`(공통코드·에러로그), `chat`(AI 플로팅 채팅), `health`
- `app/mcp_server.py` — MCP 도구 세트. 외부 MCP 클라이언트와 `chat.py` 양쪽에서 재사용 (CONNECTIONS.md 4절)
- `app/models/` — SQLAlchemy 모델: `account`(19개 계정), `common`(19개 계정 공유 범용 테이블:
  `cost_entries`/`cost_entry_details`/`extra_cols`/`memo_cols`/`memos`/`notes`/`sheet_rows`/`uploads`),
  `overseas`(해외연구소 전용: 연구소·시트 기준정보, 증감사유, 외주용역, CP 계약), `fx`(전사 공통 환율 3종 + 적용환율 캐시), `plan`(경영계획 버전),
  `system`(공통코드·에러로그), `user`
- `alembic/` — DB 마이그레이션

## 참고

- 인증(JWT 등)은 아직 미구현 — 로그인/SSO 여부가 정해지면 미들웨어 레이어로 추가 예정. 없어도 되는 구조로 잡혀있어서, 필요 없으면 안 넣고 가도 됨.
- Dockerfile은 `python:3.12-slim`(Debian 기반) 베이스를 씀 — Python 앱 컨테이너의 표준 관례이며, 배포 호스트(Ubuntu)와는 무관하게 Docker 컨테이너는 어떤 베이스 이미지든 Ubuntu 호스트 위에서 동일하게 동작함.
- `schema.sql`은 초기 스키마 스냅샷이고 그 뒤 마이그레이션을 반영 안 해서 이미 낡았다 — 실제 스키마는
  항상 `alembic/versions/`와 DB(`\d <table>`)가 기준.
