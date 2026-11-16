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
| 프런트 | **`frontend-html/`가 쓰는 프런트**(core.js가 계산 원본). `frontend/`(React, calc.js)는 2026-11-16 기준 쓰지 않음 |
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
- 원인은 역사적: 프런트(core.js)가 먼저 있었고 백엔드가 나중에 생김. 챗봇이 화면과 같은 숫자를 말해야 해서 백엔드에 복제됨
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
| 단기 (권장) | 대조 테스트: 같은 입력 → core.js·백엔드 결과 비교하는 테스트 1벌 | 이중화를 깨지 않고 어긋남만 즉시 감지. core.js를 노드로 실행해 백엔드 결과와 비교 |
| 장기 (권장) | 백엔드가 유일한 계산자, 프런트는 표시 + 즉각 피드백(셀 수정 시 합계)만 로컬 계산 | frontend-html을 계속 쓰므로 화면 단위로 하나씩 서버 계산으로 넘긴다 — 집계 ① 탭이 첫 사례 |

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
- 같이 고친 것 ②: **TDC(대만)의 현지통화가 화면과 서버에서 달랐다** — 화면은 USD, 서버는 TWD. 초기 단일 파일 `budget-platform-ax 261103.html`에서 TDC만 콕 집어 USD로 덮는 코드가 들어갔는데(`ovLabsSeed`의 `r.c==="TDC"?"USD":...`, `261102 v2.html`에는 없다 → 11/03에 추가된 결정), 같은 날 들어간 마이그레이션 `b3f4c91a7d2e`가 국가→통화 일괄 매핑("대만"→TWD)으로 백필하면서 이 예외를 놓쳤다. TDC는 $로 결산하는 연구소라 **USD가 맞다** — 마이그레이션 `d2b7f04e9a16`으로 DB를 고치고, `calc.js`의 하드코딩 초기값(TWD)과 `seed.py`도 USD로 맞췄다. DB·core.js 모두 USD임을 `test_lab_currency_tables_agree`가 못 박는다(`calc.js`는 대조 대상에서 빠졌다 — 아래 2026-11-16 결정)
  - 이력(`overseas_lab_revisions`)에 행을 추가하지 않고 현재 값을 고쳤다 — 기준정보 이력은 "통화를 바꾸기로 한 결정"을 그 버전부터 적용하기 위한 것이고, 이건 결정이 바뀐 게 아니라 처음부터 USD였어야 할 값이 잘못 들어가 있던 것이다(과거 보고서도 원래 나와야 했던 숫자로 재계산되는 게 맞다)
- 같이 고친 것 ③: **`seed.py`가 연구소 통화를 아예 안 넣고 있었다.** 통화는 마이그레이션의 1회성 백필에만 의존해서, "마이그레이션을 다 돌린 뒤 새로 시드한" DB는 모든 연구소가 컬럼 기본값 USD로 깔렸다 — 현지통화 연구소의 환차 분해(현지→$ 단계)가 통째로 사라지는 상태다. 통화는 환산의 입력이라 시드가 직접 책임지도록 `OVERSEAS_LABS`에 넣었다
- 롤백: 테스트 추가 + 조회 정렬 2곳 (DB 무변경, `git revert`로 끝)

**Phase 1 — 코드 배치 리팩토링 (DB 무변경)** — ✅ 2026-11-16 완료

끝난 것:
- `core/periods.py`(기간) · `core/values.py`(n·source_hash) — 순수 유틸
- `services/fx_rates.py`(적용 환율 계산, 전 계정) · `services/overseas/fxcalc.py`(rate_arrays)
- `services/overseas/labs.py`(all_labs·lab_views·LabView·ACCOUNT_KEY)
- `services/overseas/entries.py`(읽기/쓰기 + **분기·연간 재계산 통합 → P1-3 해결**, current_basis_month)
- `services/overseas/aggregate.py`(group_tree·krw_months·cp_quarter_amounts·extra_row_months·TOT/LABOR_TREE)
- `overseas_variance.py`는 `overseas.py`를 **전혀 import하지 않는다**
- `_month` 중복 정의 통합(P3)

