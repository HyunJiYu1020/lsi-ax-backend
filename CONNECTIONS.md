# 연결 구조 & 설정 가이드

백엔드(FastAPI) 기준으로, 프론트엔드가 어디서 어떻게 붙는지 / 설정값이 어디서 읽히는지 / MCP가 어떻게
끼어있는지를 한 곳에 정리한다. 엔드포인트별 설계 이유는 [API.md](./API.md) 참고.

## 1. 전체 구성

```
S.LSI/
├── backend/          FastAPI 서버 (:8000) — 이 저장소
├── frontend/          React/Vite — 기준정보 › 버전만 백엔드 연결, 나머지는 localStorage (아래 2-2 참고)
├── frontend-html/      정적 HTML/JS — 실제로 백엔드와 통신하는 유일한 프론트
└── .mcp.json          MCP 클라이언트(Claude Code 등)용 서버 등록 파일
```

## 2. 프론트 ↔ 백엔드 연결 포인트

### 2-1. `frontend-html` (실제 연결됨)

`index.html`이 유일한 진입점이다(`overseas.html`은 `index.html#/a/overseas`로 리다이렉트만 하는
빈 스텁). 실제 API 호출은 전부 `shared/core.js` 안에 있다:

```js
// shared/core.js:3540, 3606, 3638
const OV_API   = (window.OV_API_BASE || "http://localhost:8000") + "/api/v1/overseas";
const FX_API   = (window.OV_API_BASE || "http://localhost:8000") + "/api/v1/fx";
const PLAN_API = (window.OV_API_BASE || "http://localhost:8000") + "/api/v1/management-plan";
```

세 베이스 모두 같은 전역 `window.OV_API_BASE`를 쓴다(하드코딩 아님). 운영 배포 시엔 `index.html`에서
`core.js`를 로드하기 **전에** 아래처럼 한 줄 추가하면 된다.

```html
<script>window.OV_API_BASE = "https://api.실제도메인.com";</script>
<script src="shared/core.js"></script>
```

**실제로 `fetch`하는 경로는 이것들이다** (base: `/api/v1`):

| 호출부 (core.js) | 엔드포인트 | 시점 |
|---|---|---|
| `ovApiLoad` / `ovApiFlush` | `GET/PUT /overseas/state` | 로드 시 1회 / 단계전환·블러 시 |
| `ovLabsApiLoad` / `ovLabsApiFlush` | `GET/PUT /overseas/labs` | 로드 시 / 연구소 기준정보 저장 시 |
| `ovUploadRaw` | `POST /overseas/uploads/file` | 엑셀 업로드할 때마다(파싱 성패 무관, fire-and-forget) |
| `ovTrFlush` | `POST /overseas/reason-translate` | 증감사유 원문이 화면에 처음 보일 때(한 렌더 분량을 모아 250ms 디바운스, 묶음당 40건) |
| `ovRowMatchLoad` | `POST /overseas/row-match` | 엑셀을 읽은 직후(계정 목록에 없던 행 이름이 있을 때) |
| `ovAnomScan` | `POST /overseas/anomaly-scan` | 엑셀을 읽은 직후(반영 대기 값 검사) |
| `ovAnomLoad` | `POST /overseas/anomaly-scan` | 증감분석 화면을 그릴 때(저장된 값으로 남은 건수 — 버전 단위 캐시) |
| `ovRowAliasSave` | `PUT /overseas/row-aliases` | 대기 카드에서 고른 "같은 행 / 새 행" 답을 [반영]할 때 |
| `verAcctApiLoad` / `verAcctApiSave` | `GET/PUT /versions/{y}/{m}/{kind}/accounts` | 로드 시 / [계정 확정]·[확정 취소] 누를 때(해외연구소만) |
| `ovCarryLoad` / `ovCarrySet` | `GET/PUT /overseas/carry-defs` | 집계 ① 탭 파생 행·열 목록을 그릴 때 / [다음 버전에도] 체크할 때 |
| `ovCarryForward` | `POST /overseas/carry-forward` | 새 버전을 열 때(`ovVerCopy`) — 유지 승인한 파생 행·열만 복제 |
| (추가 행 조회/저장/삭제) | `GET/PUT/DELETE /overseas/extra-rows` | 사용자가 행 추가·수정·삭제할 때 |
| (추가 열 조회/삭제) | `GET/DELETE /overseas/extra-cols` | 추가 열 목록 조회·열 삭제 시 |
| (추가 열 값 조회/저장) | `GET/PUT /overseas/cost-extras` | 추가 열 칸 값 입력 시 |
| (고정 비용항목 코드 조회) | `GET /common-codes?group_code=OVERSEAS_CATEGORY` | 로드 시 |
| `ovCostApiBatch` | `PUT /cost-entries/batch` | `/overseas/state` 저장과 같은 시점에 **병행 저장**(중복, 아래 참고) |
| `fxApiLoad` / `fxApiFlush` | `GET/PUT /fx/state` | 로드 시 / 환율 저장 시 |
| `verApi*` | `GET/POST/PUT/DELETE /versions` | 연월×속보·실적 버전 열기·완료·삭제 시 |
| `verPlanApi*` | `GET/POST/PUT/DELETE /management-plan/versions` | 경영계획 버전(P00~) 만들기·확정·삭제 시 |
| `ovclApiFirst` | `PUT /close/stages` | 실적결산 2단계에서 해외연구소 [1차 완료]·[1차 완료 취소] 누를 때 |

