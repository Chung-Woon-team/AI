"""순수 로직만: Gemini 가 낸 격자 인식 결과의 검증(_GridRecognition). 네트워크 호출 없이 돈다."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autoyard.gemini_client import _GridRecognition


def test_accepts_valid_slot_cells():
    recognition = _GridRecognition(
        grid=[{"row": 4, "col": 4, "occupied": True}, {"row": 4, "col": 5, "occupied": False}],
        confidence=0.9,
    )

    assert len(recognition.grid) == 2


def test_rejects_road_cell():
    """(0, 0) 은 외곽 도로 칸이라 주차칸이 아니다 - 환각 방지."""
    with pytest.raises(ValidationError):
        _GridRecognition(grid=[{"row": 0, "col": 0, "occupied": True}], confidence=0.9)


def test_rejects_duplicate_cell():
    with pytest.raises(ValidationError):
        _GridRecognition(
            grid=[
                {"row": 4, "col": 4, "occupied": True},
                {"row": 4, "col": 4, "occupied": False},
            ],
            confidence=0.9,
        )
