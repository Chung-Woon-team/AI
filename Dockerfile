# AI 서비스(FastAPI) Cloud Run 배포용.
FROM python:3.12-slim

# uv 를 공식 이미지에서 바이너리만 가져온다.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 의존성 레이어 먼저 - 소스만 바뀔 때 재빌드가 빠르다.
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

COPY src ./src
COPY app ./app
RUN uv sync --no-dev

ENV PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

# Cloud Run 이 PORT 를 주입한다. 로컬은 8000.
EXPOSE 8000
# 워커 1개 고정 — LangGraph 승인 대기 상태가 프로세스 메모리에 있어서,
# 워커가 여러 개면 thread_id 를 못 찾는다. 배포 시 --max-instances 1 도 같이.
CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
