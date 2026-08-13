"""순수 로직만: Gemini 의 raw JSON(제약 ID 없음)에 constraint_id 를 순번으로 매기고
ParseResult 로 검증하는 부분. 네트워크 호출 없이 돈다.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from autoyard.gemini_client import _to_parse_result


def test_assigns_sequential_constraint_ids():
    raw = json.dumps(
        {
            "constraints": [
                {
                    "type": "BLOCK_CLOSURE",
                    "target": {"block_ids": ["B02"]},
                    "priority": "HARD",
                    "confidence": 0.99,
                },
                {
                    "type": "VEHICLE_GROUPING",
                    "target": {"attribute": "brand", "values": ["B"]},
                    "priority": "SOFT",
                    "confidence": 0.8,
                },
            ],
            "unresolved": [],
            "requires_confirmation": False,
        }
    )

    result = _to_parse_result(raw, "INS-001")

    assert [c.constraint_id for c in result.constraints] == ["C-001", "C-002"]


def test_rejects_out_of_list_block_id_format():
    """스키마가 잡아주는 형식 오류 - 3번블록 같은 표현이 그대로 들어오면 걸린다."""
    raw = json.dumps(
        {
            "constraints": [
                {
                    "type": "BLOCK_CLOSURE",
                    "target": {"block_ids": ["3번블록"]},
                    "priority": "HARD",
                    "confidence": 0.9,
                },
            ],
        }
    )

    with pytest.raises(ValidationError):
        _to_parse_result(raw, "INS-001")


def test_missing_constraints_key_defaults_to_empty():
    raw = json.dumps({"unresolved": ["애매한 표현"], "requires_confirmation": True})

    result = _to_parse_result(raw, "INS-002")

    assert result.constraints == []
    assert result.unresolved == ["애매한 표현"]
