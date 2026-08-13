"""Gemini 를 못 쓸 때 돌려주는 미리 준비된 예시.

데모 중 GEMINI_API_KEY 부재 · 네트워크 장애 · 반복된 검증 실패로 화면이 멈추면 안 된다
(README / docs/HANDOFF_AI.md 7번 폴백). confidence=0.0 으로 박아서 "실제 파싱이 아니라
폴백"임을 신호로 남긴다.
"""

from __future__ import annotations

from autoyard.schemas import ParseResult


def build_fallback_parse_result(instruction_id: str) -> ParseResult:
    """docs/HANDOFF_AI.md 5절 예시 문장("B02 블록 폐쇄")을 기준으로 한 고정 응답.

    instruction_id 는 호출마다 다르므로(요청에서 오거나 서버가 발급) 인자로 받는다 —
    선하증권 폴백과 달리 통짜 상수로 둘 수 없다.
    """
    return ParseResult(
        instruction_id=instruction_id,
        constraints=[
            {
                "constraint_id": "C-001",
                "type": "BLOCK_CLOSURE",
                "target": {"block_ids": ["B02"]},
                "time_window": {"start": None, "end": None},
                "priority": "HARD",
                "confidence": 0.0,
            },
        ],
        unresolved=["폴백 - 실제 파싱 아님"],
        requires_confirmation=True,
    )
