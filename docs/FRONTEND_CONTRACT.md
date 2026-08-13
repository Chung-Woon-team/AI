# 프론트 전달 형식

> 대상: 화면 담당 (React). 프론트가 부르는 서버는 스프링 하나뿐이다 — AI(파이썬)는 스프링이 내부적으로 부른다.
> 원칙: **프론트는 계산하지 않는다. 문자열도 조립하지 않는다.** 서버가 그릴 수 있는 형태로 내려준다.

왜 이렇게 하나 — 문구를 고칠 때 프론트를 안 건드려도 되고, 화면 담당이 도메인 규칙
(depth 가 뭔지, HARD/SOFT 가 뭔지)을 몰라도 화면을 그릴 수 있다. 해커톤에서 병렬로 일하려면 이게 제일 싸다.

## 화면 1 — 제약 생성과 검증 (장표 5쪽)

`GET /api/instructions/{instructionId}`

```jsonc
{
  "instruction": {
    "instruction_id": "INS-001",
    "raw_text": "오늘 14시부터 B02 블록은 도색작업으로 폐쇄해줘. 내일 오전 컷오프 차량은 출고 게이트 가깝게 두고, 철도로 나가는 차량은 동쪽으로 모아줘.",
    "author": "야드관리자A",
    "created_at": "2026-08-13T09:12:00"
  },
  "constraints": [
    {
      "constraint_id": "C-001",
      "type": "BLOCK_CLOSURE",
      "type_label": "블록 폐쇄",              // ← 서버가 만든 표시용 문구
      "summary": "B02 블록을 8/13 14:00부터 폐쇄",  // ← 카드 본문. 프론트는 그대로 출력만
      "priority": "HARD",
      "priority_label": "필수",
      "confidence": 0.99,
      "status": "PENDING_REVIEW",
      "status_label": "승인 대기",
      "targets": ["B02"],
      "actions": ["APPROVE", "REJECT"]        // ← 버튼을 뭘 그릴지도 서버가 지정
    }
  ],
  "unresolved": ["가까이"],                    // 모호해서 해석 못 한 표현
  "requires_confirmation": true
}
```

**프론트가 할 일**: 카드 반복 렌더 + `actions` 대로 버튼 그리기 + 클릭 시 아래 호출.

```
PATCH /api/constraints/C-001/approve   body: {"reviewer": "야드관리자A"}
PATCH /api/constraints/C-001/reject    body: {"reviewer": "...", "reason": "..."}
```

`confidence` 는 그대로 숫자로 보여줘도 되고 게이지로 그려도 된다. **임계값 판정은 서버가 이미
`status` 에 반영**했으니 프론트가 `confidence < 0.85` 같은 비교를 하지 말 것.

## 화면 2 — 야드 배치 (장표 6쪽)

`GET /api/plans/{planVersion}/yard-view`

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

**프론트가 할 일**: `cells` 를 돌면서 `state` 로 `legend` 에서 색을 찾아 칠하기. 끝.
어떤 차가 왜 움직였는지 판단하는 로직은 전부 서버에 있다.

## 화면 2 — KPI 전후 비교 (장표 6·9쪽)

`GET /api/plans/{planVersion}/kpi`

```jsonc
{
  "unit": "CELL",                 // 상수 정해지면 "METER" 로 바뀐다. 프론트는 이 값을 축 라벨에 쓴다
  "metrics": [
    { "key": "avg_move_distance", "label": "평균 이동거리",
      "before": 812.0, "after": 502.0,
      "index_before": 100, "index_after": 62,     // ← 장표 9쪽 막대그래프가 이 두 값
      "better": "LOWER", "delta_label": "38% 감소" },
    { "key": "rehandle_proxy", "label": "재취급 Proxy",
      "before": 14, "after": 9,
      "index_before": 100, "index_after": 64,
      "better": "LOWER", "delta_label": "5건 감소" }
  ],
  "highlights": [
    { "key": "hard_violations", "label": "Hard 제약 위반", "value": 0, "unit": "건", "tone": "GOOD" },
    { "key": "changed_vehicles", "label": "이동 대상 차량", "value": 42, "unit": "대", "tone": "NEUTRAL" },
    { "key": "calc_millis",     "label": "계산 소요",      "value": 1740, "unit": "ms", "tone": "GOOD" }
  ]
}
```

