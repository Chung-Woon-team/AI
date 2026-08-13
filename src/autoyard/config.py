"""환경설정 한 곳. .env 를 읽고, 없으면 안전한 기본값으로 떨어진다."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None
    gemini_model: str
    backend_base_url: str
    # 파서 신뢰도가 이 값 미만이면 담당자 확인 대상으로 붙잡아 둔다(장표 5쪽).
    confidence_threshold: float

    @property
    def gemini_enabled(self) -> bool:
        """키가 없으면 폴백 경로로 돈다. 데모가 네트워크 없이도 죽지 않게 하려는 것."""
        return bool(self.gemini_api_key)


def load_settings() -> Settings:
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        backend_base_url=os.getenv("BACKEND_BASE_URL", "http://localhost:8080"),
        confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.85")),
    )


settings = load_settings()
