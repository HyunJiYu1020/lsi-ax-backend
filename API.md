# API 엔드포인트 정리

현재 구현된 엔드포인트 전체 목록. Swagger UI(`http://localhost:8000/docs`)에서 직접 호출해볼 수도 있지만,
여기엔 "왜 이렇게 설계했는지"까지 같이 남겨둔다. 어디서 실제로 호출하는지(화면 버튼 vs AI 챗봇 전용)는
[CONNECTIONS.md](./CONNECTIONS.md) 2~4절 참고.

Base URL: `http://localhost:8000/api/v1`

## 목록

| Method | Path | 설명 | 호출처 |
|---|---|---|---|
| GET | `/health` | 서버 동작 확인용 (실제 로직 없음) | — |
| GET | `/accounts` | 19개 계정 레지스트리 전체(상태·편성 주체·비고·편성 단계·현재 단계) 조회 | 화면(전체 현황)+챗봇 |
| GET/PUT | `/accounts/{key}` | 계정 하나 조회 / 상태·편성 주체·비고 변경 | 화면(전체 현황)+챗봇 |
| PUT | `/accounts/{key}/step` | 계정의 현재 편성 단계 이동(그 계정에 정의된 단계만) | 챗봇만 (화면 연결 대기) |
| GET | `/accounts/summaries` | 19개 계정의 그 버전 월별 편성액·전년 실적 총액 — 전체 현황판이 한 번에 받는다 (`yyyymm` 필수, `version` 기본 forecast) | 화면(전체 현황)+챗봇 |
| PUT | `/accounts/{key}/summary` | 계정 하나의 현황판 숫자(월 12칸 + 전년 총액)를 한 번에 교체. 저장 자리는 공용 `cost_entries`(scope="ALL", category="total"/"prior") | 화면(전체 현황) |
| GET/PUT | `/overseas/state` | 해외연구소 전체 데이터 조회/저장. GET `basis_month=`는 **화면이 열어 본 달** — 그 달에 행이 없으면 `V["cur"]`가 빈 채로 온다(업로드 현황이 "미"로 떠야 맞다) | 화면 |
| GET/PUT | `/overseas/step` | 해외연구소 진행 단계 조회/변경 | 챗봇만 |
| GET/PUT | `/overseas/labs` | 연구소 기준정보 조회/저장 (GET `as_of=YYYY-MM`으로 그 시점 값 조회, PUT `effective_from`으로 "이 버전부터" 적용) | 화면 |
| PUT/DELETE | `/overseas/labs/one` | 연구소 **하나만** 추가·수정·삭제 — 다른 연구소를 건드리지 않는다. 에이전트·스크립트는 반드시 이걸 쓴다(`PUT /overseas/labs`는 전체 목록 교체라 빠뜨린 연구소가 삭제된다) | 챗봇만 |
| GET | `/overseas/lab-sheets` | 연구소별 워크북 시트 구성 조회 | 챗봇만 |
| GET/PUT | `/overseas/cost-entries` | 연구소 비용 데이터 셀 단위 조회/upsert (`cost_entries`/`cost_entry_details`, account_code="overseas") | 챗봇만 |
| GET/POST | `/overseas/uploads`, POST `/overseas/uploads/file` | 업로드 이력 조회/기록, 엑셀 파일 업로드 | `uploads/file`은 화면, 나머지는 챗봇만 |
| GET/PUT | `/overseas/notes` | 증감분석 비고(사유) 조회/저장 | 챗봇만 |
| POST | `/overseas/reason-translate` | 연구소가 영문으로 보낸 증감사유 → 한국어 AI 번역 (원문·번역 쌍을 `text_translations`에 캐시) | 화면+챗봇 |
| POST | `/overseas/reason-summary` | 지금 보고 있는 회신 묶음을 가로질러 읽은 요약 3~5줄. 번역이 한 문장을 옮기는 일이라면 이건 계정 20여 개로 쪼개진 한 연구소의 사유를 모아 주는 일이다. DB를 쓰지 않는다 — 화면이 무엇을 걸러 놨느냐에 따라 매번 달라지므로 저장·캐시하지 않는다 | 화면 |
| GET | `/overseas/reason-translations` | 저장된 원문·번역 쌍 목록(오역 찾기·모델 교체 확인) | 챗봇만 |
| GET/PUT | `/versions/{y}/{m}/{kind}/accounts`, `/accounts/{code}` | 버전×계정의 **계정 확정** 상태·확정 일시·확정 당시 단서(`version_account_status`) | 화면(해외연구소)+챗봇 |
| GET/PUT | `/overseas/carry-defs` | 집계 파생 행·열의 "다음 버전에도 유지" 승인 조회/설정 (`carry_defs`) | 화면+챗봇 |
| POST | `/overseas/carry-forward` | 유지 승인한 파생 행·열 정의를 새 버전으로 복제 (새 버전을 열 때) | 화면 |
| GET/PUT | `/overseas/anomaly-rules` | 이상 감지 기준(급변·합계 불일치·사유 누락·단위 의심) 조회/변경 (`anomaly_rules`) | 화면+챗봇 |
| GET | `/overseas/anomaly-rules/history` | 기준 변경 이력(before/after) | 챗봇만 |
| POST | `/overseas/anomaly-scan` | 업로드 값(또는 저장된 값)을 기준에 걸어 검사 — 반영을 막지 않고 목록만 돌려준다 | 화면+챗봇 |
| POST | `/overseas/row-match` | 업로드 파일의 행 이름이 이미 있는 행과 같은 행인지 후보 찾기 (`Travel Expense` → 출장비). 합치지는 않는다 | 화면+챗봇 |
| GET/PUT/DELETE | `/overseas/row-aliases` | 사람이 답한 "같은 행 / 별개 행" 저장·조회·삭제 (`row_aliases`(account_key='overseas')) | 화면+챗봇 |
| POST | `/overseas/row-merge` | **이미 적재된** 추가 행의 금액을 다른 행으로 합쳐 옮긴다(`dry_run` 미리보기 필수) | 챗봇만 |
| GET/PUT/DELETE | `/overseas/extra-rows` | 사용자 추가 행 조회/저장/삭제 (값은 `cost_entries`에 저장). `per`로 파일에 적혀 온 1Q~4Q·연간도 받는다 — `None`은 안 건드림, 리스트는 전체 교체(`[]`는 지움) | 화면+챗봇 |
| GET/PUT/DELETE | `/overseas/extra-cols` | 추가 열(라벨) 목록 조회/등록/삭제. `fmt`로 **그 열을 그리는 방법**을 함께 저장한다 — `raw`(그대로 · 기본) · `pct_ratio`(0.825 → 82.5%) · `pct_plain`(82.5 → 82.5%). 값이 아니라 표시만 바꾼다 | 화면+챗봇 |
| GET/PUT | `/overseas/cost-extras` | 추가 열의 칸 값 조회/저장. 숫자와 글자를 다 받는다 — 숫자로 읽히면 number, 나머지는 원문 그대로 text(`cost_entry_details.data_type`+`value`). 응답의 JSON 타입이 곧 저장된 타입이다 | 화면+챗봇 |
| GET/PUT | `/overseas/cost-headers` | 연구소 비용 시트의 **기간 열 머리글 원문**(1월…12월 · 1Q~4Q · 연간) 조회/저장. 업로드 한 번이 그 시트(연구소×버전×기준월) 머리글 전체를 교체한다 — 빈 `labels`를 보내면 지워진다. 취합 비용 표가 코드 머리글("1월") 대신 이 글자를 쓴다(집계·증감분석 표는 쓰지 않는다) | 화면+챗봇 |
| GET | `/overseas/vendor-rows` | 외주용역 상세 조회 (`overseas_vendor_rows`) | 챗봇만 |
| PUT | `/overseas/vendor-rows` | 외주용역 상세 저장 — 연구소×버전 단위 전체 교체 | 화면 |
| PUT | `/overseas/cp-rows` | CP 계약 저장 — 연도 × **적용 시점** 단위 전체 교체(`effective_from` 생략 시 지금 편성 중인 기준월) | 화면 |
| PUT | `/overseas/cp-contract-amount` | CP 계약 **금액만** 변경 — 연 합계(`year_total`) 또는 POC 하나(`poc_proj`+`poc_total`). 응답에 전·후 계약과 분기 정산액 변화 | 챗봇만 (`update_cp_contract_amount`) |
| PUT | `/overseas/reason-rows` | 증감사유 업로드 시트 저장 — 키별 upsert(누적) | 화면 |
| GET | `/overseas/vendor-by-project` | 외주용역을 과제(Project)별로 묶어 집계 (증감분석 · 챗봇) | 챗봇만 |
| GET | `/overseas/variance-basis` | 비교 기준 세 칸이 실제로 무엇과 비교하는지 해석 (전실행=직전 버전 · 전년=전년 12월 실적 · 경영계획=선택 차수) + 경영계획 차수 목록 | 화면+챗봇 |
| GET | `/overseas/variance` | 계정별 증감분석 표 (기준·당월·증감·환차 현지→$/$→원·환차外). 챗봇이 "왜 늘었어?"에 답할 때 쓰는 근거 데이터 | 화면+챗봇 |
| GET/PUT | `/overseas/variance-reasons` | 증감사유 한 칸의 전체 상태 조회/저장 (회신 원문·번역·AI 초안·확정 문장 + 저장 시점 증감액) | 화면 |
| POST | `/overseas/variance-reason-draft` | 회신 원문 → 번역 + 보고용 사유 초안 생성 (확정 사유는 건드리지 않음) | 화면 |
| GET | `/overseas/variance-reason-check` | 증감이 기준치를 넘었는데 사유가 없거나 재확인이 필요한 항목 — 확정 직전 경고. 기준은 `threshold_pct`(기본 10%)와 `threshold_amount`(원, 기본 0=안 씀)이고 **둘 다 넘을 때만** 걸린다(`threshold_pct=0`으로 비율 조건을 끌 수 있다) | 화면+챗봇 |
| GET/PUT/DELETE | `/overseas/variance-drivers` | 증감 동인 분해 표(계산식) 조회/저장/삭제. `carry_over=true`면 다음 버전에도 따라옴 | 화면+챗봇 |
| GET/PUT | `/overseas/actual-stage` | 실적 연월의 1차 상태 — 실적결산 1차 완료 여부(1차 탭 활성)·[1차 고정] | 화면 |
| GET/PUT | `/overseas/actual-first` | 실적 1차 분석(ERP 약식) 연구소 총액·1차 사유 조회/저장. 고정 상태면 409 | 화면 |
| GET | `/overseas/actual-stage-shift` | 2차 화면 상단의 "1차 → 2차 변동" (연구소별 + 합계) | 화면+챗봇 |
| GET | `/overseas/cp-contract` | 그 해 CP 계약(기준정보 › CP 계약) 요약 — POC별 금액 + 분기 하나치(연 ÷ 4). **`as_of`("YYYY-MM") 필수** — 그 버전 시점의 계약을 읽는다. `registered=false`는 "0원"이 아니라 **미등록**이고, 집계 ④ CP 보정의 정산 칸을 빈칸으로 두는 근거다 | 화면+챗봇 |
| GET | `/overseas/confirm-check` | [계정 확정] 직전 점검 — 막는 것(환율 미등록 월)과 알리는 것(미업로드 연구소 · CP 계약 미등록 · 사유 미작성 · 재확인 필요) + 확정 단서 문장("미업로드 2개 포함 확정") | 화면+챗봇 |
| GET | `/overseas/labor-ratio` | 인건비율(인건비 ÷ 개발비(매출)) 전체·연구소별. 분기·연간은 **합계끼리** 나누고, 분모가 0이면 `ratio=null`(화면 '—') | 화면+챗봇 |
| GET | `/overseas/aggregate-table` | 집계 ④ 전체 계정 표(45행 × 17칸)를 서버가 계산. `mode=usd\|krw`로 달러·원화. **`basis_month`·`kind`(·`plan_no`)로 어느 버전인지 같이 보내야 한다** — 안 보내면 지금 편성 중인 속보로 떨어져서, 화면이 과거 버전을 띄워도 최신 속보 표가 나간다 | 화면+챗봇 |
| GET | `/overseas/aggregate-table` 응답의 `derived_cols` | 집계 ① 표의 **파생 열**(화면 [+ 열 추가]) 값. 열 정의는 전부터 DB에 있었고 칸 값만 매번 계산한다 — 그 계산을 서버가 한다(2026-11-18) | 화면 |
| GET | `/overseas/aggregate-table` 응답의 `derived_rows` | 집계 ① 표의 **파생 행**(화면 [+ 행 추가]) 값 — **그 표의 단위로 계산해서** 준다(원화면 재료를 환산한 뒤 계산식 평가). 45행 사이 어디에 끼워 넣을지는 화면이 정한다(2026-11-20) | 화면 |
| GET | `/overseas/labor-ratio-table` | 집계 화면 **인건비율 팝업** 전체(전체 → 연구소 → 파트, 인건비·개발비·비율 17칸). 달러(K$) · CP 미보정 — 챗봇용 `/overseas/labor-ratio`(원화·한 기간)와 다른 표다 | 화면 |
| GET | `/overseas/lab-monthly` | 연구소 **파트** 단위 월별 원화 개발비(매출) + 합계(CP 분기 보정). 마감 2단계 전표 매핑 타깃·전실행 금액 | 화면 (마감) |
| GET | `/overseas/lab-tables` | 집계 탭 ①②③(연구소별 표 10장)을 한 번에 — ① 개발비(총계 `t2u·t2k` / 인건비 `t5u·t5` / 인건비외 `t6u·t6`, CP 분기 정산) · ② 개발비(CP_PL) `t7u·t7k` · ③ 인건비(CP_PL) `t3·t4`. 버전 파라미터는 ④ 전체 계정과 같다. 환율이 없으면 원화 표가, CP 계약이 없으면 ① 전체가 빠진다 | 화면+챗봇 |
| GET | `/overseas/summary` | 화면 맨 위 히어로 카드와 ③집계 KPI 카드(개발비(매출) · 인건비/인건비외 · 업로드 진척 · 단계). `basis_month`·`kind`·`plan_no`로 **차수를 지정**한다(집계표와 같은 파라미터 · 안 주면 최신 속보) | 화면 · 챗봇 |
| GET/PUT/DELETE | `/overseas/sheet-rows`, DELETE `/sheet-rows/all` | 열 구성 확정 전인 새 시트(JSON) 조회/저장/삭제 | 챗봇만 |
| GET/PUT | `/fx/state` | 실적·속보·경영 환율(전사 공통 3종) 조회/저장 | 화면 |
| GET | `/fx/overseas-rule`, PUT `/fx/overseas-rule/default`·`/override` | 해외연구소 환산 환율 기준(월평균 `avg` | 말일 `last`)의 기본값과 **월 단위 예외**. 화면은 한 번 받아 월마다 "예외 있으면 그 rule, 없으면 default"로 판정한다 | 화면 |
| POST | `/fx/applied/recompute` | 지정한 기준(연도·월·속보실적·룰)의 적용 환율을 다시 계산 (`dry_run=true`면 미리보기) | 챗봇만 (`get_fx_rate_preview` · `recompute_applied_fx_rates`) |
| GET | `/fx/applied` | 적용 환율 조회 | 챗봇만 (`get_applied_fx_rates`) |
| GET | `/fx/currency-coverage` | 그 해에 그 통화 환율이 어느 달까지 들어와 있는지(연구소 통화를 잡기 전 확인용) | 챗봇만 (`get_fx_currency_coverage`) |
| PUT | `/fx/rate` | 환율 원본 **한 칸**(연월×통화×종류) 직접 입력 — 새 통화의 빈칸 메우기용. 저장 후 적용 환율 표 재생성 | 챗봇만 (`upsert_fx_rate`) |
| GET/PUT/PUT(`/batch`)/DELETE | `/cost-entries` | 19개 계정이 공유하는 범용 비용 조회/upsert/일괄upsert/삭제 | 화면(해외연구소가 `/overseas/state`와 병행 저장 — 아래 참고) |
| GET/POST/PUT/DELETE | `/versions` | 연월×속보·실적(forecast/actual) 진행 상태(열림·작성중·완료) 조회/생성/변경/삭제 | 화면 |
| GET/POST | `/management-plan/versions`, PUT/DELETE `/versions/{year}/{plan_no}` | 경영계획 버전(P00~) 조회/생성/확정/삭제 | 화면 |
| GET/PUT | `/management-plan/entries` | 경영계획 버전별 금액 조회/저장 | 아무도 안 부름 — 실제 금액은 아직 `/overseas/state`의 `cost_entries(version="plan")` 슬롯을 그대로 씀 |
| GET/PUT | `/reference/cost-elements` | 기준정보 › 비용계정(Cost Elem. · 계정(1)~(5)). GET `year=`는 **고치는 대상인 연도표**, GET `version=`은 **그 버전이 보는 계정표**(처음 조회할 때 그 시점 연도표를 복사해 고정) | 화면 |
| GET/PUT | `/reference/asset-classes` | 자산 Class → 비용계정 매핑(감가상각비 전용 · 시점 없는 단일 마스터) | 화면 |
| GET/PUT | `/reference/dept-codes` | 기준정보 › 부서코드(Cost Center · Budget Unit · 팀실 小中大 · FA · 대표코드 · 사용/폐기) — **버전별**. 없는 버전은 직전 버전을 복사해 만들고 `copied_from`으로 알려준다 | 화면 |
| GET | `/reference/changes`, `/reference/changes/{id}` | 기준정보 변경 이력 — 목록(요약·누가·언제·어떻게·건수)과 한 건의 **바뀐 행 before/after**. `table`·`scope`로 걸러 받는다 | 화면(기준정보 최근 변경)+챗봇 |
| GET/PUT | `/reference/products` | 기준정보 › 제품정보(SC_FMMS: 제품 → 제품군 fam1~fam4) — **버전별**, 없는 버전은 직전 버전 복사. 여러 계정이 같이 쓴다(정률기술료 FAM3 판정·배부 검증) | 화면 |
| GET/PUT | `/reference/plm-tasks` | 기준정보 › PLM 과제(과제 코드 · 상태 · 일정 · 담당) — **버전별**. `status=Drop`으로 Drop 과제만 받을 수 있다 | 화면 |
| GET/PUT/DELETE/GET(`/index`) | `/sheet-rows` | **계정 공용** 구조 미정 시트의 행(JSON) 조회/표 단위 교체/삭제/목록. 위탁용역비·생소비 편성값(계약별 40여 칸)과 감가상각비 원천(자산대장·투자계획)·정률기술료 요율·규칙이 여기 들어간다 — 열 구성이 확정되면 전용 테이블로 옮긴다 | 화면 |
| GET | `/sheet-rows/bundle` | 그 계정의 표를 **한 번에** (`"<version>|<sheet_key>"` 키). 표마다 따로 부르면 첫 화면에서 요청 30개가 동시에 날아가 커넥션 풀이 마른다 — 그래서 묶음으로 받는다([CONNECTIONS.md](./CONNECTIONS.md) 2-1절) | 화면 |
| GET/PUT/DELETE | `/common-codes` | 공통코드 조회/저장/삭제 | 화면+챗봇 |
| GET/PUT/DELETE/POST(`/recheck`) | `/variance-reasons` | **계정 공용** 증감사유 조회/저장(칸 묶음 교체)/삭제/"재확인 필요" 표시. 자리 = 계정 × 기준연월 × 속보·실적 × 비교기준(prev\|prior\|plan) × 기간 × 범위 | 화면(감가상각비·위탁용역비·생소비) |
| GET/PUT/DELETE | `/guidelines` | 담당자가 말로 등록한 업무 지침 조회/저장/삭제 (`guidelines`) — 매 요청의 시스템 프롬프트에 주입된다 | 챗봇만 |
| GET | `/errors` | 서버 500 에러 로그 조회 | 챗봇만 |
| GET/POST | `/chat/providers`, `/chat`, `/chat/confirm` | 플로팅 AI 채팅 | 화면(챗봇 UI) |
| GET/POST | `/close/vouchers` | 실적결산 ERP 전표 조회 / 적재(재수신 시 기존 매핑 유지) | 화면 |
| GET | `/close/summary` | 1단계 취합의 계정별 요약(건수·금액·미분류) | 화면 |
| GET/PUT/DELETE | `/close/dept-rules` | 계정 자동 분류 기준정보(부서코드×비용계정 → 계정) 조회/저장/삭제 | 화면 |
| POST | `/close/classify` | 기준정보를 고친 뒤 그 달 전표 재분류 | 화면 |
| GET/PUT | `/close/overseas/mapping` | 2단계 가공 · 해외연구소 작업대(연구소별 표 + 전표별 AI 추천) 조회 / 매핑·제외·이관 저장 | 화면 |
| GET/PUT | `/close/maps` | **계정 공용** 전표매핑 조회/저장(붙일 곳·제외·이관). 해외연구소만 붙일 곳을 연구소 목록으로 검증하고, 다른 계정은 계약·과제·부서코드라 검증하지 않는다 | 화면(실적결산) |
| POST | `/close/overseas/rematch` | AI 다시 매칭 (AI가 정한 것만 — 담당자 수정·규칙은 유지) | 화면 |
| GET/POST/DELETE | `/close/match-rules` | 승인된 AI 매칭 규칙 조회/등록/삭제. `match_field`에 `assignment`(ERP 지정 필드), `match_op`에 `equals`\|`contains`\|`starts_with`, `rejected=true`로 "이 제안은 거절" 기록 | 화면(규칙은 아직 localStorage 목업)+챗봇 |
| GET/PUT | `/close/stages`, GET `/close/stages/{key}` | 계정별 결산 상태 조회/매핑 완료 토글 (해외연구소는 "1차 완료") | 화면 |

