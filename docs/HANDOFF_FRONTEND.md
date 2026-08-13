# 프론트에 내려줄 값 — AutoYard Copilot

> MOVE-AI CHALLENGE 2026 · 현대글로비스 과제
> 화면에서 쓸 데이터를 어떤 모양으로 주는지 정리한 문서다. 규격 전문은 `docs/FRONTEND_CONTRACT.md`.

## 0. 공통 규약

**프론트가 부르는 서버는 스프링 하나뿐이다.** AI(파이썬)는 스프링이 내부적으로 부르므로
프론트는 신경 쓰지 않아도 된다.

```
React ──REST──▶ Spring (:8080) ──내부 호출──▶ Python (AI)
                     │
                     ▼
                    DB
```

서버를 띄우면 아래에서 API 목록을 보고 바로 호출해볼 수 있다.

```
http://localhost:8080/swagger-ui/index.html
```

모든 응답은 이 껍데기로 나간다.

```jsonc
{ "success": true,  "data": { ... } }
{ "success": false, "error": { "code": "C003", "message": "요청한 리소스를 찾을 수 없습니다." } }
```

`success` 만 보고 분기하면 된다.

- 필드명은 `snake_case`
- 시각은 `2026-08-13T14:00:00` (Asia/Seoul 고정)
- enum 은 문자열 그대로 (`BLOCK_CLOSURE`, `HARD`, `PENDING_REVIEW`)
- **`*_label` 이 붙은 필드는 화면에 그대로 출력할 값이다.** enum 을 한글로 바꾸는 매핑은
  서버에서 이미 했으니 화면 쪽에 또 만들 필요 없다
- 색·계산값도 서버가 넣어서 보낸다 (`legend`, `index_*`, `delta_label`, `better`)

### 메서드

| 메서드 | 뜻 | 쓰는 곳 |
|---|---|---|
| `POST` | 새로 만든다 (전송) | 지시 보내기, 배치 계획 저장 |
| `GET` | 읽기만 한다 | 파싱 결과·야드 상태·KPI 조회 |
| `PATCH` | 일부 필드만 바꾼다 | 제약 승인/반려, 계획 승인 |

승인이 `PATCH` 인 이유는 제약 전체를 갈아끼우는 게 아니라 `status` 하나만 바꾸기 때문이다.

## 1. 지시와 제약

### 지시 보내기 — `POST /api/instructions`

화면에서 지시를 입력하고 전송 버튼을 누르면 부르는 API. 이게 시작점이다.

```json
{
  "raw_text": "오늘 14시부터 B02 블록은 도색작업으로 폐쇄해줘. 내일 오전 컷오프 차량은 출고 게이트 가깝게 두고, 철도로 나가는 차량은 동쪽으로 모아줘.",
  "author": "야드관리자A"
}
```

응답은 아래 `GET` 과 **같은 모양**이다. 파싱까지 끝내서 돌려주므로 따로 조회할 필요가 없다.

> 파싱에 2~3초 걸린다. 응답이 올 때까지 기다렸다가 화면을 그리면 된다.

내부적으로는 스프링이 파이썬(AI)을 불러서 파싱한 뒤 결과를 저장하고 돌려준다.
프론트는 그걸 알 필요 없이 **스프링만 부르면 된다.**

### 파싱 결과 다시 불러오기 — `GET /api/instructions/{instructionId}`

승인/반려 후 목록을 갱신할 때처럼, 이미 만들어진 결과를 다시 읽는 용도다.

```json
{
  "instruction": {
    "instruction_id": "INS-001",
    "raw_text": "오늘 14시부터 B02 블록은 도색작업으로 폐쇄해줘. 내일 오전 컷오프 차량은 출고 게이트 가깝게 두고, 철도로 나가는 차량은 동쪽으로 모아줘.",
    "author": "야드관리자A",
    "created_at": "2026-08-13T09:12:00"
  },
  "constraints": [
    { "constraint_id": "C-001", "type": "BLOCK_CLOSURE", "type_label": "블록 폐쇄",
      "summary": "B02 블록을 8/13 14:00부터 폐쇄", "priority": "HARD", "priority_label": "필수",
      "confidence": 0.99, "status": "PENDING_REVIEW", "status_label": "승인 대기",
      "targets": ["B02"], "actions": ["APPROVE", "REJECT"] },
    { "constraint_id": "C-002", "type": "OUTBOUND_PRIORITY", "type_label": "출고 우선순위",
      "summary": "8/13 09:00 이전 컷오프 차량을 게이트 가까이 배치", "priority": "SOFT", "priority_label": "권장",
      "confidence": 0.90, "status": "PENDING_REVIEW", "status_label": "승인 대기",
      "targets": [], "actions": ["APPROVE", "REJECT"] },
    { "constraint_id": "C-003", "type": "VEHICLE_GROUPING", "type_label": "묶음 배치",
      "summary": "철도 출고 차량을 동쪽 구역으로 모음", "priority": "SOFT", "priority_label": "권장",
      "confidence": 0.90, "status": "PENDING_REVIEW", "status_label": "승인 대기",
      "targets": [], "actions": ["APPROVE", "REJECT"] }
  ],
  "unresolved": ["가까이"],
  "requires_confirmation": true
}
```

