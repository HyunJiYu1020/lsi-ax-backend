# S.LSI 예산관리 플랫폼 — DB·코드 구조 재설계안

작성일: 2026-11-15 · 기준: 브랜치 `1110` (미커밋 변경 포함) · 코드 수정 없음, 문서만

---

## 1. 프로젝트 개요

**무엇을 하는 시스템인가**: 반도체 사업부의 **19개 비용계정 예산 편성·결산 플랫폼**.
현재 해외연구소(overseas) 계정이 가장 깊게 구현돼 있고, 나머지 계정은 레지스트리(상태·메모) 수준의 스텁이다. 향후 계정 약 10개가 실제 구현될 예정.

**핵심 사용 시나리오**:

1. **취합** — 연구소 12곳이 보낸 엑셀(비용·증감사유·외주용역·CP)을 업로드하면 행 이름 매칭("같은 행인가요?")과 이상 감지("전실행 대비 +120%, 맞나요?")를 거쳐 DB에 반영
2. **집계** — 연구소별 값을 환율(현지→$→원)로 환산해 집계표를 그리고, 파생 행·열(인당 인건비 등)을 만들어 다음 버전에도 유지
3. **증감분석** — 전실행·전년·경영계획 대비 증감을 계산하고, 연구소 회신(영문) → 번역 → AI 초안 → 담당자 확정의 흐름으로 증감사유를 작성
4. **실적결산** — ERP 전표를 받아 AI가 연구소별로 매핑 추천, 담당자가 확정, 반복 수정은 규칙으로 저장해 다음 달 자동 적용. 1차(ERP 약식) → 2차(PL 확정)의 2단계
5. **챗봇** — 플로팅 채팅으로 조회·수정("급변 기준 50%로 바꿔줘"). 읽기는 즉시, 쓰기는 승인, 삭제는 이중 확인

## 2. 스택

| 항목 | 내용 |
|---|---|
| 언어 | Python 3.x |
| 프레임워크 | FastAPI |
| DB | PostgreSQL (로컬; docker-compose) |
| ORM | SQLAlchemy 2.0 (typed `Mapped`) + Alembic (마이그레이션 74개, 선형 체인) |
| 스키마 | Pydantic v2 (`app/schemas/`) |
| LLM | Anthropic / Gemini / Ollama 3종 스위처블 (번역·초안·챗봇) |
| 챗봇 연동 | `app/mcp_server.py` (FastMCP) — 도구가 자기 REST API를 HTTP로 호출, `chat.py`가 in-process로 재사용 |
| 프런트 | `frontend-html/`(목업, core.js가 계산 원본) + `frontend/`(React, calc.js로 이식 중) 2벌 |
| 테스트 | pytest 19파일, 로컬 Postgres에 SAVEPOINT로 붙어 롤백 (conftest.py) |
| 인증 | 없음 (`_current_user_id()` 스텁이 접합부 역할, overseas.py:293) |

## 3. 현재 구조 진단

### 3.1 테이블 전체 지도 (~40개, 4그룹)

**A. 전 계정 공용 (account_key 패턴)** — 설계 의도대로 잘 동작
`accounts` `account_steps` `account_progress` / `cost_entries`+`details` / `management_plan_versions`+`entries`+`details` / `uploads` `notes` `memos` `memo_cols` `extra_cols` `sheet_rows` / `versions` `version_account_status` / `common_code_groups` `common_codes` `users` `error_logs` / `fx_rates` `fx_applied_rates`

**B. 해외연구소 전용 (overseas_*, 17개)** — 이 중 7개는 사실 범용 개념 (→ 문제 5)

| 소그룹 | 테이블 | 성격 |
|---|---|---|
| 기준정보 | `labs` `lab_revisions` `lab_sheets` | 진짜 전용 |
| 업로드 원본 | `reason_rows` `vendor_rows`+`details` `cp_rows` | 진짜 전용 (파일 모양이 계정 고유) |
| 실적 1차 | `actual_first_rows` `actual_stages` | 전용이나 `actual_stages`는 close와 중복 (→ 문제 6) |
| **범용 개념** | `row_aliases` `reason_translations` `carry_defs` `anomaly_rules`+`rule_logs` `variance_reasons` `variance_drivers` | 이름만 overseas — 다른 계정도 똑같이 필요 |
| 환율 예외 | `fx_rule_overrides` | 전용 (이번 브랜치 이전부터 존재) |