**다른 계정 화면의 저장 상태**(2026-11 실측): 감가상각비·위탁용역비·생소비·정률기술료·실적결산
화면은 `frontend-html`에 데이터까지 들어 있다 — "준비 중 스텁"이 아니다. 다만 서버에 저장되는 것은
아직 일부다. 전체 현황판 값(상태·편성 주체·비고·월별 금액)과 실적결산 전표·매핑, 기준정보 네 표는
서버에 올라오고(위 표의 `/accounts*` · `/close/*` · `/reference/*`), 그 계정들의 **편성 원천 데이터**
(자산대장·계약별 편성값·요율 마스터·제품정보)는 아직 브라우저(localStorage)에 있다. 무엇이 어느 쪽인지는
[CONNECTIONS.md](./CONNECTIONS.md) 2-6절의 전수 목록을 본다.

**`/overseas/state`와 `/cost-entries` 병행 저장**: 해외연구소 화면은 비용을 저장할 때
`PUT /overseas/state`(권위 있는 값)와 `PUT /cost-entries/batch`를 같은 데이터로 둘 다 호출한다
(`core.js`의 `ovCostApiBatch`). `overseas_cost_entries` 테이블이 있던 시절엔 이게 "새 공용 테이블에도
같이 쌓기 시작"하는 전환용 이중 저장이었는데, 지금은 `/overseas/state`가 이미 `cost_entries`/
`cost_entry_details`에 직접 쓰므로 **같은 행을 두 경로로 중복 upsert하는 셈**이다(결과값은 같아서
깨지진 않지만 불필요한 쓰기). 프론트 쪽에서 정리가 필요함.

