"""이미지에서 정형 데이터 뽑기 — 선하증권, 주차장 사진."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from autoyard.schemas import BillOfLadingExtraction, GridObservation

router = APIRouter(prefix="/internal/extract", tags=["extract"])


@router.post("/bl", response_model=BillOfLadingExtraction)
async def extract_bill_of_lading(file: UploadFile = File(...)) -> BillOfLadingExtraction:
    """선하증권 이미지 → 문서에 적힌 값.

    개별 차량 행은 여기서 만들지 않는다. VIN 은 범위로만 있고, 자력주행 여부도 집계값이다.
    전개는 결정론적 코드가 한다.
    """
    raise HTTPException(status_code=501, detail="아직 구현 전 (AI 파트)")


@router.post("/grid", response_model=GridObservation)
async def extract_grid(block_id: str, file: UploadFile = File(...)) -> GridObservation:
    """주차장 사진 → 격자 점유 상태. 좌표(row, col)까지만 반환한다."""
    raise HTTPException(status_code=501, detail="아직 구현 전 (AI 파트)")
