"""브리핑용 Gemini 응답 해석(`_to_headline_note`) — 네트워크 없이 순수 로직만 검증한다.

여기서 못 박는 건 하나다: 모델이 무슨 모양으로 답하든 **ValueError 로 떨어져야 한다.**
다른 예외 타입이 올라가면 `generate_briefing` 의 repair/폴백 분기를 그냥 빠져나가서
`/internal/brief` 가 500 을 내는데, 계약서 4.4 는 이 엔드포인트가 어떤 경우에도 5xx 를
내지 않는다고 못 박고 있다.
"""

from __future__ import annotations

import json

import pytest

from autoyard.gemini_client import _to_headline_note
from autoyard.schemas import PlanKpi

KPI = PlanKpi(
    avg_move_distance=502.0,
    rehandle_proxy=1.24,
    hard_violations=0,
    changed_vehicles=42,
    plan_retention_rate=91.6,
    calc_millis=1740,
)
MOVES = [
    {
        "vehicle_id": "V-0182",
        "from_slot": "B02-R04-C25",
        "to_slot": "B03-R13-C04",
        "sequence": 1,
        "reason": "B02 블록 폐쇄로 인한 재배치",
        "distance_meters": 1284.0,
    }
]


def test_accepts_clean_response():
    raw = json.dumps(
        {"headline": "B02 블록 폐쇄로 42대가 재배치되었습니다.", "note": "얕은 자리로 옮겼습니다."},
        ensure_ascii=False,
    )

    headline, note = _to_headline_note(raw, KPI, None, MOVES)

    assert headline == "B02 블록 폐쇄로 42대가 재배치되었습니다."
    assert note == "얕은 자리로 옮겼습니다."


def test_missing_note_is_none():
    raw = json.dumps({"headline": "42대가 재배치되었습니다."}, ensure_ascii=False)

    _headline, note = _to_headline_note(raw, KPI, None, MOVES)

    assert note is None


def test_empty_response_text_is_a_validation_error():
    """SDK 의 response.text 는 응답이 막히면(safety block · MAX_TOKENS) None 이다."""
    with pytest.raises(ValueError, match="비어 있습니다"):
        _to_headline_note(None, KPI, None, MOVES)


def test_non_string_headline_is_a_validation_error():
    raw = json.dumps({"headline": 42, "note": "설명."}, ensure_ascii=False)

    with pytest.raises(ValueError, match="headline 가 문자열이 아닙니다"):
        _to_headline_note(raw, KPI, None, MOVES)


def test_json_array_is_a_validation_error():
    with pytest.raises(ValueError, match="JSON 객체가 아닙니다"):
        _to_headline_note("[]", KPI, None, MOVES)


def test_invented_number_is_a_validation_error():
    raw = json.dumps({"headline": "99대가 재배치되었습니다."}, ensure_ascii=False)

    with pytest.raises(ValueError, match="숫자"):
        _to_headline_note(raw, KPI, None, MOVES)
