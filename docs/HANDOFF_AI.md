# AI 파트 안내 — AutoYard Copilot

> MOVE-AI CHALLENGE 2026 · 현대글로비스 과제
> 이 문서만 보고 시작할 수 있게 정리했다. 확정 안 된 것은 8번에 모아뒀다.

## 0. 세 줄 요약

1. AI 는 슬롯을 결정하지 않는다. 입구(파싱·비전)와 출구(브리핑)만 맡고, 배치 계산과 Hard 제약 검증은 코드가 한다.
2. 만들 건 기능 4개고, 전부 "지저분한 입력 → 정해진 JSON" 변환이다.
3. 출력은 Pydantic 검증을 통과해야 다음 단계로 넘어간다. 스키마는 `ai/src/autoyard/schemas.py` 에 이미 있다.

## 1. 맡는 부분

| # | 기능 | 입력 | 출력 (이미 있는 클래스) |
|---|---|---|---|
| ① | 제약 파서 | 현장 자연어 지시 | `ParseResult` |
| ② | 선하증권 추출 | B/L 이미지 | `BillOfLadingExtraction` |
| ③ | 점유 인식 | 주차장 사진 | `GridObservation` |
| ④ | 결과 브리핑 | 배치 결과 + KPI | 문자열 + `confirmations[]` |

①·④ 와 되묻기(ask_human)는 LangGraph 그래프의 노드로 들어간다 (6번).

### 이걸 FastAPI 로 감싸서 노출한다

프론트가 React 라서 브라우저가 파이썬 함수를 직접 못 부른다. 그래서 구조가 이렇다.

```
React ──▶ Spring (:8080, 공개 API) ──▶ Python FastAPI (:8000, 내부 전용)
```

스프링만 파이썬을 부른다. 외부에 열지 않는다.

| Method | Path | 하는 일 | 응답 |
|---|---|---|---|
| `POST` | `/internal/parse` | 자연어 지시 → 제약 JSON | `ParseResult` + `thread_id` |
| `POST` | `/internal/resume` | 승인/반려 결과를 그래프에 전달 | 재배치 결과 |
| `POST` | `/internal/replan` | 승인된 제약으로 부분 재배치 | `ReplanResult` |
| `POST` | `/internal/extract/bl` | 선하증권 이미지 → 문서 데이터 | `BillOfLadingExtraction` |
| `POST` | `/internal/extract/grid` | 주차장 사진 → 점유 상태 | `GridObservation` |
| `POST` | `/internal/brief` | 배치 결과 + KPI → 브리핑 | `briefing` + `confirmations[]` |
| `GET` | `/health` | 살아있는지 확인 | — |

응답 스키마는 전부 `schemas.py` 에 이미 있다. FastAPI 는 Pydantic 을 그대로 쓰므로
`response_model=ParseResult` 로 붙이면 검증까지 자동으로 된다.

**FastAPI 껍데기는 이미 만들어져 있다.** 경로와 요청/응답 모양만 잡힌 스텁이라 지금은 `501` 을 돌려준다.
백엔드 담당이 AI 구현을 기다리지 않고 연동 코드를 붙일 수 있게 먼저 만들어둔 것이다.

```
ai/app/
├── main.py            진입점, /health
└── routers/
    ├── parse.py       /internal/parse, /internal/resume
    ├── extract.py     /internal/extract/bl, /internal/extract/grid
    └── plan.py        /internal/replan, /internal/brief
```

각 함수 안의 `raise HTTPException(501, ...)` 을 실제 구현으로 바꾸면 된다.
요청 모델(`ParseRequest` 등)에 필요한 필드가 빠져 있으면 자유롭게 추가하면 된다.

띄우기:

```bash
uv run uvicorn app.main:app --reload --port 8000
```

http://localhost:8000/docs 에서 바로 호출해볼 수 있다.

## 2. 지켜야 하는 세 가지

나머지는 자유롭게 해도 되는데, 이 셋은 프로젝트의 존재 이유라 어긋나면 안 된다.

### ① AI 가 숫자를 만들지 않는다

브리핑에 나오는 숫자·차량ID·블록ID는 알고리즘 결과에서만 가져온다.
"평균 812m → 502m" 같은 값을 모델이 계산하거나 추정하면 안 된다. 받은 걸 문장으로 옮기기만 하면 된다.

> 심사에서 "이 숫자 어디서 나온 거냐"고 물었을 때 "AI 가 만들었다"가 되면 프로젝트 전체가 무너진다.
> 반대로 "전부 알고리즘 결과"라고 답할 수 있으면 그게 우리 강점이 된다.

### ② 문서에 없는 건 만들지 않는다

특히 선하증권. VIN 이 범위(`...0001 TO ...0060`)로만 적혀 있는데 여기서 60개를 뽑아내라고 하면
모델은 지어낸다. 개별 차량 행 전개는 코드가 하니까 문서에 적힌 그대로만 뽑으면 된다.

