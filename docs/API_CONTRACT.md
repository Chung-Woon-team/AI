# Python ↔ Spring 연동 계약 (v0 초안)

> ⚠️ **v0 = 경로 알고리즘 코드를 받기 전 초안.** 아래 §4 의 질문에 답이 나오면 확정한다.
> 양쪽이 이 문서 하나만 보고 병렬로 작업할 수 있게 하는 게 목적이다.

## 1. 역할 경계와 호출 방향

```
React (브라우저)
   │  REST  ← Swagger 로 문서화된 유일한 공개 API
   ▼
Spring Boot (:8080)      저장 · 승인이력 · Revision Log · API 관문
   │  REST (내부 호출)
   ▼
Python FastAPI (:8000)   Gemini 파싱 · 검증 · 슬롯 배정 · 경로 계산 · 브리핑
```

| | Spring | Python |
|---|---|---|
| 맡는 것 | 저장, 승인 이력, 조회 API, **외부에 열리는 유일한 창구** | 계산과 판단 전부 |
| 안 맡는 것 | 계산·판단 일절 없음 | 영속화 없음 (메모리/캐시만) |

**호출 방향은 Spring → Python 단방향이다.** 파이썬은 스프링을 부르지 않는다.
프론트는 스프링만 알면 되고, 스웨거 문서 하나로 API 전체가 설명된다.

> 파이썬을 서버(FastAPI)로 띄워야 하는 이유 — 브라우저는 파이썬 함수를 직접 못 부른다.
> 프론트가 React 라서 파이썬 앞에 HTTP 창구가 필요하다.

> `ai/tools/streamlit_app.py` 는 제품 화면이 아니다. AI 담당자가 혼자 테스트할 때 쓰는
> 개발용 도구로만 남긴다.

### Swagger

스프링에 `springdoc-openapi 3.0.0` 을 붙여뒀다. 서버를 띄우고 아래로 들어가면
API 목록을 보고 바로 호출해볼 수 있다.

```
http://localhost:8080/swagger-ui/index.html
http://localhost:8080/v3/api-docs          (JSON)
```

## 2. Spring 이 외부에 여는 API (React 가 호출)

전부 `ApiResponse` 로 감싸서 나간다: `{"success": true, "data": ...}` / `{"success": false, "error": {...}}`

### 지시 · 제약

| Method | Path | 용도 |
|---|---|---|
| `POST` | `/api/instructions` | 자연어 지시 원문 저장 → `instruction_id` 발급 |
| `POST` | `/api/instructions/{id}/constraints` | 파싱 결과 저장. **항상 `PENDING_REVIEW` 로 들어간다** |
| `GET` | `/api/constraints?status=APPROVED` | 최적화 입력용. 승인된 것만 |
| `PATCH` | `/api/constraints/{id}/approve` | body: `{"reviewer": "..."}` |
| `PATCH` | `/api/constraints/{id}/reject` | body: `{"reviewer": "...", "reason": "..."}` |

```jsonc
// POST /api/instructions
{ "raw_text": "오늘 14시부터 3번 블록은 도색작업으로 폐쇄해.", "author": "야드관리자A" }
// → { "instruction_id": "INS-001" }

// POST /api/instructions/INS-001/constraints
{
  "constraints": [{
    "constraint_id": "C-001",
    "type": "BLOCK_CLOSURE",
    "target": { "block_ids": ["B03"] },
    "value": null,
    "time_window": { "start": "2026-08-13T14:00:00", "end": null },
    "priority": "HARD",
    "confidence": 0.99
  }],
  "unresolved": [],
  "requires_confirmation": true
}
```

### 야드 상태 (최적화 입력)

| Method | Path | 용도 |
|---|---|---|
| `GET` | `/api/yard/state` | 블록·슬롯 전체 + 최신 승인 판의 배치 |
| `GET` | `/api/vehicles` | 차량 마스터 |