- `services/overseas/state.py`(화면 상태 조립) · `variance.py`(증감 계산) · `extra_rows.py`(추가 행 읽기)
- **라우트 핸들러 직접 호출 전부 제거** — `get_overseas_state` · `check_variance_reasons` ·
  `list_extra_rows`를 services 함수로 대체했다

완료 조건 4개를 모두 만족한다(검증 명령은 아래):
1. 엔드포인트 간 import 0건 2. services → endpoints 역방향 0건
3. 밑줄 이름 교차 import 0건 4. 라우트 핸들러 직접 호출 0건

`overseas.py` 3,049 → 2,371줄, `overseas_variance.py` 1,474 → 694줄.
services 1,895줄 + core 65줄로 빠져나왔다.

**증감분석을 가른 방법**: 의존을 하나씩 쫓아 옮기다 꼬였다(`_plan_revisions` → `ManagementPlanVersion`
→ `_has_cost_data` …). 원칙으로 한 번에 가르는 게 맞았다 — **@router가 붙은 라우트 핸들러만
엔드포인트에 남기고 나머지 전부 services로.** 그러면 의존 폐쇄를 계산할 필요가 없다(라우트 14개 /
도메인 44개로 갈렸다).

**이름 짓기 교훈**: 공용으로 올리는 이름은 흔한 지역 변수명과 겹치지 않게 고른다. `_yyyymm`→`yyyymm`,
`_labs`→`labs`, `_basis_month`→`basis_month`로 바꿨을 때 각각 `UnboundLocalError`로 2건·41건·12건이
깨졌다(지역 변수가 함수를 가린다). `all_labs`·`current_basis_month`처럼 접두사를 붙여 피했고,
공개 이름 25개에 대한 AST 그림자 검사를 돌린다.
- `services/overseas/` 신설, 공유 헬퍼 이동 + 공개 이름 전환 (D1-A). endpoints의 교차 import를 services 참조로 교체, `overseas_aggregate`의 라우트 핸들러 직접 호출 제거
- `_check_kind` 등 소소한 중복 통합, `core/periods.py` 추출
- 이때 **분기·연간 재계산을 `services/overseas/entries.py`의 bulk upsert에 통합** (P1-3 해결 — 쓰기 경로가 하나로 모이는 시점이 유일한 해결 기회)
- 검증: Phase 0 스냅샷 테스트 + 기존 19개 테스트. 롤백: git revert (DB 무관)

**Phase 2 — 동시 편집 보호 (스키마 무변경, 프런트 소폭)** — ✅ 2026-11-16 완료 (백엔드)
- `state_token` 발급·검증 (D2-B 1단계). 프런트: 409 수신 시 "다른 저장과 충돌 — 다시 불러오세요" 처리
- 롤백: 서버가 토큰 검증만 끄면 됨(프런트는 토큰 없이도 동작하게 구현)

**Phase 3 — 테이블 범용화 (D3-A)** — ✅ 2026-11-16 완료 (리비전 `e1a4c7b93d28` 하나로)
- 배치 1 (저위험): `reason_translations` rename / `row_aliases`·`carry_defs`·`anomaly_rules`+`logs` rename+`account_key` 백필
- 배치 2: `variance_reasons`·`variance_drivers` (uq 키 변경 포함)
- 각 리비전: `ALTER TABLE RENAME` + `ADD COLUMN account_key NOT NULL` + uq 재생성. downgrade 대칭 구현
- **데이터는 재시드한다**(열린 질문 1 결정) — 기존 행을 `'overseas'`로 백필하는 단계가 필요 없다. 스키마만 바꾸고 `app.db.seed`를 다시 돌린다. 덕분에 `DEFAULT 'overseas'` 임시 지정 → 백필 → default 제거의 3단 춤을 생략하고, `account_key`를 처음부터 기본값 없는 NOT NULL로 둘 수 있다(계정을 명시하지 않은 삽입을 DB가 바로 막는다)
- API 경로·요청 모양 유지(핸들러가 account_key 주입) → 프런트·MCP 무변경. 모델·쿼리만 수정
- 검증: 전체 테스트 + 챗봇 도구 스모크. 롤백: downgrade 2회

