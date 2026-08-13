"""/internal/parse 라우터 — 네트워크 없이 검증 가능한 분기(폴백, ID 형식 검증)를 확인한다.

실제 개발 환경에는 진짜 GEMINI_API_KEY 가 든 .env 가 있을 수 있으므로, ambient 설정에
기대지 않고 매 테스트가 `settings` 를 직접 주입한다(tests/test_extract_bl_endpoint.py 와 같은 패턴).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.routers.parse as parse_router
from app.main import app
from autoyard.config import Settings

client = TestClient(app)


def _settings(*, gemini_api_key: str | None) -> Settings:
    return Settings(
        gemini_api_key=gemini_api_key,
        gemini_model="gemini-2.5-flash",
        backend_base_url="http://localhost:8080",
        confidence_threshold=0.85,
        meters_per_cell=3.0,
        seconds_per_step=1.0,
    )


def test_fallback_when_gemini_key_missing(monkeypatch):
    monkeypatch.setattr(parse_router, "settings", _settings(gemini_api_key=None))

    response = client.post(
        "/internal/parse",
        json={"raw_text": "3번 블록 폐쇄해", "instruction_id": "INS-042"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["result"]["instruction_id"] == "INS-042"
    assert body["result"]["constraints"][0]["confidence"] == 0.0
    assert body["thread_id"].startswith("th_")


def test_fallback_when_gemini_call_fails(monkeypatch):
    monkeypatch.setattr(parse_router, "settings", _settings(gemini_api_key="fake-key"))

    def _always_fails(**kwargs):
        raise parse_router.gemini_client.ParsingFailed("의도적으로 실패시킨 테스트")

    monkeypatch.setattr(parse_router.gemini_client, "parse_instruction", _always_fails)

    response = client.post("/internal/parse", json={"raw_text": "3번 블록 폐쇄해"})

    assert response.status_code == 200
    assert response.json()["result"]["constraints"][0]["confidence"] == 0.0


def test_self_issues_instruction_id_when_not_given(monkeypatch):
    monkeypatch.setattr(parse_router, "settings", _settings(gemini_api_key=None))

    response = client.post("/internal/parse", json={"raw_text": "3번 블록 폐쇄해"})

    assert response.status_code == 200
    assert response.json()["result"]["instruction_id"].startswith("INS-")


def test_rejects_malformed_instruction_id():
    response = client.post(
        "/internal/parse",
        json={"raw_text": "3번 블록 폐쇄해", "instruction_id": "not-a-real-id"},
    )

    assert response.status_code == 422


def test_resume_is_still_a_stub():
    response = client.post(
        "/internal/resume",
        json={"thread_id": "th_abc123", "approved": True},
    )

    assert response.status_code == 501