| 필드 | 설명 |
|---|---|
| `raw_text` | 현장에서 말로 준 지시 원문 |
| `summary` | 제약 한 건을 사람이 읽을 문장으로 바꾼 것. 서버가 만든다 |
| `type` | `BLOCK_CLOSURE` / `VEHICLE_GROUPING` / `OUTBOUND_PRIORITY` 3종뿐 |
| `priority` | `HARD` = 어기면 배치 거부, `SOFT` = 페널티만 |
| `confidence` | 파서 신뢰도 0~1. **임계값 판정은 서버가 이미 `status` 에 반영했다** |
| `status` | `PENDING_REVIEW` / `APPROVED` / `REJECTED` |
| `actions` | 지금 가능한 동작. 이 목록에 있는 것만 유효하다 |
| `unresolved` | 파서가 해석 못 한 표현. 비어있지 않으면 확인이 필요하다는 뜻 |

### 승인 / 반려 — `PATCH /api/constraints/{constraintId}/approve` · `/reject`

```
PATCH /api/constraints/C-001/approve
  body: {"reviewer": "야드관리자A"}

PATCH /api/constraints/C-001/reject
  body: {"reviewer": "야드관리자A", "reason": "블록 번호가 틀림"}
```

반려 사유는 이력에 남는다.

### 화면 1 의 호출 순서

```
지시 입력 → POST /api/instructions            → 제약 카드 목록을 응답으로 받음
승인 버튼 → PATCH /api/constraints/C-001/approve
          → GET /api/instructions/INS-001     → 갱신된 목록 다시 받기
```

## 2. 야드 배치

### `GET /api/plans/{planVersion}/yard-view`

```jsonc
{
  "plan_version": "B0-r1",
  "based_on_version": "B0",
  "grid": { "rows": 50, "cols": 50 },
  "blocks": [
    { "block_id": "B02", "closed": true, "closure_reason": "도색작업",
      "bounds": { "row0": 10, "col0": 0, "row1": 19, "col1": 9 } }
  ],
  "cells": [
    { "row": 12, "col": 3, "slot_id": "B02-L03-D01",
      "state": "MOVED", "vehicle_id": "V-0182", "brand": "B" },
    { "row": 12, "col": 4, "slot_id": "B02-L03-D02", "state": "EMPTY" }
  ],
  "legend": {
    "EMPTY":  { "label": "빈자리",   "color": "#E5E7EB" },
    "KEPT":   { "label": "유지",     "color": "#3B82F6" },
    "MOVED":  { "label": "이동",     "color": "#F97316" },
    "NEW":    { "label": "신규 입고", "color": "#22C55E" },
    "CLOSED": { "label": "폐쇄 구역", "color": "#EF4444" }
  }
}
```

| 필드 | 설명 |
|---|---|
| `grid` | 격자 크기 |
| `blocks[].bounds` | 그 블록이 격자에서 차지하는 사각 범위 |
| `cells[].state` | `EMPTY` / `KEPT` / `MOVED` / `NEW` / `CLOSED`. 어떤 차가 왜 움직였는지는 서버가 판단해 이 값에 담는다 |
| `legend` | state 별 표시 문구와 색. 색을 바꾸고 싶으면 이 값을 바꿔달라고 하면 된다 |

## 3. KPI

### `GET /api/plans/{planVersion}/kpi`

