# API 엔드포인트 정리

현재 구현된 엔드포인트 전체 목록. Swagger UI(`http://localhost:8000/docs`)에서 직접 호출해볼 수도 있지만,
여기엔 "왜 이렇게 설계했는지"까지 같이 남겨둔다. 어디서 실제로 호출하는지(화면 버튼 vs AI 챗봇 전용)는
[CONNECTIONS.md](./CONNECTIONS.md) 2~4절 참고.

Base URL: `http://localhost:8000/api/v1`

## 목록

| Method | Path | 설명 | 호출처 |
|---|---|---|---|
| GET | `/health` | 서버 동작 확인용 (실제 로직 없음) | — |
| GET | `/accounts` | 19개 계정 레지스트리 전체(상태·비고·편성 단계·현재 단계) 조회 | 챗봇만 (화면 연결 대기) |
| GET/PUT | `/accounts/{key}` | 계정 하나 조회 / 상태·비고 변경 | 챗봇만 (화면 연결 대기) |
| PUT | `/accounts/{key}/step` | 계정의 현재 편성 단계 이동(그 계정에 정의된 단계만) | 챗봇만 (화면 연결 대기) |
| GET/PUT | `/overseas/state` | 해외연구소 전체 데이터 조회/저장 | 화면 |
| GET/PUT | `/overseas/step` | 해외연구소 진행 단계 조회/변경 | 챗봇만 |
| GET/PUT | `/overseas/labs` | 연구소 기준정보 조회/저장 (GET `as_of=YYYY-MM`으로 그 시점 값 조회, PUT `effective_from`으로 "이 버전부터" 적용) | 화면 |
| GET | `/overseas/lab-sheets` | 연구소별 워크북 시트 구성 조회 | 챗봇만 |
| GET/PUT | `/overseas/cost-entries` | 연구소 비용 데이터 셀 단위 조회/upsert (`cost_entries`/`cost_entry_details`, account_code="overseas") | 챗봇만 |
| GET/POST | `/overseas/uploads`, POST `/overseas/uploads/file` | 업로드 이력 조회/기록, 엑셀 파일 업로드 | `uploads/file`은 화면, 나머지는 챗봇만 |
| GET/PUT | `/overseas/notes` | 증감분석 비고(사유) 조회/저장 | 챗봇만 |
| POST | `/overseas/reason-translate` | 연구소가 영문으로 보낸 증감사유 → 한국어 AI 번역 (원문·번역 쌍을 `overseas_reason_translations`에 캐시) | 화면+챗봇 |
| GET | `/overseas/reason-translations` | 저장된 원문·번역 쌍 목록(오역 찾기·모델 교체 확인) | 챗봇만 |
| GET/PUT | `/versions/{y}/{m}/{kind}/accounts`, `/accounts/{code}` | 버전×계정의 **계정 확정** 상태·확정 일시·확정 당시 단서(`version_account_status`) | 화면(해외연구소)+챗봇 |
| GET/PUT | `/overseas/carry-defs` | 집계 파생 행·열의 "다음 버전에도 유지" 승인 조회/설정 (`overseas_carry_defs`) | 화면+챗봇 |
| POST | `/overseas/carry-forward` | 유지 승인한 파생 행·열 정의를 새 버전으로 복제 (새 버전을 열 때) | 화면 |
| GET/PUT | `/overseas/anomaly-rules` | 이상 감지 기준(급변·합계 불일치·사유 누락·단위 의심) 조회/변경 (`overseas_anomaly_rules`) | 화면+챗봇 |
| GET | `/overseas/anomaly-rules/history` | 기준 변경 이력(before/after) | 챗봇만 |
| POST | `/overseas/anomaly-scan` | 업로드 값(또는 저장된 값)을 기준에 걸어 검사 — 반영을 막지 않고 목록만 돌려준다 | 화면+챗봇 |
| POST | `/overseas/row-match` | 업로드 파일의 행 이름이 이미 있는 행과 같은 행인지 후보 찾기 (`Travel Expense` → 출장비). 합치지는 않는다 | 화면+챗봇 |
| GET/PUT/DELETE | `/overseas/row-aliases` | 사람이 답한 "같은 행 / 별개 행" 저장·조회·삭제 (`overseas_row_aliases`) | 화면+챗봇 |
| GET/PUT/DELETE | `/overseas/extra-rows` | 사용자 추가 행 조회/저장/삭제 (값은 `cost_entries`에 저장) | 화면+챗봇 |
| GET/DELETE | `/overseas/extra-cols` | 추가 열(라벨) 목록 조회/삭제 | 화면+챗봇 |
| GET/PUT | `/overseas/cost-extras` | 추가 열의 칸 값 조회/저장 | 화면+챗봇 |
| GET/PUT/DELETE | `/overseas/memo-cols` | 메모 전용 열 조회/저장/삭제 | 챗봇만 |
| GET/PUT | `/overseas/memos` | 메모 셀 조회/저장 | 챗봇만 |
| GET | `/overseas/vendor-rows` | 외주용역 상세 조회 (`overseas_vendor_rows`, 저장은 `/overseas/state`) | 챗봇만 |
| GET | `/overseas/vendor-by-project` | 외주용역을 과제(Project)별로 묶어 집계 (증감분석 · 챗봇) | 챗봇만 |
| GET | `/overseas/variance-basis` | 비교 기준 세 칸이 실제로 무엇과 비교하는지 해석 (전실행=직전 버전 · 전년=전년 12월 실적 · 경영계획=선택 차수) + 경영계획 차수 목록 | 화면+챗봇 |
| GET | `/overseas/variance` | 계정별 증감분석 표 (기준·당월·증감·환차 현지→$/$→원·환차外). 챗봇이 "왜 늘었어?"에 답할 때 쓰는 근거 데이터 | 화면+챗봇 |
| GET/PUT | `/overseas/variance-reasons` | 증감사유 한 칸의 전체 상태 조회/저장 (회신 원문·번역·AI 초안·확정 문장 + 저장 시점 증감액) | 화면 |
| POST | `/overseas/variance-reason-draft` | 회신 원문 → 번역 + 보고용 사유 초안 생성 (확정 사유는 건드리지 않음) | 화면 |
| GET | `/overseas/variance-reason-check` | 증감률이 기준치(기본 10%)를 넘었는데 사유가 없거나 재확인이 필요한 항목 — 확정 직전 경고 | 화면+챗봇 |
| GET/PUT/DELETE | `/overseas/variance-drivers` | 증감 동인 분해 표(계산식) 조회/저장/삭제. `carry_over=true`면 다음 버전에도 따라옴 | 화면+챗봇 |
| GET/PUT | `/overseas/actual-stage` | 실적 연월의 1차 상태 — 실적결산 1차 완료 여부(1차 탭 활성)·[1차 고정] | 화면 |
| GET/PUT | `/overseas/actual-first` | 실적 1차 분석(ERP 약식) 연구소 총액·1차 사유 조회/저장. 고정 상태면 409 | 화면 |
| GET | `/overseas/actual-stage-shift` | 2차 화면 상단의 "1차 → 2차 변동" (연구소별 + 합계) | 화면+챗봇 |
| GET | `/overseas/cp-contract` | 그 해 CP 계약(기준정보 › CP 계약) 요약 — POC별 금액 + 분기 하나치(연 ÷ 4). `registered=false`는 "0원"이 아니라 **미등록**이고, 집계 ④ CP 보정의 정산 칸을 빈칸으로 두는 근거다 | 화면+챗봇 |
| GET | `/overseas/confirm-check` | [계정 확정] 직전 점검 — 막는 것(환율 미등록 월)과 알리는 것(미업로드 연구소 · CP 계약 미등록 · 사유 미작성 · 재확인 필요) + 확정 단서 문장("미업로드 2개 포함 확정") | 화면+챗봇 |
| GET | `/overseas/labor-ratio` | 인건비율(인건비 ÷ 개발비(매출)) 전체·연구소별. 분기·연간은 **합계끼리** 나누고, 분모가 0이면 `ratio=null`(화면 '—') | 화면+챗봇 |
| GET | `/overseas/summary` | 화면 맨 위 히어로 카드(개발비(매출) · 인건비/인건비외 · 업로드 진척 · 단계)를 서버에서 같은 식으로 계산 | 챗봇만 (화면은 아직 자체 계산) |
| GET/PUT/DELETE | `/overseas/sheet-rows`, DELETE `/sheet-rows/all` | 열 구성 확정 전인 새 시트(JSON) 조회/저장/삭제 | 챗봇만 |
| GET/PUT | `/fx/state` | 실적·속보·경영 환율(전사 공통 3종) 조회/저장 | 화면 |
| POST | `/fx/applied/recompute` | 지정한 기준(연도·월·속보실적·룰)의 적용 환율을 다시 계산 (`dry_run=true`면 미리보기) | 챗봇만 (`get_fx_rate_preview` · `recompute_applied_fx_rates`) |
| GET | `/fx/applied` | 적용 환율 캐시 조회 | 챗봇만 (`get_applied_fx_rates`) |
| GET/PUT/PUT(`/batch`)/DELETE | `/cost-entries` | 19개 계정이 공유하는 범용 비용 조회/upsert/일괄upsert/삭제 | 화면(해외연구소가 `/overseas/state`와 병행 저장 — 아래 참고) |
| GET/POST/PUT/DELETE | `/versions` | 연월×속보·실적(forecast/actual) 진행 상태(열림·작성중·완료) 조회/생성/변경/삭제 | 화면 |
| GET/POST | `/management-plan/versions`, PUT/DELETE `/versions/{year}/{plan_no}` | 경영계획 버전(P00~) 조회/생성/확정/삭제 | 화면 |
| GET/PUT | `/management-plan/entries` | 경영계획 버전별 금액 조회/저장 | 아무도 안 부름 — 실제 금액은 아직 `/overseas/state`의 `cost_entries(version="plan")` 슬롯을 그대로 씀 |
| GET/PUT/DELETE | `/common-codes` | 공통코드 조회/저장/삭제 | 화면+챗봇 |
| GET | `/errors` | 서버 500 에러 로그 조회 | 챗봇만 |
| GET/POST | `/chat/providers`, `/chat`, `/chat/confirm` | 플로팅 AI 채팅 | 화면(챗봇 UI) |
| GET/POST | `/close/vouchers` | 실적결산 ERP 전표 조회 / 적재(재수신 시 기존 매핑 유지) | 화면 |
| GET | `/close/summary` | 1단계 취합의 계정별 요약(건수·금액·미분류) | 화면 |
| GET/PUT/DELETE | `/close/dept-rules` | 계정 자동 분류 기준정보(부서코드×비용계정 → 계정) 조회/저장/삭제 | 화면 |
| POST | `/close/classify` | 기준정보를 고친 뒤 그 달 전표 재분류 | 화면 |
| GET/PUT | `/close/overseas/mapping` | 2단계 가공 · 해외연구소 작업대(연구소별 표 + 전표별 AI 추천) 조회 / 매핑·제외·이관 저장 | 화면 |
| POST | `/close/overseas/rematch` | AI 다시 매칭 (AI가 정한 것만 — 담당자 수정·규칙은 유지) | 화면 |
| GET/POST/DELETE | `/close/match-rules` | 승인된 AI 매칭 규칙 조회/등록/삭제 | 화면 |
| GET/PUT | `/close/stages`, GET `/close/stages/{key}` | 계정별 결산 상태 조회/매핑 완료 토글 (해외연구소는 "1차 완료") | 화면 |

