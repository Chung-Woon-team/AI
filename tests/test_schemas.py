"""세팅이 제대로 됐는지 확인하는 최소 테스트. 로직 테스트는 내일 채운다."""

import pytest
from pydantic import ValidationError

from autoyard import ids
from autoyard.schemas import ConstraintPriority, ConstraintType, ParsedConstraint, ParseResult


def test_slot_id_round_trip():
    # 슬롯 ID 는 야드 절대 좌표를 품는다. 격자 규칙은 test_yard_grid.py 가 본다.
    assert ids.make_slot_id(13, 4) == "B03-R13-C04"
    assert ids.parse_slot_id("B03-R13-C04") == ("B03", 13, 4)


def test_parses_the_example_from_the_doc():
    """AI활용방안 2절의 출력 예시가 그대로 통과해야 한다."""
    c = ParsedConstraint(
        constraint_id="C-001",
        type=ConstraintType.BLOCK_CLOSURE,
        target={"block_ids": ["B03"]},
        time_window={"start": "2026-08-13T14:00:00", "end": None},
        priority=ConstraintPriority.HARD,
        confidence=0.99,
    )
    assert c.status.value == "PENDING_REVIEW"  # 승인 전이 기본값


def test_hallucinated_id_is_rejected():
    """파서가 지어낸 ID 형식은 스키마에서 막힌다."""
    with pytest.raises(ValidationError):
        ParsedConstraint(
            constraint_id="C-001",
            type=ConstraintType.BLOCK_CLOSURE,
            target={"block_ids": ["3번블록"]},
            priority=ConstraintPriority.HARD,
            confidence=0.9,
        )


def test_unresolved_forces_confirmation():
    r = ParseResult(instruction_id="INS-001", unresolved=["가까이"], requires_confirmation=False)
    assert r.requires_confirmation is True