**C. 실적결산 (close_*, 5개)** — 처음부터 19계정 공용으로 설계됨 (경로도 `/close/...`로 분리)
`close_vouchers` `close_voucher_maps` `close_match_rules` `close_dept_rules` `close_account_stages`

**D. 좋은 설계 결정 (유지할 것)**
- 전표(원본 불변)와 매핑(판단)의 분리 + 논리 키 — ERP 재수신에도 담당자 수정 생존 (models/close.py:94-127)
- `lab_revisions`의 시점 이력 — 기준정보 변경이 과거 버전을 소급 변경하지 않음
- `saved_diff`+`needs_recheck` — 재업로드 시 사유 보존 + 재확인 플래그 (허용 오차 100만 원, overseas_variance.py:685)
- "한 번 답하면 저장" 패턴 3곳 일관 적용 (row_aliases / match_rules / carry_defs)
- 모든 테이블·컬럼에 '왜'가 기록된 주석 (ADR 내장)

### 3.2 코드 레이어

```
app/core/      순수 로직 (anomaly·row_match·voucher_match·reason_translate·cost_groups) — DB 모름, 주입식. 양호
app/models/    SQLAlchemy 모델 — 양호
app/schemas/   Pydantic — 양호
app/api/v1/endpoints/  ★ 문제 집중 구역: HTTP 입출력 + 비즈니스 로직 + 공유 헬퍼가 혼재
app/mcp_server.py      챗봇 도구 55개 — REST를 HTTP 루프백 호출 (단일 소스, 의도된 선택)
```

### 3.3 의존 방향 (endpoints 내부)

```
overseas.py (3,049줄) ←─ overseas_variance.py (1,472줄) ←─ overseas_aggregate.py (305줄)
     ↑                                                              │
     └──────────────────────────────────────────────────────────────┘
overseas_close.py (1,056줄) → core/voucher_match.py   (overseas.py에 비의존 — 깨끗)
```

한 방향이라 순환은 없지만, 화살표의 내용물이 문제다: **언더스코어(내부용) 이름 10여 개와 라우트 핸들러 2개**를 직접 import한다.

## 4. 문제점 (심각도순)

### P1-1. 공유 헬퍼가 화면 파일(overseas.py) 안에 산다 — 구조 부채의 뿌리

- `overseas_variance.py:27-40`: `from app.api.v1.endpoints.overseas import _basis_month, _lab_views, _rate_arrays, _entry_months_bulk, _PERIOD_IX, ...` — `_` 접두사(파일 내부용 표시)가 붙은 이름을 외부에서 import. 이름과 용도가 모순
- `overseas_aggregate.py:22-40`: 위와 같음 + **라우트 핸들러를 함수로 직접 호출** (`get_overseas_state`, `check_variance_reasons`) — 집계 화면이 다른 화면의 응답 모델 전체에 결합되고, 확정 사전점검 한 번에 화면 전체 상태 로드 비용을 낸다
- 결과: overseas.py는 "취합 화면 엔드포인트"이면서 동시에 "해외연구소 도메인 라이브러리"다. 3,049줄 중 엔드포인트는 절반이고 나머지는 4개 파일이 공유하는 부품이다. 계정이 늘수록 이 그물이 조밀해진다

### P1-2. `PUT /overseas/state` — 화면 전체 상태 일괄 저장 (overseas.py:760-909)

- **동시 편집 보호 없음**: 같은 화면을 띄운 두 세션(동일인의 탭 2개 포함)이 저장하면 나중 것이 전체를 덮는다(last-write-wins). 충돌 감지 수단이 없어 덮인 쪽은 알 수 없다
- **API가 도메인이 아니라 프런트 상태 모양**(V/actualV/files/note/reason/vendor/cp)을 모델링 — 화면 구조 변경 = API 계약 변경. 메모 하나 고쳐도 화면이 가진 상태 전부가 전송된다
- 원자성(commit 1회)은 확보돼 있음 — 이 점은 유지 가치

### P1-3. 분기·연간 값 재계산이 저장 경로마다 다르다 — 데이터 정합성 위험

