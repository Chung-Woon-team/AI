# AutoYard Copilot (Chung-Woon)

MOVE-AI CHALLENGE 2026 · 현대글로비스 과제 — 현장 자연어 지시를 검증된 야드 배치로 바꾸는 시스템.

> **핵심 원칙: LLM 이 슬롯을 결정하지 않는다.**
> AI 는 파이프라인의 입구(파싱·비전)와 출구(브리핑)에만 있고, 배치 계산과 Hard 제약 검증은 결정론적 코드가 한다.

## 구조

```
Chung-Woon/
├── backend/          Spring Boot 4.0.7 / Java 21  — 저장 · 승인이력 · API 관문
├── ai/               Python 3.12 (uv)             — Gemini 파싱 · 검증 · 최적화 · 경로 · 브리핑
├── frontend/         React (예정)                 — 화면
├── docs/             설계 문서 (아래 참고)
├── docker-compose.yml  로컬 Postgres
└── .run/             IntelliJ 공용 실행 설정
```

**저장소는 파트별로 셋이다.** 각 폴더가 각 저장소의 루트고, `docs/` 는 세 곳에 같이 들어간다.

| 폴더 | 저장소 |
|---|---|
| `backend/` | https://github.com/Chung-Woon-team/Back-End |
| `ai/` | https://github.com/Chung-Woon-team/AI |
| `frontend/` | https://github.com/Chung-Woon-team/Front-end |

```
React ──REST──▶ Spring (:8080) ──내부 호출──▶ Python FastAPI (:8000)
                     │
                     ▼
                    DB
```

**스프링이 유일한 공개 API 창구다.** 프론트는 스프링만 알면 되고, 파이썬은 스프링만 부른다.
API 목록은 서버를 띄우고 http://localhost:8080/swagger-ui/index.html 에서 볼 수 있다.

## 배포 주소 (Cloud Run · asia-northeast3)

| 파트 | URL |
|---|---|
| 프론트 | https://autoyard-copilot-front-145786632792.asia-northeast3.run.app |
| 백엔드 | https://back-end-git-145786632792.asia-northeast3.run.app |
| AI | https://autoyard-copilot-ai-145786632792.asia-northeast3.run.app |

- 백엔드 Swagger: https://back-end-git-145786632792.asia-northeast3.run.app/swagger-ui/index.html
- AI 헬스체크: https://autoyard-copilot-ai-145786632792.asia-northeast3.run.app/health
  → `gemini_enabled` 가 **true** 여야 실제 Gemini 를 탄다. false 면 키가 안 들어간 것이고,
    그 상태에서도 서버는 200 으로 **고정 더미 데이터**를 돌려준다.

스프링에는 `AI_BASE_URL` 로 위 AI 주소를 넣는다. 파이썬에는 백엔드 주소를 넣을 필요가 없다 —
호출 방향이 Spring → Python 단방향이다.

## 야드 격자

도면대로 **56 × 56** (4 + 22 + 4 + 22 + 4). 22×22 블록 4개, 주차칸 1,936 · 도로칸 1,200.
자세한 규칙은 [docs/DOMAIN.md](docs/DOMAIN.md) 의 "야드 격자" 절에 있다.

## 작업 방식

**브랜치를 파서 PR 로 올리고, 다른 사람이 리뷰·머지한다.** `main` 에 직접 푸시하지 않는다. 이슈는 쓰지 않는다.

```bash
git switch -c feat/무엇을-하는지
# 작업
git push -u origin feat/무엇을-하는지
gh pr create --fill        # 또는 GitHub 웹에서
```

브랜치 이름은 `feat/` · `fix/` · `docs/` · `chore/` 중 하나로 시작한다.

## 문서

| 문서 | 내용 |
|---|---|
| [docs/DOMAIN.md](docs/DOMAIN.md) | 엔티티 10개 설계와 근거 |
| [docs/API_CONTRACT.md](docs/API_CONTRACT.md) | Spring ↔ Python 연동, 경로 알고리즘 CSV 포맷 |
| [docs/FRONTEND_CONTRACT.md](docs/FRONTEND_CONTRACT.md) | 서버 → 프론트 payload |
| [docs/HANDOFF_AI.md](docs/HANDOFF_AI.md) | AI 담당자에게 전달용 |
| [docs/HANDOFF_FRONTEND.md](docs/HANDOFF_FRONTEND.md) | 프론트 담당자에게 전달용 |
| [DEPLOY.md](DEPLOY.md) | Google Cloud Run 배포 |

## 실행 — backend

IntelliJ 로 **루트 폴더**를 열면 실행 목록에 `Chung-Woon [local]` 이 잡힌다. 그대로 실행.

터미널:

```bash
cd backend && ./gradlew bootRun
```

- 기본 프로필 `local` = **인메모리 H2**. 설치할 것 없음. 끄면 데이터는 사라진다.
- 확인: http://localhost:8080/api/ping
- H2 콘솔: http://localhost:8080/h2-console (JDBC URL `jdbc:h2:mem:chungwoon`, user `sa`, pw 없음)

배포와 같은 Postgres 로 돌리려면 (Docker Desktop 이 켜져 있어야 함):

```bash
docker compose up -d
```

그다음 IntelliJ 의 `Chung-Woon [dev-postgres]`, 또는 `cd backend && ./gradlew bootRun --args='--spring.profiles.active=dev'`

> **호스트 포트는 5432 가 아니라 `5433`** 이다. 이 PC 에서 다른 프로젝트(`lightrip-db`)가
> 5432 를 쓰고 있어서 옮겼다. DB 툴로 붙을 때 `localhost:5433` / `chungwoon` / `chungwoon`.
> 컨테이너 안은 그대로 5432 라 컨테이너끼리 통신할 때는 5432 를 쓴다.