---

## 금액 단위 — 저장은 $, 화면은 K$

연구소가 보내는 엑셀 양식은 **K$**(천 달러)이고, DB와 API는 **$**로 다룬다. 업로드가 그 셀을
×1000해서 저장하고, 화면이 ÷1000해서 K$로 찍는다.

| 자리 | 단위 | 누가 바꾸나 |
|---|---|---|
| 연구소가 보낸 엑셀 | K$ | — |
| DB (`cost_entry_details.amount` 등) | **$** | 업로드 파서가 ×1000 (`core.js` `ovParse`) |
| 서버 계산 | **$** | 원화 = `$ 금액 × 적용환율` (×1000이 붙지 않는다) |
| API 응답의 달러 값 | **$** | 저장 단위 그대로 — 서버는 나누지 않는다 |
| 화면 표시 | K$ | `core.js` `ovF.usd`가 ÷1000. 화면의 `$` 토글을 켜면 나누지 않고 그대로 찍는다 |
| 엑셀 다운로드 | **$** | 저장 단위 그대로 |
| 연구소 양식 왕복 다운로드 | K$ | `ovTplDl` — 연구소가 다시 올릴 파일이라 보낸 단위로 돌려준다 |

**응답의 `unit` 필드는 값의 단위가 아니라 "화면이 기본으로 보여주는 단위" 라벨이다.** 집계표가
`unit: "K$"`를 주면서 값은 $로 내려준다 — 화면이 나누는 쪽을 맡고 있어서다. 응답 값을 그대로
읽는 쪽(챗봇·스크립트)은 **$로 읽어야 한다.**

달러가 아닌 칸은 환산하지 않는다:

- **인력(`hc`)** — 단위가 명(名)이다. 분기·연간은 합이 아니라 **평균**이다.
- **실적 1차의 `local_amount`** — 현지통화(ILS·INR) 1단위로 저장하고, 환산은 읽을 때 한다.
- **CP 계약(`overseas_cp_rows`)** — 계약서 그대로 $다(전부터 그랬고, 이번에 비용 쪽이 같은 단위가 됐다).

전환 마이그레이션은 `a1c7d4f08b32`(기존 금액 ×1000)다. 되돌리려면 코드도 같이 되돌려야
금액이 맞는다 — 무엇을 곱하고 무엇을 안 곱했는지는 그 리비전 주석에 적혀 있다.

**이상 감지 기준도 같은 단위다**(`anomaly_rules.amount`). 기준만 K$로 남으면 "급변 10 이상"이
$10 이상이 되어 전 칸이 걸린다 — 마이그레이션이 같이 올렸다.

---

## GET /overseas/state

연구소별 비용 데이터, 환율, 업로드 이력, 증감사유 메모, 현재 단계를 한 번에 반환한다.

### 기준월은 호출하는 쪽이 정한다 (`basis_month`)

`basis_month=11`을 보내면 **그 달**의 속보를 돌려준다. 그 달에 `cost_entries` 행이 하나도 없으면
`V["cur"]`는 빈 채로 내려간다 — 다른 달로 대체하지 않는다. 안 보내면 종전처럼 DB의 최신 속보월로
짐작한다(옛 클라이언트 호환).