**`ovCostApiBatch`는 중복 저장이다** — `overseas_cost_entries` 테이블이 있던 시절, 공용 테이블
(`cost_entries`)로 데이터를 옮기는 동안 "새 테이블에도 같이 쌓기"용으로 넣은 병행 호출인데, 지금은
`/overseas/state` 저장 로직이 이미 `cost_entries`/`cost_entry_details`에 직접 쓰기 때문에 같은 행을
두 경로로 중복 upsert하는 셈이다. API.md 참고.

**`/management-plan/entries`는 아직 안 붙어 있다** — core.js 주석에 그대로 적혀 있듯, 경영계획
"버전 목록"만 `/management-plan/versions`로 동기화하고, 실제 금액은 여전히 `/overseas/state`의
`cost_entries`(version="plan") 슬롯을 그대로 쓴다.

**외주용역 시트**는 `/overseas/state`의 `vendor` 필드로 연구소×버전 단위 전체 교체로 저장되고,
DB는 마스터(`overseas_vendor_rows`) + 월별 디테일(`overseas_vendor_row_details`)로 나뉘어 있다
(`cost_entries`/`cost_entry_details`와 같은 모양). 증감사유는 `reason` 필드 ↔ `overseas_reason_rows`.
그 사유의 한국어 번역은 따로 쌓인다 — `POST /overseas/reason-translate`가 원문 1건당 1행으로
`overseas_reason_translations`에 원문·번역을 같이 저장하고, 화면(`ovTrKo`)은 그 값을 우선 쓴다.
서버 번역이 아직 안 왔거나 실패했을 때만 목업 시절 사전(`OV_KO`·`ovTrRule`)으로 폴백한다.

**위 외의 `/overseas/*` 엔드포인트**(`memo-cols`, `memos`, `sheet-rows`, `notes`, `cost-entries`,
`lab-sheets`, `step`, `reason-translations`)는 화면에 전용 버튼이 없고, **AI 플로팅 챗봇을 통해서만** 호출된다 — 4절 참고.

### 2-2. `frontend` (React/Vite) — 일부 연결

API 호출 층은 `src/api/client.js` 하나다. 베이스 주소는 `frontend-html`과 같은 규칙을 쓴다:

```js
// src/api/client.js
const BASE = (import.meta.env.VITE_API_BASE || window.OV_API_BASE || "http://localhost:8000") + "/api/v1";
```

**실제로 `fetch`하는 경로** (base: `/api/v1`):