- `models/common.py:119-123` (cost_entry_details docstring)이 스스로 인정: 공용 경로(cost.py)는 월 값을 쓸 때 분기·연간 행을 12개월 합으로 재계산하지만, **해외연구소 경로(`/overseas/state`)는 월 행만 쓰고 재계산하지 않는다** → overseas의 QUARTER/YEAR 행은 백필 시점 값으로 정체
- `overseas_aggregate.py`가 `CostEntryDetail`을 직접 읽으므로, 정체된 분기·연간 행을 읽는 경로가 생기면 화면 합계와 다른 숫자가 나온다. "같은 데이터, 두 가지 쓰기 규칙"은 반드시 사고로 이어지는 패턴

### P1-4. 수식 삼중화 (core.js / calc.js / 백엔드)

- `overseas.py:912-915`: "식을 바꿀 때 세 곳을 같이 고칠 것" 주석이 유일한 안전장치
- 원인은 역사적: 목업(core.js)이 먼저 있었고 백엔드가 나중에 생김. 챗봇이 화면과 같은 숫자를 말해야 해서 백엔드에 복제됨
- 환율(`_rate_arrays`), 인건비 분해(labor split), 집계 트리가 해당. 한 곳만 고치면 **화면 숫자 ≠ 챗봇 숫자**가 되고, 이건 사용자가 가장 먼저 알아차리는 종류의 버그다

### P2-5. 범용 개념 7개 테이블에 overseas_ 접두사 — 계정 확장 시 테이블 폭발

- `row_aliases`(행 이름 매핑) `reason_translations`(번역 캐시) `carry_defs`(파생 정의 유지) `anomaly_rules`+`rule_logs`(이상 감지 기준) `variance_reasons`+`variance_drivers`(증감사유·동인)
- 전부 "파일을 받고, 증감을 분석하는 계정"이면 똑같이 필요한 기능. 지금 구조로 계정 10개를 만들면 계정당 7~8개씩 테이블이 복제된다 (`dep_row_aliases`, `outsource_anomaly_rules`...)
- 이미 `cost_entries`·`notes`·`uploads`가 `account_key` 컬럼 패턴으로 전 계정을 받고 있으므로, 같은 패턴으로 맞추면 된다. **데이터가 적은 지금이 이관 비용 최저점**

### P2-6. `overseas_actual_stages` ↔ `close_account_stages` 상태 중복

- `models/close.py:256-258`: "해외연구소의 first_done_at은 overseas_actual_stages.close_first_done과 **짝이다**" — 같은 사실(1차 완료)을 두 테이블이 나눠 들고 애플리케이션이 동기화할 의무를 진다
- `overseas_actual_stages`는 이번 브랜치에서 신설된 테이블로, (연월)당 1행에 두 플래그를 담는다: `close_first_done`(결산 1차 완료 → 증감분석 화면의 [1차 분석] 탭 활성화)과 `first_locked`([1차 고정] — 보고 끝난 1차 값·사유 쓰기 금지)
- `close_first_done`은 `close_account_stages.mapping_done`(account_key='overseas')과 사실상 같은 정보다. `first_locked`만 고유

### P2-7. `fx_applied_rates` 파생 테이블을 코드가 우회

- `overseas.py:930-933` (`_rate_arrays` docstring): 적용 환율 스냅샷 테이블에 "지금 기준월 조합만 채워져 있어서" 읽지 않고 매번 원본(`fx_rates`)에서 계산한다고 명시 — 실제로 환율 0으로 금액이 전부 0이 된 사고가 있었다고 기록
- 파생 테이블이 있는데 신뢰할 수 없어 우회한다면, 그 테이블은 "두 번째 진실"이라는 위험만 남는다. 쓰든(항상 갱신 보장) 버리든(매번 계산) 하나로 정해야 한다

### P2-8. chat.py의 공급자별 턴 루프 3벌

- `_run_turn_anthropic`(409-463) / `_run_turn_ollama`(472-540) / `_run_turn_gemini`(592-658) — 도구 호출 루프·승인 분기·메시지 왕복이 공급자마다 거의 같은 구조로 반복(~250줄). 공급자 추가·규칙 변경 시 3곳 수정

### P3 (낮음, 기록만)

- `_check_kind` 중복 (overseas_variance.py:102, overseas_aggregate.py:53) — 헬퍼 추출 때 통합
- 금액 float 처리 — 재확인 판정에 허용 오차 100만 원이 이미 있어 실해 없음. 신규 비교 지점 추가 시 같은 패턴 유지만 주의
- 인증 부재 — `_current_user_id()` 스텁으로 접합부 한 곳에 수렴돼 있어 의도된 부채로 적절
- ~~GET /overseas/state가 전 연도 CP 반환~~ — 2026-11-15 수정 완료 (기준연도 필터)