```jsonc
// GET /api/yard/state
{
  "plan_version": "B0",
  "grid":   { "size": 56, "road_width": 4, "block_size": 22 },
  "blocks": [{ "block_id": "B03", "zone_code": "QC-HOLD",
               "origin_row": 30, "origin_col": 4, "grid_size": 22,
               "lane_count": 22, "depth_per_lane": 11,
               "closed": false, "closure_reason": null }],
  "slots":  [{ "slot_id": "B03-R30-C04", "block_id": "B03",
               "grid_row": 30, "grid_col": 4,
               "lane": 0, "depth": 0, "access_side": "NORTH", "status": "OCCUPIED" }],
  "placements": { "V-0001": "B03-R30-C04" }
}
```

### 계획 저장 (최적화 출력)

| Method | Path | 용도 |
|---|---|---|
| `POST` | `/api/plans` | 재배치 결과 저장. `DRAFT` 로 들어감 |
| `PATCH` | `/api/plans/{planVersion}/approve` | `human_approval` 통과 시 |
| `PATCH` | `/api/plans/{planVersion}/reject` | |
| `POST` | `/api/plans/{planVersion}/briefing` | AI 브리핑 문장 첨부 |
| `GET` | `/api/plans` | Revision Log 목록 (최신순) |
| `GET` | `/api/plans/{planVersion}` | 한 판 상세 (placements + moves + kpi) |

```jsonc
// POST /api/plans
{
  "plan_version": "B0-r1",
  "based_on_version": "B0",
  "triggered_by_instruction_id": "INS-001",
  "placements": { "V-0182": "B02-R04-C30" },
  "moves": [{
    "vehicle_id": "V-0182",
    "from_slot": "B03-R30-C04",
    "to_slot":   "B02-R04-C30",
    "sequence": 1,
    "reason": "B03 폐쇄(C-001)",
    "distance_meters": 412.5      // ← 경로 알고리즘 산출
  }],
  "kpi": {
    "avg_move_distance": 502.0, "rehandle_proxy": 9.0,
    "hard_violations": 0, "changed_vehicles": 42,
    "plan_retention_rate": 91.6, "calc_millis": 1740
  },
  "kpi_before": { "avg_move_distance": 812.0, "...": "..." },
  "consistency_issues": [
    { "code": "OBSERVATION_MISMATCH", "slot_id": "B03-R30-C09",
      "message": "사진은 점유, 계획은 비어있음" }
  ]
}
```

### 관측

| Method | Path | 용도 |
|---|---|---|
| `POST` | `/api/observations` | 사진 인식 결과 저장 |
| `GET` | `/api/observations/latest?blockId=B03` | 관측 최신성 표시용 |

## 2-B. Python FastAPI 가 여는 내부 API (Spring 이 호출)

외부에 노출하지 않는다. 스프링만 부른다.

| Method | Path | 용도 | 응답 |
|---|---|---|---|
| `POST` | `/internal/parse` | 자연어 지시 → 제약 JSON | `ParseResult` + `thread_id` |
| `POST` | `/internal/resume` | 승인/반려 결과를 그래프에 전달 | 재배치 결과 |
| `POST` | `/internal/replan` | 승인된 제약으로 부분 재배치 | `ReplanResult` |
| `POST` | `/internal/extract/bl` | 선하증권 이미지 → 문서 데이터 | `BillOfLadingExtraction` |
| `POST` | `/internal/extract/grid` | 주차장 사진 → 점유 상태 | `GridObservation` |
| `POST` | `/internal/brief` | 배치 결과 + KPI → 브리핑 문장 | `briefing` + `confirmations[]` |
| `GET` | `/health` | 살아있는지 확인 | — |

응답 스키마는 전부 `ai/src/autoyard/schemas.py` 에 이미 정의돼 있다.

### ⚠️ `thread_id` — 승인 흐름의 핵심

