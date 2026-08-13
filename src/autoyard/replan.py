"""승인된 제약 + 야드 현황 + 차량 목록 → 재배치 결과.

`/internal/replan` 이 쓴다. **결정론적 코드다 — AI(LLM) 를 안 쓴다** (app/routers/plan.py 의
모듈 docstring 그대로). `mapf.py` 의 시공간 A* 를 실제 야드 상태에 적용하는 자리다.

## Minimal Replanning 원칙 (장표 7쪽)

차량은 꼭 필요할 때만 옮긴다. 새로 배치가 필요한 경우는 둘뿐이다.

1. 아직 슬롯이 없는 차 (신규 입고)
2. 지금 있는 블록이 이번에 승인된 `BLOCK_CLOSURE` 로 막힌 차 (강제 이동)

이미 자리가 있고 그 블록이 안 막혔으면, `VEHICLE_GROUPING`/`OUTBOUND_PRIORITY` 같은 SOFT 제약이
있어도 **옮기지 않는다** — 이미 정착한 차를 단지 "더 나은 배치"를 위해 흔들면 현장 신뢰를 잃는다
(장표 7쪽). 그 두 제약은 새로 배치되는 차가 "어디로" 갈지에만 영향을 준다.

## 아직 확정 안 된 것 (단순화한 부분, docs/API_CONTRACT.md §4-2 참고)

- `METERS_PER_CELL`: 팀 확정값 없어서 `config.py` 의 임시값(3.0m) 사용.
- `target.filter` 해석: 자유 형식이라 실제로 관측된 키(`cutoff_date`)만 안다. 그 외 키는
  차량 필드명과 그대로 맞춰 매칭을 시도하되, 확정 규칙이 아니다.
- `preferred_zone`(VEHICLE_GROUPING 의 `value`) 은 "WEST"/"EAST" 같은 방향 표현인데 우리
  블록 배치(B01~B04)가 방향과 고정 대응이 안 돼 있어서, 방향 대신 "같은 그룹은 같은 블록에
  모은다"로 구현했다. 그룹의 첫 차가 어느 블록으로 갈지 정하면 나머지가 그걸 따라간다.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from autoyard import ids, mapf, yard_grid
from autoyard.config import settings
from autoyard.schemas import ConstraintType, Move, ParsedConstraint, PathStep, PlanKpi, ReplanResult

RAMP: mapf.Cell = (0, 0)
"""차량이 처음 들어오는 지점(입구). mapf.ipynb 원본과 같다."""

TRUCK_EXIT: mapf.Cell = (55, 0)
TRAIN_EXIT: mapf.Cell = (55, 55)

MAX_SLOT_CANDIDATES = 25
"""슬롯 후보를 이 개수까지만 시도한다 — 500대 2초 목표(API_CONTRACT.md)를 지키기 위한 상한."""

_CUTOFF_DATE_KEY = "cutoff_date"


@dataclass
class _VehicleState:
    vehicle_id: str
    current_slot: str | None
    priority: str
    next_mode: str | None
    departure_cutoff_at: str | None
    raw: dict


def compute_replan(
    base_plan_version: str | None,
    constraints: list[ParsedConstraint],
    yard_state: dict,
    vehicles: list[dict],
) -> ReplanResult:
    start = time.perf_counter()

    blocks = yard_state.get("blocks") or []
    slots = yard_state.get("slots") or []
    placements_before: dict[str, str] = dict(yard_state.get("placements") or {})

    closed_blocks = _closed_blocks(blocks, constraints)
    empty_slots_by_block = _empty_slots_by_block(slots, placements_before)
    grouping_constraints = [c for c in constraints if c.type == ConstraintType.VEHICLE_GROUPING]
    outbound_constraints = [c for c in constraints if c.type == ConstraintType.OUTBOUND_PRIORITY]

    all_states = [_to_vehicle_state(v, placements_before) for v in vehicles if _is_plannable(v)]
    staying, needs_placement = _split_by_need(all_states, closed_blocks)

    placements: dict[str, str] = dict(placements_before)
    for vs in staying:
        placements[vs.vehicle_id] = vs.current_slot  # 그대로 유지 — 이동 목록에 안 들어간다.

    priority_boost = _priority_boosted_ids(needs_placement, outbound_constraints)
    order = _placement_order(needs_placement, priority_boost)

    reservation = mapf.ReservationTable()
    moves: list[Move] = []
    unplaced: list[str] = []
    group_block: dict[str, str] = {}
    sequence = 1
    # 같은 지점(대개 RAMP)에서 동시에 출발하면 t=1 상태 수가 이웃 칸 수로 제한돼 있어서
    # 금방 자물쇠가 걸린다(예: 모서리 RAMP 는 이웃이 2칸 + 제자리 대기 1칸 = 3칸뿐이라
    # 3대가 넘는 순간부터 전부 갈 곳이 없어짐). 원본 mapf.ipynb 의 "톨게이트 통제"(그룹마다
    # 80초 간격)와 같은 이유로, 같은 출발점을 쓰는 차는 한 스텝씩 지연시켜 내보낸다.
    next_departure_time: dict[mapf.Cell, int] = {}

    for vs in order:
        group_key = _group_key(vs, grouping_constraints)
        prefer_block = group_block.get(group_key) if group_key else None
        prefer_shallow = vs.vehicle_id in priority_boost

        origin_road = _access_road(vs.current_slot) if vs.current_slot else RAMP
        start_time = next_departure_time.get(origin_road, 0)

        chosen_slot, path = _assign_slot(
            vs, closed_blocks, empty_slots_by_block, reservation,
            start_time=start_time, prefer_block=prefer_block, prefer_shallow=prefer_shallow,
        )

        if chosen_slot is None:
            unplaced.append(vs.vehicle_id)
            continue

        next_departure_time[origin_road] = start_time + 1

        reservation.reserve_path(path, vs.vehicle_id)
        _mark_slot_taken(empty_slots_by_block, chosen_slot)
        placements[vs.vehicle_id] = chosen_slot

        if group_key and prefer_block is None:
            group_block[group_key] = ids.parse_slot_id(chosen_slot)[0]

        moves.append(Move(
            vehicle_id=vs.vehicle_id,
            from_slot=vs.current_slot,
            to_slot=chosen_slot,
            sequence=sequence,
            reason=_reason_for(vs, closed_blocks),
            distance_meters=_path_distance_meters(path),
            path=[PathStep(row=row, col=col, t=t) for row, col, t in path],
        ))
        sequence += 1

    calc_millis = int((time.perf_counter() - start) * 1000)
    kpi = _compute_kpi(placements, moves, unplaced, calc_millis)

    return ReplanResult(
        plan_version=_next_plan_version(base_plan_version),
        based_on_version=base_plan_version,
        placements=placements,
        moves=moves,
        kpi=kpi,
        kpi_before=None,
        unplaced=unplaced,
    )


# --------------------------------------------------------------------------
# 입력 해석
# --------------------------------------------------------------------------


def _closed_blocks(blocks: list[dict], constraints: list[ParsedConstraint]) -> set[str]:
    closed = {b["block_id"] for b in blocks if b.get("closed")}
    for c in constraints:
        if c.type == ConstraintType.BLOCK_CLOSURE and c.target.block_ids:
            closed.update(c.target.block_ids)
    return closed


def _empty_slots_by_block(slots: list[dict], placements_before: dict[str, str]) -> dict[str, list[dict]]:
    occupied = set(placements_before.values())
    by_block: dict[str, list[dict]] = {}
    for s in slots:
        slot_id = s["slot_id"]
        if slot_id in occupied or s.get("status") not in (None, "EMPTY"):
            continue
        row, col = ids.slot_cell(slot_id)
        block = yard_grid.block_at(row, col)
        by_block.setdefault(block.block_id, []).append(
            {"slot_id": slot_id, "row": row, "col": col, "depth": block.depth(row)}
        )
    return by_block


def _to_vehicle_state(v: dict, placements_before: dict[str, str]) -> _VehicleState:
    vehicle_id = v["vehicle_id"]
    return _VehicleState(
        vehicle_id=vehicle_id,
        current_slot=placements_before.get(vehicle_id),
        priority=v.get("priority") or "NORMAL",
        next_mode=v.get("next_mode"),
        departure_cutoff_at=v.get("departure_cutoff_at"),
        raw=v,
    )


def _is_plannable(v: dict) -> bool:
    return v.get("status") != "DEPARTED"


def _split_by_need(
    states: list[_VehicleState], closed_blocks: set[str]
) -> tuple[list[_VehicleState], list[_VehicleState]]:
    staying, needs_placement = [], []
    for vs in states:
        if vs.current_slot is not None:
            block_id, _, _ = ids.parse_slot_id(vs.current_slot)
            if block_id not in closed_blocks:
                staying.append(vs)
                continue
        needs_placement.append(vs)
    return staying, needs_placement


# --------------------------------------------------------------------------
# 배치 순서 · 그룹핑
# --------------------------------------------------------------------------


def _matches_filter(raw: dict, filt: dict) -> bool:
    for key, expected in filt.items():
        if key == _CUTOFF_DATE_KEY:
            cutoff = raw.get("departure_cutoff_at")
            if cutoff is None or str(cutoff)[:10] != str(expected)[:10]:
                return False
            continue
        if raw.get(key) != expected:
            return False
    return True


def _priority_boosted_ids(
    states: list[_VehicleState], outbound_constraints: list[ParsedConstraint]
) -> set[str]:
    boosted: set[str] = set()
    for c in outbound_constraints:
        filt = c.target.filter or {}
        for vs in states:
            if _matches_filter(vs.raw, filt):
                boosted.add(vs.vehicle_id)
    return boosted


def _placement_order(states: list[_VehicleState], priority_boost: set[str]) -> list[_VehicleState]:
    def sort_key(vs: _VehicleState):
        urgent = 0 if vs.priority == "URGENT" else 1
        boosted = 0 if vs.vehicle_id in priority_boost else 1
        cutoff = vs.departure_cutoff_at or "9999-99-99"
        seq = vs.raw.get("discharge_sequence")
        seq = seq if seq is not None else 10**9
        return (urgent, boosted, cutoff, seq, vs.vehicle_id)

    return sorted(states, key=sort_key)


def _group_key(vs: _VehicleState, grouping_constraints: list[ParsedConstraint]) -> str | None:
    for c in grouping_constraints:
        target = c.target
        if target.vehicle_ids and vs.vehicle_id in target.vehicle_ids:
            return f"ids:{c.constraint_id}"
        if target.attribute and target.values:
            actual = vs.raw.get(target.attribute)
            if actual is not None and actual in target.values:
                return f"{target.attribute}:{','.join(sorted(target.values))}"
    return None


# --------------------------------------------------------------------------
# 슬롯 배정
# --------------------------------------------------------------------------


def _access_road(slot_id: str) -> mapf.Cell:
    row, col = ids.slot_cell(slot_id)
    return yard_grid.block_at(row, col).access_road_cell(row, col)


def _assign_slot(
    vs: _VehicleState,
    closed_blocks: set[str],
    empty_slots_by_block: dict[str, list[dict]],
    reservation: mapf.ReservationTable,
    *,
    start_time: int,
    prefer_block: str | None,
    prefer_shallow: bool,
) -> tuple[str | None, list[mapf.TimedCell]]:
    candidate_blocks = list(empty_slots_by_block.keys() - closed_blocks)
    if prefer_block and prefer_block not in closed_blocks and empty_slots_by_block.get(prefer_block):
        candidate_blocks = [prefer_block]

    origin_road = _access_road(vs.current_slot) if vs.current_slot else RAMP
    target_exit = TRAIN_EXIT if vs.next_mode == "RAIL" else TRUCK_EXIT

    candidates = [slot for block_id in candidate_blocks for slot in empty_slots_by_block.get(block_id, [])]

    if prefer_shallow:
        candidates.sort(key=lambda s: (s["depth"], _manhattan((s["row"], s["col"]), target_exit)))
    else:
        candidates.sort(key=lambda s: _manhattan((s["row"], s["col"]), origin_road))

    for slot in candidates[:MAX_SLOT_CANDIDATES]:
        goal_road = yard_grid.block_at(slot["row"], slot["col"]).access_road_cell(slot["row"], slot["col"])
        try:
            path = mapf.space_time_astar(reservation, origin_road, goal_road, start_time, vs.vehicle_id)
        except mapf.PathNotFound:
            continue
        return slot["slot_id"], path

    return None, []


def _mark_slot_taken(empty_slots_by_block: dict[str, list[dict]], slot_id: str) -> None:
    block_id, _, _ = ids.parse_slot_id(slot_id)
    empty_slots_by_block[block_id] = [
        s for s in empty_slots_by_block.get(block_id, []) if s["slot_id"] != slot_id
    ]


def _manhattan(a: mapf.Cell, b: mapf.Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _reason_for(vs: _VehicleState, closed_blocks: set[str]) -> str:
    if vs.current_slot is None:
        return "신규 배치"
    block_id, _, _ = ids.parse_slot_id(vs.current_slot)
    if block_id in closed_blocks:
        return f"{block_id} 블록 폐쇄로 인한 재배치"
    return "재배치"


def _path_distance_meters(path: list[mapf.TimedCell]) -> float:
    # 경로는 진입도로 ↔ 진입도로만 잰다. 슬롯 자체까지 왕복 2칸을 더한다.
    cells_moved = max(len(path) - 1, 0) + 2
    return round(cells_moved * settings.meters_per_cell, 1)


# --------------------------------------------------------------------------
# KPI / 버전
# --------------------------------------------------------------------------


def _compute_kpi(
    placements: dict[str, str], moves: list[Move], unplaced: list[str], calc_millis: int
) -> PlanKpi:
    changed = len(moves)
    total_placed = len(placements)
    avg_distance = sum(m.distance_meters for m in moves) / changed if changed else 0.0
    retention = ((total_placed - changed) / total_placed * 100) if total_placed else 100.0

    return PlanKpi(
        avg_move_distance=round(avg_distance, 1),
        rehandle_proxy=round(_rehandle_proxy(placements), 2),
        hard_violations=len(unplaced),
        changed_vehicles=changed,
        plan_retention_rate=round(retention, 1),
        calc_millis=calc_millis,
    )


def _rehandle_proxy(placements: dict[str, str]) -> float:
    """배정된 슬롯들의 평균 depth. depth 가 클수록(도로에서 멀수록) 나중에 빼기 어렵다는
    가정의 근사치다 — 실측 재취급 횟수가 아니다(docs/DOMAIN.md, 장표 9쪽 한계 명시)."""
    if not placements:
        return 0.0
    depths = []
    for slot_id in placements.values():
        row, col = ids.slot_cell(slot_id)
        depths.append(yard_grid.block_at(row, col).depth(row))
    return sum(depths) / len(depths)


def _next_plan_version(base: str | None) -> str:
    suffix = uuid.uuid4().hex[:6]
    return f"{base}-r{suffix}" if base else f"B0-r{suffix}"