다른 18개 계정(감가상각비·위탁용역비·생소비 등)은 아직 화면이 "준비 중" 스텁이라 저장할 데이터가 없다.
19개 계정이 공유하는 `/cost-entries`는 엔드포인트도 있고 해외연구소가 실제로 호출도 하지만(아래 참고),
그 18개 계정 전용 화면이 없어서 그 계정들 몫으로는 아직 0건이다.

**`/overseas/state`와 `/cost-entries` 병행 저장**: 해외연구소 화면은 비용을 저장할 때
`PUT /overseas/state`(권위 있는 값)와 `PUT /cost-entries/batch`를 같은 데이터로 둘 다 호출한다
(`core.js`의 `ovCostApiBatch`). `overseas_cost_entries` 테이블이 있던 시절엔 이게 "새 공용 테이블에도
같이 쌓기 시작"하는 전환용 이중 저장이었는데, 지금은 `/overseas/state`가 이미 `cost_entries`/
`cost_entry_details`에 직접 쓰므로 **같은 행을 두 경로로 중복 upsert하는 셈**이다(결과값은 같아서
깨지진 않지만 불필요한 쓰기). 프론트 쪽에서 정리가 필요함.

---

## GET /overseas/state

연구소별 비용 데이터, 환율, 업로드 이력, 증감사유 메모, 현재 단계를 한 번에 반환한다.