## 5. 재설계안

### 5.1 목표 모듈 구조 (코드 배치)

```
app/
├── core/                      # 현행 유지 — 순수 로직, DB 모름
│   ├── anomaly.py  row_match.py  voucher_match.py  reason_translate.py
│   ├── cost_groups.py  chat_prompts.py  config.py  storage.py
│   └── periods.py             # (신설) _PERIOD_IX, _yyyymm 등 기간 유틸 — 전 도메인 공용
│
├── services/                  # (신설) DB를 아는 도메인 로직 — 엔드포인트에서 추출
│   ├── overseas/
│   │   ├── labs.py            # lab_views(시점 이력 해석 포함), 검증
│   │   ├── entries.py         # entry_months_bulk, bulk_upsert_* (+ 분기·연간 재계산 통합 ← P1-3 해결)
│   │   ├── fxcalc.py          # rate_arrays — 환율 배열 계산
│   │   ├── state.py           # 화면 상태 조립 (현 get_overseas_state 본체)
│   │   └── variance.py        # compute_variance, resolve_bases
│   └── close/
│       └── mapping.py         # auto_map, hints, suggestions 조립
│
├── api/v1/endpoints/          # HTTP 입출력만 — 파라미터 검증 → services 호출 → 응답 변환
│   ├── overseas.py            # 취합·기준정보·행/열 (목표 ~1,200줄)
│   ├── overseas_variance.py   # 증감분석
│   ├── overseas_aggregate.py  # 집계 (services를 호출 — 라우트 핸들러 직접 호출 제거)
│   └── overseas_close.py      # 결산
```

원칙: **endpoints → services → core/models** 한 방향. endpoints끼리는 서로 import하지 않는다. 언더스코어 이름의 교차 import 전부 소멸(공개 이름으로 전환).

> "공용 모듈로 빼기 vs 그냥 파일 분리"에 대해: 파일만 더 쪼개면(복사) 같은 수식이 4벌이 된다 — P1-4와 같은 병을 백엔드 안에 또 만드는 것. 공유가 실재하므로 **공용 모듈 추출이 맞다**. 5.3 D1 참조.

### 5.2 목표 DB 스키마

**(a) 범용화 — 7개 테이블에서 overseas를 떼고 `account_key` 추가** (P2-5)

> "테이블명에서 overseas를 떼서 범용으로 쓰면 되는거 아닌가?" — 정확히 그것이다. 단, 이름만 바꾸면 계정 구분이 없어 데이터가 섞이므로, 계정 구분 컬럼을 같이 넣고 기존 데이터는 `'overseas'`로 백필한다.

| 현재 | 목표 | 변경 내용 |
|---|---|---|
| `overseas_row_aliases` | `row_aliases` | +`account_key` (uq: account_key, alias_norm) |
| `overseas_reason_translations` | `text_translations` | 이름만 변경 — 전역 캐시라 계정 컬럼 불필요 |
| `overseas_carry_defs` | `carry_defs` | +`account_key` (uq: account_key, kind, def_key) |
| `overseas_anomaly_rules` | `anomaly_rules` | +`account_key` (uq: account_key, rule_key). 기본값(DEFAULTS)은 계정별 코드 주입 |
| `overseas_anomaly_rule_logs` | `anomaly_rule_logs` | +`account_key` |
| `overseas_variance_reasons` | `variance_reasons` | +`account_key` (기존 uq 키에 추가) |
| `overseas_variance_drivers` | `variance_drivers` | +`account_key` (기존 uq 키에 추가) |

`account_key`는 `accounts.key` FK, 기본값 없음(명시 입력). API 경로·요청 모양은 유지 가능 — 핸들러가 `account_key='overseas'`를 채워 넣으면 프런트·챗봇(MCP 도구는 REST 경유라 자동 호환) 무변경.

**(b) 병합 — `overseas_actual_stages` → `close_account_stages`** (P2-6)

```
close_account_stages (변경 후)
├── basis_year, basis_month, account_key   (기존)
├── mapping_done, first_done_at, note      (기존 — close_first_done 역할 흡수)
├── first_locked      BOOLEAN  (이동)      ← overseas_actual_stages.first_locked
└── first_locked_at   TIMESTAMPTZ (이동)
```

