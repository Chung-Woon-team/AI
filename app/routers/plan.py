"""배치 최적화와 브리핑.

여기 두 엔드포인트는 성격이 다르다.
- /replan 은 결정론적 코드다. AI 를 쓰지 않는다.
- /brief 는 AI 가 문장을 만들지만, 숫자는 입력에서만 가져온다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from autoyard import replan as replan_engine
from autoyard.schemas import ParsedConstraint, PlanKpi, ReplanResult

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
    moves: list[dict] = Field(default_factory=list)
    # 코드가 대조해서 넘긴 "확인이 필요한 지점". AI 가 판정하지 않는다.
    consistency_issues: list[dict] = Field(default_factory=list)


class BriefResponse(BaseModel):
    briefing: str
    confirmations: list[dict] = Field(default_factory=list)


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
    raise HTTPException(status_code=501, detail="아직 구현 전 (AI 파트)")