**설계 메모**: 셀 하나하나에 대응하는 세분화된 엔드포인트(`/cost-entries`, `/fx-rates` ...)로 안 쪼개고
**상태 전체를 통째로** 주고받는 쪽을 택했다. 프론트(`core.js`)가 이미 `S.ov`/`S.fxm`라는 큰 상태 객체
하나를 메모리에서 통째로 다루는 구조라서, 이 모양 그대로 받는 게 프론트 코드를 거의 안 건드리고 붙일 수 있는
가장 리스크 낮은 방법이었음. `/cost-entries` 등 세분화된 엔드포인트는 그래서 화면 대신 AI 챗봇(MCP)
전용으로 남아 있다 — 사람이 "이 연구소 이 달 값 얼마로 바꿔줘" 하고 대화로 셀 단위를 고칠 때 쓰는 경로.

### Response

```jsonc
{
  "step": 0,                         // 0=취합, 1=집계, 2=증감분석
  "V": {
    "cur": {                         // 당월 속보
      "d": {                         // 연구소pk -> 비용항목 -> [12개월]
        "SARC:CPU": { "sal": [10.5, 11, ...], "bonus": [1,1,...], "B": [...], "hc": [...] }
      },
      "etc": [0,0,0.8,0,0,0,0,0,0,0,0,0]   // 원화 기타 (연구소 무관, 계정 전체 단위)
    },
    "prev": { "d": {...}, "etc": [...] },  // 전실행
    "prior": { "d": {...}, "etc": [...] }, // 전년 실적
    "plan": { "d": {...}, "etc": [...] }   // 경영계획
  },
  "files": {                          // 버전별 연구소별 마지막 업로드 파일
    "cur": { "SARC:CPU": { "file": "OV_SARC_CPU_2609.xlsx", "at": "10.23 14:00" } },
    "prev": {}, "prior": {}, "plan": {}
  },
  "note": {                           // "기준버전|기간|범위" -> 비용항목 -> 사유 텍스트
    "prev|y|ALL": { "sal": "급여 인상으로 인한 증가" }
  },
  "fx": {                             // 버전별 환율
    "cur": { "end":[...], "act":[...], "cp":[...], "ilsE":[...], "ilsA":[...], "inrE":[...], "inrA":[...] },
    "prev": {...}, "prior": {...}, "plan": {...}
  },
  "reason": [                         // 증감사유 시트 원본 (overseas_reason_rows)
    { "lab_code": "SARC", "version": "cur", "category_key": "sal", "block": "y",  // y=연간, m=당월
      "plan_value": 8407.1, "prev_value": 10525.8, "cur_value": 9425,
      "diff_plan": 1017.9, "diff_prev": -1100.8,
      "remark_plan": "- Increase mainly due to ...", "remark_prev": "",
      "prev_label": "M2608A", "cur_label": "M2609A", "source_file": "OV_SARC_2609.xlsx" }
  ],
  "vendor": [                         // 외주용역 상세 (overseas_vendor_rows + 월별 디테일)
    { "lab_code": "SARC", "version": "cur", "row_no": 1,
      "category": "Outsourcing", "project": "GPU Driver", "company": "Vendor SARC-01",
      "description": "Tool flow consulting", "m": [35.7, 19.9, 6.4, "..."],
      "source_file": "OV_SARC_2609.xlsx" }
  ],
  "cp": [ /* CP 계약 (overseas_cp_rows) */ ]
}
```

`reason` · `vendor`의 `lab_code`는 파트가 없는 연구소 코드다 — 두 시트는 SARC도 CPU+GPU를 합쳐
연구소 단위로만 올라온다. `vendor`는 업로드마다 행 수가 달라져서 PUT에서 **연구소×버전 단위로 전체
교체**하고(보내지 않은 연구소는 그대로), `reason`은 cost처럼 보낸 행만 upsert한다.

비용항목 키(`sal`, `bonus`, `B`, `hc` 등)는 고정 목록이 아니라 자유 문자열이다(DB가 long-format이라
새 항목이 생겨도 스키마 변경 없이 행만 늘어남).

### 월 값의 `null` — 미등록과 0은 다르다

`d[pk][항목][m]`과 `etc[m]`은 `number | null`이다. **`null`은 "그 칸이 아직 안 올라왔다"(미등록/미업로드),
`0`은 "0으로 올라왔다"**는 뜻이고 둘은 서로 다르다 (공통 RULE: *미등록/미업로드 칸은 0이 아닌 빈칸으로
표시, 합계는 있는 값만 더해 진행*). 화면은 `null`을 빈칸으로 그리고 `0`은 `0`으로 그린다. 한 번이라도
0으로 뭉개면 그 뒤로는 어디서도 되살릴 수 없어서, 계산 직전까지 `null`을 그대로 들고 다닌다.

- **조회**: 디테일 행이 없는 달은 `null`. 행은 있지만 한 달도 안 올라온 항목은 `[null × 12]`.
- **저장**: `null`인 달은 저장하지 않는다(미등록으로 남음). 이미 값이 있던 달을 `null`로 보내도 지워지지
  않는다 — PUT은 "보낸 것만 반영"이라서다. 값을 0으로 만들려면 `0`을 보내라.
- **합계**: 있는 값만 더한다(없는 달은 더할 게 없으니 결과가 같다). `/overseas/summary`의 `uploaded`는
  "값이 0이 아닌 칸이 있나"가 아니라 "등록된 칸이 하나라도 있나"로 센다 — 전부 0으로 올라온 연구소도
  업로드된 것이다.

---

## PUT /overseas/state

위와 같은 모양의 JSON을 받아서 DB에 반영(upsert)한다. `lab_id`로 못 찾는 연구소 코드가 섞여 있으면
`400`으로 거부한다.