"1차 완료"의 진실은 `mapping_done` 한 곳이 된다. 증감분석 [1차 분석] 탭 활성화도 이 행을 읽는다. 동기화 의무 소멸, 테이블 1개 감소.

**(c) 유지할 것** — 나머지 overseas 전용 9개(기준정보 3, 업로드 원본 4, actual_first, fx_override), close 4개, 공용 전부. vendor의 마스터-디테일도 유지(cost_entries 패턴과 일관).

**(d) `fx_applied_rates` 결정** (P2-7) — 두 안 중 택1 (5.3 D5):
- (권장) **테이블 폐기**: 적용 환율은 항상 `fx_rates`에서 계산. 이미 `_rate_arrays`가 이 방식이고 성능 문제 없음(연 단위 소량)
- 유지한다면: 원본 쓰기 시 전 조합 재계산을 보장하는 트리거 지점 단일화 필수

**(e) 동시 편집 보호** (P1-2) — 상태 저장이 남는 동안의 안전장치:
- GET 응답에 `state_token`(해당 도메인 행들의 `max(updated_at)` 등) 포함 → PUT에 echo → 불일치 시 409 + "다시 불러오세요". 스키마 변경 없음

### 5.3 핵심 결정 사항 — 대안 비교

**D1. 공유 헬퍼 처리** (P1-1)

| 안 | 내용 | 평가 |
|---|---|---|
| **A (권장)** | services/ 계층으로 추출, 공개 이름 | import 그물 해소, 테스트 단위 생김. 비용: 이동 작업 1~2일 |
| B | 각 파일에 복사(완전 분리) | 수식 4벌 — P1-4 재생산. 탈락 |
| C | 현행 유지 | 계정 추가마다 악화. 탈락 |

**D2. PUT /overseas/state** (P1-2)

| 안 | 내용 | 평가 |
|---|---|---|
| A | 즉시 도메인별 분해 | 프런트 저장 로직 전면 수정 — 리스크 큼 |
| **B (권장)** | 1단계 낙관적 잠금 → 2단계 점진 분해 | 잠금은 반나절 작업으로 최악 사고(조용한 덮어쓰기) 차단. 분해는 외주용역·CP(이미 "단위 교체" 의미)부터 떼어내고, 마지막에 state를 읽기 전용화 |
| C | 유지 + 잠금만 | 프런트 종속(P1-2 후반)이 영구화 |

**D3. 범용화 시점** (P2-5)

| 안 | 내용 | 평가 |
|---|---|---|
| **A (권장)** | 지금 rename + account_key 백필 | 데이터 소량·이용자 소수인 지금이 최저 비용. 두 번째 계정 때 테이블 신설 없이 행만 추가 |
| B | 두 번째 계정 구현 때 | 그 시점엔 운영 데이터·화면·챗봇 도구가 더 붙어 있어 이관 범위 확대 |
| C | 계정별 테이블 유지 | 10계정 × 7테이블. 탈락 |

**D4. 수식 단일화** (P1-4)

| 안 | 내용 | 평가 |
|---|---|---|
| 단기 (권장) | 대조 테스트: 같은 입력 → core.js·calc.js·백엔드 결과 비교하는 테스트 1벌 | 삼중화를 깨지 않고 어긋남만 즉시 감지. React 쪽은 calc.js를 노드로 실행해 백엔드 결과와 비교 |
| 장기 (권장) | 백엔드가 유일한 계산자, 프런트는 표시 + 즉각 피드백(셀 수정 시 합계)만 로컬 계산 | 목업(frontend-html) 폐기 시점과 묶어 진행하는 게 효율적 — 열린 질문 2 |

**D5. fx_applied_rates** — 5.2(d) 참조. 폐기 권장(계산 비용 무시 가능, "두 번째 진실" 제거).

**D6. chat.py 공급자 루프** (P2-8) — 공통 턴 루프 1벌 + 공급자 어댑터(메시지 변환·도구 스키마 변환만 담당)로 재구성. 우선순위 낮음 — 동작엔 문제 없고, 공급자를 2종 이하로 줄이는 결정(열린 질문 7)이 먼저다.

## 6. 단계별 이행 계획

각 단계는 독립 배포·롤백 가능. DB 변경은 단계당 Alembic 리비전 1~2개, 롤백 = `alembic downgrade`.

