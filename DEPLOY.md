# 배포 (Google Cloud)

## 0. 먼저 결정할 것: DB 를 어디에 둘 것인가

해커톤 기준으로 선택지는 셋이다.

| 방식 | 세팅 시간 | 비용 | 비고 |
|---|---|---|---|
| **A. Neon / Supabase 무료 Postgres** | 5분 | 0원 | 가입 → 접속 URL 복사 → 끝. GCP 밖이지만 Cloud Run 에서 잘 붙는다 |
| **B. Cloud SQL for PostgreSQL** | 20~30분 | 인스턴스 켜둔 시간만큼 과금 (최소 사양 하루 몇백 원) | "GCP 로 구축했다"고 말할 수 있음. 신규 $300 크레딧 있으면 실질 무료 |
| C. GCE VM 에 docker postgres | 30분+ | VM 비용 | 백업·방화벽 다 직접. 해커톤엔 비추 |

**추천: 데모가 목적이면 A, 심사에서 "GCP 아키텍처"를 보면 B.**
어느 쪽이든 앱 코드는 그대로다 — `prod` 프로필이 `DB_URL` / `DB_USERNAME` / `DB_PASSWORD`
환경변수만 읽기 때문에, 값만 바꿔 끼우면 된다.

> 참고: Cloud Run 컨테이너 안에 Postgres 를 같이 띄우는 건 안 된다. 요청이 없으면 인스턴스가
> 죽으면서 데이터가 통째로 날아간다. DB 는 반드시 바깥에 둬야 한다.

---

## 1. 사전 준비 (1회)

```bash
gcloud auth login
gcloud config set project <PROJECT_ID>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
```

Cloud SQL 을 쓸 거면 여기에 `sqladmin.googleapis.com` 도 추가.

리전은 **`asia-northeast3`(서울)** 로 통일한다. DB 와 앱 리전이 다르면 응답이 눈에 띄게 느려진다.

---

## 2-A. Neon / Supabase 를 쓸 경우

1. 가입하고 Postgres 프로젝트 생성 (리전은 가능하면 서울/도쿄)
2. 접속 정보에서 host / db / user / password 를 받아 아래 형태로 조립

```
DB_URL=jdbc:postgresql://<HOST>/<DB>?sslmode=require
DB_USERNAME=<USER>
DB_PASSWORD=<PASSWORD>
```

> 대시보드가 주는 URL 은 보통 `postgresql://user:pw@host/db` 형태다. JDBC 는 앞에 `jdbc:` 가 붙고
> 아이디/비번을 URL 안에 넣지 않는다는 점만 다르다. `sslmode=require` 는 대부분 필수다.

3. 3번(Cloud Run 배포)으로 이동.

---

## 2-B. Cloud SQL 을 쓸 경우

인스턴스 생성 (10분쯤 걸린다. 만들어 놓고 다른 작업 하면 된다):

```bash
gcloud sql instances create chungwoon-db \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --region=asia-northeast3 \
  --storage-size=10GB
```

DB 와 계정 생성:

```bash
gcloud sql databases create chungwoon --instance=chungwoon-db
gcloud sql users create chungwoon --instance=chungwoon-db --password=<강한비밀번호>
```

붙이는 방법이 둘인데, **해커톤이면 (1)로 충분하다.**

**(1) 퍼블릭 IP — 간단**

```bash
gcloud sql instances describe chungwoon-db --format="value(ipAddresses[0].ipAddress)"
```

나온 IP 로:

```
DB_URL=jdbc:postgresql://<IP>:5432/chungwoon
```

Cloud Run 은 나가는 IP 가 고정이 아니라서, 이 경로로 쓰려면 인스턴스의
"승인된 네트워크"를 열어야 한다(`0.0.0.0/0`). **데모가 끝나면 반드시 인스턴스를 삭제하거나 이 설정을
되돌릴 것** — 인터넷 전체에 DB 포트를 여는 설정이다.