| 호출부 | 엔드포인트 | 시점 |
|---|---|---|
| `components/VersionPage.jsx` | `GET/POST /versions`, `PUT/DELETE /versions/{y}/{m}/{kind}` | 기준정보 › 버전 화면 진입·연도 변경·열기/확정/되돌리기/닫기 |
| `state/store.jsx` (`reloadVersions`) | `GET /versions` | 앱 시작 시 1회 — 편성 화면이 "열린 버전만" 고르게 하려고 |
| `state/store.jsx` (`touchVersion`) | `PUT /versions/{y}/{m}/{kind}` | 완료된 버전의 값을 고칠 때(→ 작성중, 확정일 삭제) |
| `steps/VarianceStep.jsx` | `GET /overseas/variance-basis` | 증감분석 진입·기준월·속보/실적·경영계획 차수 변경 시 |
| `steps/VarianceStep.jsx` | `GET /overseas/variance-reasons`, `GET /overseas/variance-reason-check` | 비교 기준·기간·연구소·1차/2차를 바꿀 때마다 |
| `components/ReasonCell.jsx` → `store.jsx` | `PUT /overseas/variance-reasons` | 사유 [저장] 누를 때 / 회신 원문 입력칸에서 포커스가 빠질 때 |
| `components/ReasonCell.jsx` → `store.jsx` | `POST /overseas/variance-reason-draft` | [✦ 번역 + AI 초안] 누를 때 |
| `components/DriverPanel.jsx` → `VarianceStep` | `POST /chat`, `GET/PUT/DELETE /overseas/variance-drivers` | 동인 요청 / 목록 조회 / [다음 버전에도 유지]·[표 삭제] |
| `components/AskPanel.jsx` | `POST /chat`, `POST /chat/confirm` | 분석 질의 보낼 때 / 쓰기 승인·취소 |
| `steps/ActualFirstPanel.jsx` | `GET/PUT /overseas/actual-first`, `GET/PUT /overseas/actual-stage` | 실적 1차 탭 진입·값/사유 저장·[1차 고정] 토글 |
| `components/StageShiftBar.jsx` | `GET /overseas/actual-stage-shift` | 실적 2차 탭 진입 시 |
| `steps/AggregateStep.jsx` → `store.jsx` (`ovLoadCpc`) | `GET /overseas/cp-contract` | 집계 진입·기준월/속보실적 변경 시 — CP 계약이 없으면 ④ CP 보정의 정산 칸을 빈칸으로 두고 알린다 |
| `steps/AggregateStep.jsx` | `GET /overseas/confirm-check` | [계정 확정] 누른 직후 — 환율 미등록 월이면 막고, 미업로드·사유 미작성은 경고 후 진행(단서 문장을 note로 저장) |
| `steps/AggregateStep.jsx` → `store.jsx` (`ovLoadAcct` · `ovSetAcctStatus`) | `GET/PUT /versions/{y}/{m}/{kind}/accounts` | 집계 진입 시 상태 읽기 / [계정 확정]·[작성중으로] |
| `state/store.jsx` (`touchVersion`) | `PUT /versions/{y}/{m}/{kind}/accounts/overseas` | 확정 후 값을 고칠 때(→ 작성중, 확정일·단서 삭제) |
| `steps/AggregateStep.jsx` → `store.jsx` (`ovSetFxRule`) | `PUT /fx/overseas-rule/override` · `/default` | 실적 월 환산 기준을 [실적 평균]으로 바꿀 때(이번 버전만) / "앞으로도?"를 승인할 때(기본값 변경) |
| `components/VersionPage.jsx` | `GET /versions/{y}/{m}/{kind}/accounts` | 기준정보 › 버전 화면에서 열린 버전마다 — "해외연구소 완료 · 11.15"를 상태 아래 줄에 보여준다 |

왜 이것들만인가: **여러 사람이 같이 보는 값**은 브라우저에 들고 있으면 안 된다. 버전 상태·확정일이
그렇고, 증감분석 화면에서 새로 붙은 것들도 같은 이유다 — 증감사유는 보고서에 실리는 문장이고,
실적 1차 값·[1차 고정]은 이미 보고한 숫자이고, 동인 표의 "다음 버전에도 유지" 승인은 다음 달 화면을
바꾼다. 비교 기준 해석은 성격이 조금 다른데, DB에 어떤 버전이 열려 있고 어디에 데이터가 들어왔는지를
봐야 알 수 있어서 브라우저가 계산할 수 없다(1월 속보의 전실행은 전년 12월 실적이다).

