"""자연어 지시 파싱과 승인 재개.

/parse 는 실제 구현이다. /resume 은 LangGraph 그래프가 아직 없어서 501 그대로 둔다 —
"파싱 → 검증 → 저장(PENDING_REVIEW)" 까지가 이번 범위고, 승인 재개는 그래프 조립 단계에서 붙인다
(docs/HANDOFF_AI.md 6번).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from autoyard import gemini_client
from autoyard.config import settings
from autoyard.fallback import build_fallback_parse_result
from autoyard.ids import INSTRUCTION_ID
from autoyard.schemas import ParseResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["parse"])

_instruction_seq = 0


def _next_instruction_id() -> str:
    """instruction_id 를 스프링이 안 넘겨줬을 때 쓰는 자체 발급(개발용 - Streamlit/Swagger 단독 테스트).

    파이썬 인스턴스가 1개로 고정된다는 전제(docs/API_CONTRACT.md thread_id 절)와 같은 이유로
    프로세스 메모리 카운터로 충분하다. 정식 흐름에서는 스프링이 POST /api/instructions 로
    먼저 발급한 instruction_id 를 요청에 실어 보내는 걸 기대한다.
    """
    global _instruction_seq
    _instruction_seq += 1
    return f"INS-{_instruction_seq:03d}"


class ParseRequest(BaseModel):
    raw_text: str = Field(description="현장에서 말로 준 지시 원문")
    author: str | None = None
    # 스프링이 POST /api/instructions 에서 먼저 발급한 ID. 없으면 이 서버가 자체 발급한다.
    instruction_id: str | None = None
    # "오늘"/"내일" 같은 상대 시각을 절대 시각으로 바꿀 기준. 없으면 서버 현재 시각을 쓴다.
    reference_datetime: datetime | None = None
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
    if req.instruction_id is not None and not INSTRUCTION_ID.match(req.instruction_id):
        raise HTTPException(
            status_code=422, detail=f"instruction_id 형식이 아닙니다: {req.instruction_id!r}"
        )
    instruction_id = req.instruction_id or _next_instruction_id()
    # 프로젝트 컨벤션상 시각은 tz 없는 로컬시각으로 통일한다(docs/API_CONTRACT.md 3-4번, Asia/Seoul 고정).
    reference_datetime = req.reference_datetime or datetime.now()  # noqa: DTZ005

    # thread_id 는 지금은 자리만 잡아둔 값이다 — LangGraph 체크포인터가 아직 없어서
    # 실제로 어떤 대기 상태도 가리키지 않는다. /resume 이 구현되면 그때 진짜 값이 된다.
    thread_id = f"th_{uuid.uuid4().hex[:8]}"

    if not settings.gemini_enabled:
        logger.info("GEMINI_API_KEY 없음 - 폴백 파싱 결과 반환")
        return ParseResponse(thread_id=thread_id, result=build_fallback_parse_result(instruction_id))

    try:
        result = gemini_client.parse_instruction(
            raw_text=req.raw_text,
            instruction_id=instruction_id,
            valid_block_ids=req.valid_block_ids,
            valid_brands=req.valid_brands,
            valid_zones=req.valid_zones,
            reference_datetime=reference_datetime,
        )
    except gemini_client.ParsingFailed as exc:
        logger.warning("지시 파싱 실패, 폴백으로 대체: %s", exc)
        result = build_fallback_parse_result(instruction_id)

    return ParseResponse(thread_id=thread_id, result=result)


@router.post("/resume")
def resume_after_approval(req: ResumeRequest) -> dict:
    """human_approval 에서 멈춰 있는 그래프를 깨운다.

    thread_id 로 어느 그래프인지 찾으므로, 파이썬 인스턴스는 1개로 고정해야 한다.
    """
    raise HTTPException(status_code=501, detail="아직 구현 전 (AI 파트)")