**(2) Cloud SQL 커넥터 — 안전한 쪽**

`build.gradle` 에 의존성 추가:

```gradle
implementation 'com.google.cloud.sql:postgres-socket-factory:1.20.1'
```

환경변수:

```
DB_URL=jdbc:postgresql:///chungwoon?cloudSqlInstance=<PROJECT>:asia-northeast3:chungwoon-db&socketFactory=com.google.cloud.sql.postgres.SocketFactory
```

그리고 배포할 때 `--add-cloudsql-instances <PROJECT>:asia-northeast3:chungwoon-db` 를 붙이고,
Cloud Run 서비스 계정에 `roles/cloudsql.client` 를 준다. 퍼블릭 IP 를 열지 않아도 된다.

---

## 3. Cloud Run 배포

**Cloud Run 서비스 2개 + 프론트 정적 호스팅**을 올린다.
순서가 중요하다 — 스프링이 AI 주소를 알아야 하므로 **AI 를 먼저** 올린다.

```
프론트 (React 정적 호스팅)
   │
   ▼
chungwoon-api  (backend/, Spring)   ← 두 번째. AI_BASE_URL 이 필요하다
   ├──▶ DB (Cloud SQL 또는 Neon)
   └──▶ chungwoon-ai (ai/, FastAPI) ← 먼저 배포. URL 을 받아둔다
```

> 앞 버전과 순서가 반대다. 호출 방향이 **Spring → Python** 으로 정해지면서 바뀌었다.

### 3-1. AI (FastAPI)

```bash
cd ai && gcloud run deploy chungwoon-ai --source . --region asia-northeast3 --no-allow-unauthenticated --max-instances 1 --timeout 300 --set-env-vars "GEMINI_MODEL=gemini-2.5-flash"
```

**`--max-instances 1` 을 반드시 붙일 것.** LangGraph 체크포인터가 승인 대기 상태를 그 인스턴스
메모리에 들고 있다. 인스턴스가 2개로 늘어나면 승인 요청이 다른 인스턴스로 가면서
**"승인 눌렀는데 아무 일도 안 일어나는"** 현상이 생긴다. 데모에서 제일 치명적인 사고다.

**`--no-allow-unauthenticated`** — 이 서비스는 스프링만 부르므로 외부에 열지 않는다.
스프링의 서비스 계정에 `roles/run.invoker` 를 주고, 호출할 때 ID 토큰을 붙이면 된다.
설정이 번거로우면 데모 동안은 `--allow-unauthenticated` 로 열어도 되지만, 그러면 누구나
Gemini 를 호출할 수 있으니 데모 끝나고 반드시 내릴 것.

`GEMINI_API_KEY` 는 환경변수 대신 Secret Manager 를 쓴다:

```bash
gcloud run services update chungwoon-ai --region asia-northeast3 --set-secrets "GEMINI_API_KEY=gemini-api-key:latest"
```

확인:

```bash
curl https://<AI URL>/health
```

`{"status":"UP","gemini_enabled":true,...}` 가 나오면 성공.
`gemini_enabled` 가 `false` 면 키가 안 들어간 것이다 — 폴백으로 돌게 되니 데모 전에 꼭 확인.

### 3-2. API (Spring)

`backend/Dockerfile` 이 있으므로 **`backend/` 안에서** 올리면 Cloud Build 가 그걸로 빌드한다.

```bash
cd backend && gcloud run deploy chungwoon-api --source . --region asia-northeast3 --allow-unauthenticated --set-env-vars "SPRING_PROFILES_ACTIVE=prod,DB_URL=<위에서 만든 URL>,DB_USERNAME=<유저>,DB_PASSWORD=<비번>,AI_BASE_URL=https://<3-1 에서 받은 AI URL>"
```

Cloud SQL 커넥터(2-B의 2번)를 쓴다면 `--add-cloudsql-instances <PROJECT>:asia-northeast3:chungwoon-db` 를 추가.