반면 편성 금액 자체(해외연구소 3단계 표·환율·대시보드)는 아직 `data/seed.js`의 더미 데이터 +
`logic/calc.js`의 클라이언트 계산 + `localStorage`로 돈다. 그래서 **서버가 안 떠 있어도 표는 그대로
그려진다** — 증감분석 화면도 라벨과 사유 저장만 못 하고 숫자는 계속 보인다(`ApiError.status === 0`을
각 패널이 안내 문구로 처리한다). 화면 계산과 서버 계산이 어긋나면 안 되므로, `logic/calc.js`의
환차 분해 식을 고칠 때는 `app/api/v1/endpoints/overseas_variance.py`의 `_variance_cells`도 같이 고친다.

**[계정 확정] 버튼은 해외연구소 편성 화면에 두지 않는다.** 계정 확정은 계정 담당자가 전체 현황에서
하는 결정이라, 편성 화면에 같은 버튼을 또 두면 어느 쪽이 진짜인지 알 수 없다 — 이 화면은 상태
뱃지(작성중/완료)만 읽어서 보여준다.

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
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | (없음) | 업로드 원본 파일을 S3에 보존하려면 채운다(버킷 전용 IAM, `s3:PutObject`/`s3:GetObject`로 최소화). 비워두면 업로드 기능은 S3 저장만 건너뛰고 나머지(파일명·파싱 결과)는 그대로 동작 |
| `AWS_REGION` | `ap-northeast-2` | S3 리전 |
| `S3_UPLOAD_BUCKET` | (없음) | 업로드 원본 보관용 버킷명. 비어 있으면 S3 저장 스킵 |

## 4. MCP 연결

두 가지 경로로 같은 MCP 도구 세트(`app/mcp_server.py`)가 쓰인다. 어느 쪽이든 **MCP 서버는 DB에 직접
접근하지 않고, 백엔드 HTTP API(`/api/v1/...`)를 호출하는 얇은 어댑터**라는 점이 동일하다.

이 도구 세트가 `/overseas/*`의 거의 모든 엔드포인트(`extra-rows`, `memo-cols`, `memos`, `sheet-rows`,
`notes`, `cost-entries`, `uploads`, `labs`, `lab-sheets`, `step`)와 `/common-codes`, `/errors`까지를
다 덮는다 — 즉 **2-1에서 설명한 화면 버튼보다 실제 API 커버리지가 훨씬 넓고**, 화면에 없는 나머지는
전부 챗봇을 거쳐야만 호출된다.

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
- `calculate` · `combine_series` · `set_display_unit`은 이 이름 규칙을 안 따르지만 데이터를 바꾸지 않아
  승인 없이 바로 실행된다(`chat.py`의 `SAFE_NON_PREFIXED_TOOLS`).

**시스템 프롬프트는 2층**(`chat.py`의 `_COMMON_RULES` + `_ACCOUNT_RULES`): 계정 무관 공통 규칙에,
지금 보고 있는 계정(`context.key`)의 전용 규칙만 골라 붙인다. 해외연구소 화면은 약 10,500자, 다른
계정 화면은 약 6,200자가 된다. 계정이 늘면 `_ACCOUNT_RULES`에 그 계정 블록만 추가하면 된다.

**어느 도구를 쓸지(행이냐 열이냐, 연구소냐 행이냐 같은 구분)는 프롬프트가 아니라 도구 설명
(`mcp_server.py`의 `description`)에 적는다** — 4-1의 외부 MCP 클라이언트는 이 프롬프트 없이 도구만
받기 때문에, 도구 사용법을 `chat.py`에만 적으면 그쪽에서는 적용되지 않는다. 프롬프트에는 화면 정보
해석(`ref_tab`이면 기준정보 화면 등)처럼 특정 도구에 매달 수 없는 것만 남긴다.

