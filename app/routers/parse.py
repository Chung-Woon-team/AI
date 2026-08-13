"""자연어 지시 파싱과 승인 재개.

아직 구현 전이라 501 을 돌려준다. 스프링 쪽에서 연동 코드를 먼저 붙일 수 있도록
경로와 요청/응답 모양만 잡아뒀다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from autoyard.schemas import ParseResult

router = APIRouter(prefix="/internal", tags=["parse"])


class ParseRequest(BaseModel):
    raw_text: str = Field(description="현장에서 말로 준 지시 원문")
    author: str | None = None
    # 파서가 존재하지 않는 ID 를 지어내지 않도록, 유효 목록을 항상 같이 넘긴다.
    valid_block_ids: list[str] = Field(default_factory=list)
    valid_brands: list[str] = Field(default_factory=list)
    valid_zones: list[str] = Field(default_factory=list)


class ParseResponse(BaseModel):
    """ParseResult 에 thread_id 를 얹은 것.

    thread_id 가 있어야 나중에 승인 결과를 이 그래프로 되돌려 보낼 수 있다.
    스프링은 이 값을 DB 에 보관한다.
    """

    thread_id: str
    result: ParseResult


class ResumeRequest(BaseModel):
    thread_id: str
    approved: bool
    reviewer: str | None = None
    reason: str | None = Field(default=None, description="반려 사유")


@router.post("/parse", response_model=ParseResponse)
def parse_instruction(req: ParseRequest) -> ParseResponse:
    raise HTTPException(status_code=501, detail="아직 구현 전 (AI 파트)")


@router.post("/resume")
def resume_after_approval(req: ResumeRequest) -> dict:
    """human_approval 에서 멈춰 있는 그래프를 깨운다.

    thread_id 로 어느 그래프인지 찾으므로, 파이썬 인스턴스는 1개로 고정해야 한다.
    """
    raise HTTPException(status_code=501, detail="아직 구현 전 (AI 파트)")