**기준정보 변경 시점**: 연구소 기준정보(`/overseas/labs`)는 *변경한 버전부터* 적용되고 지난 버전은
영향받지 않는다 (공통 RULE). PUT에 `effective_from`(`"YYYY-MM"`)을 주면 그 달부터, 안 주면 지금 편성
중인 기준월부터 적용된다. 변경 이력은 `overseas_lab_revisions`에 남고, `/overseas/summary`·
`/overseas/labor-split`은 **그 버전이 보는 시점**의 기준정보로 계산한다 — 통화를 USD→ILS로 바꿔도 이미
마감한 달의 환차 분해가 소급해서 달라지지 않는다. 그때 뭐였는지는 `GET /overseas/labs?as_of=2026-08`로
확인한다. (예외: `DELETE /overseas/labs/one`은 이력이 아니라 진짜 삭제라 과거 버전에서도 사라진다 —
"이번 달부터 안 쓴다"는 뜻이면 삭제 대신 `is_active=false`로 저장해야 한다.)

**저장 시점(체크포인트)**: 프론트에서 매 입력마다 호출하지 않는다. 아래 두 시점에서만 호출됨:
1. 단계 전환("다음" 버튼/단계 탭 클릭)
2. 증감분석 화면 사유 입력란에서 포커스가 빠질 때(blur)

그 사이 입력값은 `localStorage`에만 있다가(작업 중 안전망), 체크포인트에 도달하면 한 번에 반영된다.

---

## GET/PUT /fx/state

전사 공통 환율 3종(실적·속보·경영, 전부 `fx_rates` 테이블에 `kind` 컬럼으로 구분해 저장)을 한 번에
조회/저장한다. `/overseas/state`와 똑같은 이유로 통째로 주고받는 모양을 택했다.

**저장 부수효과**: `PUT /fx/state`가 성공하면 끝에서 `fx_applied_rates`에 이미 캐시돼 있던 모든
(기준연도·월·속보실적·룰) 조합을 자동으로 다시 계산해 덮어쓴다(`_refresh_all_applied_rates`). 원본
3개 테이블 중 뭐가 바뀌든 이 한 엔드포인트를 통해 들어오므로, 캐시가 상한 채로 남는 일은 없다. 다만
**아직 한 번도 계산된 적 없는 새 기준**은 이걸로 안 생긴다 — `POST /fx/applied/recompute`를 최소 한 번
호출해서 만들어둬야 한다(어떤 기준을 "기본값"으로 미리 만들어둘지는 서버가 임의로 정할 수 없어서).

---

## GET /overseas/step / PUT /overseas/step

```json
{ "step": 0 }
```

```json
// Request
{ "step": 1 }
// Response (성공)
{ "step": 1 }
// Response (존재하지 않는 단계, 400)
{ "detail": "해외연구소에 정의되지 않은 단계입니다: 99" }
```

`account_steps` 테이블에 실제로 정의된 단계(해외연구소는 0~2)만 허용된다 — DB의 복합 외래키로 강제됨.

---

## cost_entries / cost_entry_details — 19개 계정 공유 비용 테이블

`cost_entries`는 "계정×행×비용항목×버전의 편성 회차" 헤더(어느 연월 기준으로 편성했는지, version은
forecast(속보)|actual(실적))만 들고, 실제 금액은 `cost_entry_details`가 `cost_entry_id`를 FK로
참조하는 행으로 가진다(`period_type`이 MONTH|QUARTER|YEAR|CUSTOM 중 뭔지로 월·분기·연간·커스텀열을
구분). `/cost-entries` 엔드포인트가 이 두 테이블을 바로 다룬다.

해외연구소는 `account_code="overseas"`로 이 테이블을 쓴다 — `/overseas/state`가 저장할 때 직접
upsert하고, 화면도 `PUT /cost-entries/batch`로 같은 데이터를 병행 저장한다(위 "병행 저장" 참고).
다른 18개 계정은 화면이 아직 "준비 중" 스텁이라 이 테이블에 해당 계정 몫 데이터가 없다.

---

## POST /overseas/reason-translate

연구소가 영문으로 써 보낸 증감사유를 보고용 한국어로 옮긴다. 번역은 챗봇과 같은 공급자
(`settings.chat_provider` — anthropic | gemini | ollama)를 쓰고, 프롬프트와 용어표는
`app/core/reason_translate.py`에 있다. 그 프롬프트는 원래 프런트 목업(`core.js`의 `OV_KO` 사전 +
`ovTrRule` 정규식)이 하던 번역 모양을 규칙으로 옮긴 것이다 — 보고용 명사형 간결체, 금액은 원문
표기 유지(`$449K`), 여러 사유는 `① ② ③`, Increase/Decrease 방향 보존.

```
POST /overseas/reason-translate
{ "texts": ["higher basic salary from new hires", ...],  // 최대 40건
  "provider": null,      // 생략 시 settings.chat_provider
  "refresh": false }     // true면 저장된 번역을 무시하고 다시 번역해 덮어쓴다
→ { "items": [{ "source": "...", "ko": "신규 채용에 따른 기본급 증가", "engine": "gemini:...", "cached": false }],
    "provider": "gemini", "translated": 1, "cached": 0, "failed": 0, "error": "" }
```

- **같은 원문은 한 번만 번역한다**: 정규화(연속 공백 1칸)한 원문의 SHA-256을 키로
  `overseas_reason_translations`에 원문·번역을 같이 저장하고, 다음 호출부터는 그 값을 돌려준다
  (`cached: true`). 화면 한 장에 사유가 수십 건이라 매번 부르면 느리고 비싼 것도 있지만, 더 중요한
  건 같은 회신 문장이 매달 같은 한국어로 보여야 보고 문구가 흔들리지 않는다는 점이다. 원문을 지우지
  않고 같이 남기는 건 공통 RULE(원문 보관) — 번역이 이상할 때 되짚을 근거가 된다.
- **실패해도 200**: 키가 없거나 공급자 연결이 막히면 `ko: null` + `error`에 이유를 담아 돌려준다.
  증감분석 화면은 번역이 없어도 원문으로 그려져야 하는데 여기서 502를 던지면 화면 쪽에서 "사유가
  아예 없는 것"과 구분하기 어렵다. 화면(`core.js`의 `ovTrRule`)은 그때 목업 사전으로 폴백한다.
- 이미 한국어인 사유는 모델이 그대로 돌려준다(프롬프트 규칙 7) — 넣어도 깨지지 않지만 호출 낭비다.

---

## POST /overseas/row-match · /overseas/row-aliases