### ③ 없는 ID 는 통과시키지 않는다

유효한 블록·브랜드·구역 목록을 항상 프롬프트에 같이 넣고, 그 밖의 값이 나오면 `unresolved` 로 뺀다.
ID 형식 검증은 `autoyard/ids.py` 가 자동으로 한다.

## 3. 환경 세팅

```bash
git clone <repo> && cd Chung-Woon/ai
uv sync --extra dev
```

```bash
cp .env.example .env
```

`.env` 에 `GEMINI_API_KEY` 를 넣는다. 없어도 돌아가게 짜야 한다 (7번 폴백).

확인:

```bash
uv run pytest
```

AI 서버 띄우기:

```bash
uv run uvicorn app.main:app --reload --port 8000
```

`tools/streamlit_app.py` 가 하나 있는데 **제품 화면이 아니다.** 프론트는 React 로 간다.
혼자 결과를 눈으로 확인할 때 쓰는 개발용 도구다.

```bash
uv run streamlit run tools/streamlit_app.py
```

`uv` 가 없으면 `pip install uv` 또는 https://docs.astral.sh/uv 참고.
여기서 막히면 바로 연락. 환경 문제로 시간 버리는 게 제일 아깝다.

## 4. 지금 있는 것 / 없는 것

```
ai/
├── src/autoyard/
│   ├── ids.py        ✅ ID 규칙 정규식 (V-0001, B03, B03-L02-D04, C-001, INS-001)
│   ├── schemas.py    ✅ Pydantic 스키마 전부
│   ├── config.py     ✅ .env 로딩, 키 없으면 gemini_enabled=False
│   └── (없음)        ❌ gemini 클라이언트, 프롬프트, 그래프, 최적화
├── app/
│   ├── main.py       ✅ FastAPI 진입점, /health 동작
│   └── routers/      ⚠️ 경로만 잡힌 스텁 — 전부 501 반환
├── tools/            ✅ 개발용 Streamlit (제품 화면 아님)
├── tests/            ✅ 11개 통과
└── Dockerfile        ✅ uvicorn, --workers 1
```

빈 칸부터 시작하면 된다. 스키마는 일단 그대로 써보고, 안 맞으면 고쳐도 된다.
다만 고칠 때 자바 쪽도 같이 고쳐야 하니 그때 한 번 알려주면 된다 (9번).

## 5. 기능별 상세

### ① 제약 파서

이런 문장이 들어온다 (장표 5쪽).

```
"오늘 14시부터 B02 블록은 도색작업으로 폐쇄해줘.
 내일 오전 컷오프 차량은 출고 게이트 가깝게 두고, 철도로 나가는 차량은 동쪽으로 모아줘."
```

이렇게 나오면 된다.

```python
from autoyard.schemas import ParseResult

ParseResult(
    instruction_id="INS-001",
    constraints=[
        {
            "constraint_id": "C-001",
            "type": "BLOCK_CLOSURE",
            "target": {"block_ids": ["B02"]},
            "time_window": {"start": "2026-08-13T14:00:00", "end": None},
            "priority": "HARD",
            "confidence": 0.99,
        },
    ],
    unresolved=["가까이"],       # 애매해서 해석 못 한 표현
    requires_confirmation=True,
)
```

지원하는 `type` 은 3종뿐이다. 그 외 의도는 거절한다 (장표 8쪽 "미지원 Intent 는 거절").

| type | 지시 예시 | target 에 필요한 것 |
|---|---|---|
| `BLOCK_CLOSURE` | "3번 블록 폐쇄해" | `block_ids` |
| `VEHICLE_GROUPING` | "브랜드 B 는 서쪽으로 모아줘" | `attribute`+`values` 또는 `vehicle_ids` |
| `OUTBOUND_PRIORITY` | "내일 컷오프 차량은 게이트 가깝게" | `filter` |

HARD / SOFT 는 이렇게 나누면 된다 — 어기면 배치가 아예 거부돼야 하는 건 `HARD`(폐쇄),
"하면 좋은" 건 `SOFT`(묶음 배치).

`confidence` 는 `.env` 의 `CONFIDENCE_THRESHOLD`(기본 0.85) 미만이면 승인 대기로 붙잡힌다.
"가까이", "많이" 처럼 애매한 표현은 confidence 를 낮추고 `unresolved` 에도 넣는다.

스키마가 자동으로 잡아주는 것들 — 제약 ID 형식, 블록 ID 형식(`3번블록` 이면 에러),
time_window 순서, type 별 필수 target, `unresolved` 가 있는데 `requires_confirmation=False` 면
자동으로 True 로 교정.

### ② 선하증권 추출

샘플 3장이 있다 (`NXR-USN-NTD-2608110{1,2,3}`). 집계값만 뽑으면 된다.