LangGraph 의 `human_approval` 은 **그래프가 멈춰서 승인을 기다리는** 구조다. 그 대기 상태는
파이썬 프로세스 메모리(체크포인터)에 있다. 스프링이 나중에 "승인됐다"고 알려주려면
**어느 그래프를 깨울지** 지목해야 한다.

```
1. Spring → POST /internal/parse
             ← { "thread_id": "th_a1b2", "constraints": [...] }
2. Spring 이 thread_id 를 DB 에 저장 (PlanRevision 또는 Instruction 에)
3. 사용자가 승인
4. Spring → POST /internal/resume  { "thread_id": "th_a1b2", "approved": true }
             ← 재배치 결과
```

**파이썬 인스턴스는 반드시 1개로 고정해야 한다.** 여러 개로 늘어나면 승인 요청이 엉뚱한
인스턴스로 가서 "그런 thread 없다"가 된다. Cloud Run 이면 `--max-instances 1`.

### 타임아웃

스프링에서 파이썬을 부를 때 기본 타임아웃(보통 몇 초)으로 두면 끊긴다.

| 호출 | 예상 소요 | 권장 타임아웃 |
|---|---|---|
| `/internal/parse` | 2~3초 (Gemini) | 30초 |
| `/internal/extract/*` | 3~10초 (이미지) | 60초 |
| `/internal/replan` | 2초 이내 (목표) | 30초 |
| `/internal/brief` | 2~3초 | 30초 |

### 파이썬이 죽어 있을 때

스프링은 파이썬 없이도 **조회 API 는 정상 동작해야 한다.** 이미 저장된 계획·제약·이력을
보여주는 데는 파이썬이 필요 없다. 파이썬 호출이 실패하면 그 요청만
`{"success": false, "error": {"code": "AI_UNAVAILABLE", ...}}` 로 돌려주고, 나머지 화면은 살려둔다.

## 3. 규칙

1. **필드명은 `snake_case`.** 자바는 카멜케이스지만 JSON 경계에서는 파이썬 쪽에 맞춘다. 스프링에서 `@JsonNaming` 으로 변환.
2. **ID 형식은 양쪽이 같은 정규식을 쓴다.** 파이썬 `autoyard/ids.py`, 자바 PK 컬럼. 한쪽만 바꾸면 조용히 깨진다.
3. **enum 값은 문자열 그대로.** `BLOCK_CLOSURE`, `HARD`, `PENDING_REVIEW`. 숫자 코드 금지.
4. **시각은 ISO-8601 로컬시각** (`2026-08-13T14:00:00`), 타임존은 `Asia/Seoul` 고정.
5. **파이썬은 스프링이 죽어도 돌아야 한다.** 저장 실패는 UI에 경고만 띄우고 데모는 계속 진행 — 심사 중 백엔드 하나 때문에 전체가 멈추면 안 된다.

## 4. 경로 알고리즘 출력 포맷 (`df_schedule.csv`)

알고리즘 파트가 이 모양으로 준다고 확정됨. 코드는 추후 수령.

### 실측한 구조

```
컬럼: "Car 1" … "Car 100"   (100열)
행:   타임스텝 (348행, 인덱스 암묵적)
셀:   "(row, col)" 문자열 또는 공백
```

| 항목 | 실측값 |
|---|---|
| 격자 | 50 × 50 (row·col 모두 0~49) |
| 차량 | 100대 |
| 타임스텝 | 348 |
| 출발점 | 전 차량 `(0, 0)` 공통 |
| 도착점 | **`(49, 0)` 과 `(49, 49)` 단 2곳** (홀짝 번갈아) |
| 공백 셀 | 아직 진입 전 (Car 100 은 t=198 에 진입) |
| 정차 후 | 최종 좌표가 끝까지 반복됨 (최대 270스텝) |

### 이 포맷을 우리 모델로 바꾸는 규칙 (어댑터)