연구소마다 같은 비용을 다른 이름으로 적어 보낸다 — 고정 계정 출장비의 영문명은 `Travel Exp`인데
`Travel Expense`로 적는 연구소가 있다. 이름이 (공백·기호를 뺀) 글자까지 같아야 같은 행으로 보던
탓에, 한 글자만 달라도 "계정 목록에 없는 행"으로 따로 들어가 집계표에 출장비가 두 줄로 쪼개졌다.

```
POST /overseas/row-match
{ "labels": ["Travel Expense", "Cloud Usage Fee"], "yyyymm": "202609", "version": "forecast" }
→ { "items": [
     { "label": "Travel Expense",
       "resolved": null,                                  // 전에 답해 둔 게 없음 → 물어봐야 한다
       "suggestions": [{ "kind": "category", "key": "travel", "label": "출장비",
                         "label_en": "Travel Exp", "matched_on": "Travel Exp",
                         "score": 0.95, "labs": [] }] },
     { "label": "Cloud Usage Fee", "resolved": null, "suggestions": [] } ] }   // 비슷한 게 없음 = 새 행
```

- **자동으로 합치지 않는다.** 후보만 돌려주고 합칠지는 사람이 답한다 — `Travel Exp`/`Test Exp`처럼
  꼬리말이 같거나 `Cloud Usage`/`Cloud Service`처럼 앞이 같은, 정말 다른 행이 섞인다. 점수(`score`)는
  후보를 보여줄지 가르고 정렬하는 데만 쓴다. 판정 규칙은 `app/core/row_match.py`에 있다(꼬리말
  `Exp`·`Expense`·`비용` 제거 후 비교, 한쪽이 줄여 쓴 이름인지, 오타 한두 글자인지).
- **후보는 두 종류**다: 고정 비용항목(`kind:"category"` — 집계로 계산되는 그룹 행은 제외한다. 거기엔
  업로드 값을 꽂을 수 없다)과 그 달에 이미 쓰이는 추가 행(`kind:"extra_row"`, 그 행을 쓰는 연구소를
  `labs`에 같이 준다 — "SARC·DSRA가 쓰는 행"이라는 걸 알면 판단하기 쉽다).
- **답은 저장해서 다시 묻지 않는다**: `PUT /overseas/row-aliases`가 `overseas_row_aliases`에 남기고,
  다음 호출부터 그 이름은 `resolved`로 내려간다. "별개 행"이라는 답도 `target_kind:"distinct"`로
  저장한다 — 안 남기면 매달 12개 연구소에 같은 질문을 다시 하게 된다. 잘못 답했으면 다시 PUT해서
  덮어쓰거나 DELETE로 지운다(이미 그 답으로 들어간 과거 데이터는 바뀌지 않는다 — 다시 업로드해야 한다).

화면(`core.js`)은 파일을 읽은 직후 이 API를 불러서, 반영 대기 카드에 `‘Travel Expense’은 이미 있는
행과 같은 행인가요? [같은 행: 출장비 (Travel Exp)] [새 행으로]`를 띄운다. [반영]을 누를 때
고정 계정으로 합친 행은 업로드 값에 미리 더해 버전 반영 규칙(과거월 변경 보호)을 똑같이 받게 하고,
추가 행으로 합친 행은 이름을 정식 이름으로 바꿔 넣는다(집계표는 행 이름으로 묶기 때문에 이름만
맞추면 한 줄로 합쳐진다). 고른 답은 그때 서버에 저장한다 — 반영을 취소하면 답도 남지 않는다.

---

## 계정 확정 (`/versions/{y}/{m}/{kind}/accounts`)

버전 전체의 확정(`/versions` PUT)과는 다른 층이다 — 계정 담당자가 각자 자기 계정을 먼저 확정하고,
19개가 전부 완료되면 버전을 확정한다. 누가 언제 확정했는지는 여러 사람이 같이 보는 값이라
브라우저(localStorage)에 둘 수 없어서 `version_account_status`에 둔다. 행이 없으면 "작성 중"이다.

```
PUT /versions/2026/9/forecast/accounts/overseas
{ "status": "done", "note": "미업로드 2개 포함 확정" }
→ { "account_code": "overseas", "status": "done",
    "confirmed_at": "2026-11-15T05:03:11Z", "note": "미업로드 2개 포함 확정" }
```

- `status: "drafting"`으로 되돌리면 **확정 일시와 note를 같이 지운다** — 되돌렸는데 "미업로드 2개
  포함 확정"이 남아 있으면 그게 지금 상태인 줄 읽힌다(공통 RULE: 완료 후 수정 시 작성중 + 일자 삭제).
- `note`는 "무엇을 알고도 확정했는지"를 남기는 칸이다. 해외연구소 화면은 확정 전 검증 결과를 여기
  넣는다 — 미업로드 연구소가 있으면 `미업로드 2개 포함 확정`.
- 해외연구소 화면의 확정 검증(`core.js`의 `ovConfirmCheck`)은 **막는 것과 알리는 것**을 나눈다:
  적용 환율(원/$)이나 현지통화 환율이 없는 달이 있으면 **확정을 막고**(환산이 안 된 총액이 버전에
  박힌다), 미업로드·증감사유 없음은 경고만 하고 확정은 할 수 있게 한다(지금까지 온 것만으로 보고하는
  게 실제 업무다). 확정 후 값이 바뀌면 화면이 자동으로 "작성 중"으로 되돌린다(`ovUnconfirm`).
  이상 감지 미확인 경고는 이상 감지 기능이 생기면 여기에 붙인다.
- 지금 이 API를 쓰는 건 해외연구소뿐이다(나머지 18개 계정 화면은 아직 예시 상태라 로컬에만 둔다).
  엔드포인트는 계정 구분 없이 받으므로 화면이 붙는 대로 그대로 쓰면 된다.

---

## 집계 화면이 서버에 묻는 것 (`/overseas/cp-contract` · `/overseas/confirm-check` · `/overseas/labor-ratio`)

집계표 숫자는 브라우저가 계산하지만, 아래 셋은 서버가 판정해야 한다 — 여러 사람이 같이 보는 값이거나
(CP 계약·확정 상태), 화면과 챗봇이 다른 숫자를 말하면 안 되는 값(인건비율)이다.