> `--set-env-vars` 는 값에 콤마가 들어가면 파싱이 깨진다. URL 에 `&` 나 `,` 가 있으면
> `--set-env-vars ^@^DB_URL=...` 처럼 구분자를 바꾸거나 Secret Manager 를 쓰는 게 안전하다.
> 비밀번호는 원래 `--set-secrets` 로 넣는 게 맞다.

배포가 끝나면 URL 이 출력된다. 확인:

```bash
curl https://<API URL>/api/ping
```

`{"success":true,"data":{"message":"pong","profile":"prod",...}}` 가 나오면 성공.
Swagger 는 `https://<API URL>/swagger-ui/index.html` 에서 열린다.

> 스프링이 AI 를 부를 때 타임아웃을 넉넉히 잡아야 한다. Gemini 파싱이 2~3초, 이미지 추출이
> 최대 10초까지 걸린다. 기본값(보통 몇 초)이면 정상 응답인데도 끊긴다.

### 3-3. 프론트 (React)

Cloud Run 에 올릴 필요 없다. 정적 빌드라 어디든 올라간다 — Vercel, Firebase Hosting,
Cloud Storage + Cloud CDN 아무거나. 제일 빠른 건 Vercel 이다.

빌드할 때 API 주소를 환경변수로 넣는다 (Vite 기준):

```
VITE_API_BASE_URL=https://<API URL>
```

### 3-4. 마지막에 CORS

프론트 배포 도메인을 `backend/.../global/config/WebConfig.java` 의 `allowedOriginPatterns` 에
추가하고 API 를 다시 배포한다. `https://*.run.app` 과 `https://*.vercel.app` 은 이미 열려 있다.

## 4. 안 될 때 보는 곳

```bash
gcloud run services logs read chungwoon-api --region asia-northeast3 --limit 50
```

```bash
gcloud run services logs read chungwoon-ai --region asia-northeast3 --limit 50
```

자주 겪는 것들:

- **컨테이너가 PORT 에서 리스닝하지 않았다** → Cloud Run 이 주입하는 `PORT` 를 써야 한다.
  스프링은 `application.yaml` 의 `server.port: ${PORT:8080}`, 파이썬은 `ai/Dockerfile` 의
  `--port ${PORT:-8000}` 이 그 역할이다. 지우지 말 것.
- **기동 중 DB 연결 실패로 죽음** → DB_URL/계정 오타, 방화벽(승인된 네트워크), sslmode 누락 순으로 확인.
- **첫 요청이 10초 넘게 걸림** → 콜드 스타트다. 심사 직전에 한 번 호출해서 깨워두거나
  `--min-instances=1` 을 주면 된다 (그만큼 과금).
- **CORS 에러** → 프론트 배포 도메인을 `WebConfig` 의 `allowedOriginPatterns` 에 추가.
- **승인을 눌렀는데 아무 일도 안 일어남** → AI 인스턴스가 2개 이상으로 늘어난 것이다.
  `--max-instances 1` 확인.
- **스프링이 AI 를 부르다 타임아웃** → 기본 타임아웃이 짧아서 그렇다. 파싱 30초, 이미지 추출 60초 정도로.
- **AI 응답은 오는데 결과가 이상함** → `/health` 의 `gemini_enabled` 확인. `false` 면 키가 안 들어가
  폴백(미리 저장된 예시 JSON)으로 돌고 있는 것이다.

## 5. 데모 끝나고

과금이 계속되지 않게 정리한다.

```bash
gcloud run services delete chungwoon-api --region asia-northeast3
gcloud run services delete chungwoon-ai --region asia-northeast3
gcloud sql instances delete chungwoon-db
```

Cloud SQL 을 퍼블릭 IP + `0.0.0.0/0` 으로 열어뒀다면, 인스턴스를 지우지 않을 거라도
**승인된 네트워크 설정은 반드시 되돌릴 것.**
