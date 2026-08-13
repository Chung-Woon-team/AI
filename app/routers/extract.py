"""이미지에서 정형 데이터 뽑기 — 선하증권, 주차장 사진."""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, UploadFile
from fastapi.concurrency import run_in_threadpool

from autoyard import gemini_client
from autoyard.config import settings
from autoyard.fallback import FALLBACK_BILL_OF_LADING, build_fallback_grid_observation
from autoyard.schemas import BillOfLadingExtraction, GridObservation, ObservationSource

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

    # ⚠️ run_in_threadpool 로 감싸는 이유 — gemini_client 는 동기 블로킹 SDK 다.
    # async 핸들러 안에서 그대로 부르면 이벤트 루프가 통째로 멈춰서, 이 인스턴스가 처리 중인
    # 다른 요청이 전부 같이 밀린다. 실측으로 /health 가 86초, /internal/replan 이 78초까지
    # 밀렸다 — 사진 한 장 올리는 동안 AI 서버 전체가 마비된 것이다.
    # (parse·replan·brief 라우터는 async 가 아닌 def 라 FastAPI 가 알아서 스레드풀로 돌린다.)
    try:
        return await run_in_threadpool(gemini_client.extract_bill_of_lading, image_bytes, mime_type)
    except gemini_client.ExtractionFailed as exc:
        logger.warning("선하증권 추출 실패, 폴백으로 대체: %s", exc)
        return FALLBACK_BILL_OF_LADING


@router.post("/grid", response_model=GridObservation)
async def extract_grid(
    source_type: ObservationSource = ObservationSource.MANUAL,
    file: UploadFile = File(...),
) -> GridObservation:
    """야드 전체 사진 한 장 → 격자 점유 상태. 좌표(row, col)까지만 반환한다.

    블록 하나가 아니라 야드 전체를 담은 사진 한 장을 받는다 — block_id 를 안 받는 이유는
    GridObservation 문서 참고. 어느 블록이 닫혔는지·배치가 맞는지는 여기서 안 따진다,
    스프링이 지금 DB 상태와 대조해서 판단한다.
    """
    if not settings.gemini_enabled:
        logger.info("GEMINI_API_KEY 없음 - 폴백 격자 관측 반환")
        return build_fallback_grid_observation(source_type)

    image_bytes = await file.read()
    mime_type = file.content_type or "image/jpeg"

    # 위 /bl 과 같은 이유로 스레드풀에서 돌린다 (동기 블로킹 SDK → 이벤트 루프 정지).
    try:
        return await run_in_threadpool(
            gemini_client.extract_grid_observation, image_bytes, mime_type, source_type
        )
    except gemini_client.ExtractionFailed as exc:
        logger.warning("격자 관측 추출 실패, 폴백으로 대체: %s", exc)
        return build_fallback_grid_observation(source_type)