```json
{
  "unit": "CELL",
  "metrics": [
    { "key": "avg_move_distance", "label": "평균 이동거리", "before": 812.0, "after": 502.0,
      "index_before": 100, "index_after": 62, "better": "LOWER", "delta_label": "38% 감소" },
    { "key": "rehandle_proxy", "label": "재취급 Proxy", "before": 14, "after": 9,
      "index_before": 100, "index_after": 64, "better": "LOWER", "delta_label": "5건 감소" }
  ],
  "highlights": [
    { "key": "hard_violations", "label": "Hard 제약 위반", "value": 0, "unit": "건", "tone": "GOOD" },
    { "key": "changed_vehicles", "label": "이동 대상 차량", "value": 42, "unit": "대", "tone": "NEUTRAL" },
    { "key": "calc_millis", "label": "계산 소요", "value": 1740, "unit": "ms", "tone": "GOOD" }
  ]
}
```

| 필드 | 설명 |
|---|---|
| `unit` | 축 라벨에 쓸 단위. 지금은 `CELL`(격자 칸), 실제 거리 상수가 정해지면 `METER` 로 바뀐다 |
| `before` / `after` | 실측값 |
| `index_before` / `index_after` | 기준판을 100 으로 놓은 지수. 장표 9쪽 그래프가 이 값이다 |
| `better` | `LOWER` = 낮을수록 좋은 지표. 증감 표시 방향을 정할 때 쓴다 |
| `delta_label` | 변화량 문구. 서버가 만든다 |
| `tone` | `GOOD` / `WARN` / `NEUTRAL` |

## 4. 브리핑

### `GET /api/plans/{planVersion}/briefing`

```json
{
  "briefing": "B02 블록 폐쇄로 42대가 재배치되었습니다.\n- 평균 이동거리: 812 → 502 (38% 감소)\n- 예상 재취급: 14건 → 9건\n- Hard 제약 위반: 0건",
  "confirmations": [
    { "code": "URGENT_FAR_SLOT", "severity": "WARN",
      "message": "긴급 차량 3대 중 1대(V-0182)가 게이트에서 먼 슬롯에 배정됨",
      "action_hint": "현장 확인 권장" },
    { "code": "OBSERVATION_MISMATCH", "severity": "WARN",
      "message": "사진 인식 결과 B02-L05가 점유 상태로 나왔으나 계획은 비어있음",
      "action_hint": "현장 확인 권장" }
  ]
}
```

`briefing` 은 통짜 문자열이고, **"확인이 필요한 지점"은 `confirmations` 로 따로 내려준다.**
본문에 섞으면 묻히기 때문에 분리했다. `severity` 는 `WARN` / `INFO`.

## 5. Revision Log

### `GET /api/plans`

```json
{
  "plans": [
    { "plan_version": "B0-r1", "based_on_version": "B0",
      "status": "APPROVED", "status_label": "승인됨",
      "triggered_by": { "instruction_id": "INS-001", "raw_text": "오늘 14시부터 B02 블록은…" },
      "approved_by": "야드관리자A", "approved_at": "2026-08-13T09:15:22",
      "summary": "42대 재배치 · 평균 이동거리 38% 감소" }
  ]
}
```

## 6. 지금 상태

**API 는 아직 구현 전이다.** 백엔드에 DB 와 엔티티는 있지만 컨트롤러가 없어서, 현재 응답하는 건
`GET /api/ping` 뿐이다. 위 JSON 을 그대로 목 데이터로 쓰면 되고, API 가 나오면 불러오는 부분만 바꾸면 된다.

payload 모양은 확정이라 나중에 바뀌지 않는다.

## 7. 아직 안 정해진 것

1. **격자 크기** — 야드 화면 격자는 **56×56** 으로 확정(도면: 4+22+4+22+4, 블록 4개 22×22, 주차칸 1,936).
   경로 알고리즘 출력 50×50, 장표 사진 10×10 과의 축척만 정리 중이다
2. **`bounds`** — 좌표와 슬롯 ID 사이 매핑 규칙이 정해져야 채울 수 있다
3. **`unit`** — 격자 한 칸이 몇 m 인지 아직 안 정했다. 그래서 지금은 `CELL` 이고,
   `index_*` 는 상대값이라 지금도 정확하다

## 참고

| 문서 | 내용 |
|---|---|
| `docs/FRONTEND_CONTRACT.md` | payload 규격 전문 |
| `docs/DOMAIN.md` | 도메인 용어(슬롯·블록·depth·재취급) |
| `README.md` | 전체 구조, 실행법 |
