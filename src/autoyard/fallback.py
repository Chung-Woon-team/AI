"""Gemini 를 못 쓸 때 돌려주는 미리 준비된 예시.

데모 중 GEMINI_API_KEY 부재 · 네트워크 장애 · 반복된 검증 실패로 화면이 멈추면 안 된다
(README / docs/HANDOFF_AI.md 7번 폴백). confidence=0.0 으로 박아서 "실제 결과가 아니라
폴백"임을 신호로 남긴다 — 두 스키마 다 이게 유일하게 "확인 필요"를 표현할 수 있는 값이다.
"""

from __future__ import annotations

from datetime import datetime

from autoyard import yard_grid
from autoyard.schemas import BillOfLadingExtraction, GridCell, GridObservation, ObservationSource, ParseResult

# 값은 docs/DOMAIN.md, docs/HANDOFF_AI.md 에 실린 DOC 01 샘플(60대, 대수 교차검증 통과)과 같다.
FALLBACK_BILL_OF_LADING = BillOfLadingExtraction(
    bl_number="NXR-USN-NTD-26081101",
    document_type="BILL_OF_LADING",
    booking_number="NXR-SP2608E-001",
    lot_code="EV-A-0811",
    vessel_name="MV SYNTH PACIFIC",
    voyage_number="SP2608E",
    port_of_loading="KRUSN",
    port_of_discharge="USNTD",
    unit_count=60,
    vin_range_from="SYNT26E0000000001",
    vin_range_to="SYNT26E0000000060",
    powertrain="BATTERY_EV",
    driveable_count=60,
    tow_count=0,
    unloading_priority="P2",
    target_yard_zone="EV-A / ROWS 01-06",
    discharge_seq_from=41,
    discharge_seq_to=100,
    confidence=0.0,
)


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


def build_fallback_grid_observation(source_type: ObservationSource) -> GridObservation:
    """격자 전부를 EMPTY 로 채워서 돌려준다. confidence=0.0 이라 호출부가 이대로 확정하면 안 되고,
    사람이 다시 찍거나 수기로 확인하게 유도해야 한다 — 선하증권 폴백과 같은 신호 규칙.
    """
    return GridObservation(
        source_type=source_type,
        captured_at=datetime.now(),
        grid=[GridCell(row=row, col=col, occupied=False) for row, col in yard_grid.slot_cells()],
        confidence=0.0,
        requires_confirmation=True,
    )
