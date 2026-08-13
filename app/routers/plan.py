"""배치 최적화와 브리핑.

여기 두 엔드포인트는 성격이 다르다.
- /replan 은 결정론적 코드다. AI 를 쓰지 않는다.
- /brief 는 AI 가 문장을 만들지만, 숫자는 입력에서만 가져온다.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from autoyard import briefing, fallback, gemini_client
from autoyard import replan as replan_engine
from autoyard.config import settings
from autoyard.schemas import ParsedConstraint, PlanKpi, ReplanResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["plan"])


class ReplanRequest(BaseModel):
    """승인된 제약만 넘어온다. PENDING_REVIEW 는 여기 오면 안 된다."""

    base_plan_version: str | None = None
    constraints: list[ParsedConstraint] = Field(default_factory=list)
    # 야드 현황과 차량 목록. 스프링의 GET /api/yard/state 응답을 그대로 넘기면 된다.
    yard_state: dict = Field(default_factory=dict)
    vehicles: list[dict] = Field(default_factory=list)


class BriefRequest(BaseModel):
    kpi: PlanKpi
    kpi_before: PlanKpi | None = None
    # 이동 1건 = {vehicle_id, from_slot, to_slot, sequence, reason, distance_meters}.
    # 스프링이 최대 200건만 보낸다 — 개수를 세지 말 것. 대수는 kpi.changed_vehicles 가 정본이다.
    # list[Move] 로 조이지 않는다: Move 는 extra="forbid" 라 스프링이 여분 키를 하나만
    # 보내도 422 로 터진다.
    moves: list[dict] = Field(default_factory=list)
    # 코드가 대조해서 넘긴 "확인이 필요한 지점". AI 가 판정하지 않는다.
    consistency_issues: list[dict] = Field(default_factory=list)


class BriefResponse(BaseModel):
    briefing: str
    confirmations: list[dict] = Field(default_factory=list)
    # 폴백 신호. 다른 스키마는 confidence=0.0 으로 폴백임을 알리는데 여기엔 confidence 가
    # 없어서 이 필드가 그 역할을 한다. "AI" | "FALLBACK".
    source: str = "AI"


@router.post("/replan", response_model=ReplanResult)
def replan(req: ReplanRequest) -> ReplanResult:
    """mapf.ipynb 기반 시공간 A* 로 배치를 계산한다. 결정론적 코드 — AI 아님."""
    not_approved = [c.constraint_id for c in req.constraints if c.status != "APPROVED"]
    if not_approved:
        raise HTTPException(
            status_code=422,
            detail=f"PENDING_REVIEW/REJECTED 제약은 여기 오면 안 됩니다: {not_approved}",
        )

    return replan_engine.compute_replan(
        base_plan_version=req.base_plan_version,
        constraints=req.constraints,
        yard_state=req.yard_state,
        vehicles=req.vehicles,
    )


@router.post("/brief", response_model=BriefResponse)
def brief(req: BriefRequest) -> BriefResponse:
    """KPI 수치를 담당자용 한국어 브리핑 문장으로 풀어쓴다.

    숫자가 들어가는 줄은 briefing.py 가 100% 조립하고, Gemini 는 숫자 없는 headline 과
    note 만 만든다. 그래서 어떤 경우에도 5xx 를 내지 않는다 — 키가 없든 호출이 실패하든
    검증에 걸리든 200 + `source="FALLBACK"` 이다. 422 는 Pydantic 입력 검증 실패에만 나온다.
    """
    draft = briefing.build_briefing_draft(req.kpi, req.kpi_before, req.moves)

    if not settings.gemini_enabled:
        logger.info("GEMINI_API_KEY 없음 - 폴백 브리핑(수치 템플릿) 반환")
        return BriefResponse(**fallback.build_fallback_brief(draft, req.consistency_issues))

    try:
        headline, note = gemini_client.generate_briefing(
            draft, req.kpi, req.kpi_before, req.moves
        )
    except gemini_client.BriefingFailed as exc:
        logger.warning("브리핑 생성 실패, 폴백으로 대체: %s", exc)
        return BriefResponse(**fallback.build_fallback_brief(draft, req.consistency_issues))

    return BriefResponse(
        briefing=briefing.assemble(draft, headline, note),
        # 정본은 스프링이다. 여기서 판정·추가하지 않고 받은 그대로 에코한다.
        confirmations=req.consistency_issues,
        source="AI",
    )
