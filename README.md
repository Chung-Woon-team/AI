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

현재 들어있는 것은 초기세팅까지다:

```
ai/
├── src/autoyard/        도메인 로직
│   ├── ids.py           ID 규칙 정규식 (V-0001, B03, B03-L02-D04) — 스프링 PK 와 같은 규칙
│   ├── schemas.py       Pydantic 스키마 — AI 출력의 통과 관문
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

라우터는 **경로와 요청/응답 모양만 잡힌 스텁**이라 지금은 `501` 을 돌려준다.
백엔드 담당이 연동 코드를 먼저 붙일 수 있게 껍데기부터 만들어 둔 것이다.
LangGraph 그래프(7노드), Gemini 클라이언트, 최적화, 경로 어댑터는 아직 없다.

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
