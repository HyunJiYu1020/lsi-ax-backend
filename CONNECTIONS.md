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
| `ovAggTableLoad` | `GET /overseas/aggregate-table` | 집계 ④ 전체 계정 표를 그릴 때(버전·모드별 캐시) |
| `ovLabTablesLoad` | `GET /overseas/lab-tables` | 집계 ①②③ 탭(표 10장)을 그릴 때 — 한 번에 받는다 |
| `ovSummaryLoad` | `GET /overseas/summary` | 화면 맨 위 히어로 카드·③집계 KPI 카드 (전실행 대비는 `version=prev`로 한 번 더) |
| `ovLRLoad` | `GET /overseas/labor-ratio-table` | 인건비율 팝업을 열 때 |
| `ovVarLoad` | `GET /overseas/variance-table` (+`/labor-split`) | 증감분석 **계정별 상세**를 그릴 때 |
| `ovVarLabsLoad` | `GET /overseas/variance-labs` | 증감분석 **연구소별 요약**을 그릴 때 |
| `ovFirstLoad` | `GET /overseas/actual-first` | 실적 차수의 [1차 · ERP 약식] 탭을 열 때 |
| `ovclAmtLoad` | `GET /overseas/lab-monthly` | 마감 2단계 전표 매핑 타깃·전실행 금액이 필요할 때 |
| `ovVerReload` | `GET /overseas/state` (+`/labs`·`/fx/state`) | 머리에서 버전을 바꿀 때마다(120ms 디바운스) — 조회에 **보고 있는 달**이 들어가므로(`ovStateQ`) 달이 바뀌면 다시 받아야 한다 |
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
| `ovHdrSave` / (머리글 조회) | `GET/PUT /overseas/cost-headers` | 업로드 [반영] 시 파일의 기간 열 머리글을 저장 / 취합 비용 표를 그릴 때(`ovCustEnsure`) |
| (고정 비용항목 코드 조회) | `GET /common-codes?group_code=OVERSEAS_CATEGORY` | 로드 시 |
| `ovCostApiBatch` | `PUT /cost-entries/batch` | `/overseas/state` 저장과 같은 시점에 **병행 저장**(중복, 아래 참고) |
| `ocApiLoad` / `ocApiFlush` / `ocApiFlushState` | `GET/PUT /sheet-rows` (account_key=outsource·consum) | 로드 시 / 위탁용역비·생소비 표를 [저장]할 때 / 변경사유·확정 상태가 바뀔 때 |
| `shApiLoad` / `shApiFlush` / `shApiFlushState` | `GET/PUT /sheet-rows` (account_key=dep·ratetech) | 로드 시 / 감가상각비 원천표·정률기술료 표를 저장할 때 / 확정·오류 수정·사유가 바뀔 때 |
| `refChLoad` | `GET /reference/changes?table=&scope=` | 기준정보 화면을 열 때 — 맨 아래 "최근 변경" 세 줄 |
| `rsnApiLoad` / `rsnApiFlush` / `rsnApiRecheck` | `GET/PUT /variance-reasons`, `POST /variance-reasons/recheck` | 로드 시(다른 로더 뒤) / 사유칸 입력 후 0.9초 / 편성값을 다시 저장할 때(금액이 바뀌었으니 "재확인 필요") |
| `pmApiLoad` / `pmApiFlush` | `GET/PUT /reference/products` | 로드 시·버전 변경 시 / 제품정보를 저장·업로드할 때 |

**계산은 서버만 한다 — 폴백을 두지 않는다.** 위 `ov*Load` 조회가 실패하거나 값이 없으면 화면은
숫자 자리를 빈칸·"–"로 두고 안내를 그린다. 전에는 브라우저가 같은 식을 들고 있다가 서버가 없으면
직접 계산했는데, 사용자는 그게 폴백인지 알 수 없었다 — **숫자가 안 나오는 것보다 다른 숫자가 나오는
쪽이 위험하다**("화면 숫자 ≠ 챗봇 숫자"가 그렇게 생겼다). 지금 브라우저에 남은 계산은 2-6절 끝의
목록뿐이고, 그 자리들은 서버 API가 아직 없다.

