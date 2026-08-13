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
    # 격자 한 칸의 실제 크기(m). distance_meters/avg_move_distance KPI 계산에 쓴다.
    # 팀 확정값이 아직 없어서 3.0m(표준 주차 폭 근사치)로 임시로 잡아둔다 —
    # docs/API_CONTRACT.md §4 "METERS_PER_CELL 없으면 진행 막힘" 참고. 확정되면 .env 로 덮어쓸 것.
    meters_per_cell: float
    seconds_per_step: float
    # Gemini 호출 1회의 상한(ms). SDK 기본값은 사실상 무제한이라 안 걸어두면 과부하(503) 때
    # 한없이 매달린다 — 실측 78초. 스프링의 read timeout 이 60초라 그 전에 폴백으로 떨어져야
    # 하는데, 안 그러면 파이썬이 만든 폴백 응답이 사용자에게 도달하지 못한다.
    # 정상 이미지 추출이 36초까지 걸린 적이 있어(실측) 그보다는 넉넉해야 한다.
    # 기본값을 두는 이유: 이 둘은 성능 튜닝 노브라 테스트가 Settings 를 직접 만들 때
    # 매번 넘기게 하면 본질과 무관한 인자만 늘어난다.
    gemini_timeout_ms: int = 40_000
    # 첫 호출이 이 시간을 넘겨 실패했으면 재시도하지 않는다. 재시도가 예산을 두 배로 쓰면
    # 스프링이 먼저 끊어버려서, 빨리 폴백으로 떨어지는 쪽이 사용자에게 이득이다.
    gemini_retry_budget_seconds: float = 15.0

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
        meters_per_cell=float(os.getenv("METERS_PER_CELL", "3.0")),
        seconds_per_step=float(os.getenv("SECONDS_PER_STEP", "1.0")),
        gemini_timeout_ms=int(os.getenv("GEMINI_TIMEOUT_MS", "40000")),
        gemini_retry_budget_seconds=float(os.getenv("GEMINI_RETRY_BUDGET_SECONDS", "15.0")),
    )


settings = load_settings()