**Phase 0 — 안전망 (DB 무변경, 배포 리스크 0)** — ✅ 2026-11-15 완료
- 수식 대조 테스트 작성 (D4 단기): `_rate_arrays`·labor split·집계 트리에 고정 입력 → 세 구현 결과 비교
  - `tests/js_formula_harness.mjs` — core.js를 vm 샌드박스에, calc.js를 ESM import로 **실제 실행**해서 비교한다(수식을 파이썬으로 베끼면 네 번째 복제본이 되므로). core.js는 모듈이 아니라서 DOM·fetch를 전부 받아넘기는 stub 전역에 통째로 실행하고, 스크립트 끝에 객체 리터럴을 붙여 완료값으로 함수를 꺼낸다
  - `tests/test_formula_crosscheck.py` — 집계 트리(ovPartEl·ovAgg vs `_krw_months`·`resolve_leaves`), 집계 그룹 정의(OV_EL.calc vs `GROUP_CALC`), 인건비 분해(ovLaborSplit, 고정 입력 + 실제 DB로 백엔드까지 3자), 연구소 통화표
  - 고정 입력은 **리프를 하나도 비우지 않는다** — 안 채운 리프가 있으면 그 리프를 집계에 넣고 빼도 합이 안 변해서 대조가 눈을 감는다(변이 테스트로 확인했다)
- `/overseas/state` GET·PUT 왕복 스냅샷 테스트(현재 동작 고정) — 이후 모든 단계의 회귀 검증 기준
  - `tests/test_overseas_state_roundtrip.py` — 금액을 하드코딩한 골든 스냅샷이 아니라 **왕복 동일성**(GET→PUT→GET이 같다)을 성질로 고정한다. DB 내용에 의존하지 않아 회귀 기준으로 더 튼튼하다
  - 같이 고정한 것: 응답 모양(버전 키·12의 배수 월 배열·메모 키 3토막), 목록 순서, 미등록(None) vs 0.0 구분, "PUT으로는 달을 지울 수 없다"(덧쓰기만), 거부된 저장은 아무것도 남기지 않는다
- 발견해서 같이 고친 것: **증감사유·CP 목록에 `ORDER BY`가 없어** 같은 요청이 매번 다른 순서를 돌려줬다(저장 한 번에 1,936행이 섞였다). 화면 표가 새로 고칠 때마다 섞이고 챗봇의 "위에서 세 번째 줄"도 어긋나는 건이라 정렬을 추가했다 (overseas.py의 `reason`·`cp` 조회)
- 남은 미해결 항목: **TDC(대만)의 현지통화**가 core.js(USD)와 DB·calc.js(TWD)에서 다르다. core.js는 TDC만 콕 집어 USD로 바꾸는 코드가 두 군데 있어(`ovLabsSeed`, `ovEnsure`의 `tdcUsd` 플래그) 의도된 결정처럼 보이고, DB는 국가→통화 일괄 매핑(마이그레이션 `b3f4c91a7d2e`)으로 TWD가 됐다. 어느 쪽이 실제 업무인지 확인이 필요해 `test_tdc_currency_is_an_open_question`에 `xfail(strict=True)`로 걸어 뒀다 — 정하면 한쪽으로 맞추고 그 테스트를 지울 것. 영향: TDC의 환차가 2단계로 쪼개지는지 1단계인지가 달라진다(총액은 같고 환차/환차外 배분이 바뀐다)
- 롤백: 테스트 추가 + 조회 정렬 2곳 (DB 무변경, `git revert`로 끝)

**Phase 1 — 코드 배치 리팩토링 (DB 무변경)**
- `services/overseas/` 신설, 공유 헬퍼 이동 + 공개 이름 전환 (D1-A). endpoints의 교차 import를 services 참조로 교체, `overseas_aggregate`의 라우트 핸들러 직접 호출 제거
- `_check_kind` 등 소소한 중복 통합, `core/periods.py` 추출
- 이때 **분기·연간 재계산을 `services/overseas/entries.py`의 bulk upsert에 통합** (P1-3 해결 — 쓰기 경로가 하나로 모이는 시점이 유일한 해결 기회)
- 검증: Phase 0 스냅샷 테스트 + 기존 19개 테스트. 롤백: git revert (DB 무관)