```
CSV 한 열(차량 1대)
  → 연속 중복 제거          … 정차 후 반복 구간 제거
  → 이동한 칸 수 (n_cells)
  → distance_meters = n_cells × METERS_PER_CELL     ← 상수 필요
  → travel_seconds  = n_steps × SECONDS_PER_STEP    ← 상수 필요
  → 최종 좌표 → slot_id                              ← 매핑 규칙 필요
  → 좌표열 자체 → 시각화용 (DB 저장 안 함)
```

### ⛔ 이게 없으면 진행이 막힌다

1. **`METERS_PER_CELL`** — 격자 1칸의 실제 크기(m). **이 값이 없으면 `distance_meters` 와
   `avg_move_distance` KPI 를 만들 수 없다.** 장표 6쪽의 "평균 이동거리 812m → 502m" 가 이 상수에 걸려 있다.
2. **`SECONDS_PER_STEP`** — 1 타임스텝의 실제 시간(초). 거리만 쓸 거면 없어도 되지만,
   "몇 분 걸리는 작업인가"를 보여주려면 필요.
3. **경로 CSV 격자 정합** — 야드 격자는 도면대로 **56×56** 으로 확정됐고 `(row, col)` ↔ `slot_id`
   변환은 `YardGrid.slotId` / `ids.make_slot_id` 로 끝났다. 남은 건 `df_schedule.csv` 의 50×50 좌표와
   장표 사진의 10×10 격자를 56×56 으로 어떻게 옮기냐다.
4. **차량 ID** — CSV 는 `Car 1`, 도메인 규칙은 `V-0001`. 컬럼명을 바꾸거나 어댑터에서
   `Car N → V-{N:04d}` 로 변환. **어느 쪽이든 한 곳에서만** 하기로 정할 것.

### 확인이 필요한 관측 2건

- **목적지가 2곳뿐** — 100대가 전부 `(49,0)`/`(49,49)` 로만 간다. 슬롯 배정 결과라면
  목적지가 100개여야 한다. 현재는 **경로 탐색 실험**으로 보이며, 슬롯 배정(③)과 어떻게
  이어붙일지 정해야 한다.
- **동시 점유** — 그 두 셀에 각각 255·210 스텝 동안 여러 대가 겹쳐 있다. 게이트 대기열이라
  의도된 것인지, 충돌 회피가 아직 없는 것인지 확인 필요.

### 포맷 자체에 대한 제안 (선택)

와이드 포맷은 500대로 늘리면 컬럼이 500개가 된다. 롱 포맷이 다루기 쉽다:

```csv
step,vehicle_id,row,col
0,V-0001,0,0
1,V-0001,0,1
```

다만 이건 어댑터로 흡수 가능하니, 알고리즘 파트가 편한 쪽으로 해도 된다.

**성능 전제 (중요)**

슬롯 후보마다 경로 알고리즘을 부르면 호출 수가 `차량 수 × 후보 슬롯 수` 로 폭발한다.
장표 목표는 500대 2초. 그래서 기본 전략은 **거리 행렬 사전 계산**이다.

```
앱 기동 시 1회:  게이트/블록 간 거리 행렬 계산 → 메모리 캐시
배치 계산 중:    조회만 (O(1))
블록 폐쇄 시:    해당 블록 관련 항목만 무효화
```

경로 좌표열은 **승인된 판만, 시각화용으로** 따로 보관한다. 500대 × 수백 스텝을 매 판마다
DB에 넣으면 감당이 안 된다.

## 5. 아직 안 정한 것

- 좌표 ↔ 슬롯 매핑 규칙 (§4-2)
- 경로 좌표열 저장 위치 — 스프링에 별도 테이블 vs 파이썬 메모리 vs 파일
- 인증 — 지금은 `permitAll`. 데모라면 그대로 두고, 필요하면 공유 시크릿 헤더 하나면 충분하다.
