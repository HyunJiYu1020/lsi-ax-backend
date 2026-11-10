# API 엔드포인트 정리

현재 구현된 엔드포인트 전체 목록. Swagger UI(`http://localhost:8000/docs`)에서 직접 호출해볼 수도 있지만,
여기엔 "왜 이렇게 설계했는지"까지 같이 남겨둔다. 어디서 실제로 호출하는지(화면 버튼 vs AI 챗봇 전용)는
[CONNECTIONS.md](./CONNECTIONS.md) 2~4절 참고.

Base URL: `http://localhost:8000/api/v1`

## 목록

| Method | Path | 설명 | 호출처 |
|---|---|---|---|
| GET | `/health` | 서버 동작 확인용 (실제 로직 없음) | — |
| GET/PUT | `/overseas/state` | 해외연구소 전체 데이터 조회/저장 | 화면 |
| GET/PUT | `/overseas/step` | 해외연구소 진행 단계 조회/변경 | 챗봇만 |
| GET/PUT | `/overseas/labs` | 연구소 기준정보 조회/저장 | 화면 |
| GET | `/overseas/lab-sheets` | 연구소별 워크북 시트 구성 조회 | 챗봇만 |
| GET/PUT | `/overseas/cost-entries` | 연구소 비용 데이터 셀 단위 조회/upsert (실제로는 `overseas_cost_lines` 테이블) | 챗봇만 |
| GET/POST | `/overseas/uploads`, POST `/overseas/uploads/file` | 업로드 이력 조회/기록, 엑셀 파일 업로드 | `uploads/file`은 화면, 나머지는 챗봇만 |
| GET/PUT | `/overseas/notes` | 증감분석 비고(사유) 조회/저장 | 챗봇만 |
| GET/PUT | `/overseas/fx-rates` | 해외연구소 전용 환율(`fx_rates`) 조회/저장 | 챗봇만 |
| GET/PUT/DELETE | `/overseas/custom-rows` | 사용자 추가 행 조회/저장/삭제 | 챗봇만 |
| GET/PUT/DELETE | `/overseas/memo-cols` | 메모 전용 열 조회/저장/삭제 | 챗봇만 |
| GET/PUT | `/overseas/memos` | 메모 셀 조회/저장 | 챗봇만 |
| GET/PUT/DELETE | `/overseas/sheet-rows`, DELETE `/sheet-rows/all` | 정형화 안 된 시트(JSON) 조회/저장/삭제 | 챗봇만 |
| GET/PUT | `/fx/state` | 실적·속보·경영 환율(전사 공통 3종) 조회/저장 | 화면 |
| POST | `/fx/applied/recompute` | 지정한 기준(연도·월·속보실적·룰)의 적용 환율을 다시 계산 | 아무도 안 부름 (수동 테스트 외 이력 없음) |
| GET | `/fx/applied` | 적용 환율 캐시 조회 | 아무도 안 부름 |
| GET/POST | `/management-plan/versions`, PUT/DELETE `/versions/{year}/{plan_no}` | 경영계획 버전(P00~) 조회/생성/확정/삭제 | 화면 |
| GET/PUT | `/management-plan/entries` | 경영계획 버전별 금액 조회/저장 | 아무도 안 부름 — 실제 금액은 아직 `/overseas/state`의 `cost_entries(version="plan")` 슬롯을 그대로 씀 |
| GET/PUT/DELETE | `/common-codes` | 공통코드 조회/저장/삭제 | 챗봇만 |
| GET | `/errors` | 서버 500 에러 로그 조회 | 챗봇만 |
| GET/POST | `/chat/providers`, `/chat`, `/chat/confirm` | 플로팅 AI 채팅 | 화면(챗봇 UI) |

다른 18개 계정(감가상각비·위탁용역비·생소비 등)은 아직 전용 엔드포인트 없음 — 화면 자체가 "준비 중" 스텁이라 저장할
데이터가 없음. 이 계정들의 비용 데이터를 담으려고 만든 공용 테이블 `cost_entries`/`cost_entry_months`도
같은 이유로 아직 0건 — 테이블 구조만 있고 아무 엔드포인트도 안 붙어 있다(설계는 아래 참고).

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
  }
}
```

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

전사 공통 환율 3종(실적 `fx_actual_rates`, 속보 `fx_forecast_rates`, 경영 `fx_plan_rates`)을 한 번에
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

## cost_entries / cost_entry_months — 설계만 있고 아직 안 쓰임

다른 18개 계정용으로 설계해둔 공용 비용 테이블. `cost_entries`는 "계정×행×비용항목×버전의 편성
회차" 헤더(어느 연도·몇 월 기준으로 편성했는지)만 들고, 실제 월별 금액은 `cost_entry_months`가
`cost_entry_id`를 FK로 참조하는 행으로 가진다(당해 12개월 + 9월 속보부터 같이 들어오는 차년도 월이
같은 헤더 아래 자유롭게 달릴 수 있음). 화면에 뿌릴 때는 두 테이블을 join한 뒤 `target_year`·
`target_month`로 행렬전환(pivot)해서 그리드 열로 조합하면 된다.

지금은 이 테이블을 읽거나 쓰는 엔드포인트가 하나도 없다 — 18개 계정 화면이 아직 "준비 중" 스텁이라
붙일 필요가 없었다. 해외연구소의 `/overseas/cost-entries`는 이름이 비슷하지만 **이 테이블이 아니라
`overseas_cost_lines`를 쓴다** — 헷갈리지 말 것.

---

## 아직 없는 것

- 인증/권한 (누가 호출해도 다 허용됨 — 로컬 프로토타입 단계라 보류)
- 다른 18개 계정용 엔드포인트 (`cost_entries`/`cost_entry_months`는 테이블만 준비됨)
- `/management-plan/entries` 실사용 연결 (버전 목록만 동기화, 금액은 여전히 `overseas/state` 경유)
- `/fx/applied/*`를 실제로 부르는 화면/챗봇 도구 (엔드포인트만 있고 호출 이력 없음)