- **CP 계약** — CP 분기 정산은 업로드된 CP PL이 아니라 기준정보 › CP 계약 금액으로 한다(연 계약액
  ÷ 4를 3 · 6 · 9 · 12월에). 그래서 집계는 "계약이 등록됐는지"를 알아야 한다. 미등록일 때
  `registered=false`로 **200**을 주는 게 중요하다 — 404로 주면 화면이 "서버 오류"와 구분하지 못해
  업로드된 CP PL로 그려 버리고, 그러면 계약 없이 숫자가 선다. 미등록이면 그 칸은 0이 아니라 빈칸이고
  합계에서도 빠진다(공통 RULE: CP 계약 미등록이면 CP 정산 칸 빈칸 + 알림).
- **확정 사전점검** — `blocking`(막는 것)과 `warnings`(알리는 것)를 나눠 준다. 지금 막는 건 적용
  환율(원/$)이 없는 달뿐이다(환산이 안 된 총액이 버전에 박힌다). 미업로드 연구소 · CP 계약 미등록 ·
  사유 미작성 · 재확인 필요는 경고만 하고 확정은 할 수 있게 한다. 같이 내려주는 `note`
  ("미업로드 2개 포함 확정")를 그대로 `PUT /versions/.../accounts/overseas`의 note에 넣으면 버전
  화면에 단서가 남는다. 사유 판정은 `/overseas/variance-reason-check`와 **같은 함수**를 쓴다 —
  두 군데서 따로 세면 화면과 경고가 어긋난다.
- **인건비율** — 인건비 ÷ 개발비(매출). 분기 · 연간은 월별 비율을 평균 내지 않고 **기간 합계끼리**
  나눈다(달마다 금액이 다르면 월별 비율의 평균은 실제 비율과 다르다). 분모가 0이거나 아직 안 올라온
  줄은 `ratio=null`이다 — 0%가 아니다. 기준은 `/overseas/summary`와 같다(원화 · CP 계약 분기 정산).

---

## 집계 파생 행·열의 "다음 버전에도 유지" (`/overseas/carry-defs`, `/overseas/carry-forward`)

집계 ① 탭에서 만드는 파생 행·열(인당 인건비, 2Q 합계 열 등)은 버전×기준연월 단위로 저장돼서
새 버전을 열면 사라진다. 매달 보는 지표는 따라가야 하고 그 달만 보려고 만든 건 따라가면 안 되므로,
**만든 직후 한 번 묻고** 승인한 것만 `overseas_carry_defs`에 남긴다. 새 버전을 열 때(`core.js`의
`ovVerCopy`) `POST /overseas/carry-forward`가 그 정의만 새 버전으로 복제한다.

- **값이 아니라 계산식만** 복제한다 — 재료(업로드 값)가 새 버전 것으로 바뀌면 결과도 그 버전 숫자로
  다시 나와야 한다.
- 복제 대상에 **같은 행·열이 이미 있으면 건너뛴다**(`skipped`에 이름이 담긴다). 덮어쓰면 새 버전에서
  사람이 고친 계산식이 지난 버전 것으로 되돌아간다. 그래서 두 번 호출해도 결과가 같다.
- 유지 설정은 집계 ① 탭의 행·열 목록에서 [다음 버전에도] 체크로 언제든 켜고 끌 수 있다.

---

## 이상 감지 (`/overseas/anomaly-rules`, `/overseas/anomaly-scan`)

업로드 값이 이상해 보이면 **반영 전에** 알린다. 막지는 않는다 — 연구소가 확정해서 보낸 값을 본사가
"이상해 보인다"는 이유로 거부할 수는 없고, 대신 담당자가 연구소에 확인하거나 그대로 가져갈지
고르게 한다. 판정 규칙은 `app/core/anomaly.py`, 기준값은 DB에 있다.

| 기준 | 기본값 | 파라미터 |
|---|---|---|
| `jump` (급변) | 전실행 대비 ±10% **그리고** 변동액 $10K 이상 | `pct`, `amount` |
| `sum_mismatch` (합계 불일치) | 파일의 소계·합계 행 ≠ 하위 합, 오차 $0.5K 초과 | `amount`(허용 오차) |
| `missing_reason` (사유 누락) | 급변으로 걸린 항목에 증감사유가 비어 있음 | (켜고/끄기만) |
| `unit_suspect` (단위 의심) | K$ 칸에 1,000,000 이상, 또는 전 버전 대비 1000배·1/1000 | `amount`, `ratio` |

- **급변은 비율과 금액을 둘 다 넘어야 걸린다.** 비율만 보면 잡비 $1K → $2K(+100%)까지 걸려서
  연구소 12개면 경고가 수십 개 뜨고, 그러면 아무도 목록을 안 읽는다.
- **기준은 챗봇으로 바꾸고 저장된다**: `"이제 50% 넘는것만 알려줘"` → `set_anomaly_rule(jump, pct=50)`
  → 승인 팝업 → `PUT /overseas/anomaly-rules`. 해외연구소 전체 공통이라 창을 새로 열어도·다른
  담당자가 봐도 같은 기준이 적용된다. **보낸 항목만 바뀐다**(비율만 보내면 금액 하한은 그대로) —
  사용자가 말하지 않은 값이 덮이면 경고 범위가 엉뚱하게 달라진다. `reset=true`는 기본값 복구.
  변경은 `overseas_anomaly_rule_logs`에 before/after로 남는다(기준이 바뀌면 경고 수가 달라지므로).
- **`"이번만 50%로 봐줘"`는 저장하지 않는다** — `anomaly-scan`의 `overrides`로 그 조회에만 적용한다.
- **`vals`를 주면 그 값으로, 안 주면 저장된 값으로** 검사한다. 앞은 반영 대기 중인 파일(화면이
  업로드 직후 호출), 뒤는 [계정 확정] 전에 "아직 남아 있는 이상"을 세는 경로다.
- **검사하지 못한 기준은 `skipped`로 돌려준다.** 비교할 전실행이 없으면 급변을 낼 수 없는데, 그걸
  조용히 빼면 `findings: []`가 "이상 없음"으로 읽힌다.
- 화면은 대기 카드에 기준별로 묶어 보여주고(그 기준의 설명 문장 `desc`를 같이 띄운다), 걸린 칸을
  숫자 미리보기 표에서 빨갛게 칠한다. [계정 확정] 때는 남은 건수를 경고로 올린다.

---

## 아직 없는 것