**Phase 4 — actual_stages 병합** — ✅ 2026-11-16 완료 (리비전 `f3c8a21e6b47` 하나로)
- **재시드 가능하므로 2단계 배포가 필요 없다**(열린 질문 1 결정) — 아래 4a/4b는 운영 데이터를 지키며 옮길 때의 절차였다. 한 리비전에서 `close_account_stages`에 `first_locked`·`first_locked_at`를 추가하고 `overseas_actual_stages`를 drop한 뒤 재시드한다
- (운영 데이터가 생긴 뒤에 하게 되면) 4a: `close_account_stages`에 `first_locked`·`first_locked_at` 추가 + 데이터 복사 + **읽기 전환** (쓰기는 양쪽 기록). 문제 시 읽기만 원복
- 4b: 한 사이클 검증 후 `overseas_actual_stages` 쓰기 중단·테이블 drop
- 롤백: 4a는 읽기 스위치 원복, 4b 이전엔 데이터 양쪽에 존재

**Phase 5 — PUT /state 점진 분해 (D2-B 2단계, 풀스택)** — ✅ 2026-11-16 완료

- 전용 저장 API 3개: `PUT /overseas/vendor-rows`(연구소×버전 교체) · `/cp-rows`(연도 교체) ·
  `/reason-rows`(키별 upsert 누적). 각각 `services/overseas/{vendor,cp,reason_rows}.py`
- **`PUT /state`와 전용 API가 같은 서비스 함수를 부른다** — 쓰기 규칙이 두 벌이 되면 P1-3의
  "같은 데이터, 두 가지 쓰기 규칙"을 다시 만든다. 그래서 두 경로 병행이 안전하고, 도메인마다
  `test_matches_the_bulk_state_path`로 결과가 같은지 확인한다
- 프런트: `OV_SPLIT_SAVE` 플래그. true면 업로드 시점에 전용 API로 보내고 일괄 페이로드에서 뺀다.
  false 한 줄로 옛 동작 복귀(롤백 조건 충족). 조립은 `ovReasonPayload`·`ovVendorPayload`·
  `ovCpPayload`로 분리해 두 경로가 같은 조립을 쓴다
- 부수 수확: 증감사유 upsert가 `uploaded_at`을 갱신하지 않아 `state_token`이 내용 변경을 놓치던
  **Phase 2의 보호 구멍**을 테스트가 잡아 고쳤다

`PUT /state`에 남은 쓰기: 비용 값 그리드 · 실적 스냅샷 · 업로드 이력 · 메모 · 진행 단계.
PLAN의 최종 목표("값 그리드 저장만")에는 업로드 이력·메모·진행 단계를 더 떼어내면 도달하지만,
그 셋은 "단위 교체"가 아니라 성격이 섞여 있어 분리 이득이 작다 — 필요해질 때 하면 된다.
- 외주용역 → CP → 증감사유 순으로 전용 저장 API로 이관(각각 독립 배포), 프런트 저장 버튼이 바뀐 도메인만 호출하도록 수정
- 최종: `/overseas/state`는 GET 전용(화면 초기 로드) + 값 그리드 저장만 담당
- 롤백: 도메인별 플래그로 구(일괄)·신(개별) 경로 병행 유지 후 전환

**Phase 6 — 수식 백엔드 단일화 (D4 장기)** — 집계 화면(①~④) 완료, 증감·취합 화면 남음
- 집계·증감·환산 결과를 내려주는 조회 API 확충 → 화면이 그 값을 쓰게 교체 → core.js의 계산 코드 제거
- Phase 0의 대조 테스트가 이행 중 가드레일

**2026-11-16 (집계 ① 전체 계정 표)**
- 서버: `GET /overseas/aggregate-table` (45행 × 17칸). `to_17`·`agg_months`·`pct_17`·`pl_rows`가
  services/aggregate.py에 있고, 미등록(None)과 보고된 0을 끝까지 구분해 내려준다
- 프런트: 서버 값을 쓰고, 서버가 없거나 실패하면 `ovPLRows`로 떨어진다. 로컬 계산을 **지우지
  않고 폴백으로 남긴** 이유는 한 화면씩 넘기는 중이라 되돌릴 자리를 남겨 둔 것이다 — ②~④ 탭까지
  서버 값을 쓰게 되면 그때 같이 지운다

