"""이미지에서 정형 데이터 뽑기 — 선하증권, 주차장 사진."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile

from autoyard import gemini_client
from autoyard.config import settings
from autoyard.fallback import FALLBACK_BILL_OF_LADING
from autoyard.schemas import BillOfLadingExtraction, GridObservation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/extract", tags=["extract"])


@router.post("/bl", response_model=BillOfLadingExtraction)
async def extract_bill_of_lading(file: UploadFile = File(...)) -> BillOfLadingExtraction:
    """선하증권 이미지 → 문서에 적힌 값.

    개별 차량 행은 여기서 만들지 않는다. VIN 은 범위로만 있고, 자력주행 여부도 집계값이다.
    전개는 결정론적 코드가 한다(스프링 쪽).
    """
    if not settings.gemini_enabled:
        logger.info("GEMINI_API_KEY 없음 - 폴백 선하증권 반환")
        return FALLBACK_BILL_OF_LADING

    image_bytes = await file.read()
    mime_type = file.content_type or "image/jpeg"

    try:
        return gemini_client.extract_bill_of_lading(image_bytes, mime_type)
    except gemini_client.ExtractionFailed as exc:
        logger.warning("선하증권 추출 실패, 폴백으로 대체: %s", exc)
        return FALLBACK_BILL_OF_LADING


@router.post("/grid", response_model=GridObservation)
async def extract_grid(block_id: str, file: UploadFile = File(...)) -> GridObservation:
    """주차장 사진 → 격자 점유 상태. 좌표(row, col)까지만 반환한다."""
    raise HTTPException(status_code=501, detail="아직 구현 전 (AI 파트)")