왜 명시하게 됐나: 전에는 서버가 항상 `current_basis_month`(DB의 최신 속보월)로 짐작했다. 그래서
`select * from cost_entries where yyyymm='202611'`이 0행인데도 화면에서 **속보 2026년 11월**을 열면
9월 데이터가 `cur`로 내려와, 화면이 그걸 11월 버킷에 써넣고 업로드 현황이 전 연구소 ✓로 보였다.
아직 아무것도 안 올린 달은 전부 "미"여야 한다. 다른 엔드포인트(`/overseas/summary`,
`/overseas/labor-split`, 증감분석)는 이미 `basis_month`를 받고 있었고 — `services/versions.py`와
`services/labor_split.py`가 "`build_state`는 월을 짐작한다"며 이 함수를 **피해 가는** 주석을 달아
둘 정도였다 — 이 엔드포인트만 남아 있었다.

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
      }
    },
    "prev": { "d": {...} },                // 전실행
    "prior": { "d": {...} },               // 전년 실적
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
  "cp": [ /* CP 계약 (overseas_cp_rows) */ ],
  "basis_month": 11,                  // 돌려준 달 = 보낸 basis_month(안 보냈으면 서버가 짐작한 달)
  "monthless_month": 9                // 아래 참고 — 월 구분이 없는 자료가 실제로 속한 달
}
```

### `monthless_month` — 월 구분이 없는 자료가 실제로 속한 달

`reason`(증감사유) · `vendor`(외주용역) · `files`(업로드 이력)는 아직 **월 구분이 없다**:
`overseas_reason_rows.version` · `overseas_vendor_rows.version` · `uploads.version`이 모두
`cur | prev | prior | plan` 역할값뿐이다. 그래서 10월에 받은 회신이 11월 속보를 볼 때도 `cur`로
그대로 잡힌다. `monthless_month`는 그 자료가 **실제로 어느 달 것인지**(= DB의 최신 속보월)를 밝힌다 —
화면은 `basis_month`가 아니라 이 달의 버킷에 넣어야 한다. 안 그러면 아직 아무것도 안 올린 달에
직전 달 회신이 흘러들어 업로드 현황에 ✓가 켜진다.

`files["cur"]`는 한 걸음 더 간다: **그 달에 실제로 값이 있는 연구소에만** 파일명을 붙인다. 값이 없는
달의 파일명은 어느 달 것인지 말할 수 없으므로, 업로드 현황은 "미"인데 비용 화면에는 "받은 파일:
OV_SARC_2610.xlsx"가 남는 모순을 막는다.

이 필드는 세 테이블에 월 버전(`version_id`)이 들어오면 사라진다(그때는 `basis_month`와 늘 같아진다).
`uploads`는 19개 계정이 공유하는 테이블이라 그 마이그레이션은 따로 다룬다.

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

**기준정보 변경 이력(`ref_change_logs` · `ref_change_rows`)**: 기준정보 다섯 표는 모두 **표 전체
교체**로 저장된다. 그래서 이력이 없으면 "한 줄 오타를 고친 것"과 "엑셀을 잘못 올려 표가 통째로 갈린
것"을 구분할 수 없다. 실제로 받는 질문이 셋이다 — 부서코드가 18행에서 3행으로 왜 줄었나 · 이 계정명은
언제부터 이랬나 · 이 과제를 누가 Drop으로 바꿨나. 그래서 이렇게 남긴다:

- **변경이 없으면 기록하지 않는다.** 화면은 [저장]을 누를 때마다 PUT을 보낸다 — 그대로 쌓으면 이력이
  "아무 일도 없었음"으로 가득 차서 진짜 변경을 못 찾는다. 변경 유무는 **서버가** 판단한다(화면이
  판단하면 화면마다 기준이 갈린다).
- **바뀐 행만, 바뀐 칸만** before/after로 남긴다. 전체 스냅샷을 저장하면 용량이 터지고(제품정보 수천
  행 × 저장마다), 40칸 행을 통째로 복사하면 이력이 표보다 커진다.
- 바뀐 행이 상한(200행)을 넘으면 상세를 생략하고 **생략했다는 사실**을 남긴다(`detail_omitted`) —
  조용히 자르면 "상세 없음"이 "안 바뀜"으로 읽힌다.
- `how`(screen · upload · copy · seed · chat)를 화면이 보낸다. 서버는 PUT만 보고는 표에서 고친 것 ·
  엑셀을 올린 것 · 전월 복사를 구분할 수 없고, 그 셋은 사고의 성격이 다르다.
- `actor`는 **문자열**이고 비어 있을 수 있다. 이 플랫폼에 아직 로그인이 없다 — 서버가 가짜 이름을
  박으면 감사 기록에 거짓 주체가 남는다(빈칸보다 나쁘다). 인증이 붙으면 `users.id`로 승격한다.
- 전표 분류 기준(`close_dept_rules`)도 같은 이력에 넣는다. 그 표가 바뀌면 그 달 전표가 다른 계정으로
  가서 "왜 이 계정으로 갔나"라는 같은 질문을 받는다.
- **되돌리기는 없다.** before 값이 있어 기술적으로는 가능하지만, 기준정보 복원은 "변경한 버전부터
  적용" 규칙과 엮인다(이미 만든 버전의 스냅샷을 어떻게 할 것인가). 지금은 조회만 하고, 복원은
  담당자가 [전년 복사]·[전월 복사]로 한다.

**증감사유는 왜 경로가 둘인가**: `/overseas/variance-reasons`(해외연구소)와 `/variance-reasons`(공용)이
같은 테이블(`variance_reasons`)을 쓴다. 해외연구소 경로는 회신 원문 번역·AI 초안·**서버가 계산한 지금
증감액**까지 묶어 주고 1차 고정(보고까지 끝난 값) 검증이 붙어 있다 — 연구소 PL 수식이 서버에 있어서
가능한 일이다. 다른 계정은 증감액 수식이 아직 화면에만 있어서, 공용 경로는 저장 시점 증감액을 화면이
보내 준 값으로 박고 "사유 문장을 잃지 않는 것"만 책임진다. 그 무게 차이를 한 경로에 합치면, 수식이
없는 계정이 "증감액 0"을 받아 재확인 표시가 아무 근거 없이 켜진다. 자리(키)는 같으므로 같은 칸을
양쪽에서 읽는다(테스트 `test_overseas_path_still_sees_its_own_rows`가 그걸 지킨다).

**편성값을 전용 테이블 대신 `sheet_rows`(JSON)에 두는 기준**: 열 구성이 아직 바뀌는 중인 표만
그렇게 둔다. 위탁용역비·생소비 계약 한 줄은 40여 칸이고 그 구성이 달마다 바뀐다(2026-11에도
기준정보 양식이 바뀌었다) — 열마다 마이그레이션을 붙이면 화면 한 번 고칠 때 DB 배포가 따라오고,
그러면 결국 "그냥 브라우저에 두자"로 돌아간다. 대신 다음 세 조건을 지킨다: ① 화면 키를 그대로
담는다(변환을 한 겹 더 만들지 않는다), ② 쓰기는 표 단위 교체다(행 단위로 쪼개면 저장 한 번이
수백 요청이 되고 중간 실패가 표를 반만 바꾼다), ③ 열이 확정되면 전용 테이블로 옮긴다(해외연구소
외주용역이 그렇게 `overseas_vendor_rows`로 갔다). 반대로 **열이 이미 고정된 표는 처음부터 전용
테이블**로 만든다 — 제품정보(SC_FMMS)는 양식이 고정이고 여러 계정이 같이 써서 `ref_products`다.

**기준정보 네 표(`/reference/*`)의 시점 규칙**: 같은 공통 RULE("변경한 버전부터")을 표마다 다르게
푼다 — 화면이 그렇게 갈라 놓았고 이유가 표마다 다르다.

- **비용계정은 연 단위**다. 계정 체계는 회계연도 단위로 바뀌고 월마다 손대는 표가 아니다. 대신
  버전이 그 계정표를 **처음 조회할 때 사본을 떠서 고정한다**(`ref_cost_element_snapshots`) —
  11월에 계정명을 고쳤는데 9월 속보가 그 이름으로 다시 그려지면 이미 보고한 표와 화면이 달라진다.
  사본 생성을 "버전 생성 시점"이 아니라 "첫 조회 시점"에 두는 이유는, 버전을 만드는 경로가 화면·
  챗봇·스크립트로 여러 개라 생성 훅을 다 붙이면 한 군데가 빠져 조용히 사본 없는 버전이 생긴다.
  그 버전 연도의 표가 아직 없으면 **그 이하 가장 최근 연도표**에서 뜬다(1월 속보를 새해 계정표보다
  먼저 여는 게 실무 순서라서, 여기서 빈 표를 주면 계정명이 전부 사라진다).
- **부서코드·PLM 과제는 버전 단위**다. 조직 개편과 과제 Drop은 달마다 생기고, 그게 그 달 편성값의
  귀속부서·과제코드 검증 결과를 바꾼다. 없는 버전을 조회하면 **직전 버전을 복사해** 만들고
  (매달 18개 부서를 다시 입력하게 만들 수 없다) 복사 출처를 `copied_from`으로 같이 내려준다 —
  복사한 사실을 숨기면 담당자가 직전 달 표를 이번 달 확정값으로 착각한다. 바로 앞 버전에 행이
  없으면 그 앞까지 거슬러 본다(중간 버전을 안 열고 건너뛰는 일이 흔하다). 경영계획 버전은
  경영계획끼리만 복사한다 — 월 버전과 섞으면 P01이 전월 속보 표를 들고 온다.
- **자산 Class 매핑은 시점이 없다**. 자산 Class는 ERP 코드 체계이고 그게 가리키는 상각비 계정도
  함께 고정이라 달마다 다른 값일 이유가 없다(그래서 화면도 이 표만 버전 선택 없이 보여준다).

쓰기는 네 표 모두 **표 전체 교체(PUT)**다. 화면이 그리드를 통째로 고친 뒤 [저장]을 누르고 엑셀
업로드도 "이 탭 전체가 파일 내용으로 바뀝니다"라서, 한 줄 단위 API를 두면 저장 한 번이 수십 개
요청으로 갈라져 중간에 하나가 실패하면 표가 반만 바뀐 상태로 남는다. 버전 키는 서버 형식
(`"YYYY-MM-forecast"` · `"YYYY-MM-actual"` · `"YYYY-plan-NN"`)만 받는다 — 화면 id(`"2026-09-S"`)는
표기 규칙이라 바뀔 수 있고, 서버에서 연·월로 범위를 잡을 수 없다.

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

## 도메인별 전용 저장 API — 왜 `/overseas/state`에서 떼어냈나

`PUT /overseas/state`는 화면이 가진 상태 전부를 한 번에 받는다. 그래서 외주용역 한 시트를 올려도,
메모 하나를 고쳐도 비용·증감사유·외주용역·CP가 매번 같이 올라갔다. 도메인별로 떼어낸 세 개는
저장 단위가 이미 분명한 것들이다:

| API | 저장 단위 | 왜 그 단위인가 |
|---|---|---|
| `PUT /overseas/vendor-rows` | 연구소×버전 **전체 교체** | 업로드마다 행 수가 달라서 upsert만 하면 연구소가 뺀 항목이 남는다 |
| `PUT /overseas/cp-rows` | 연도 × 적용 시점 **전체 교체** | 연 1회 올리는 파일이라 사라진 POC·직급은 지워야 한다. 다른 시점 세트(지난 버전이 보는 값)는 건드리지 않는다 |
| `PUT /overseas/reason-rows` | 키별 **upsert 누적** | 회신을 항목별로 쌓아 가는 자료라, 한 시트를 올렸을 때 다른 항목 사유가 사라지면 안 된다 |

**`PUT /overseas/state`와 이 세 개는 같은 서비스 함수를 부른다**(`services/overseas/{vendor,cp,reason_rows}.py`).
쓰기 규칙이 두 벌이 되면 "같은 데이터, 두 가지 쓰기 규칙"이 되어 반드시 어긋나기 때문이다. 덕분에
프런트가 어느 경로로 저장해도 결과가 같고, 프런트의 `OV_SPLIT_SAVE` 플래그를 끄면 옛 동작(전부
일괄)으로 즉시 되돌아간다.

세 API 모두 `state_token`을 선택적으로 받는다 — 아래 동시 편집 보호와 같은 지문이다.

### CP 계약 교체를 막는 두 가드 (2026-11-18)

CP는 연도 단위 전체 교체라, 업로드 한 번이 그 해 계약을 통째로 지운다. 2026-11-16에 그렇게 날아갔다
— 파싱이 머리글만 맞추고 본문을 한 줄로 읽어서(`Type="P"` · `Projects="J"` · `Total=$1`) POC 3개·직급
행 11개($14,643,313)가 그 한 줄로 바뀌었다. 화면은 "POC 1개를 반영했습니다"라는 성공 메시지를 띄웠고,
숫자가 0이 된 자리는 CP 계약 화면이 아니라 **집계 ② 개발비 탭**(계약금 ÷ 4를 3·6·9·12월에 넣는 곳)
이라 이틀 뒤에야 발견됐다. 사고 낸 화면과 증상 보이는 화면이 달라서 그 사이 아무 신호도 없었다.

가드는 **두 가지를 따로** 본다. 하나로는 막히지 않는다:

| | 보는 것 | 어디서 | 하나로 안 되는 이유 |
|---|---|---|---|
| 가드 1 | 사람이 의도한 교체인가 — POC 수·직급 행 수·총 계약금 중 하나라도 **줄면** 거부(`confirm_shrink=true`면 통과) | `guard_cp_shrink` · 업로드 경로(`cp-rows` · `state`) + 화면 확인창 | 자기들끼리 합이 맞는 **다른** 파일(작년 계약서)을 잘못 올리면 가드 2는 통과한다 |
| 가드 2 | 데이터가 깨졌나 — `Total = 인건비 + Direct R&D + Overhead`(상대오차 1%), 그리고 "합계만 있고 재료가 전부 0"이면 거부 | `validate_cp_rows` · `replace_cp_rows` 안(모든 쓰기 경로) | 화면 확인창은 그 화면에만 있다. 챗봇·일괄 저장·스크립트는 그 자리를 지나지 않는다 |

**금액 크기로는 판단하지 않는다.** "너무 작으면 틀렸다"는 기준은 계약이 진짜 작은 해나 단위를 다르게
적은 해를 사람이 못 넣게 만든다. 양식 **안에서** 합이 안 맞는 것만 거부한다 — 실제 계약서
(`CP_계약_2026.xlsx`)의 POC 3개 모두 그 관계가 반올림 $1 이내로 성립한다(그래서 상대오차로 본다:
$1은 총액 $6.8M의 0.00002%지만 깨진 세트는 100% 어긋난다).

금액만 바꾸는 경로(`POST /overseas/cp-contract-amount`)에는 가드 1이 없다 — POC를 보존하고, 반영 전에
전후 비교를 사람에게 이미 보여준다. 가드 2는 그 경로에도 적용된다(배율은 합계 관계를 보존하므로
정상 계약이 걸리지 않는다).

## 동시 편집 보호 — `state_token`

`GET /overseas/state`가 "지금 상태의 지문"(`state_token`)을 같이 내려준다. 저장할 때 그걸 그대로
돌려보내면, 그 사이 다른 세션(같은 사람의 탭 2개 포함)이 저장했는지 서버가 안다 — 다르면 **409**로
막고 아무것도 쓰지 않는다. 막지 않으면 나중 저장이 전체를 덮고(last-write-wins) 덮인 쪽은 자기
수정이 사라진 걸 알 방법이 없다.

- 지문은 이 화면이 저장하는 테이블들의 `max(타임스탬프)`와 `count(*)`를 묶어 만든다. 스키마 변경은 없다
- 저장 성공 시 **새 지문을 응답에 담아 준다** — 안 주면 화면이 들고 있는 지문이 낡아서 다음 저장이
  자기 자신과 충돌한 것처럼 409가 된다
- `state_token`이 빈 문자열이면 검사하지 않는다(토큰을 모르는 클라이언트 호환 + 서버 쪽 롤백 수단)

## GET/PUT /fx/state

전사 공통 환율 3종(실적·속보·경영, 전부 `fx_rates` 테이블에 `kind` 컬럼으로 구분해 저장)을 한 번에
조회/저장한다. `/overseas/state`와 똑같은 이유로 통째로 주고받는 모양을 택했다.

**저장 부수효과**: `PUT /fx/state`가 성공하면 끝에서 `materialize_applied_rates`가 원본이 있는 모든
연도의 적용 환율 조합을 **전부** 다시 만든다(연도당 기준월 12 × 속보/실적 2 × 평균/말일 2 = 48조합).
조합이 유한해서 전부 만들어 둘 수 있고, 그러면 읽는 쪽(`applied_rows`)은 "없으면?" 분기가 필요
없어진다 — 예전처럼 "이미 있던 조합만" 갱신하면 아무도 아직 안 본 조합(비교 버전의 기준 등)이 영원히
비어 있고, 그걸 0으로 읽어서 금액이 조용히 전부 0이 된다.

## GET /fx/currency-coverage · PUT /fx/rate

연구소 통화와 환율표를 잇는 다리. **왜 필요한가**: 환율이 없는 통화로 연구소를 잡아도 아무것도
막히지 않는다. 집계가 404로 멈추는 건 원/$가 없을 때뿐이고, 현지통화가 없으면
`fxcalc.rate_arrays`가 0으로 두고 쓰는 쪽이 환차 분해를 건너뛴다 — 숫자는 서는데 환산이 빠진
숫자가 되고, 화면에 아무 신호도 안 남는다.

- `GET /fx/currency-coverage?currency=INR&year=2026` → `registered`(그 해 실적·속보 중 하나라도
  있나) · `forecast_months`/`actual_avg_months` · `rate_unit` · `note`
- `PUT /fx/rate` → 한 칸(연월×통화×종류) upsert 후 적용 환율 표 재생성. 응답은 저장 결과가 아니라
  **갱신된 coverage**다(한 칸 넣어도 남는 구멍이 있을 수 있어서)
- `rate`에 넣는 값은 **원/통화가 아니다** — 업로드 엑셀과 같은 형태로 USD는 원/$, EUR·GBP·AUD·NZD는
  $/통화, 나머지는 통화/$(INR이면 95.3)다. `rate_unit`이 그 통화의 방향을 알려준다
- `PUT /overseas/labs/one`(연구소 하나 저장) 응답의 `fx_warning`이 같은 확인을 저장 시점에 자동으로
  한다 — 따로 조회해야 보이게 두면 호출하는 쪽이 빼먹고, 빼먹은 티도 안 난다

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
  `text_translations`에 원문·번역을 같이 저장하고, 다음 호출부터는 그 값을 돌려준다
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
- **답은 저장해서 다시 묻지 않는다**: `PUT /overseas/row-aliases`가 `row_aliases`(account_key='overseas')에 남기고,
  다음 호출부터 그 이름은 `resolved`로 내려간다. "별개 행"이라는 답도 `target_kind:"distinct"`로
  저장한다 — 안 남기면 매달 12개 연구소에 같은 질문을 다시 하게 된다. 잘못 답했으면 다시 PUT해서
  덮어쓰거나 DELETE로 지운다(이미 그 답으로 들어간 과거 데이터는 바뀌지 않는다 — 다시 업로드해야 한다).

화면(`core.js`)은 파일을 읽은 직후 이 API를 불러서, 반영 대기 카드에 `‘Travel Expense’은 이미 있는
행과 같은 행인가요? [같은 행: 출장비 (Travel Exp)] [새 행으로]`를 띄운다. [반영]을 누를 때
고정 계정으로 합친 행은 업로드 값에 미리 더해 버전 반영 규칙(과거월 변경 보호)을 똑같이 받게 하고,
추가 행으로 합친 행은 이름을 정식 이름으로 바꿔 넣는다(집계표는 행 이름으로 묶기 때문에 이름만
맞추면 한 줄로 합쳐진다). 고른 답은 그때 서버에 저장한다 — 반영을 취소하면 답도 남지 않는다.

---

## 증감사유 번역은 **업로드할 때** 해 둔다

증감사유를 저장하면(`PUT /overseas/reason-rows` · `PUT /overseas/state`) 그 자리에서 원문을 번역해
`text_translations`에 넣는다. 조회는 그 값을 읽기만 한다.

**왜 조회할 때가 아닌가**: 조회 때 번역하면 첫 번째로 화면을 연 사람은 LLM을 기다리는 동안 폴백
번역(`core.js`의 `ovTrRule` — 목업 시절 사전·정규식)을 보고, 번역이 도착하면 눈앞에서 값이 바뀐다.
LLM이 실패하면 그 폴백이 그대로 남아서 **사람마다·시점마다 다른 문장**이 보인다. 업로드할 때 한 번
번역해 두면 조회는 항상 같은 값을 읽는다.

**저장을 붙잡지 않는다**: 번역은 응답을 보낸 **뒤에** 돌린다(`prewarm_in_background` → FastAPI
`BackgroundTasks`). `PUT /overseas/state`는 화면의 모든 저장이 지나가는 길이라(메모 한 줄을 고쳐도
여기로 온다) 그 자리에서 LLM을 기다리면 번역과 무관한 저장까지 수 초씩 늘어지고, 키·네트워크 문제가
"저장이 안 된다"로 번진다. 실측: 저장 응답 0.03초, 번역은 2초 뒤 DB에 들어온다.

- 백그라운드 작업은 **자기 세션을 새로 연다** — 요청 세션은 응답과 함께 닫힌다.
- 이미 같은 지침으로 번역해 둔 원문은 건너뛴다(재업로드가 흔하다). 한 번에 `MAX_PREWARM=120`건까지.
- 번역이 실패해도 저장은 끝난 상태다. 조회 때 기존 경로(`POST /overseas/reason-translate`)가 같은
  캐시에 다시 채워 넣는다 — 중복 번역이 아니다.

## 번역 지침이 바뀌면 캐시가 자동으로 미스가 된다 (`text_translations.guide_hash`)

캐시는 원문 해시로만 찾으므로, 담당자가 번역 지침을 바꿔도(`"engineer는 연구원으로 번역한다"`) 이미
캐시된 번역은 옛 규칙 그대로 계속 나온다 — 지침을 등록했는데 번역이 안 바뀌면 담당자는 기능이 안
되는 것으로 읽는다. 그래서 번역할 때 쓴 지침의 지문을 `guide_hash`에 같이 남기고, 조회 시 지금
지문과 다르면 캐시를 못 쓴 것으로 보고 다시 번역해 덮어쓴다.

```
지침 없음           → guide_hash=""  · 기존 캐시 그대로 유효(쓸데없는 재번역 없음)
"engineer→연구원" 등록 → 지문 변경 → 그 원문 재번역 → "엔지니어 2명" → "연구원 2명"
```

- `source_hash`는 그대로 unique다 — **원문 1건 = 번역 1개**를 깨지 않고, 다시 번역하면 덮어쓴다.
- 지문은 `guidelines.guide_fingerprint(db, account_key, "translate")` 한 곳에서 만든다.
- 적용되는 경로가 셋이다: `POST /overseas/reason-translate`(조회), `POST
  /overseas/variance-reason-draft`(초안), 업로드 시 `prewarm_reason_translations`. 세 곳이 같은
  규칙을 써야 같은 원문에 서로 다른 번역이 보이지 않는다.
- 담당자가 `refresh=true`를 기억할 필요가 없어졌다(수동 재번역 경로는 그대로 남아 있다).

---

## POST /overseas/row-merge — 이미 적재된 행 합치기

`row-aliases`와 **짝이지만 하는 일이 다르다**. 별칭은 *앞으로 올라올* 파일의 이름을 맞추고, 합치기는
*이미 들어간* 금액을 옮긴다. 담당자가 `"Other랑 Misc 기타경비로 합쳐줘"` 한 마디로 둘 다 원하므로
보통 같이 호출되지만, **따로 둔 이유는 되돌릴 수 있느냐가 다르기 때문이다** — 별칭은 DELETE로
지우면 그만이고, 합치기는 원본 행이 cascade로 지워져서 `Other`가 얼마였는지 영구히 사라진다.
분리돼 있으면 담당자가 `"앞으로만 적용하고 이번 달은 그대로 둬"`를 고를 수 있다.

```
POST /overseas/row-merge
{ "yyyymm": "202611", "version": "forecast", "source_labels": ["Other", "Misc Fee"],
  "target_kind": "category", "target_key": "misc", "lab_pk": "", "dry_run": true }
→ { "dry_run": true, "target_label": "기타경비",
    "merged": [{ "lab_pk": "SARC:CPU", "source_label": "Other",
                 "source_total": 12.0, "target_before": 40.1, "target_after": 52.4 }, ...],
    "removed_rows": 0, "unconfirmed": false,
    "note": "2건을 '기타경비'(으)로 합칩니다 — 되돌릴 수 없습니다. 확인한 뒤 dry_run=false로 다시 호출하세요." }
```

- **`dry_run=true`가 기본이다.** 되돌릴 수 없는 작업이라 호출부가 먼저 미리보기를 보여주고 사람
  확인을 받은 뒤 `dry_run=false`로 다시 부른다. 챗봇에서는 미리보기가 승인 팝업을 거치지 않는다
  (`chat.py`의 `DRY_RUN_TOOLS`) — 미리보기에도 승인을 받으면 사용자가 두 번 승인하게 된다.
- **지나간 칸은 거절한다.** `yyyymm`이 지금 편성 기준월(`current_basis_yyyymm` = 속보 `yyyymm`
  최댓값)보다 이전이면 400이다. 이미 그 숫자로 보고가 나간 칸을 뒤에서 바꾸면 총액은 그대로인데
  계정별 줄이 달라져서, 그 달 보고서를 다시 뽑으면 기타경비가 늘어난 걸로 나온다. 지나간 달은
  **그 달 파일을 다시 업로드**하는 게 맞다(버전 반영 규칙을 정상적으로 타니까). 전년 실적(`prior`)은
  이 조건에 자동으로 걸려 빠진다. 날짜를 달력에서 계산하지 않는 이유도 같다 — 11월 중순에 이미
  12월 기준이 열렸을 수도 있어서, 기준월은 DB가 정한다.
- **집계 그룹 행에는 합칠 수 없다.** `GROUP_CALC`(개발비(A)·일반운영비·통제성경비 등)과 `pct`는
  하위 항목의 합으로 계산되는 줄이라 값을 꽂으면 리프 합과 어긋난다(`row-match`가 후보에서 그룹
  행을 빼는 것과 같은 이유).
- **source는 추가 행만이다** — `label IS NOT NULL`(고정 계정은 NULL) + `is_derived=False`(파생 행은
  월값을 저장하지 않아 옮길 금액이 없다). 그래서 `Misc`처럼 이미 고정 계정(`en: "Misc."`)으로 적재된
  이름은 애초에 대상이 아니고, 결과에 안 나온다 — 버그가 아니라 합칠 게 없는 것이다.
- **연구소별로 따로 합친다.** 같은 이름이 12개 연구소에 흩어져 있는 게 애초의 문제라서, 섞어 더하면
  그 연구소 실적이 다른 연구소로 옮겨간다. `lab_pk`를 비우면 전체를 돌면서 각각 합친다.
- 추가 행으로 합치면 대상 행의 이름을 정식 이름으로 맞춘다 — 집계표는 행 이름으로 묶기 때문에
  (`ovPLCustomRows`) 이름이 어긋나면 합쳤는데 두 줄로 보인다.
- **분기·연간을 반드시 재계산한다**(`resync_summaries`). `upsert_entry_months`는 MONTH 행만 쓰기
  때문에, 빼먹으면 월은 맞고 분기·연간은 합치기 전 값에 멈춘다.
- **값이 바뀌면 확정을 되돌린다**(`unconfirmed`) — 아래 참고.

## 값이 바뀌면 확정은 거짓이 된다 (`app/shared/core/confirm_state.py`)

화면은 이미 하고 있다(`core.js`의 `ovUnconfirm` — 값을 고치면 자동으로 작성 중으로 돌린다). 서버에서
값을 바꾸는 경로가 생기면 같은 일을 서버도 해야 한다. `unconfirm_account(db, yyyymm, version,
account_key)`가 그 한 곳이고, 확정 일시와 `note`까지 같이 비운다(공통 RULE: 완료 후 수정 시 작성중 +
일자 삭제). 되돌렸는데 `"미업로드 2개 포함 확정"`이 남아 있으면 그게 지금 상태인 줄 읽힌다.

`commit`은 호출부가 한다 — 값 변경과 상태 되돌리기가 한 트랜잭션에 묶여야, 값은 바뀌었는데 확정은
남아 있는 중간 상태가 생기지 않는다. 확정 상태가 아니었으면 아무것도 하지 않는다(확정하지 않은 것을
"확정 취소"로 남기면 이력이 거짓이 된다).

---

## GET/PUT/DELETE /guidelines — 업무 지침 (AI-18)

담당자가 챗봇에 말로 알려주는 팀의 약속을 저장해 두고, **매 요청의 시스템 프롬프트에 넣는다**.
`"사유는 3줄 이내, 금액은 억원으로 써"` → `upsert_guideline` → 다음 답변부터 적용.

```
PUT /guidelines
{ "topic": "금액 단위", "text": "금액은 억원 단위로 쓴다", "account_key": "", "applies_to": "all" }
→ { "topic": "금액 단위", "text": "금액은 억원 단위로 쓴다", "account_key": "", "applies_to": "all",
    "replaced_text": "" }                      // 같은 주제를 덮어썼으면 그 전 문장이 들어온다
```

- **주제(`topic`)가 키다.** 같은 `(account_key, applies_to, topic)`이면 덮어쓰고 그 전 문장을
  `replaced_text`로 돌려준다. 같은 주제 지침이 둘 이상 살아 있으면 모델이 매번 다른 걸 골라서
  지난주와 이번주 보고서 단위가 달라진다. 그래서 `"금액은 백만원으로 써"`가 들어올 때 기존 `"억원"`
  지침이 바로 잡힌다 — 프롬프트에 `[금액 단위]`가 주제와 함께 보이므로 모델이 충돌을 알아차리고
  "바꿀까요?"를 먼저 묻는다(`GUIDELINE_RULES`가 그걸 강제한다).
- **주제가 달라도 뜻이 겹치면 저장하지 않고 되묻는다 (409).** `topic` 덮어쓰기가 막는 건 *같은*
  주제뿐이라, 주제를 조금씩 달리 말하면(`금액 단위` → `금액 표기` → `단위`) 뜻이 겹치는 지침이 셋 다
  살아서 같은 프롬프트에 실린다 — 덮어쓰기가 막으려던 바로 그 상태가 된다. 지침은 **화면이 없어서**
  담당자가 쌓인 걸 눈으로 훑을 자리도 없다. 그래서 새 주제로 들어올 때 `similar_guidelines()`가
  낱말이 겹치는 기존 지침을 찾아 409로 멈추고, 후보와 "무엇을 물어야 하는지"를 같이 돌려준다.

  ```
  PUT /guidelines  { "topic": "금액 표기", "text": "금액은 백만원으로 적는다" }
  → 409  지침을 저장하지 않았다. 등록하려는 주제 '금액 표기'과 뜻이 겹칠 수 있는 지침이 이미 있다:
         - [금액 단위] "금액은 억원으로 쓴다" (전 계정 · 어디서나)
         ... (1) 기존 지침을 바꾸는 것이면 topic을 위에 있는 주제 그대로 넣어 다시 호출해라(덮어쓴다).
             (2) 뜻이 다른 별개 지침이라 둘 다 필요하면 confirm_new_topic=true로 다시 호출해라.
  ```

  - **자동으로 합치거나 거절하지 않는다.** 뜻이 같은지는 낱말 겹침으로 단정할 수 없어서
    (`금액 단위`와 `금액 반영 시점`은 '금액'이 겹쳐도 다른 주제다) 규칙은 **물어볼 가치가 있는
    후보까지만** 좁히고, 뜻을 읽는 일은 모델이 사용자에게 물어 정한다 — `row_match.py`가 행 이름을
    자동 병합하지 않는 것과 같은 판단이다. `쓴다`·`지침` 같은 낱말은 `STOP_WORDS`로 빼고(겹쳐도
    근거가 못 된다), 후보는 세 개까지만 보여준다(넷 다섯 개면 고르기 더 어렵다).
  - **후보 범위는 그 지침과 같은 프롬프트에 실릴 수 있는 것 전부다.** 넓은 걸 등록하면 좁은 지침까지,
    좁은 걸 등록하면 자기를 덮는 넓은 지침까지 본다 — 한쪽만 보면 "전 계정 `[금액 단위]`"가 있는데
    "해외연구소 `[금액 표기]`"를 또 등록하는 걸 놓친다. 대화 프롬프트에는 `chat` 지침만 보여서
    모델 스스로는 `translate`·`variance_reason` 지침과의 겹침을 알 수 없다 — 그게 서버가 봐야 하는 이유다.
  - **묻는 건 승인 카드보다 먼저다.** `chat.py`의 `_guideline_dup_hint`가 `_write_block_hint`에서
    같은 검사를 해서, 승인 버튼을 누른 뒤에 되묻는 일(= 승인 두 번)이 생기지 않게 한다. 엔드포인트의
    409는 MCP 직접 호출 같은 다른 경로를 지키는 backstop이다.
  - `confirm_new_topic`의 기본값이 `false`인 게 핵심이다 — 모델이 아무것도 안 넣으면 막히는 쪽이
    기본이어야, 묻지 않고 쌓는 일이 안 생긴다.
- **적용 범위는 두 축이 겹쳐 걸린다**: 계정(`account_key=""`는 전 계정 공통)과 기능(`applies_to`:
  `all` | `chat` | `variance_reason` | `translate`). 금액 단위는 전 계정·전 기능이고, 번역 용어는
  해외연구소·`translate`에만 걸려야 한다 — `all`로 두면 번역 지침이 집계표 질문 답변까지 바꾼다.
  넓은 것 → 좁은 것 순으로 프롬프트에 들어가서, 좁은 지침이 뒤에 와 더 구체적인 지시로 읽힌다.
- **왜 코드(`chat_prompts.py`)에 안 적는가**: 거기 있는 건 `"숫자를 지어내지 마라"`처럼 틀리면 사고가
  나는 불변 규칙이고, 이 테이블은 팀의 취향이라 담당자가 말로 바꿔야 한다. 코드에 적으면 배포가 필요하다.
  그래서 프롬프트에서도 **불변 규칙 뒤에** 붙인다 — 앞에 두면 담당자 취향이 안전 규칙을 덮어쓰는 것처럼 보인다.
- `account_key`에 FK가 없다. 빈 문자열이 "전 계정 공통"이라는 뜻을 가져야 하는데 FK로는 그 값을
  표현할 수 없고, NULL로 두면 Postgres가 UNIQUE·ON CONFLICT에서 NULL을 서로 다른 값으로 취급해
  같은 공통 지침이 여러 줄로 쌓인다. 비어 있지 않을 때만 엔드포인트가 `accounts.key`를 확인한다.

지침이 실제로 실리는 곳은 세 군데다(읽는 쪽은 `app/shared/core/guidelines.py` 한 곳):

| 어디 | `applies_to` | 넣는 코드 |
|---|---|---|
| 대화 답변 | `chat` | `chat.py`의 `_chat_guidelines` → `build_system_prompt(context, guidelines)` |
| 증감사유 초안 | `variance_reason` | `variance.py` → `draft_reason(..., guidelines, examples)` |
| 회신 번역 | `translate` | `variance.py`·`collect.py` → `translate_batch(..., guidelines)` |

증감사유 초안에는 지침 말고 **지난 수정 사례**도 같이 넘긴다(`past_draft_edits`) — `variance_reasons`의
`ai_draft`(AI 초안)와 `reason_text`(담당자 확정 문장)가 다른 건을 최근 5건 뽑아 예시로 준다. 말로
등록하는 지침으로는 안 잡히는 말투·생략 습관이 여기서 전해진다. 초안을 그대로 쓴 건은 제외한다 —
고친 게 없으면 배울 것도 없고 예시 자리만 차지한다.

주의: **번역 캐시는 지침이 바뀐 걸 모른다.** `text_translations`는 원문 해시로만 찾으므로, 번역 지침을
새로 걸어도 이미 캐시된 번역은 옛 규칙 그대로다. 지침을 바꿨으면 `refresh=true`로 다시 번역해 덮어써야 한다.

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

## PUT /overseas/cp-contract-amount

"CP 2026 계약액 5.2M$로 바꿔줘" — 계약 파일을 다시 올리지 않고 **금액만** 고치는 경로.
업로드(`PUT /overseas/cp-rows`)가 표 전체를 갈아끼우는 것과 달리, 지금 올라와 있는 계약을 그대로
두고 금액 항목만 같은 배율로 움직인다.

- **금액 단위는 `$`**다(K$가 아니다) — 5.2M$는 `5200000`이다. `overseas_cp_rows`는 계약서 금액을
  그대로 담고, 집계 표가 K$로 보이는 건 `cp_quarter_amounts`가 1000으로 나누기 때문이다
  (÷4는 분기 분할, ÷1000이 단위 환산). 응답의 `before`/`after`도 `$`이고, `note`에만 집계 표와
  맞춰 보라고 K$ 환산을 괄호로 같이 적는다
- **두 가지 모드 중 하나만** — `year_total`(연 합계를 이 값으로, POC가 여럿이면 지금 비중대로
  나눠 가진다) 또는 `poc_proj`(+`poc_type`) + `poc_total`(POC 하나만). 둘 다 주거나 둘 다 안 주면
  400이다 — 어느 쪽인지에 따라 금액이 몇 배씩 달라져서 서버가 짐작하면 안 된다
- **움직이는 것**: `poc_total` · `poc_direct` · `poc_ovh` · `poc_cost_sum` · 직급 행의 `cost_year`.
  배율 하나를 전부에 곱하므로 `direct + ovh = total` 같은 관계가 그대로 보존되고, CP 계약 화면의
  직급 행 합과 POC 헤더값도 계속 맞는다
- **안 움직이는 것**: 인원(`dedicated` · `common`)과 맨먼스(`man_month` · `poc_mm_sum`) —
  "계약액이 얼마로 바뀌었다"는 돈 이야기지 인력 구성 이야기가 아니다
- **적용 시점**: `effective_from` 생략 시 지금 편성 중인 기준월. 9월에 바꾸면 9월 이후 버전만 새
  금액으로 분기 정산액이 계산되고 8월 이하는 그대로다
- **응답은 전·후를 같이** 준다(`before` · `after` · `note`). 이 변경의 핵심이 "얼마가 얼마로,
  분기 정산액은 어떻게"라서, 바뀐 숫자만 주면 사용자가 무엇을 승인했는지 되짚을 수 없다
- 그 시점에 계약이 등록돼 있지 않으면 400이다 — 조용히 새 계약을 만들면 업로드한 적 없는 금액이
  집계에 선다

---

## 집계 화면이 서버에 묻는 것 (`/overseas/cp-contract` · `/overseas/confirm-check` · `/overseas/labor-ratio`)

집계표 숫자는 서버가 계산해서 표째로 내려준다(`/overseas/aggregate-table` · `/overseas/lab-tables`).
아래 셋은 표와 별개로 서버가 판정해야 하는 값이다 — 여러 사람이 같이 보는 값이거나
(CP 계약·확정 상태), 화면과 챗봇이 다른 숫자를 말하면 안 되는 값(인건비율)이다.

표 위아래의 **카드**도 같이 서버로 넘어왔다 (2026-11-18 · `/overseas/summary`). 화면 맨 위 히어로
총액과 ③집계 단계 KPI 카드 4장은 그 전까지 브라우저가 localStorage 값 + 브라우저 환율로 직접
계산했다 — 그래서 다른 담당자가 올려 저장한 뒤 새로고침하지 않으면 **같은 화면에서 카드와 집계표가
다른 숫자**를 보여 줬다. 카드 총액은 집계 **① 개발비 · 총계** 표의 합계 행과 같은 값이다(둘 다 CP
분기 정산 반영) — ② 개발비(CP_PL) · ④ 전체 계정 표와는 다르다(그쪽은 보정하지 않은 PL 그대로다). "전실행 대비" 카드는 `version=prev`를
한 번 더 받아 차이만 화면에서 낸다(두 금액 모두 서버 값이라 복제가 아니다).

**인건비율 팝업과 마감 화면 금액도 서버로 넘어왔다** (2026-11-18). 팝업은
`/overseas/labor-ratio-table`, 마감 2단계는 `/overseas/lab-monthly`를 쓴다. 이름이 비슷한
`/overseas/labor-ratio`와 헷갈리지 말 것 — 그쪽은 **원화 · CP 분기 정산 · 한 기간 한 줄**(챗봇이
"인건비율 얼마야"에 답하는 값)이고, 팝업은 **달러(K$) · CP는 올라온 PL 그대로 · 17칸 · 파트까지**다.
단위가 다른 건 팝업이 "환율과 상관없이 비율만 본다"는 화면이기 때문이고, 그래서 환율이 없는 달에도
비율은 나온다. 비율은 기간 **합계끼리** 나눈 값이고 분모가 0·미등록이면 null이다(0%가 아니다).

`/overseas/lab-monthly`가 연구소 묶음이 아니라 **파트(pk)** 단위인 이유: 마감 전표 매핑 단위가
파트다(SARC는 CPU · GPU 두 워크북이고 전표는 과제코드로 갈린다). 집계표 ① 원화 표는 연구소 묶음이라
파트가 없고 `summary`는 합계 하나뿐이라, 둘 다 이 화면이 쓸 모양이 아니다.

**파생 열(계산 열)의 값도 서버가 낸다** (2026-11-18 · `aggregate-table`의 `derived_cols`).
열 자체(이름·계산식)는 전부터 `extra_cols`에 저장됐고 칸 값은 저장하지 않는다 — 계산식으로 매번
계산하는 값이라 그 계산만 프런트(`ovXVal`)에 남아 있었다. 값은 순서가 아니라 **행 키**로 준다:
`k:계정키`(고정 45행) · `l:라벨`(취합에서 추가한 원시 행 — 집계표에서는 라벨로 합쳐진 한 줄) ·
`d:행키`(집계에서 추가한 파생 행). 화면이 45행 사이에 추가 행을 끼워 넣기 때문이다(그 **배치**는
화면 일이라 그대로 뒀다). `null`은 "그 행은 이 열로 묶을 수 없다"는 뜻이다(비율·환율 행) — 0이 아니다.
규칙: 인력 행은 평균, 나머지는 합(`op=avg_months`면 전부 평균), 비율 파생 행은 구간 분자 합 ÷ 구간
분모 합. **취합에서 온 원시 열**(사람이 칸마다 값을 넣는 열)은 계산이 아니라 저장된 값이라 그대로
`/overseas/cost-extras`에서 받아 쓴다. 집계 ① 표에서도 **합치지 않고 원문 그대로** 그린다(2026-11-23)
— 비고(글자) · 사용률(0.825) · 금액이 섞여 오는 열이라 연구소끼리 더하면 뜻이 없고(사용률 18개 합
15.3), 글자는 숫자 변환에 걸려 사라졌다. 같은 이름의 열을 **두 연구소 이상이 쓰면 연구소마다 한
열로 가른다**("비고 (SARC)" · "비고 (SSIR)") — 한 연구소만 쓰는 열은 이름 그대로 둔다. 머리글에는
열 이름만 적고(설명 줄 없음), 누가 적은 값인지는 칸 설명(title)에 둔다.

**비율은 열마다 사람이 한 번 고른다** (2026-11-23 · `extra_cols.fmt`). 같은 사용률을 어떤 연구소는
`0.825`로, 어떤 연구소는 `82.5`로 적어 보내서 값만 보고는 어느 쪽인지 알 수 없다 — 추측해서
일률적으로 ×100 하면 한쪽은 반드시 100배가 된다. 그래서 집계 ① 표 머리글의 고르개로 `그대로` ·
`비율 %(0.8 → 80%)` · `비율 %(80 → 80%)` 중 하나를 고르고, 그 선택만 서버에 남는다. 저장된 칸 값은
형식과 무관하게 **원문 그대로**이고(형식을 바꿔도 값은 안 건드린다), 글자 값은 어떤 형식이든 원문
그대로 그린다. 형식은 **열 이름 단위**다 — 같은 이름 열을 여러 연구소가 쓰면 한꺼번에 바뀐다.

**파생 행(계산 행)의 연산은 사칙연산 네 개다** (2026-11-20 · `formula.op`). `sum_elementwise`(재료
2개 이상) · `sub_elementwise`(`[피감수, 감수]`) · `mul_elementwise`(`[피승수, 승수]`) ·
`div_elementwise`(`[분자, 분모]`) — 전부 같은 달끼리 계산한다. 연간 칸은 연산마다 규칙이 다르다:
합·차는 재료의 연간합으로, 곱은 **월별 결과의 합**으로(연간합끼리 곱하면 12배 규모가 된다),
나누기는 "분자 연간합 ÷ 분모 연간합"으로 낸다. 말로 한 번 계산할 때 쓰는 `combine_series` ·
`calculate`(MCP 도구)도 같은 네 연산을 받는다 — 표에 남길 행은 숫자가 아니라 계산식을 저장한다.

**원화 토글은 "환산한 뒤 계산"이다** (2026-11-20 · `services/extra_rows.scale_materials`). 재료를
먼저 원화로 바꾸고 계산식을 평가한다. 그러면 금액 행은 `A·r ± B·r`로 원화 금액이 되고, 금액÷금액인
비율 행은 `(A·r)/(B·r)`로 환율이 약분돼 **달러 표와 같은 값**이 되고, 금액÷인력은 원/명이 된다 —
결과에 환율을 곱하는 방식(계산 → 환산)은 비율에서 틀린다(0.32에 환율을 곱하면 뜻이 없는 숫자다).
그래서 "이 결과가 비율인가 금액인가"를 따로 판정하지 않는다. 인력(명)·비율(%) 재료에는 환율을
곱하지 않는다. 재료로 쓰는 연구소 집합은 집계표와 같다(`labs.is_summable` — 합계 시트
`SARC:TOTAL` 제외).

`/overseas/lab-tables`의 연구소 줄에는 `key`(연구소 코드, 하위 파트 줄은 pk)가 붙는다 — 한 표를
두 버전으로 받아 같은 연구소끼리 짝지을 때 쓴다. **라벨로 짝지으면 안 된다**: 같은 연구소인데 표마다
라벨이 다르다(개발비 달러 표는 `SRCX(APJT포함)`, 원화 표는 `SRCX`). 합계·소계·환율·"인당 개발비" 줄은
`key`가 빈 문자열이라 그걸로 연구소 줄만 걸러낸다.

- **어느 버전의 표인가는 호출하는 쪽이 정한다** (2026-11-17). 두 집계표 엔드포인트는
  `basis_year` · `basis_month` · `kind`(`forecast`\|`actual`\|`plan`) · `plan_no`를 받고, 버전 해석
  (전실행이 무엇인가 · 경영계획 몇 차수인가)은 증감분석과 **같은 층**(`services/variance.resolve_bases`
  · `load_basis`)을 쓴다. `basis_month`를 빼면 서버가 "DB에 들어 있는 가장 최근 속보"로 떨어진다 —
  그래서 화면이 상단에서 26.08 실적을 골라도 집계표만 최신 속보 숫자를 그리던 버그가 있었다.
  응답의 `kind` · `plan_no` · `version_label`("26.08 실적")로 어느 버전을 받았는지 확인할 수 있고,
  ① 캡션에도 그 라벨이 들어간다. 증감분석(`/overseas/variance-table`)도 같은 파라미터를 받는다 —
| GET | `/overseas/variance-labs` | 증감분석 **연구소별** 요약(19행 × 17기간) — 계정별 표(`variance-table`)의 짝이다. 축이 반대다: 계정별은 "한 연구소의 계정들", 이쪽은 "개발비(매출)의 연구소들". 두 표가 같은 숫자를 보여야 하므로 같은 계산을 쓴다. 줄마다 증감사유 칸의 `[분석 초안]`(`reason_draft`)을 같이 준다 | 화면 |
  화면이 두 화면에 같은 값(`ovVerQ`)을 보낸다.
- **경영계획 차수는 "적용 차수"로 푼다**. 규칙 한 곳: `services/entries.pick_plan_no` —
  ★(`is_final`, 그 연도의 공식 비교 기준) → **확정된(`is_confirmed`) 차수 중 최신** → 가장 최근 차수.
  `GET /overseas/state`는 `plan_no`로 "지금 V\[plan\]이 어느 차수인지"를 같이 준다(빈 문자열 = 그 해 차수 없음 ≠ 0원).
  `PUT /overseas/state`의 `plan_no`는 **어느 차수에 쓸지**이고, 비우면 확정되지 않은 최신 차수에
  쓴다. **확정된 차수는 덮지 않는다** — 저장 전체를 세우지는 않고 건너뛰되 응답의 `plan_skipped`로
  밝힌다(확정을 화면 저장이 덮으면 이미 보고한 경영계획 대비 숫자가 소급해서 바뀐다).

- **CP 계약** — CP 분기 정산은 업로드된 CP PL이 아니라 기준정보 › CP 계약 금액으로 한다(연 계약액
  ÷ 4를 3 · 6 · 9 · 12월에). 그래서 집계는 "계약이 등록됐는지"를 알아야 한다. 미등록일 때
  `registered=false`로 **200**을 주는 게 중요하다 — 404로 주면 화면이 "서버 오류"와 구분하지 못해
  업로드된 CP PL로 그려 버리고, 그러면 계약 없이 숫자가 선다. 미등록이면 그 칸은 0이 아니라 빈칸이고
  합계에서도 빠진다(공통 RULE: CP 계약 미등록이면 CP 정산 칸 빈칸 + 알림).

  **적용 시점(`as_of`)이 필수인 이유**: 분기 정산액은 저장된 값이 아니라 **조회할 때마다 다시
  계산되는** 값이다. 그래서 시점 없이 "지금 계약"을 읽으면, 계약액을 고치는 순간 이미 마감해서
  보고한 과거 버전의 분기 정산액까지 새 계약액으로 다시 계산된다. `overseas_cp_rows`는 시점별
  스냅샷(`effective_from`)으로 저장하고, 읽을 때는 그 버전 시점 이하 가장 최근 세트를 통째로
  쓴다 — 9월에 바꾸면 9월 이후 버전만 새 계약으로 계산되고 8월 이하는 바뀌기 전 값 그대로다
  (연구소 기준정보 `lab_views(as_of=...)`와 같은 규칙). `as_of`를 선택 인자로 두지 않은 이유는,
  빼먹은 호출부 하나가 그 화면만 조용히 소급시키는데 소급은 "숫자가 틀린다"가 아니라 "과거
  보고서가 달라진다"는 형태라 아무도 못 알아채기 때문이다.
- **확정 사전점검** — `blocking`(막는 것)과 `warnings`(알리는 것)를 나눠 준다. 지금 막는 건 적용
  환율(원/$)이 없는 달뿐이다(환산이 안 된 총액이 버전에 박힌다). 미업로드 연구소 · CP 계약 미등록 ·
  사유 미작성 · 재확인 필요는 경고만 하고 확정은 할 수 있게 한다. 같이 내려주는 `note`
  ("미업로드 2개 포함 확정")를 그대로 `PUT /versions/.../accounts/overseas`의 note에 넣으면 버전
  화면에 단서가 남는다. 사유 판정은 `/overseas/variance-reason-check`와 **같은 함수**를 쓴다 —
  두 군데서 따로 세면 화면과 경고가 어긋난다.
- **인건비율 · 인건비 분해도 버전을 받는다** (2026-11-17). `/overseas/labor-ratio`와
  `/overseas/labor-split`이 집계표와 같은 `basis_month` · `kind`(· `plan_no`)를 받고, 인건비율은
  재료 읽기까지 집계표와 **같은 함수**(`_table_inputs`)를 쓴다 — 연구소 추리기·추가 행·환율 규칙이
  갈리면 같은 화면의 집계표와 인건비율이 다른 분모를 쓴다. 응답에 `kind` · `version_label`
  (분해는 `base_label`)이 실려 어느 버전을 받았는지 드러난다. 챗봇 도구 `get_labor_ratio` ·
  `get_labor_split`도 같은 두 값을 받는다(화면 컨텍스트의 `fx_basis_month` · `fx_basis_kind`).
- **인건비율** — 인건비 ÷ 개발비(매출). 분기 · 연간은 월별 비율을 평균 내지 않고 **기간 합계끼리**
  나눈다(달마다 금액이 다르면 월별 비율의 평균은 실제 비율과 다르다). 분모가 0이거나 아직 안 올라온
  줄은 `ratio=null`이다 — 0%가 아니다. 기준은 `/overseas/summary`와 같다(원화 · CP 계약 분기 정산).

---

## 집계 파생 행·열의 "다음 버전에도 유지" (`/overseas/carry-defs`, `/overseas/carry-forward`)

집계 ① 탭에서 만드는 파생 행·열(인당 인건비, 2Q 합계 열 등)은 버전×기준연월 단위로 저장돼서
새 버전을 열면 사라진다. 매달 보는 지표는 따라가야 하고 그 달만 보려고 만든 건 따라가면 안 되므로,
**만든 직후 한 번 묻고** 승인한 것만 `carry_defs`에 남긴다. 새 버전을 열 때(`core.js`의
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
| `analysis_plan` (증감-경영계획) | 증감분석 PLAN 열 ≠ 본사 경영계획, 오차 $0.5K 초과 | `amount`(허용 오차) |
| `analysis_prev` (증감-전실행) | 증감분석 전실행 열 ≠ 전실행(직전 버전), 오차 $0.5K 초과 | `amount`(허용 오차) |
| `analysis_cur` (증감-당월) | 증감분석 당월 열 ≠ **같은 파일의 비용 시트**, 오차 $0.5K 초과 | `amount`(허용 오차) |

- **증감분석 세 열은 블록마다 본다.** 시트는 왼쪽이 연간 · 오른쪽이 당월 덩어리인데, 열(PLAN ·
  M2610A · M2611A)마다 맞댈 상대가 다르다 — 연간 블록은 **12개월 합**, 당월 블록은 **기준월 한
  칸**과 비교한다. 소계·합계 행은 그 행의 하위 계정 합과 비교한다(본사는 소계를 저장하지 않는다).
  당월 열만 DB가 아니라 **같은 업로드의 비용 시트**(`cost_vals`)와 맞댄다: 반영 전이라 비용 값이
  아직 DB에 없고, 보려는 것도 "한 파일 안에서 두 시트가 같은 말을 하는가"다.
- **비교 기준은 서버가 푼다.** 전실행은 직전 버전(11월 속보 → 10월 실적, 1월 속보 → 전년 12월
  실적)이고 경영계획은 적용 차수다 — 둘 다 `services/versions.resolve_bases`를 쓴다(증감분석
  화면과 같은 층). 급변이 쓰는 `base_yyyymm`(전월 **속보**)과는 다른 값이다: 그쪽은 "지난번 편성과
  얼마나 달라졌나"를 보고, 이쪽은 연구소가 옮겨 적은 칸이 본사 장부와 같은지를 본다.

- **급변은 비율과 금액을 둘 다 넘어야 걸린다.** 비율만 보면 잡비 $1K → $2K(+100%)까지 걸려서
  연구소 12개면 경고가 수십 개 뜨고, 그러면 아무도 목록을 안 읽는다.
- **기준은 챗봇으로 바꾸고 저장된다**: `"이제 50% 넘는것만 알려줘"` → `set_anomaly_rule(jump, pct=50)`
  → 승인 팝업 → `PUT /overseas/anomaly-rules`. 해외연구소 전체 공통이라 창을 새로 열어도·다른
  담당자가 봐도 같은 기준이 적용된다. **보낸 항목만 바뀐다**(비율만 보내면 금액 하한은 그대로) —
  사용자가 말하지 않은 값이 덮이면 경고 범위가 엉뚱하게 달라진다. `reset=true`는 기본값 복구.
  변경은 `anomaly_rule_logs`에 before/after로 남는다(기준이 바뀌면 경고 수가 달라지므로).
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
- 다른 18개 계정용 화면 (엔드포인트·테이블은 이미 공유돼 있어서, 화면만 붙이면 됨)
- `/management-plan/entries` 실사용 연결 (버전 목록만 동기화, 금액은 여전히 `overseas/state` 경유)
- `/overseas/state` ↔ `/cost-entries/batch` 중복 저장 정리 (위 "병행 저장" 참고)

## 증감분석 화면 (`/overseas/variance*` · `/overseas/actual-*`)

이 엔드포인트들은 `app/accounts/overseas/endpoints/variance.py`에 따로 모여 있다 — `overseas.py`가
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

`close_account_stages`(account_key='overseas')의 두 값은 서로 다른 일을 한다: `mapping_done`은 실적결산 1차가
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
`display_status`가 "1차 완료"가 되고 `close_account_stages.mapping_done`이 켜져서
증감분석 화면의 [1차 분석] 탭이 열린다. **계정 완료(`account_done`)는 건드리지 않는다** — 전표
매핑은 ERP 약식 1차일 뿐이고, 계정 완료는 PL이 나온 뒤 실적 시뮬레이션 화면의 [확정]
(`version_account_status`)으로만 된다. 1차 없이도 2차를 진행할 수 있다. [1차 고정] 상태에서 1차
완료를 되돌리려 하면 409로 막는다(보고한 1차 값의 근거가 사라진다).

다른 계정은 매핑 완료가 곧 계정 확정이지만, 그 토글은 아직 이 엔드포인트가 하지 않는다 — 전표매핑
계정 화면이 붙을 때 같이 넣어야 중간 상태가 안 생긴다.