**이행 중 배운 것**
- **값 출처를 바꿀 때 행 목록도 같이 바뀐다**: 서버는 기본 45행만 주는데 화면은 사용자 추가 행·
  파생 행을 앵커 자리에 끼워 넣고 있었다. 서버 값으로 그냥 갈아타면 추가한 행이 조용히 사라진다.
  병합을 `ovPLMerge`로 떼어내 두 경로가 같이 쓰게 했다 — 복붙하면 "같은 수식 세 곳" 문제를
  다시 만든다. 앵커가 `OV_EL` 인덱스 기준이라 **서버 행 순서가 OV_EL과 같아야** 하고, 그 전제를
  먼저 확인한 뒤에 바꿨다
- **대조 테스트는 합성 데이터라 "서버가 틀렸는지"는 못 잡는다**: 실제 응답으로 내부 정합성
  (개발비+MARK UP=개발비(매출), (%)=MARK UP÷개발비(매출), 분기=3개월 합, 연간=12개월 합,
  인력=평균)을 따로 확인했다. 인력이 평균인지 합인지는 틀려도 테스트가 통과하는 자리였다
- 부수 수확: 집계표 (%) 행에 라벨이 없어 화면에 코드명 `pct`가 그대로 보였다. 증감분석은
  `"(%)"`를 직접 적어 뒀어서, 둘 다 `CATEGORY_LABELS` 한 곳을 보게 고쳤다

**2026-11-16 (집계 ②③④ 탭 · 표 6장)**
- 서버: `GET /overseas/lab-tables` — 여섯 표를 **한 번에** 준다. 비싼 건 표 조립이 아니라 화면
  상태를 읽는 일이고 그 읽기를 여섯 표가 같이 쓴다 — 탭마다 부르면 같은 읽기를 여섯 번 한다
- ①과 준비 과정(상태 읽기·연구소 추리기·환율)이 같아서 `_table_inputs`로 묶었다. 따로 적어 두면
  "어느 연구소를 넣는가" 같은 규칙이 표마다 갈린다(P1-3의 교훈)
- CP 분기 정산(`cp_quarter_months`)과 인당 개발비(`per_capita_dev_17`)를 서비스로 옮겼다
- 재료가 없으면 표를 빼고 준다 — 환율이 없으면 원화 표, CP 계약이 없으면 ④. 0원 표를 내려보내면
  "보정했다"로 읽힌다

**이행 중 배운 것 (②③④)**
- **행 구성까지 대조해야 했다**: ①은 계정 45행이 고정이라 숫자만 맞으면 됐지만, ②③④는 표마다
  행 조합이 다르다 — ③ 달러 표만 "CP·SRCX 제외" 줄이 없고(프런트가 `filter(i!==1)`로 뺀다),
  ②·④만 SARC 하위 줄이 있고, 연구소 줄 이름도 표마다 꼬리말이 다르다. 숫자만 맞추면 행이 하나
  어긋난 표를 통과시킨다. 처음 대조에서 틀린 것은 환율 행 들여쓰기 하나뿐이었다
- **화면만 바꾸면 엑셀이 어긋난다**: 다운로드(`ovTblDl`)가 `ovTables()`를 다시 불러 계산하고
  있었다. ① 탭을 넘길 때 이걸 놓쳐서 **화면은 서버 값, 파일은 브라우저 값**인 상태였다(값이
  같아서 안 보였다). 행 고르기를 `ovSumRows`로 묶어 화면·파일이 같이 쓰게 했다
- 프런트는 SARC 하위 줄을 `SARC:CPU` · `SARC:GPU`로 **하드코딩**해 뒀다. 서버는 기준정보의 파트
  이름에서 뽑는다 — 파트 구성이 바뀌면 화면만 못 따라간다
- 확인이 필요한 것: 인당 개발비의 구간 평균 인력이 "인원이 올라온 달 수"가 아니라 **구간 길이
  (3 · 12)**로 나눈다. `to_17`의 avg가 고쳤던 것과 같은 모양이라 일부 달만 인원이 올라오면 인당
  개발비가 높게 나온다. 프런트와 같게 맞춰 뒀다 — 고치면 화면 숫자가 바뀌므로 함께 바꿀 일이다

