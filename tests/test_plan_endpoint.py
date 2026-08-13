"""/internal/replan 라우터 — 실제 알고리즘까지 타지만 DB·외부 호출은 없다."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from autoyard import ids

client = TestClient(app)

SLOT_A = ids.make_slot_id(4, 4)


def _base_body(**overrides) -> dict:
    body = {
        "base_plan_version": None,
        "constraints": [],
        "yard_state": {
            "blocks": [],
            "slots": [{"slot_id": SLOT_A, "block_id": "B01", "status": "EMPTY"}],
            "placements": {},
        },
        "vehicles": [{"vehicle_id": "V-0001", "status": "EXPECTED"}],
    }
    body.update(overrides)
    return body


def test_replan_places_a_new_vehicle():
    response = client.post("/internal/replan", json=_base_body())

    assert response.status_code == 200
    body = response.json()
    assert body["placements"]["V-0001"] == SLOT_A
    assert body["kpi"]["changed_vehicles"] == 1


def test_replan_rejects_non_approved_constraints():
    pending = {
        "constraint_id": "C-001",
        "type": "BLOCK_CLOSURE",
        "target": {"block_ids": ["B01"]},
        "priority": "HARD",
        "confidence": 0.9,
        "status": "PENDING_REVIEW",
    }

    response = client.post("/internal/replan", json=_base_body(constraints=[pending]))

    assert response.status_code == 422


def test_brief_is_still_a_stub():
    response = client.post(
        "/internal/brief",
        json={"kpi": {
            "avg_move_distance": 1.0, "rehandle_proxy": 0.0, "hard_violations": 0,
            "changed_vehicles": 0, "plan_retention_rate": 100.0, "calc_millis": 1,
        }},
    )

    assert response.status_code == 501
