"""/internal/extract/bl 라우터 — 네트워크 없이 검증 가능한 두 분기(폴백 경로)를 확인한다.

실제 개발 환경에는 진짜 GEMINI_API_KEY 가 든 .env 가 있을 수 있으므로, ambient 설정에
기대지 않고 매 테스트가 `settings` 를 직접 주입한다.
"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient

import app.routers.extract as extract_router
from app.main import app
from autoyard.config import Settings
from autoyard.fallback import FALLBACK_BILL_OF_LADING
from autoyard.gemini_client import ExtractionFailed

client = TestClient(app)


def _settings(*, gemini_api_key: str | None) -> Settings:
    return Settings(
        gemini_api_key=gemini_api_key,
        gemini_model="gemini-2.5-flash",
        backend_base_url="http://localhost:8080",
        confidence_threshold=0.85,
    )


def _upload():
    return {"file": ("bl.png", io.BytesIO(b"fake-image-bytes"), "image/png")}


def test_fallback_when_gemini_key_missing(monkeypatch):
    monkeypatch.setattr(extract_router, "settings", _settings(gemini_api_key=None))

    response = client.post("/internal/extract/bl", files=_upload())

    assert response.status_code == 200
    body = response.json()
    assert body["bl_number"] == FALLBACK_BILL_OF_LADING.bl_number
    assert body["confidence"] == 0.0


def test_fallback_when_gemini_call_fails(monkeypatch):
    """키는 있지만 Gemini 호출/검증이 재시도까지 실패하는 경우 — 데모가 멈추면 안 된다."""
    monkeypatch.setattr(extract_router, "settings", _settings(gemini_api_key="fake-key"))

    def _always_fails(image_bytes, mime_type):
        raise ExtractionFailed("의도적으로 실패시킨 테스트")

    monkeypatch.setattr(extract_router.gemini_client, "extract_bill_of_lading", _always_fails)

    response = client.post("/internal/extract/bl", files=_upload())

    assert response.status_code == 200
    assert response.json()["confidence"] == 0.0


def test_grid_extraction_still_a_stub():
    response = client.post(
        "/internal/extract/grid",
        params={"block_id": "B03"},
        files={"file": ("grid.png", io.BytesIO(b"fake-image-bytes"), "image/png")},
    )

    assert response.status_code == 501
