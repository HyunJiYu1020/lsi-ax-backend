# 구조 결정 기록 — 무엇을 왜 이렇게 배치했나

지금 구조가 왜 이 모양인지, 그리고 아직 안 끝난 일과 안 정한 일이 무엇인지를 적는다. 날짜별 진행
기록과 이미 끝난 리팩토링의 경위는 git 이력에 있다.

- 실행 방법·시드·마이그레이션 → [README.md](./README.md)
- 엔드포인트별 설계 이유 → [API.md](./API.md)
- 프런트가 어디로 붙는지·무엇이 서버고 무엇이 브라우저인지 → [CONNECTIONS.md](./CONNECTIONS.md)

---

## 1. 지금 구조

```
app/
├── shared/                    # 계정이 공유하는 것
│   ├── core/                  # DB를 모르는 순수 로직 (anomaly · row_match · voucher_match
│   │                          #   reason_translate · periods · values · cost_groups · confirm_state)
│   ├── models/  schemas/      # 전 계정 공용 테이블·스키마
│   ├── services/              # DB를 아는 공용 로직 (fx_rates · reference)
│   └── endpoints/             # 계정 공용 API (accounts · fx · sheet · chat · versions · reference)
├── accounts/overseas/         # 해외연구소 — 유일하게 깊게 구현된 계정
│   ├── endpoints/             # collect(취합) · aggregate(집계) · variance(증감) · close(결산)
│   └── services/              # versions · entries · aggregate · variance · labor_split
│                              #   labs · fxcalc · cp · vendor · state · extra_rows · reason_rows
├── api/v1/router.py           # 라우터 묶음
├── db/                        # session · seed · models_registry
└── mcp_server.py              # 챗봇 도구 — 자기 REST API를 호출한다
```

**의존 방향은 한 방향이다**: `endpoints → services → shared/core · models`. endpoints끼리 서로
import하지 않는다 — 라우트 핸들러를 함수로 부르면 그 화면의 응답 모델 전체에 묶인다.

**계정이 10개 이상 늘어날 예정**이라 "이 파일이 공통인가 한 계정 것인가"를 **경로로** 구분한다.
`shared/`에 있으면 전 계정이 쓰는 것이고, `accounts/<계정>/`에 있으면 그 계정 것이다.

범용 개념 테이블(`row_aliases` · `reason_translations` · `carry_defs` · `anomaly_rules` ·
`variance_reasons` · `variance_drivers`)은 계정별 접두사 대신 `account_key` 컬럼으로 구분한다.

## 2. 남은 일

**수식 백엔드 단일화 잔여** (서버 API가 없어서 브라우저가 아직 계산하는 자리)

- **외주용역 증감분석** — `ovFxSplit` · `ovOutsAnalysis`. 과제·업체 단위 증감이라 비용 본체로는
  낼 수 없다(`cost_entries`에 과제 칸이 없다). `overseas_vendor_rows` 범위 전용 API가 필요하다
- **증감사유 초안·AI 요약의 증감 문장** — `ovRsnDraft` · `ovRsnAi`가 `ovAnaEl` · `ovLaborSplit`로
  문장을 만든다. 표 숫자는 서버에서 오는데 문장은 브라우저가 만들어서, 둘이 어긋날 수 있다
- **취합 그리드의 소계·분기·연간** — `ovPartEl` · `ov17` · `ovPct17`. 이건 **저장 전 입력값**이라
  서버가 모른다. 옮기지 않는 게 맞고, 대조 하네스(`tests/overseas/js_formula_harness.mjs`)가
  이 넷을 백엔드와 계속 비교한다

**원칙 — 폴백을 두지 않는다.** 서버가 죽으면 숫자를 **안 보여준다**. 폴백이 있는 동안은 틀릴 수
있는 숫자가 조용히 그려지고 사용자는 그게 폴백인지 알 수 없었다 — 숫자가 안 나오는 것보다 다른
숫자가 나오는 쪽이 위험하다.

**`PUT /state`에 남은 쓰기**: 비용 값 그리드 · 실적 스냅샷 · 업로드 이력 · 메모 · 진행 단계.
최종 목표("값 그리드 저장만")에는 뒤 셋을 더 떼어내면 닿지만, 그 셋은 한 번에 저장돼야 뜻이 맞는
값이라 지금 모양을 유지한다.

**구조 부채**

- `endpoints/collect.py`가 2,760줄이다 — 취합·기준정보·행/열·이상감지·업로드가 한 파일에 있다.
  공유 부품은 뺐지만 엔드포인트 자체가 많다
- `PUT /overseas/state`와 `PUT /cost-entries/batch`가 같은 데이터를 **중복 upsert**한다
  (`core.js`의 `ovCostApiBatch`). 결과값은 같아서 깨지지 않지만 불필요한 쓰기다 — 프런트 정리 필요
- `app/mcp_server.py`가 1,879줄이다 — 도구가 늘어난 만큼 커졌다
- 범용 테이블은 **해외연구소 하나로만 검증된 상태**다. 두 번째 계정을 올릴 때 모양
  (matrix / line_items / monthly_total)이 안 맞으면 그때 고치는 걸 전제로 둔다

## 3. 아직 안 정한 것

- **인증 방식** — 로그인 수단이 정해지지 않았다. `updated_by_id` 계열은 계속 NULL이고,
  `_current_user_id()` 한 곳만 채우면 된다
- **다음에 구현할 계정** — 지금 있는 계정이 해외연구소뿐이라 정해지지 않은 게 정상이다
- **React(`frontend/`)의 수명** — 쓰지 않는다. 되살리려면 `calc.js`의 MARK UP 비율 분모를 먼저
  고쳐야 한다(개발비(매출)이 아니라 개발비로 나눈다)
- **ERP 연동** — 화면 문구·챗봇 프롬프트에 "I/F 연결 예정"으로만 나온다. 실적 1차는 사람이 올린
  엑셀이다