- 인증/권한 (누가 호출해도 다 허용됨 — 로컬 프로토타입 단계라 보류)
- `/accounts` ↔ 대시보드 화면 연결 (엔드포인트는 생겼지만 화면은 아직 이 값을 읽지 않는다 —
  그래서 카드에서 바꾼 상태·비고가 서버에 남지 않는다)
- 다른 18개 계정용 화면 (엔드포인트·테이블은 이미 공유돼 있어서, 화면만 붙이면 됨)
- `/management-plan/entries` 실사용 연결 (버전 목록만 동기화, 금액은 여전히 `overseas/state` 경유)
- `/overseas/state` ↔ `/cost-entries/batch` 중복 저장 정리 (위 "병행 저장" 참고)

## 증감분석 화면 (`/overseas/variance*` · `/overseas/actual-*`)

이 엔드포인트들은 `app/api/v1/endpoints/overseas_variance.py`에 따로 모여 있다 — `overseas.py`가
다루는 건 "취합·집계"(업로드·기준정보·행/열)이고 여기 있는 건 전부 "증감분석 한 화면"의 일이라서다.
경로 공간은 같은 `/overseas/...`를 쓰되 파일만 갈랐다.

**비교 기준을 화면이 추측하면 안 된다.** "전실행"은 직전 버전이라 가리키는 대상이 달마다 바뀐다 —
1월 속보의 전실행은 전년 12월 실적이고(연도를 넘어간다), 실적 버전의 전실행은 같은 달 속보다.
브라우저가 "기준월 - 1"로 계산하면 두 경우 다 틀린 라벨을 보여준다. 전년(= 전년 12월 실적)이 없을
때 최근 실적·속보로 물러나는 것도 서버가 판단하고, 무엇으로 대체했는지를 `fallback_note`에 문장으로
담아 보낸다 — 조용히 대체하면 담당자는 전년 실적과 비교한 줄 알고 보고한다.

**환차는 USD 외 통화를 쓰는 연구소 전부를 현지→$ / $→원으로 가른다.** 예전에는 이스라엘·인도만
그렇게 나누고 나머지는 USD처럼 계산해서, 영국·중국·일본 연구소의 증감이 "$→원 환차 + 환차外"로
뭉개졌다(현지통화가 움직인 몫이 환차外로 샜다). 지금은 `overseas_labs.currency`가 USD가 아니면
전부 두 단계로 나누고, USD 연구소는 현지→$ 단계가 없어 그 칸이 0이다. 통화가 섞인 전체 합계에서도
`diff = fx_local + fx_usd + fx_outside`가 행마다 성립한다(`tests/test_overseas_variance.py`가 검증).
경영계획 대비만 예외다 — 속보/실적 환율 기준이 없어 환차를 가를 근거가 없으므로 환차 칸을 0으로
두고 증감 전부를 환차外로 넣고, 그 사실을 `note`에 적어 보낸다(0을 "환차 없음"으로 읽지 않도록).

**증감사유는 텍스트 한 줄이 아니라 과정이다.** 연구소가 영문으로 써 보낸 회신(`source_text`),
그 번역(`source_ko`), 증감 금액을 붙여 만든 보고용 초안(`ai_draft`), 담당자가 고쳐서 [저장]한
문장(`reason_text`)이 모두 남는다 — "이 숫자를 왜 이렇게 설명했나"를 되짚을 수 있어야 하고,
원문은 지우지 않는다(공통 RULE). 공유 `notes` 테이블로는 안 되는 이유가 하나 더 있다: 거기엔
기준연월이 없어서 9월 속보에 쓴 사유가 10월 속보에도 그대로 붙는다.

`saved_diff`가 재업로드 대응의 핵심이다. [저장] 시점의 증감액을 같이 박아 두고, 연구소가 파일을
다시 올려 금액이 바뀌면 **사유를 지우지 않고** `needs_recheck`만 켜서 화면에 "재확인 필요"로 띄운다
(사양: 사유 유지 + 재확인 필요). 담당자가 쓴 문장을 서버가 버리면 매달 처음부터 다시 쓰게 된다.
환율 소수점 때문에 몇 원씩 흔들리는 건 변동으로 보지 않는다(`RECHECK_TOLERANCE_KRW`).

**증감 동인 표는 숫자가 아니라 계산식을 저장한다.** "인건비를 인력수 × 인당 단가로"처럼 말로
들어온 요청을 챗봇이 `upsert_variance_driver`로 옮기고, 조회할 때마다 계산식을 다시 평가한다 —
AI가 그때 계산한 숫자를 박아 두면 원본이 업로드로 바뀐 뒤에도 옛 값이 남아 틀린 표가 된다.
`carry_over`가 "다음 버전에도?"의 답이다: 처음 붙일 때는 `false`로 두고 담당자가 승인하면 켠다.
조회는 "이 버전에 붙인 표 + 이전 버전에서 carry_over로 켜 둔 표"의 합집합이고, 같은 키가 양쪽에
있으면 이 버전 것을 쓴다(이번에 고친 표가 지난달 설정에 덮이면 안 된다).

**실적 1차와 2차는 저장소부터 다르다.** 1차(ERP 약식)는 결산 초기에 뽑은 연구소 총액뿐이라
계정 상세도 외주용역도 없고, 2차(PL)는 속보와 같은 구성이다. 1차 값을 `cost_entries`에 넣으면
같은 (연구소·연월·실적) 자리를 2차가 덮어써서 이미 보고한 1차 값이 사라지므로,
`overseas_actual_first_rows`에 따로 담는다(1차 사유도 2차 사유와 별도). 금액은 현지통화로 받고
화면·서버가 "현지 → $ → 원화"로 환산하는데, 비용 데이터가 전부 K$라 환산 결과도 K$로 맞춘다
(1000으로 나누지 않으면 1차 금액만 1000배로 보인다). ERP가 $ 환산액을 같이 준 경우엔 그 값을
이기고, 그때는 현지→$ 단계를 ERP가 이미 밟았으므로 그 환차를 0으로 둔다.

`overseas_actual_stages`의 두 값은 서로 다른 일을 한다: `close_first_done`은 실적결산 1차가
끝났는지(끝나지 않았으면 가져올 ERP 값이 없어 화면 1차 탭을 비활성으로 둔다), `first_locked`는
[1차 고정](보고한 값·사유 수정 금지, 해제해야 쓰기 가능 — 거부는 409)이다. **1차 고정은
`versions.status`를 건드리지 않는다** — 버전 확정과 1차 고정은 다른 층의 결정이고, 1차를 고정해도
2차 작업이 남아 있어 버전은 작성중 그대로여야 한다.