`index_*` 를 서버가 넣어주는 이유 — **격자 1칸이 몇 m 인지 아직 안 정했는데, 지수는 상대값이라
지금도 정확하다.** 장표 9쪽 그래프를 지금 당장 그릴 수 있다.

`better: "LOWER"` 는 화살표 방향/색을 결정하라고 주는 값이다. 프론트가 "이건 낮을수록 좋은
지표던가?"를 몰라도 된다.

## 브리핑

`GET /api/plans/{planVersion}/briefing`

```jsonc
{
  "briefing": "B02 블록 폐쇄로 42대가 재배치되었습니다.\n- 평균 이동거리: 812 → 502 (38% 감소)\n...",
  "confirmations": [
    { "code": "URGENT_FAR_SLOT", "message": "긴급 차량 3대 중 1대(V-0182)가 게이트에서 먼 슬롯에 배정됨",
      "severity": "WARN" },
    { "code": "OBSERVATION_MISMATCH", "message": "사진 인식 결과 B02-L05가 점유 상태로 나왔으나 계획은 비어있음",
      "severity": "WARN", "action_hint": "현장 확인 권장" }
  ]
}
```

브리핑은 통짜 문자열이지만 **"확인이 필요한 지점"은 구조화해서 따로 내려준다.** 프론트가 이걸
경고 배지로 눈에 띄게 그릴 수 있어야 하기 때문이다 (장표 5절 "눈에 띄게 짚어준다").

## 이동 애니메이션 (선택, 나중에)

`GET /api/plans/{planVersion}/paths`

```jsonc
{ "steps": 348, "paths": { "V-0001": [[0,0],[0,1],[0,2]] } }
```

⚠️ 500대면 수십만 좌표라 응답이 무겁다. **화면 2가 다 돌아간 뒤에 붙일 것.** 없어도 데모는 성립한다.

## Revision Log

`GET /api/plans`

```jsonc
{ "plans": [
  { "plan_version": "B0-r1", "based_on_version": "B0",
    "status": "APPROVED", "status_label": "승인됨",
    "triggered_by": { "instruction_id": "INS-001", "raw_text": "오늘 14시부터…" },
    "approved_by": "야드관리자A", "approved_at": "2026-08-13T09:15:22",
    "summary": "42대 재배치 · 평균 이동거리 38% 감소" }
] }
```

## 공통 규칙

1. **모든 응답은 `ApiResponse` 로 감싼다** — `{"success": true, "data": {…}}`.
   실패는 `{"success": false, "error": {"code": "...", "message": "..."}}`.
   프론트는 `success` 만 보고 분기하면 된다.
2. **필드명은 `snake_case`**, 시각은 `2026-08-13T14:00:00` (Asia/Seoul 고정).
3. **`*_label` 이 붙은 필드는 그대로 화면에 출력**하라고 주는 값이다. 프론트에서 enum 을
   한글로 바꾸는 매핑 테이블을 만들지 말 것 — 두 곳에서 관리하면 반드시 어긋난다.
4. **색은 `legend` 에 실어 보낸다.** 디자인이 바뀌면 서버 상수 한 줄만 고친다.
5. **갱신은 폴링 또는 버튼.** 웹소켓은 하루짜리에 과하다. 승인 버튼을 누르면 해당 화면만
   다시 GET 하면 충분하다.

## 아직 안 정한 것

- `bounds` (블록의 격자 상 위치) — 좌표↔슬롯 매핑이 정해져야 채울 수 있다.
- 격자 크기 — 경로 알고리즘 출력은 50×50, 장표 사진은 10×10 이라 축척이 안 맞아 정리 중.
- 색상값 — 위는 임시값. 디자인 담당이 정하면 교체.