```python
from autoyard.schemas import BillOfLadingExtraction
```

| 문서에 적힌 것 | 넣을 필드 |
|---|---|
| `VIN RANGE: ...0001 TO ...0060` | `vin_range_from` / `vin_range_to` (60개로 펼치지 않는다) |
| `60 DRIVEABLE / 0 TOW` | `driveable_count` / `tow_count` |
| `UNITS 017 & 031 REQUIRE TOW` | `tow_unit_numbers=[17, 31]` (이렇게 콕 집어 적힌 경우만) |
| `SEQ 041-100` | `discharge_seq_from=41`, `discharge_seq_to=100` |
| `P2 · STANDARD EV BLOCK` | `unloading_priority="P2"` |
| `EV-A / ROWS 01-06` | `target_yard_zone` (문자열 그대로) |

한 문서에 품목이 여러 줄일 수 있다. DOC 03 이 그런 경우로,
전기버스 10대(3.25m) + 대형트럭 8대(3.80m) = 18 UNITS 다. 높이가 다르니 `cargo_lines[]` 로 나눠 담는다.

이 문서들은 숫자가 세 군데서 교차 검증된다.

| | UNITS | DRIVEABLE+TOW | SEQ 구간 크기 |
|---|---|---|---|
| DOC 01 | 60 | 60+0 | 041~100 = 60 |
| DOC 02 | 42 | 40+2 | 101~142 = 42 |
| DOC 03 | 18 | 18+0 | 001~018 = 18 |

스키마가 이걸 자동으로 검사한다. OCR 이 60 을 6 으로 잘못 읽으면 바로 걸린다.
정답지 없이도 추출 정확도를 잴 수 있다는 뜻이라, 장표 8쪽의 `Entity Exact Match` 지표를
이걸로 만들면 깔끔하다.

### ③ 점유 인식

주차장 사진 → 격자 점유 상태. 자유 서술이 아니라 좌표 JSON 으로 고정한다.

```python
from autoyard.schemas import GridObservation

GridObservation(
    source_type="FIXED_CAMERA",
    captured_at="2026-08-13T10:30:00",   # 업로드 시각이 아니라 촬영 시각
    block_id="B03",
    grid=[{"row": 0, "col": 0, "occupied": True}, ...],
    confidence=0.93,
    requires_confirmation=False,
)
```

좌표(row, col)까지만 반환하면 된다. 슬롯 ID 매핑은 알고리즘 파트가 하는데 규칙이 아직 없다 (8번).
자신 없는 사진은 `requires_confirmation=True` 로 담당자 확인 대상으로 넘긴다.

### ④ 결과 브리핑

알고리즘이 계산한 KPI 와, 코드가 대조해서 넘긴 "확인 필요" 목록이 들어온다.
판정하거나 바꾸지 않고, 문장으로 옮기고 강조만 한다.

두 부분으로 나눠서 준다.

```jsonc
{
  "briefing": "B02 블록 폐쇄로 42대가 재배치되었습니다.\n- 평균 이동거리: 812 → 502 (38% 감소)\n...",
  "confirmations": [
    { "code": "URGENT_FAR_SLOT", "severity": "WARN",
      "message": "긴급 차량 3대 중 1대(V-0182)가 게이트에서 먼 슬롯에 배정됨" }
  ]
}
```

`confirmations` 를 따로 빼는 이유는 프론트가 이걸 경고 배지로 그리기 때문이다.
통짜 문장에 섞으면 묻힌다.

여유가 되면 자체 검증을 하나 넣으면 좋다 — 브리핑에 나온 ID·숫자가 입력에 실제로 있었는지
대조하고 없으면 걸러내기. 원칙 ① 을 코드로 강제하는 장치라 심사에서 설명하기도 좋다.

## 6. 그래프에서의 위치

```
START → ①parse_instruction → ◇confidence 낮거나 애매?
                              ├─ 예 → ②ask_human → 다시 ①
                              └─ 아니오 → ③validate(코드) → ④human_approval(사람 승인)
                                          → ⑤run_replan(코드) → ⑥check_consistency(코드)
                                          → ⑦brief(AI) → END
```

- **AI 노드**: ① parse_instruction, ② ask_human, ⑦ brief
- **코드 노드**: ③ validate, ⑤ run_replan, ⑥ check_consistency
- **사람**: ④ human_approval — 그래프가 여기서 멈추고 승인을 기다린다

State 필드: `instruction_text`, `constraint_set`, `clarification`, `validation_result`,
`approved`, `replan_result`, `consistency_issues`, `briefing`

### ⚠️ `thread_id` — 승인 흐름의 핵심