**첫 화면 요청 수를 묶는다**: 화면 하나가 표 열 개 넘게 쓴다(위탁용역비 4버전 × 2시트, 감가상각비
원천 7표 + 상태, 정률기술료 7표 + 상태). 표마다 한 번씩 부르면 첫 화면에서 요청 30개 이상이 동시에
날아가 **서버 커넥션 풀(기본 5+10)이 말라** 그 뒤 요청이 전부 타임아웃된다(개발 중에 실제로 서버가
멈췄다). 그래서 ① 계정당 묶음 조회(`GET /sheet-rows/bundle`)로 받고, ② `apiBoot()`가 로더를
150ms 간격으로 순서대로 띄운다. 지금 첫 화면 요청은 14개 수준이다.
| `accApiLoad` | `GET /accounts` · `GET /accounts/summaries?yyyymm=` | 로드 시 / 기준월이 바뀔 때 — 전체 현황판의 상태·편성 주체·비고·월별 금액 |
| `accApiFlushMeta` / `accApiFlushSummary` | `PUT /accounts/{key}` · `PUT /accounts/{key}/summary` | 계정 카드에서 상태·비고를 고칠 때 / 전년 실적·월별 금액이 바뀔 때 |
| `clsApiLoad` | `GET /close/vouchers` · `GET /close/maps` | 실적결산 화면을 열 때(그 연월마다 한 번) |
| `clsApiIngest` | `POST /close/vouchers` | 전표 엑셀을 올릴 때(ERP 재수신과 같은 경로 — 기존 매핑은 서버가 살린다) |
| `clsApiSaveMaps` | `PUT /close/maps` | [수정 저장]을 누를 때(바뀐 전표만) |
| `clsApiStage` | `PUT /close/stages` | [매핑 완료] · [취소]를 누를 때 |
| `refApiLoad` | `GET /reference/cost-elements?version=` · `/asset-classes` · `/dept-codes?version=` · `/plm-tasks?version=` | 로드 시 / 선택 버전이 바뀔 때(버전마다 한 번) |
| `refApiFlush` | `PUT /reference/{cost-elements\|asset-classes\|dept-codes\|plm-tasks}` | 기준정보 표를 [저장]할 때 · 엑셀/텍스트로 업로드할 때 |
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
`text_translations`에 원문·번역을 같이 저장하고, 화면(`ovTrKo`)은 그 값을 우선 쓴다.
서버 번역이 아직 안 왔거나 실패했을 때만 목업 시절 사전(`OV_KO`·`ovTrRule`)으로 폴백한다.