**2026-11-16 (증감분석 계정별 상세)**
- 서버: `GET /overseas/variance-table` — 17기간(월 12 + 분기 4 + 연간) × 계정 45행을 한 번에.
  기간별 응답을 합치지 않고 따로 주는 이유: 월 금액을 더해 분기를 만들 수 없는 행이 있다
  (인력은 평균, 비율은 분자·분모를 각각 더한 뒤 나눈다)
- 준비물(기준 해석 · 연구소 목록 · 두 버전 금액)을 `_variance_setup`으로 빼서 한 번만 만든다.
  기간마다 만들면 같은 DB 읽기를 17번 한다 — 그게 이 화면이 브라우저에서 계산되던 이유다
- 인건비 분해를 `services/labor_split.py`로 추출했다. 엔드포인트(`/overseas/labor-split`, 한 기간)와
  증감분석 표(17기간)가 **같은 함수**를 쓴다 — 식이 두 벌이 되면 화면과 챗봇이 다른 답을 한다
- 표에 싣는 분해는 합계만이다(`LaborSplitTotals`) — 화면이 합계 줄만 그리는데 연구소별 상세까지
  실으면 안 쓰는 줄 340개가 따라온다(260KB → 198KB). 상세가 필요하면 엔드포인트를 쓴다

**이행 중 찾은 버그 (실제 사고 자리)**
- **증감분석 (%) 행의 분모가 개발비(A)였다** — 화면과 집계표는 개발비(매출)(tot)이다. MARK UP이
  0인 개발 데이터에서는 두 값이 같아서 드러나지 않았고, B가 0이 아니면 챗봇이 화면과 다른 %를
  말한다(18.75% vs 23.08% — 집계표에서 React가 틀렸던 그 숫자와 같다). tot으로 고쳤다
- **증감분석 표가 38행, 화면은 45행**이었다 — 참고 행(IT 관련 비용·외주용역비 7개)이 빠져 있어서
  화면이 서버 값을 쓰면 그 줄이 빈칸이 된다. `variance_cells`·`variance_rows`가 집계표와 같은
  `AGGREGATE_ROWS`를 돌게 했다
- **엑셀 다운로드가 연구소마다 따로 받아야 한다** — 선로딩(`ovVarPreload`) 없이 그리면 아직 안
  받은 연구소만 브라우저 계산 값이 들어가 **한 파일에 서버 값과 로컬 값이 섞인다**(집계
  다운로드에서 이미 한 번 겪었다)

**남은 범위** (2026-11-16 기준)
1. 증감분석 요약 표(행 = 연구소, `ovLabDiff`)는 아직 브라우저 계산 — 연구소 × 기간이라
   연구소별로 받거나 전 연구소를 한 번에 주는 엔드포인트가 필요하다
2. 취합 화면 — 아직 브라우저 계산. 다만 **입력 중 즉시 합계는 로컬이 맞다**(셀마다 왕복할 수
   없다). 저장된 값을 보여주는 집계만 서버로 넘기는 게 맞다
3. 집계 ①~④의 폴백 제거 — 서버가 유일한 계산자가 되면 `ovPLRows`·`ovTables`를 지운다
4. `core.js` 계산 함수 제거 — 위 셋이 끝난 뒤

**(별도 트랙) fx_applied_rates 폐기** — 어느 Phase와도 독립. 읽는 곳이 fx.py 조회 화면뿐인지 확인 → 화면을 계산 API로 전환 → drop.

## 7. 결정이 필요한 질문

1. ~~**운영 데이터**: 보존 대상인가, 재시드 가능한가?~~ — **2026-11-16 결정: 개발용 데이터라 재시드 가능.** Phase 3·4는 백필 없이 재시드 방식으로 짠다(아래 Phase 3·4 참조)
   - 2026-11-16 확인: **계정은 10개 또는 그 이상 추가 예정.** Phase 3(테이블 범용화)의 전제가 성립한다 — 계정당 테이블을 복제하지 않는 구조가 맞다