| 프로필 | DB | ddl-auto | 용도 |
|---|---|---|---|
| `local` (기본) | H2 인메모리 | `create-drop` | 평소 개발 |
| `dev` | 로컬 Postgres | `update` | 배포 전 검증 |
| `prod` | 환경변수 주입 | `update` | Cloud Run |

## 실행 — ai

```bash
cd ai && uv sync --extra dev
```

```bash
cd ai && uv run pytest
```

AI 서버 띄우기 (http://localhost:8000):

```bash
cd ai && uv run uvicorn app.main:app --reload --port 8000
```

API 문서는 http://localhost:8000/docs . 살아있는지 확인은 `/health`.

> 개발용 Streamlit 화면이 `ai/tools/streamlit_app.py` 에 있다. **제품 화면이 아니다** —
> 프론트는 React 로 가고, 이건 AI 담당자가 혼자 결과를 눈으로 볼 때 쓰는 도구다.
> `cd ai && uv run streamlit run tools/streamlit_app.py`

`.env.example` 를 `.env` 로 복사해서 쓴다. **`GEMINI_API_KEY` 가 없어도 폴백 경로로 동작**하도록
설계했다 — 심사 중 네트워크나 API 장애로 데모가 멈추면 안 되기 때문.

현재 구조는 이렇다:

```
ai/
├── src/autoyard/        도메인 로직
│   ├── ids.py           ID 규칙 정규식 (V-0001, B03, B03-L02-D04) — 스프링 PK 와 같은 규칙
│   ├── schemas.py       Pydantic 스키마 — AI 출력의 통과 관문
│   ├── yard_grid.py     야드 격자 상수 (블록 4개 × 85칸)
│   ├── mapf.py          시공간 A* 경로 탐색
│   ├── replan.py        재배치 계산 + KPI 산출 (결정론적 — AI 아님)
│   ├── briefing.py      브리핑 문장 조립 (순수 함수 — 숫자가 든 줄은 전부 여기서 만든다)
│   ├── gemini_client.py Gemini 호출 (파싱 / 선하증권 / 격자 인식 / 브리핑)
│   ├── fallback.py      Gemini 를 못 쓸 때 돌려줄 값
│   └── config.py        .env 로딩
├── app/                 FastAPI (스프링이 부르는 내부 API)
│   ├── main.py          진입점, /health
│   └── routers/
│       ├── parse.py     /internal/parse, /internal/resume
│       ├── extract.py   /internal/extract/bl, /internal/extract/grid
│       └── plan.py      /internal/replan, /internal/brief
├── tools/               개발용 (배포 이미지에 안 들어감)
└── Dockerfile           Cloud Run 배포용 (uvicorn)
```

구현된 것: `/internal/parse`, `/internal/extract/bl`, `/internal/extract/grid`,
`/internal/replan`, `/internal/brief`. 아직 `501` 스텁인 것은 `/internal/resume` 하나뿐이다
(LangGraph 체크포인터가 없어서 되돌아갈 대기 상태가 없다).

`/internal/brief` 는 **숫자를 만들지 않는다.** 요청으로 받은 KPI 6개 수치로 파이썬이 불릿
6줄을 조립하고(`briefing.build_briefing_draft`), Gemini 에게는 숫자가 없는 첫 문장(headline)과
부연 한 줄(note)만 시킨다. 그래서 LLM 이 수치를 지어낼 경로 자체가 없다. 키가 없거나 호출이
실패하면 초안을 그대로 돌려주고 `source="FALLBACK"` 으로 표시한다 — 어떤 경우에도 5xx 를 내지
않는다.

## 팀 규칙

**응답 형식** — 컨트롤러는 `ApiResponse` 로 감싼다.

```java
return ApiResponse.ok(dto);                        // {"success": true, "data": {...}}
throw new BusinessException(ErrorCode.NOT_FOUND);  // 자동으로 404 + 에러 바디
```

에러가 늘면 `ErrorCode` 에 상수만 추가. 컨트롤러에서 try-catch 하지 말 것.

**엔티티** — `BaseTimeEntity` 를 상속하면 `createdAt`/`updatedAt` 이 자동으로 채워진다.

**ID 규칙** — `V-0001` / `B03` / `B03-L02-D04` / `C-001` / `INS-001`.
자바 PK 와 파이썬 `ids.py` 가 **같은 규칙**이다. 한쪽만 고치면 연동이 조용히 깨진다.

**브랜치**

```
main            ← 배포 브랜치. 직접 푸시 금지
└── feat/<기능명>  ← 각자 작업 → PR → main
```

**충돌 줄이는 법** — 도메인 폴더를 사람별로 나눠 가지면 거의 안 부딪힌다. 대신 `build.gradle`,
`application*.yaml`, `SecurityConfig`, `pyproject.toml` 은 공용이니 건드리기 전에 한마디 하고 만질 것.

## 알아둘 것

- 윈도우 CLI 의 `java` 는 11, `JAVA_HOME` 만 21 이다. Gradle 은 `JAVA_HOME` 을 보므로 문제없지만
  `java -jar` 를 직접 쓸 일이 생기면 `& "$env:JAVA_HOME\bin\java.exe" -jar ...` 로 부를 것.
- **"Port 8080 was already in use" 로 죽으면** — `bootRun` 을 강제 종료했을 때 자바 프로세스가
  남아 포트를 물고 있는 경우다. devtools 가 앱을 별도 프로세스로 띄우기 때문에 잘 생긴다.

  ```bash
  netstat -ano | findstr :8080
  ```

  거기 나온 PID 를 `taskkill /PID <pid> /F` 로 죽이면 된다. 급하면 포트만 바꿔도 된다 —
  `./gradlew bootRun --args='--server.port=18080'`