---

## 실적결산 · 전표매핑 (`/close/*`) — 해외연구소 2단계 가공

편성(`/overseas/*`)과 경로 공간을 가른 이유: 다루는 자료가 **ERP 전표**이고, 19개 계정이 같이 쓸
자리다. 해외연구소 전용 경로 아래에 두면 다음 계정을 붙일 때 경로를 옮겨야 한다.

**전표와 매핑 판단을 다른 테이블에 둔다**(`close_vouchers` / `close_voucher_maps`). ERP가 같은 달
전표를 재전송하면 전표는 덮어쓰지만 담당자가 손으로 고친 매핑은 살아남아야 하기 때문이다(사양:
전표 재수신 시 기존 매핑 유지, 신규만 매칭). 그래서 매핑의 키가 전표 id가 아니라 (연월·전표번호·
라인)이다 — 전표를 지우고 다시 넣어도 매핑이 그대로 붙는다. `replace=true`로 적재하면 이번에 안 온
전표를 지우지만(전기 취소분) 그 매핑은 남겨서, 같은 전표가 돌아오면 또 살아난다.

**계정 자동 분류는 (부서코드 × 비용계정)으로 한다**(`close_dept_rules`). 비용계정만으로는 안
갈라진다 — 같은 ERP 계정코드가 국내 부서에서 나오면 그 계정 몫이고 해외연구소 부서에서 나오면
해외연구소 몫인 비용이 있다. `cost_center`가 빈 문자열인 행은 "그 비용계정 전체"를 뜻하고, 둘 다
채운 행이 먼저 이긴다(구체적인 기준이 이겨야 예외를 한 줄로 적을 수 있다). 기준정보에 없는 조합은
**오류로 막지 않고 미분류로 남긴다** — 막으면 담당자가 기준정보를 고칠 기회 없이 적재가 실패한다.

**해외연구소의 매핑 단위는 연구소(파트까지)다.** 다른 계정은 전표의 코스트센터가 곧 편성 단위지만
해외연구소 비용은 **과제코드 단위**로 떨어져서, 전표의 부서로는 연구소를 가를 수 없다. 그래서
`scope_key`가 `overseas_labs.pk`("SARC:CPU")이고, AI는 과제코드·거래처·코스트센터·적요 네 단서로
추정한다(`app/core/voucher_match.py`). 신뢰도는 점수의 합이 아니라 **몇 개의 독립적 단서가 같은
곳을 가리키나**로 가른다 — 적요에 연구소명이 있으면 낱말 겹침은 더하지 않는다(둘은 같은 근거라서,
같이 세면 단서 하나짜리 전표가 "두 단서 일치"로 올라가 자동 확정된다). 2위도 쓸 만한 근거를 갖고
있으면(1위의 절반 이상) 단서가 서로 다른 연구소를 가리키는 것이라 **사람에게 넘긴다**.

**제외는 업무 규칙이라 단정한다.** NRE 공용 부서코드(`close_dept_rules.is_shared`)는 여러 연구소가
같이 쓰는 코드라 전표의 부서로 가를 수 없고, 적요의 'CP' 표시만이 유일한 단서다 — 있으면 CP(미청구
NRE) 연구소 몫, 없으면 해외연구소 실적이 아니다(국내가 부담한 공용 비용). 제외한 전표도 **지우지
않는다**: 제외분 합계를 보여줘야 "연구소 합계 + 제외 + 이관 = 전표 총액"이 맞는지 확인할 수 있다.
'CP'는 낱말 경계로 본다 — 'CPU'·'CAPEX'에 걸리면 SARC:CPU 전표가 전부 CP로 간다.

**이관**은 "이 전표는 다른 계정 것"이라는 판단이다. 보낸 쪽에 `transferred_to`, 받은 쪽에
`transferred_from`을 남겨 양쪽 담당자가 같은 전표를 두 번 세지 않게 한다. 이관 보낸 금액은 이 계정
집계에서 빠진다.

**AI 규칙은 승인한 것만 등록한다**(`close_match_rules`). 같은 수정을 3번 하면 "앞으로 이렇게
할까요?"를 내려주고(`/close/overseas/mapping`의 `suggestions`, 저장 응답에도 같이 실린다), 승인하면
`decided_by='rule'`로 자동 적용된다. 서버가 스스로 규칙을 만들지 않는 이유: 한 달만 예외로 옮긴
전표도 세 번 고치면 반복처럼 보이고, 규칙이 되면 다음 달에 조용히 틀린 곳으로 붙어서 아무도
모른다. 같은 단서가 여러 곳으로 간 이력이 있으면 후보로 내지 않는다(규칙이 되면 절반이 틀린다).
규칙을 지워도 이미 그 규칙으로 붙은 매핑은 그대로 둔다(기준정보 변경은 변경한 버전부터).

**금액은 매핑으로 바뀌지 않는다.** [저장]하는 건 "어디에 붙일지"고, `krw_amount`는 ERP가 준 값
그대로다 — 엑셀 다운로드도 원래 금액을 내려야 ERP와 맞춰볼 수 있다.

**해외연구소의 완료는 '1차 완료'까지다.** `PUT /close/stages`로 매핑 완료를 켜면 해외연구소는
`display_status`가 "1차 완료"가 되고 `overseas_actual_stages.close_first_done`이 같이 켜져서
증감분석 화면의 [1차 분석] 탭이 열린다. **계정 완료(`account_done`)는 건드리지 않는다** — 전표
매핑은 ERP 약식 1차일 뿐이고, 계정 완료는 PL이 나온 뒤 실적 시뮬레이션 화면의 [확정]
(`version_account_status`)으로만 된다. 1차 없이도 2차를 진행할 수 있다. [1차 고정] 상태에서 1차
완료를 되돌리려 하면 409로 막는다(보고한 1차 값의 근거가 사라진다).

다른 계정은 매핑 완료가 곧 계정 확정이지만, 그 토글은 아직 이 엔드포인트가 하지 않는다 — 전표매핑
계정 화면이 붙을 때 같이 넣어야 중간 상태가 안 생긴다.
