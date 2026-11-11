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
| GET/PUT | `/overseas/labs` | 연구소 기준정보 조회/저장 | 화면 |
| GET | `/overseas/lab-sheets` | 연구소별 워크북 시트 구성 조회 | 챗봇만 |
| GET/PUT | `/overseas/cost-entries` | 연구소 비용 데이터 셀 단위 조회/upsert (`cost_entries`/`cost_entry_details`, account_code="overseas") | 챗봇만 |
| GET/POST | `/overseas/uploads`, POST `/overseas/uploads/file` | 업로드 이력 조회/기록, 엑셀 파일 업로드 | `uploads/file`은 화면, 나머지는 챗봇만 |
| GET/PUT | `/overseas/notes` | 증감분석 비고(사유) 조회/저장 | 챗봇만 |
| GET/PUT/DELETE | `/overseas/extra-rows` | 사용자 추가 행 조회/저장/삭제 (값은 `cost_entries`에 저장) | 화면+챗봇 |
| GET/DELETE | `/overseas/extra-cols` | 추가 열(라벨) 목록 조회/삭제 | 화면+챗봇 |
| GET/PUT | `/overseas/cost-extras` | 추가 열의 칸 값 조회/저장 | 화면+챗봇 |
| GET/PUT/DELETE | `/overseas/memo-cols` | 메모 전용 열 조회/저장/삭제 | 챗봇만 |
| GET/PUT | `/overseas/memos` | 메모 셀 조회/저장 | 챗봇만 |
| GET | `/overseas/vendor-rows` | 외주용역 상세 조회 (`overseas_vendor_rows`, 저장은 `/overseas/state`) | 챗봇만 |
| GET | `/overseas/vendor-by-project` | 외주용역을 과제(Project)별로 묶어 집계 (증감분석 · 챗봇) | 챗봇만 |
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

---

## PUT /overseas/state

위와 같은 모양의 JSON을 받아서 DB에 반영(upsert)한다. `lab_id`로 못 찾는 연구소 코드가 섞여 있으면
`400`으로 거부한다.

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

## 아직 없는 것

- 인증/권한 (누가 호출해도 다 허용됨 — 로컬 프로토타입 단계라 보류)
- `/accounts` ↔ 대시보드 화면 연결 (엔드포인트는 생겼지만 화면은 아직 이 값을 읽지 않는다 —
  그래서 카드에서 바꾼 상태·비고가 서버에 남지 않는다)
- 다른 18개 계정용 화면 (엔드포인트·테이블은 이미 공유돼 있어서, 화면만 붙이면 됨)
- `/management-plan/entries` 실사용 연결 (버전 목록만 동기화, 금액은 여전히 `overseas/state` 경유)
- `/overseas/state` ↔ `/cost-entries/batch` 중복 저장 정리 (위 "병행 저장" 참고)
