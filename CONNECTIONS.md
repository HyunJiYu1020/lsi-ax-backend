# 연결 구조 & 설정 가이드

백엔드(FastAPI) 기준으로, 프론트엔드가 어디서 어떻게 붙는지 / 설정값이 어디서 읽히는지 / MCP가 어떻게
끼어있는지를 한 곳에 정리한다. 엔드포인트별 설계 이유는 [API.md](./API.md) 참고.

## 1. 전체 구성

```
S.LSI/
├── backend/          FastAPI 서버 (:8000) — 이 저장소
├── frontend/          React/Vite 프로토타입 — ⚠ 백엔드 미연결 (아래 2-3 참고)
├── frontend-html/      정적 HTML/JS — 실제로 백엔드와 통신하는 유일한 프론트
└── .mcp.json          MCP 클라이언트(Claude Code 등)용 서버 등록 파일
```

## 2. 프론트 ↔ 백엔드 연결 포인트

### 2-1. `frontend-html` (실제 연결됨)

- `overseas.html:456` — `OV_API = (window.OV_API_BASE || "http://localhost:8000") + "/api/v1/overseas"`
- `shared/core.js:2235` — `CHAT_API = (window.OV_API_BASE || "http://localhost:8000") + "/api/v1/chat"`

두 파일 모두 전역 `window.OV_API_BASE`로 베이스 URL을 바꿀 수 있다(하드코딩 아님). 운영 배포 시엔
`index.html`/`overseas.html`에서 `core.js`를 로드하기 **전에** 아래처럼 한 줄 추가하면 된다.

```html
<script>window.OV_API_BASE = "https://api.실제도메인.com";</script>
<script src="shared/core.js"></script>
```

실제로 호출하는 경로 (base: `/api/v1`):

| 호출부 | 엔드포인트 |
|---|---|
| `overseas.html` | `GET/PUT /overseas/state`, `PUT /overseas/custom-rows`, `DELETE /overseas/custom-rows`, `PUT/DELETE /overseas/memo-cols`, `PUT /overseas/memos` |
| `shared/core.js` (플로팅 채팅) | `GET /chat/providers`, `POST /chat`, `POST /chat/confirm` |

### 2-2. `frontend` (React/Vite) — 백엔드 미연결

`src/` 전체를 검색해도 `fetch`/`axios`/API 베이스 URL이 하나도 없다. 지금은 `data/seed.js`의 더미 데이터와
`logic/calc.js`의 순수 클라이언트 계산만으로 동작하는 독립 프로토타입이다. 나중에 이 쪽을 실제 백엔드에 붙이려면
`frontend-html`의 `OV_API_BASE` 패턴을 참고해 `VITE_API_BASE` 같은 Vite 환경변수를 새로 만들어야 한다 — 현재는
그 배선 자체가 존재하지 않는다는 점이 핵심.

### 2-3. CORS (`app/main.py`)

```python
allow_origins=settings.cors_origins              # .env의 CORS_ORIGINS, 운영 도메인용
allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?"  # 로컬 개발 전용, 포트 무관 허용
```

로컬에서는 `frontend-html`을 아무 포트로 띄워도(`python -m http.server`, VSCode Live Server 등) 다 통과된다.
**운영 배포 시엔 `allow_origin_regex` 줄을 지우고 `CORS_ORIGINS`에 실제 도메인만 명시할 것.**

## 3. 설정값 (`.env`)

`cp .env.example .env` 후 채운다. 각 값이 코드에서 읽히는 지점은 `app/core/config.py: Settings`.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://slsi:slsi@localhost:5432/slsi` | SQLAlchemy 접속 문자열. `docker compose up db`로 띄운 로컬 Postgres 기준 |
| `CORS_ORIGINS` | `["http://localhost:5173"]` | 운영 도메인 화이트리스트(JSON 배열). 로컬 개발은 2-3의 regex가 대신 처리하므로 사실상 미사용 |
| `CHAT_PROVIDER` | `anthropic` | 플로팅 채팅(`/api/v1/chat`)이 쓸 LLM. `anthropic`(유료, 키 필요) 또는 `ollama`(로컬, 무료) |
| `ANTHROPIC_API_KEY` | (없음) | `CHAT_PROVIDER=anthropic`일 때만 필요. https://console.anthropic.com 발급 |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | anthropic 공급자가 쓸 모델명 |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | `CHAT_PROVIDER=ollama`일 때만. 기본값 쓰면 생략 가능 |
| `OLLAMA_MODEL` | `gemma4:26b` | ollama 공급자가 쓸 모델명 |

## 4. MCP 연결

두 가지 경로로 같은 MCP 도구 세트(`app/mcp_server.py`)가 쓰인다. 어느 쪽이든 **MCP 서버는 DB에 직접
접근하지 않고, 백엔드 HTTP API(`/api/v1/...`)를 호출하는 얇은 어댑터**라는 점이 동일하다.

### 4-1. 외부 MCP 클라이언트 (Claude Code/Desktop 등) — stdio

루트의 `.mcp.json`:

```jsonc
{
  "mcpServers": {
    "slsi-overseas": {
      "command": "uv",
      "args": ["run", "--directory", "backend", "python", "-m", "app.mcp_server"],
      "env": { "SLSI_API_BASE": "http://localhost:8000/api/v1" }
    }
  }
}
```

- FastAPI 서버(`uv run uvicorn app.main:app --reload`)가 먼저 떠 있어야 동작한다.
- `SLSI_API_BASE`를 바꾸면 다른 환경(예: 배포된 서버)의 API를 호출하게 할 수 있다.

### 4-2. 플로팅 채팅창 — in-process

`app/api/v1/endpoints/chat.py`는 서브프로세스 없이 `app/mcp_server.py`의 `mcp` 인스턴스를 그대로
import해서 `mcp.list_tools()` / `mcp.call_tool()`을 직접 호출한다(4-1과 도구 스키마 중복 정의 안 함).

- 읽기 도구(`list_*`, `get_*`)는 바로 실행.
- 쓰기 도구(`upsert_*`, `record_*`, `update_*`, `delete_*`)는 `/chat/confirm`으로 사람이 승인해야 실행된다.