④ 에서 그래프가 **멈춰서 승인을 기다린다.** 그 대기 상태는 파이썬 프로세스 메모리(체크포인터)에 있다.
스프링이 나중에 "승인됐다"고 알려줄 때 **어느 그래프를 깨울지** 지목해야 하므로,
`/internal/parse` 응답에 `thread_id` 를 실어 보내야 한다.

```
1. Spring → POST /internal/parse
             ← { "thread_id": "th_a1b2", "constraints": [...] }
2. Spring 이 thread_id 를 DB 에 저장
3. 사용자가 승인
4. Spring → POST /internal/resume  { "thread_id": "th_a1b2", "approved": true }
             ← 재배치 결과
```

**파이썬 인스턴스는 1개로 고정한다.** 여러 개로 늘어나면 승인 요청이 엉뚱한 인스턴스로 가서
"그런 thread 없다"가 된다. 배포할 때 `--max-instances 1`.

체크포인터는 일단 `MemorySaver` 로 충분하다. 프로세스가 죽으면 대기 중인 승인이 날아가지만,
데모 범위에서는 문제되지 않는다.

## 7. 폴백 — 데모가 멈추면 안 된다

심사 중에 API 장애나 네트워크 문제로 화면이 죽는 게 제일 큰 사고다.

- `GEMINI_API_KEY` 가 없으면 `config.settings.gemini_enabled == False` 가 된다.
  이때는 미리 저장해둔 예시 JSON 을 반환하도록 한다
- API 호출 실패 시 1회 재시도, 그래도 안 되면 폴백
- 스키마 검증 실패 시 1회 repair 재시도(에러 메시지를 모델에 되돌려주기), 그래도 안 되면 수동 입력으로 전환

`GEMINI_MODEL` 기본값은 `gemini-2.5-flash` 로 잡아뒀다. 바꿔도 된다.

## 8. 아직 확정 안 된 것

이건 우리가 정해서 알려줘야 하는 것들이다. 혼자 추측해서 만들다 버리는 일 없게, 막히면 물어보면 된다.

1. **좌표 ↔ 슬롯 매핑** — ③ 의 출력은 `(row, col)` 인데 도메인은 `B03-L02-D04` 다. 변환 규칙이 아직 없다.
   경로 알고리즘 CSV 는 50×50 인데 장표 사진은 10×10 이라 축척도 안 맞아서 정리 중이다
2. **추출 항목이 확정이 아니다** — 알고리즘에 넣을 값이 정해지면 ② 의 필드가 바뀔 수 있다
3. **`next_mode` / `departure_cutoff_at` / `priority`** — 선하증권에 없는 값이다.
   어디서 채울지 미정 (합성 생성 / 별도 CSV / UI 입력)
4. **차량 ID** — 경로 알고리즘은 `Car 1`, 우리 규칙은 `V-0001`. 어디서 변환할지 미정

## 9. 고칠 때 규칙

- 스키마를 고치면 자바 엔티티도 같이 고쳐야 한다. 같은 이름·같은 enum 값을 쓰고 있어서
  한쪽만 바뀌면 연동이 조용히 깨진다
- `ids.py` 는 되도록 그대로 두는 게 좋다. 자바 PK 와 계약 문서 두 개에 다 퍼져 있다
- 스키마에 `extra="forbid"` 가 걸려 있다. 모델이 스키마에 없는 필드를 뱉으면 에러가 난다.
  환각을 잡으려고 일부러 그렇게 해뒀다. 프롬프트를 먼저 고치고 스키마를 안 고치면 터지는데,
  에러 메시지에 새 필드명이 그대로 찍히니 그걸 보고 추가하면 된다. 이 설정은 유지한다

## 10. 시작 순서

1. `uv sync` → `uv run pytest` 로 환경 확인
2. `schemas.py` 훑어보기 — 특히 `ParseResult`, `BillOfLadingExtraction`
3. ① 제약 파서부터. 장표 5쪽 예시 문장 하나가 제약 3건으로 파싱되면 절반은 끝난 거다
4. 폴백 경로를 파서 만들 때 같이 넣기. 나중에 붙이려면 까먹는다
5. **`/internal/parse` 스텁을 실제 구현으로 바꾸기.** 백엔드 담당이 여기부터 붙일 수 있어서,
   나머지 기능보다 이게 먼저 도는 게 팀 전체로는 빠르다
6. ④ 브리핑 — 알고리즘 결과 형식은 `docs/API_CONTRACT.md` 참고
7. ②·③ 은 그다음. 그래프 조립은 ①·④ 가 돌아가고 나서

## 참고 문서

| 문서 | 내용 |
|---|---|
| `docs/DOMAIN.md` | 엔티티 구조, 선하증권 추출 파이프라인 |
| `docs/API_CONTRACT.md` | 백엔드 연동, 경로 알고리즘 CSV 포맷 |
| `docs/FRONTEND_CONTRACT.md` | 화면에 내려줄 payload |
| `README.md` | 전체 구조, 실행법 |
