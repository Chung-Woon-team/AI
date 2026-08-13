"""/internal/replan · /internal/brief 라우터 — 실제 로직까지 타지만 DB·외부 호출은 없다.

브리핑 테스트는 Gemini 를 타지 않는다. 실제 개발 환경에 진짜 GEMINI_API_KEY 가 든 .env 가
있을 수 있으므로 ambient 설정에 기대지 않고 매 테스트가 `settings` 를 직접 주입한다
(tests/test_parse_endpoint.py 와 같은 패턴).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.routers.plan as plan_router
from app.main import app
from autoyard import briefing, ids
from autoyard.config import Settings
from autoyard.schemas import PlanKpi

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


# --------------------------------------------------------------------------
# /internal/brief
# --------------------------------------------------------------------------

KPI_AFTER = {
    "avg_move_distance": 502.0,
    "rehandle_proxy": 1.24,
    "hard_violations": 0,
    "changed_vehicles": 42,
    "plan_retention_rate": 91.6,
    "calc_millis": 1740,
}
KPI_BEFORE = {
    "avg_move_distance": 812.0,
    "rehandle_proxy": 1.93,
    "hard_violations": 2,
    "changed_vehicles": 61,
    "plan_retention_rate": 86.9,
    "calc_millis": 2110,
}
MOVES = [
    {
        "vehicle_id": "V-0182",
        "from_slot": "B02-R03-C07",
        "to_slot": "B03-R12-C04",
        "sequence": 1,
        "reason": "B02 블록 폐쇄로 인한 재배치",
        "distance_meters": 1284.0,
    },
    {
        "vehicle_id": "V-0411",
        "from_slot": None,
        "to_slot": "B01-R02-C11",
        "sequence": 2,
        "reason": "신규 배치",
        "distance_meters": 312.0,
    },
]
ISSUES = [
    {
        "code": "LONG_MOVE",
        "severity": "WARN",
        "message": "V-0182 의 이동거리가 1284m 로 이 판 평균(502m)의 2배를 넘습니다.",
        "action_hint": "더 가까운 대체 슬롯이 있는지 확인하세요.",
        "slot_id": "B03-R12-C04",
    }
]

EXPECTED_DRAFT = (
    "B02 블록 폐쇄로 42대가 재배치되었습니다.\n"
    "- 평균 이동거리: 812 → 502m (38% 감소)\n"
    "- 재취급 Proxy: 1.9 → 1.2 (36% 감소)\n"
    "- 계획 유지율: 86.9 → 91.6% (5% 증가)\n"
    "- Hard 제약 위반: 0건\n"
    "- 이동 대상 차량: 42대\n"
    "- 계산 소요: 1740ms"
)


def _settings(*, gemini_api_key: str | None) -> Settings:
    return Settings(
        gemini_api_key=gemini_api_key,
        gemini_model="gemini-2.5-flash",
        backend_base_url="http://localhost:8080",
        confidence_threshold=0.85,
        meters_per_cell=3.0,
        seconds_per_step=1.0,
    )


def _brief_body(**overrides) -> dict:
    body = {
        "kpi": KPI_AFTER,
        "kpi_before": KPI_BEFORE,
        "moves": MOVES,
        "consistency_issues": ISSUES,
    }
    body.update(overrides)
    return body


def test_brief_falls_back_when_gemini_key_missing(monkeypatch):
    monkeypatch.setattr(plan_router, "settings", _settings(gemini_api_key=None))

    response = client.post("/internal/brief", json=_brief_body())

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "FALLBACK"
    assert body["briefing"] == EXPECTED_DRAFT


def test_brief_falls_back_when_gemini_call_fails(monkeypatch):
    monkeypatch.setattr(plan_router, "settings", _settings(gemini_api_key="fake-key"))

    def _always_fails(*args, **kwargs):
        raise plan_router.gemini_client.BriefingFailed("의도적으로 실패시킨 테스트")

    monkeypatch.setattr(plan_router.gemini_client, "generate_briefing", _always_fails)

    response = client.post("/internal/brief", json=_brief_body())

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "FALLBACK"
    assert body["briefing"] == EXPECTED_DRAFT


def test_brief_uses_gemini_headline_and_note_but_keeps_number_lines(monkeypatch):
    monkeypatch.setattr(plan_router, "settings", _settings(gemini_api_key="fake-key"))

    note = "폐쇄 구역에 있던 차량을 인접 블록의 얕은 자리로 옮기면서 평균 이동거리와 재취급 부담이 함께 줄었습니다."

    def _succeeds(*args, **kwargs):
        return "B02 블록이 닫혀 42대를 옮겼습니다.", note

    monkeypatch.setattr(plan_router.gemini_client, "generate_briefing", _succeeds)

    response = client.post("/internal/brief", json=_brief_body())

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "AI"
    lines = body["briefing"].split("\n")
    assert lines[0] == "B02 블록이 닫혀 42대를 옮겼습니다."
    # 숫자가 들어간 줄은 LLM 을 거치지 않는다 - 초안과 바이트 단위로 같아야 한다.
    assert lines[1:7] == EXPECTED_DRAFT.split("\n")[1:]
    assert body["briefing"].endswith("\n\n" + note)


def test_brief_echoes_consistency_issues_unchanged(monkeypatch):
    """confirmations 의 정본은 스프링이다. 파이썬이 판정하거나 손대면 두 벌이 생긴다."""
    monkeypatch.setattr(plan_router, "settings", _settings(gemini_api_key=None))

    response = client.post("/internal/brief", json=_brief_body())

    assert response.json()["confirmations"] == ISSUES


def test_brief_without_kpi_before_omits_comparison(monkeypatch):
    monkeypatch.setattr(plan_router, "settings", _settings(gemini_api_key=None))

    response = client.post("/internal/brief", json=_brief_body(kpi_before=None))

    assert response.status_code == 200
    briefing_text = response.json()["briefing"]
    assert "- 평균 이동거리: 502m" in briefing_text
    assert "→" not in briefing_text


def test_brief_rejects_kpi_with_missing_field():
    """PlanKpi 는 6필드 전부 필수다. 스프링이 null 을 실어 보내면 422 로 막는다."""
    response = client.post(
        "/internal/brief",
        json=_brief_body(kpi={**KPI_AFTER, "calc_millis": None}),
    )

    assert response.status_code == 422


def test_brief_draft_matches_briefing_module():
    """엔드포인트 응답과 순수 함수 출력이 갈라지지 않는지 못 박아 둔다."""
    assert (
        briefing.build_briefing_draft(
            PlanKpi(**KPI_AFTER), PlanKpi(**KPI_BEFORE), MOVES
        )
        == EXPECTED_DRAFT
    )