2. ~~**frontend-html(목업)의 수명**: React로 수렴할 계획과 시점은?~~ — **2026-11-16 결정: frontend-html이 쓰는 프런트다(목업이 아니다). React(`frontend/`)는 지금 쓰지 않는다.**
   - 용어 정정: "목업"은 `1110.html`·`budget-platform-ax 261103.html` 같은 초기 단일 파일을 말한다. `frontend-html/`은 그걸 폴더로 쪼갠 뒤 계속 고쳐 온 제품이고, 실제로 `1110.html`보다 기능이 앞서 있다
   - 그래서 **대조 테스트의 React 비교를 걷어냈다**(2026-11-16) — 안 돌리는 코드와 맞춰 보는 비교는 깨져도 아무도 안 아프고, 고치려면 멈춰 있는 폴더를 건드려야 한다. 남은 건 core.js ↔ 백엔드 2자 비교이고, 변형 4종(avg 분모, 빈칸→0, 비율 분모, 그룹 리프)으로 여전히 회귀를 잡는 걸 확인했다
   - 지운 테스트 3종은 git 이력에 있다: `test_labor_split_matches_between_frontends`, `test_17_column_aggregation_matches_calc_js`, `test_react_markup_ratio_uses_a_different_base`. React를 다시 꺼내면 되살리면 된다
   - 미해결로 남는 것: React `calc.js`의 MARK UP 비율 분모가 개발비(매출)가 아니라 개발비다. 지금은 안 돌리는 코드라 고치지 않았고, 되살릴 때 먼저 고쳐야 한다
3. ~~**다음에 구현할 계정 2~3개와 그 데이터 모양**~~ — **2026-11-16: 아직 정해지지 않았고, 그게 정상이다**(지금 있는 계정이 해외연구소뿐이다).
   - 남는 사실: Phase 3의 범용 테이블은 해외연구소 하나로만 검증된 상태다. 두 번째 계정을 올릴 때 모양(matrix / line_items / monthly_total)이 안 맞으면 그때 고치는 걸 전제로 둔다 — 미리 맞추려 해도 맞출 대상이 없다
4. ~~**바꾸면 안 되는 API가 있는가**~~ — **2026-11-16 확인: 없다. 경로 정리는 자유다.**
   - 부르는 곳은 `frontend-html`(34곳) · 멈춘 React(1곳) · 챗봇 MCP(`app/mcp_server.py`)뿐이다
   - CORS는 localhost만 허용한다(`allow_origin_regex`), 즉 외부 브라우저에서 부를 수 없다
   - ERP는 화면 문구와 챗봇 프롬프트에 "I/F 연결 예정"으로만 나오고 연동 코드가 없다 — 실적 1차는 사람이 올린 엑셀이다
5. ~~**인증 도입 시점**~~ — **2026-11-16: 로그인 방식이 안 정해졌으므로 미정인 게 정상.**
   - 남는 사실: `updated_by_id` 계열은 계속 NULL이다. `_current_user_id()` 스텁 한 곳에 접합부가 모여 있어서(P3 참조) 나중에 그 함수만 채우면 된다 — 지금 할 일은 없다
6. ~~**챗봇 공급자 3종 모두 유지?**~~ — **2026-11-16 결정: Gemini 하나만 쓴다.**
   - Anthropic · Ollama는 지우지 않고 주석으로 남겼다(`chat.py` · `reason_translate.py` · `config.py` · `.env`). 되살릴 곳을 각 주석에 적어 뒀다
   - 프런트는 `core.js`의 `AG_PROVIDERS` 한 곳만 본다 — 공급자를 다시 넣으면 선택 상자도 같이 돌아온다. 고를 게 하나면 드롭다운을 그리지 않는다
   - **D6(공통 턴 루프 + 공급자 어댑터)은 이로써 할 일이 없어졌다** — 루프가 한 벌뿐이다. 공급자를 다시 늘릴 때가 실제로 필요한 시점이다
7. **일정 제약**: 각 Phase에 쓸 수 있는 시간. Phase 0~2는 합쳐 2~3일 규모, Phase 3~4는 각 1~2일, Phase 5~6은 프런트 포함이라 별도 산정 필요
