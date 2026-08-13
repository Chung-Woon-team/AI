"""AI 서비스 진입점 (FastAPI).

스프링만 이 서버를 부른다. 외부에 열지 않는다.

    React ──▶ Spring (:8080, 공개 API) ──▶ 여기 (:8000, 내부 전용)

로컬 실행:
    uv run uvicorn app.main:app --reload --port 8000

API 문서:
    http://localhost:8000/docs
"""

from __future__ import annotations

from fastapi import FastAPI

from app.routers import extract, parse, plan
from autoyard import __version__
from autoyard.config import settings

app = FastAPI(
    title="AutoYard AI Service",
    description="자연어 지시 파싱 · 선하증권 추출 · 점유 인식 · 배치 최적화 · 브리핑",
    version=__version__,
)

app.include_router(parse.router)
app.include_router(extract.router)
app.include_router(plan.router)


@app.get("/health", tags=["health"])
def health() -> dict:
    """스프링이 이 서버가 살아있는지 확인할 때 부른다."""
    return {
        "status": "UP",
        "version": __version__,
        # 키가 없으면 폴백 경로로 도는 중이라는 뜻. 데모 전에 확인할 것.
        "gemini_enabled": settings.gemini_enabled,
        "model": settings.gemini_model,
    }