**위 외의 `/overseas/*` 엔드포인트**(`sheet-rows`, `notes`, `cost-entries`,
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
환차 분해 식을 고칠 때는 `app/accounts/overseas/services/variance.py`의 `variance_cells`도 같이 고친다.

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

### 2-4. 기준정보 네 표(비용계정 · 자산 Class 매핑 · 부서코드 · PLM 과제)

여태 이 네 표만 브라우저(localStorage)에 있었다. 그러면 ① 옆자리 담당자는 다른 부서코드표를 보고,
② 브라우저를 비우면 표가 사라지고, ③ 챗봇·집계가 서버에서 계정명·FA를 못 찾는다. 2026-11 화면
개편(비용계정 = Cost Elem. + 계정(1)~(5), 부서코드 = Cost Center + Budget Unit + 팀실 小中大,
PLM 과제 전체 + 상태)에 맞춰 서버(`/api/v1/reference/*`)로 옮겼다.

- **서버가 진실**이다. `refApiLoad`가 선택 버전의 네 표를 받아 `S.acct` · `S.depCls` · `S.dept` ·
  `S.osDrop`에 꽂고 다시 그린다. 버전을 바꾸면 그 버전 표를 다시 받는다.
- **못 붙으면 그대로 돈다.** 한 번 실패하면 더 조르지 않고(`REF_API_OFF`) 지금까지 쓰던
  localStorage 값으로 화면을 연다 — 백엔드 없이 화면만 띄우는 경우가 실제로 있다.
- **서버가 빈 표를 주면 화면 값을 지우지 않는다.** 아직 시드하지 않은 DB에 붙었을 때 기준정보를
  비워 버리면 업로드 데이터 전체가 "맵핑 오류"가 되고, 담당자에게는 서버 연결이 화면을 망친 것으로
  보인다.
- 저장은 표 단위 전체 교체다(`refApiFlush`). 화면 [저장]이 성공했을 때만 올린다 — 입력 오류로
  저장이 막힌 표를 서버에 밀어 넣으면 화면과 DB가 갈라진다.
- 초기 데이터는 `app/db/seed_reference.py`가 화면 seed와 **같은 내용**으로 심는다(서버에 붙은
  브라우저와 안 붙은 브라우저가 다른 표를 보면 "연결했더니 부서코드가 바뀌었다"로 읽힌다).
- 저장할 때 **어떻게 저장했는지**(`how`: 화면 저장 · 업로드 · 전월/전년 복사)와 파일명을 같이 보낸다 —
  이력에서 "표가 통째로 갈린 이유"를 가리는 단서다. 담당자 이름(`actor`)은 **보내지 않는다**: 로그인이
  없어서 화면이 아는 이름이 없고, 가짜 이름을 보내면 감사 기록에 거짓 주체가 남는다. 인증이 붙는
  시점에 이 한 줄만 채우면 된다.

### 2-5. 금액 단위 — 서버는 $, 화면은 K$

연구소 양식은 K$인데 DB·API는 $다. 경계가 **업로드 파서**와 **표시 포맷터** 두 곳뿐이다:

- `ovParse`(업로드) — 엑셀 셀 × 1000 → 서버로
- `ovF.usd`(표시) — 서버 값 ÷ 1000 → 화면. 화면의 `$` 토글(`OV_USD_UNIT`)을 켜면 나누지 않는다
- `ovUsdIn`/`ovUsdOut` — 사람이 칸에 입력·수정할 때 쓰는 같은 규칙의 짝
- `ovTplDl`(연구소 양식 왕복 다운로드)만 K$로 되돌린다 — 연구소가 다시 올릴 파일이라서다

그 밖의 어떤 자리에서도 1000을 곱하거나 나누지 않는다. 자세한 규칙과 환산하지 않는 칸
(인력·원화기타·현지통화·CP 계약)은 [API.md](./API.md)의 "금액 단위" 절에 있다.

### 2-6. 역할 분담 전수 목록 (서버 vs 브라우저)

판단 기준은 하나다. **서버** = 여러 사람이 같이 보는 사실 · 숫자의 근거 · 누가 언제 무엇을 했는지 ·
버전별로 고정돼야 하는 것 · 챗봇과 집계가 읽어야 하는 것. **브라우저** = 그 사람 화면 상태 ·
[저장] 누르기 전 임시값 · 서버에서 다시 만들 수 있는 캐시.

화면 상태(`S`)의 최상위 키를 전부 분류한 결과다(2026-11 기준, 70개 키).

| 데이터 | 어디에 | 비고 |
|---|---|---|
| 해외연구소 비용·증감사유·외주업체 (`ov.VD[버전].d/R/U`) | 서버 | `cost_entries` · `overseas_reason_rows` · `overseas_vendor_rows` |
| 연구소 기준정보 (`ov.labs`) | 서버 | `overseas_labs` + 시점 이력 |
| 환율 3종 (`fx2`) | 서버 | `fx_rates` · `fx_applied_rates` |
| 버전 목록·상태 (`vers`) | 서버 | `versions` · `management_plan_versions` |
| 기준정보 4표 (`acct` `depCls` `dept` `osDrop`) | 서버 | `ref_*` (2-4절) |
| 19계정 상태·편성 주체·비고·월별 금액 (`accts`) | 서버 | `accounts` · `cost_entries`(scope="ALL") |
| 실적결산 전표·매핑·완료 상태 (`cls.by[연월].vou/ovr/done`) | 서버 | `close_vouchers` · `close_voucher_maps` · `close_account_stages` |
| 전표 계정 자동 분류 기준 | 서버 | `close_dept_rules` (`app/db/seed_reference.py`가 심는다) |
| 감가상각비 원천 (`ex` `gk` `nw` `ai` `ref` `prior` `team`) | 서버 | `sheet_rows`(account=dep) — 열 구성이 확정되면 전용 테이블로 |
| 감가상각비 수정·확정 흔적 (`ovr` `snap` `confirmed` `shared` `status` `up` `reasons`) | 서버 | `sheet_rows`(account=dep, sheet=state) |
| 위탁용역비·생소비 편성값 (`os*` `cs*`) + 변경사유·확정 | 서버 | `sheet_rows`(account=outsource·consum, version=cur/prev/prior/plan) |
| 정률기술료 요율·규칙·조정·상신 이력 (`rt`) | 서버 | `sheet_rows`(account=ratetech). 화면 보기 상태(`rt.tab` 등)는 브라우저 |
| 제품정보 (`prodm`) | 서버 | `ref_products` — 버전별(부서코드·PLM과 같은 규칙) |
| 증감사유 (`reasons` `depNote` `osNote` `csNote`) | 서버 | `variance_reasons` — 계정 공용 경로(`/variance-reasons`)로 저장. 감가상각비 `state` 시트에서는 빼냈다(한 값을 두 곳에 쓰면 어느 쪽이 진실인지 알 수 없다) |
| 기준정보 변경 이력 | 서버 | `ref_change_logs` · `ref_change_rows` — 저장할 때 서버가 직접 남긴다(화면의 `refY.hist`는 쓰지 않는다). 기준정보 화면 맨 아래 "최근 변경" 줄이 이걸 읽는다 |
| 화면 위치·보기 (`step` `visited` `view` `tab` `dash*` `mapFlt` `unit` `ov.tab` `rt.tab` `vers.sel` …) | 브라우저 | 그대로 — 사람마다 달라야 하는 값 |
| 저장 전 임시값·메시지 (`draft` `pending` `uMsg` `log` `lastUpload`) | 브라우저 | 그대로 |
| 다시 만들 수 있는 캐시 (`fxm`, `refY.snap`, 서버에서 받은 달의 전표, 실적결산 AI 추천) | 브라우저(메모리만) | `save()`가 저장에서 뺀다(`savePruned`). `ov.VD`는 **일부러 남긴다** — 취합 그리드가 서버 응답을 기다리는 동안 입력값을 보여 주는 캐시다. 집계·증감분석 표는 이 캐시로 계산하지 않는다(폴백 금지) |
| 마이그레이션 표시·상수 (`ver` `acctVer` `dataVer` `fx2.ctry` `_oldAcct`) | 브라우저 | `_oldAcct`는 옛 잔재 — 지울 것 |

**크기와 저장 실패**: `S` 전체가 `localStorage` 키 한 개(`budgetPlatform.ax.v1`)에 통째로 들어간다.
예시 데이터로 약 1MB이고 그 중 610KB가 `ov.VD`다. 두 가지를 바꿔 뒀다:

- `save()`는 **파생 캐시를 뺀 사본**을 저장한다(`savePruned` — `fxm` · `refY.snap` · 실적결산 AI
  추천 · 서버에서 받은 달의 전표 원본 · 옛 잔재 `_oldAcct`).
- 저장이 실패하면 **상단 표시와 작업 로그에 남긴다**. 예전에는 `catch`로 삼켜서, 한도를 넘긴 뒤에도
  담당자는 저장된 줄 알고 창을 닫았다(서버에 올라가는 값은 살아 있지만 화면 위치·임시 입력은 사라진다).

`ov.VD`를 저장에서 빼지 않은 것은 의도다 — **취합 그리드**는 사람이 칸을 채우는 화면이라 새로고침
했을 때 서버 응답이 오기 전까지 빈 표를 보여 주면 "값이 날아갔다"로 읽힌다. 다만 이 캐시로
**계산하지는 않는다**: 집계·증감분석 표는 서버 값이 없으면 빈칸으로 둔다(위 2-1절 폴백 금지).
실데이터에서 한도가 문제가 되면 "선택한 버전만 저장"으로 줄이는 게 다음 수순이다.

**브라우저에 아직 계산이 남은 자리** (서버 API가 없거나, 저장 전 값이라 서버가 모르는 자리)

| 자리 | 함수 | 왜 남아 있나 |
|---|---|---|
| 취합 그리드의 소계·분기·연간·비율 | `ovPartEl` · `ov17` · `ovPct17` | **저장 전 입력값**이라 서버가 모른다 — 칸을 채우는 즉시 소계가 움직여야 한다 |
| 외주용역 증감분석 (`#/a/overseas` 분석 탭) | `ovFxSplit` · `ovOutsAnalysis` | 서버 API가 없다 — 옮겨야 할 자리(PLAN.md 2절) |
| 증감사유 초안·AI 요약에 쓰는 증감 문장 | `ovAnaEl` · `ovLaborSplit` (`ovRsnDraft` · `ovRsnAi`) | 표 숫자는 서버에서 오는데 **문장**은 브라우저가 만든다. 두 값이 어긋나면 안 되므로 서버로 옮길 자리 |
| 취합 추가 열(원시 열)의 값 | `ovAggXCols` | 계산이 아니다 — 연구소가 적어 보낸 **원문을 모으기만** 한다(더하지 않는다 · `ovXRawCell`). 같은 이름을 두 연구소 이상이 쓰면 연구소마다 한 열로 가른다. 저장된 행 키는 집계 줄 키로 번역해서 모은다(`rowKeyOf`). 숫자를 %로 그릴지는 서버에 저장된 열 형식(`extra_cols.fmt`)이 정한다 — 화면이 값을 보고 추측하지 않는다 |
| 환율 배열 | `ovRate` · `ovRateOV` | 요율(`rt*`) · 일반(`g*`) 계정이 같이 쓴다 — 그 계정들은 `app/accounts/`에 백엔드가 없다 |


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

이 도구 세트가 `/overseas/*`의 거의 모든 엔드포인트(`extra-rows`, `sheet-rows`,
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
`notes`(챗봇으로 넣은 "테스트 메모"·"테스트 사유"·중복 더미 행)에서 실제로 발견되어 정리했다
(`memos`·`memo_cols`는 그 뒤 테이블 자체를 없앴다 — 화면에 보이지 않는 열이라 챗봇이 만들어도
사용자가 확인할 수 없었고, 같은 자리를 `extra_cols`가 대신한다).
운영 전환 시 이 경로로 들어간 데이터를 주기적으로 점검할 것.

---

## 5. 실적결산 화면(`#/close`)의 해외연구소 탭 — 지금 어디까지 붙어 있나

화면은 `shared/core.js` 맨 뒤의 "해외연구소 실적결산" 절에 있다. 기존 실적결산 코드를 고치지 않고
**뒤에서 감싸는 방식**으로 붙였다(`const _ovclStep1=clsStep1; clsStep1=function(...)`) — core.js가
이미 쓰는 후방 확장 관용구와 같고, 한 파일을 여러 사람이 동시에 고칠 때 충돌이 안 난다.

| 화면 동작 | 지금 | 서버 엔드포인트 |
|---|---|---|
| **1단계 [전표 업로드]** | **서버 적재** | `POST /close/vouchers` ✅ |
| **화면 열기(전표·매핑 읽기)** | **서버에서 읽음** | `GET /close/vouchers` · `GET /close/maps` ✅ |
| **2단계 매핑·제외·이관 → [수정 저장]** | **서버 저장**(바뀐 전표만) | `PUT /close/maps` ✅ |
| **[1차 완료] / [매핑 완료] / 취소** | **서버 저장** | `PUT /close/stages` ✅ |
| **AI 1차 추천(신뢰도·근거)** | **서버 계산값** (못 받으면 브라우저 판정으로 폴백) | `GET /close/maps`의 `decided_by='ai'` ✅ |
| **[✦ AI 다시 매칭]** | **서버가 다시 계산** | `POST /close/overseas/rematch` ✅ |
| **AI 규칙 등록 · 제안 거절 · 삭제** | **서버 저장**, 화면 열 때 서버에서 읽음 | `GET/POST/DELETE /close/match-rules` ✅ |
| **규칙 제안(몇 번 반복했나)** | **서버 계산**(팀 합산·과거 달 포함) | `GET /close/overseas/mapping`의 `suggestions` ✅ |
| **전표의 지정(Assignment)** | **읽어서 서버에 올림** | `POST /close/vouchers` ✅ |

연결은 **화면 동작을 고치지 않고 뒤에 덧붙이는 층**으로 했다(`core.js` 맨 뒤 `clsApi*` — 업로드·저장·
완료 지점을 감싸서 서버 호출만 추가한다). 서버에서 못 받으면 `CLS_API_OFF`로 예시 전표로 떨어지고
로그만 남긴다 — 화면은 서버 없이도 돌아가야 한다.

**[1차 완료]를 가장 먼저 붙인 이유**: 그 값은 혼자 쓰는 값이 아니다 — 증감분석 화면의 [1차 분석]
탭을 열지 말지가 `close_account_stages.mapping_done`에 달려 있어서, 브라우저에만 두면 결산
담당자와 증감분석 담당자가 서로 다른 상태를 본다.

**2026.11.18에 나머지도 서버로 넘겼다.** 그 전까지는 화면이 추천·규칙을 브라우저에서 따로 계산해서,
같은 판정이 두 벌로 돌고 규칙이 담당자 한 사람의 브라우저에만 쌓였다. 바뀐 점:

- **AI 추천은 서버 것을 쓴다.** `clsApiLoad`가 버리던 `decided_by='ai'` 매핑을 `D.srvAi`에 담아
  추천·신뢰도(`high/mid/low` → 높음·중간·낮음)·근거로 쓴다. 서버에 못 붙었거나 그 전표의 서버 추천이
  없으면 브라우저 판정(`clsAI`·`ovclAI`)으로 돌아간다 — 화면은 서버 없이도 돌아가야 한다.
- **[✦ AI 다시 매칭]은 서버가 다시 계산한다**(`clsApiRematch`). 방금 등록한 규칙을 그 달 전체에
  적용하는 것도 이 경로다. 담당자가 고친 것(`user`)과 규칙이 붙인 것(`rule`)은 서버가 건드리지 않는다.
- **규칙·거절은 서버에 저장되고 화면 열 때 서버에서 읽는다**(`ovclRuleSave`·`ovclRulesLoad`).
  받아오면 브라우저 목록을 **대체한다**(합치지 않는다) — 합치면 다른 담당자가 지운 규칙이 내
  브라우저에만 살아남아 같은 달을 둘이 다르게 본다. 거절은 규칙 목록이 아니라 "다시 묻지 마" 표시로 간다.
- **제안 횟수를 서버가 센다**(`ovclSuggestLoad`). 서버는 `close_voucher_maps`의 담당자 수정을
  **사람 구분 없이, 과거 달까지** 세므로 "팀 합쳐 3회"가 된다. 전에는 브라우저가 내 수정만 세서
  "내가 혼자 3번"이어야 제안이 떴다 — 갑이 2번·을이 1번 고치면 아무에게도 안 떴다.
- **전표의 지정(Assignment) 열을 읽는다.** 업로드 열 목록(`CLS_COLS`)에 넣었고 머리글이 `지정`이든
  `Assignment`든 받는다(`CLS_COL_ALIAS`). 그래서 `"지정이 'CP-'로 시작하면 CP"` 규칙이 실제로 걸린다.

남은 차이 하나: 화면 [규칙 등록]은 제안에서 나온 값을 **필드 기본 비교 방법**으로 건다(적요는 포함,
나머지는 같음). `starts_with` 같은 비교 방법을 골라야 하는 규칙은 지금은 챗봇(`upsert_match_rule`)으로
등록한다 — 제안 카드에는 비교 방법을 고르는 자리가 없다.

**해외연구소만 완료가 두 층이다.** 다른 계정은 [매핑 완료]가 곧 계정 확정이지만, 해외연구소의 전표
매핑은 ERP 약식 **1차**일 뿐이다. 그래서 이 화면은 [1차 완료]까지만 하고, 계정 완료는 편성 화면의
실적 시뮬레이션 [확정](`PUT /versions/{y}/{m}/actual/accounts/overseas`, `verAcctApiSave`)으로만 된다.
3단계 확정 표의 해외연구소 줄이 "1차 완료 / 완료"를 가려 보여주는 게 그 구분이다 — 상태를 하나로
묶으면 1차만 끝난 달을 "결산 끝"으로 읽는다. 1차 없이도 2차를 진행할 수 있다.