**Phase 2 — 동시 편집 보호 (스키마 무변경, 프런트 소폭)**
- `state_token` 발급·검증 (D2-B 1단계). 프런트: 409 수신 시 "다른 저장과 충돌 — 다시 불러오세요" 처리
- 롤백: 서버가 토큰 검증만 끄면 됨(프런트는 토큰 없이도 동작하게 구현)

**Phase 3 — 테이블 범용화 (D3-A, 리비전 7개를 2배치로)**
- 배치 1 (저위험): `reason_translations` rename / `row_aliases`·`carry_defs`·`anomaly_rules`+`logs` rename+`account_key` 백필
- 배치 2: `variance_reasons`·`variance_drivers` (uq 키 변경 포함)
- 각 리비전: `ALTER TABLE RENAME` + `ADD COLUMN account_key DEFAULT 'overseas'` 백필 + default 제거 + uq 재생성. downgrade 대칭 구현
- API 경로·요청 모양 유지(핸들러가 account_key 주입) → 프런트·MCP 무변경. 모델·쿼리만 수정
- 검증: 전체 테스트 + 챗봇 도구 스모크. 롤백: downgrade 2회

**Phase 4 — actual_stages 병합 (2단계 배포로 롤백 안전)**
- 4a: `close_account_stages`에 `first_locked`·`first_locked_at` 추가 + 데이터 복사 + **읽기 전환** (쓰기는 양쪽 기록). 문제 시 읽기만 원복
- 4b: 한 사이클 검증 후 `overseas_actual_stages` 쓰기 중단·테이블 drop
- 롤백: 4a는 읽기 스위치 원복, 4b 이전엔 데이터 양쪽에 존재

**Phase 5 — PUT /state 점진 분해 (D2-B 2단계, 풀스택)**
- 외주용역 → CP → 증감사유 순으로 전용 저장 API로 이관(각각 독립 배포), 프런트 저장 버튼이 바뀐 도메인만 호출하도록 수정
- 최종: `/overseas/state`는 GET 전용(화면 초기 로드) + 값 그리드 저장만 담당
- 롤백: 도메인별 플래그로 구(일괄)·신(개별) 경로 병행 유지 후 전환

**Phase 6 — 수식 백엔드 단일화 (D4 장기, 목업 폐기와 연동)**
- 집계·증감·환산 결과를 내려주는 조회 API 확충 → React가 calc.js 호출을 API 호출로 교체 → core.js(목업)와 calc.js의 계산 코드 제거
- Phase 0의 대조 테스트가 이행 중 가드레일

**(별도 트랙) fx_applied_rates 폐기** — 어느 Phase와도 독립. 읽는 곳이 fx.py 조회 화면뿐인지 확인 → 화면을 계산 API로 전환 → drop.

## 7. 결정이 필요한 질문

1. **운영 데이터**: 지금 DB의 데이터(시드 + 테스트 입력)는 보존 대상인가, 재시드 가능한가? — 재시드 가능하면 Phase 3·4의 마이그레이션을 더 단순하게(백필 생략) 짤 수 있다
2. **frontend-html(목업)의 수명**: React로 수렴할 계획과 시점은? — Phase 6의 범위가 2벌 수정이냐 1벌이냐로 갈린다. 목업을 곧 버린다면 대조 테스트도 calc.js·백엔드 2자 비교로 축소
3. **다음에 구현할 계정 2~3개와 그 데이터 모양**(matrix / line_items / monthly_total): Phase 3에서 일반화한 테이블이 실제로 맞는지 두 번째 계정으로 검증하고 싶다 — 어느 계정이 먼저인가?
4. **바꾸면 안 되는 API가 있는가**: 외부(타 시스템·ERP I/F)가 호출하는 엔드포인트가 있나? 없다면 Phase 5에서 경로 정리 자유도가 커진다
5. **인증 도입 시점**: Phase 몇 근처에 붙일 계획인지 — `updated_by_id` 계열이 전부 NULL인 상태가 길어질수록 이력 가치가 떨어진다
6. **챗봇 공급자 3종(Anthropic/Gemini/Ollama) 모두 유지?** — 2종 이하로 줄이면 chat.py 리팩토링(D6)이 절반으로 준다
7. **일정 제약**: 각 Phase에 쓸 수 있는 시간. Phase 0~2는 합쳐 2~3일 규모, Phase 3~4는 각 1~2일, Phase 5~6은 프런트 포함이라 별도 산정 필요