**표시 단위(K$ ↔ $)**: "K$ 말고 $로 보여줘"는 `set_display_unit` 도구 호출 하나로 끝나고 DB는 건드리지
않는다 — 저장값은 언제나 K$다. 프론트(`core.js`의 `OV_USD_UNIT`)가 해외연구소 표의 달러 칸만 1000배 해서
그리고, 지금 어느 단위로 보고 있는지를 매 요청 `context.usd_unit`으로 돌려보낸다. $ 단위로 보는 중에
사용자가 말한 금액을 저장할 때는 모델이 아니라 서버(`chat.py`의 `_to_kdollar`)가 1000으로 나눠 K$로
넣는다 — 모델에게 나눗셈을 맡기면 빼먹었을 때 1000배 큰 금액이 그대로 저장되기 때문이다.
`OV_USD_UNIT`은 `S`(localStorage)에 저장하지 않아서 새로고침하면 K$ 표시로 돌아간다.

**주의**: 이 승인 게이트가 있어도 "테스트로 한번 해볼게요" 같은 대화로 더미 데이터가 실제로 저장될 수
있다 — 2026-11-04에 `error_logs`/`fx_applied_rates`(수동 API 테스트)와 `memos`/`memo_cols`/
`notes`(챗봇으로 넣은 "테스트 메모"·"테스트 사유"·중복 더미 행)에서 실제로 발견되어 정리했다.
운영 전환 시 이 경로로 들어간 데이터를 주기적으로 점검할 것.

---

## 5. 실적결산 화면(`#/close`)의 해외연구소 탭 — 지금 어디까지 붙어 있나

화면은 `shared/core.js` 맨 뒤의 "해외연구소 실적결산" 절에 있다. 기존 실적결산 코드를 고치지 않고
**뒤에서 감싸는 방식**으로 붙였다(`const _ovclStep1=clsStep1; clsStep1=function(...)`) — core.js가
이미 쓰는 후방 확장 관용구와 같고, 한 파일을 여러 사람이 동시에 고칠 때 충돌이 안 난다.

| 화면 동작 | 지금 | 서버 엔드포인트 |
|---|---|---|
| 1단계 전표 업로드 · ERP I/F | 예시 전표(`ovclSeedVou`) + localStorage | `POST /close/vouchers` (연결 대기) |
| 2단계 연구소 매핑 · 제외 · 이관 | 브라우저에서 계산·저장 | `GET/PUT /close/overseas/mapping` (연결 대기) |
| AI 다시 매칭 | 브라우저(`ovclAI`) | `POST /close/overseas/rematch` (연결 대기) |
| AI 규칙 등록 | 브라우저(`D.rules`) | `GET/POST /close/match-rules` (연결 대기) |
| **[1차 완료] / 취소** | **서버 저장** | `PUT /close/stages` ✅ |

**[1차 완료]만 먼저 서버에 붙인 이유**: 그 값은 혼자 쓰는 값이 아니다 — 증감분석 화면의 [1차 분석]
탭을 열지 말지가 `overseas_actual_stages.close_first_done`에 달려 있어서, 브라우저에만 두면 결산
담당자와 증감분석 담당자가 서로 다른 상태를 본다. 나머지(전표·매핑)는 아직 예시 전표로 도는 화면이라
서버에 쌓을 실제 자료가 없다. 실패는 조용히 삼키고 로그에만 남긴다(`log()`) — 화면은 서버 없이도
돌아가야 한다.

**해외연구소만 완료가 두 층이다.** 다른 계정은 [매핑 완료]가 곧 계정 확정이지만, 해외연구소의 전표
매핑은 ERP 약식 **1차**일 뿐이다. 그래서 이 화면은 [1차 완료]까지만 하고, 계정 완료는 편성 화면의
실적 시뮬레이션 [확정](`PUT /versions/{y}/{m}/actual/accounts/overseas`, `verAcctApiSave`)으로만 된다.
3단계 확정 표의 해외연구소 줄이 "1차 완료 / 완료"를 가려 보여주는 게 그 구분이다 — 상태를 하나로
묶으면 1차만 끝난 달을 "결산 끝"으로 읽는다. 1차 없이도 2차를 진행할 수 있다.
