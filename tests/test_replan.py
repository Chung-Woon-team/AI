"""compute_replan 순수 로직. 실제 56×56 격자를 그대로 쓰되, 슬롯 몇 개만 골라 시나리오를 만든다.

DB·AI·스프링 없이 돈다 - /internal/replan 은 결정론적 코드라 이 정도로 충분히 검증된다.
"""

from __future__ import annotations

from autoyard import ids, replan
from autoyard.schemas import ParsedConstraint

B01_SLOT_A = ids.make_slot_id(4, 4)  # depth 0 - 도로에 바로 붙음
B01_SLOT_B = ids.make_slot_id(4, 5)  # depth 0
B01_SLOT_DEEP = ids.make_slot_id(6, 4)  # depth 2 - 가장 깊음 (5칸 레인의 한가운데)
B03_SLOT_A = ids.make_slot_id(13, 4)  # 다른 블록


def _slot(slot_id: str, status: str = "EMPTY") -> dict:
    block_id, _, _ = ids.parse_slot_id(slot_id)
    return {"slot_id": slot_id, "block_id": block_id, "status": status}


def _yard_state(*, blocks=None, slots=None, placements=None) -> dict:
    return {"blocks": blocks or [], "slots": slots or [], "placements": placements or {}}


def test_new_vehicle_gets_placed_and_recorded_as_a_move():
    yard_state = _yard_state(slots=[_slot(B01_SLOT_A)])
    vehicles = [{"vehicle_id": "V-0001", "status": "EXPECTED"}]

    result = replan.compute_replan(None, [], yard_state, vehicles)

    assert result.placements["V-0001"] == B01_SLOT_A
    assert len(result.moves) == 1
    assert result.moves[0].from_slot is None
    assert result.moves[0].to_slot == B01_SLOT_A
    # 프론트 애니메이션용 경로 - DB 에는 안 남지만 이 응답에는 있어야 한다.
    path = result.moves[0].path
    assert len(path) >= 2
    assert path[0].t < path[-1].t
    assert all(isinstance(step.row, int) and isinstance(step.col, int) for step in path)
    assert (path[-1].row, path[-1].col) == ids.slot_cell(B01_SLOT_A)
    assert result.moves[0].reason == "신규 배치"
    assert result.moves[0].distance_meters > 0
    assert result.kpi.changed_vehicles == 1


def test_already_parked_vehicle_in_open_block_stays_put():
    """Minimal Replanning - 이미 있는 자리가 멀쩡하면 안 건드린다."""
    yard_state = _yard_state(
        slots=[_slot(B01_SLOT_A, "OCCUPIED"), _slot(B01_SLOT_B)],
        placements={"V-0001": B01_SLOT_A},
    )
    vehicles = [{"vehicle_id": "V-0001", "status": "IN_YARD"}]

    result = replan.compute_replan(None, [], yard_state, vehicles)

    assert result.placements["V-0001"] == B01_SLOT_A
    assert result.moves == []
    assert result.kpi.changed_vehicles == 0
    assert result.kpi.plan_retention_rate == 100.0


def test_block_closure_forces_relocation():
    yard_state = _yard_state(
        blocks=[{"block_id": "B01", "closed": False}],
        slots=[_slot(B01_SLOT_A, "OCCUPIED"), _slot(B03_SLOT_A)],
        placements={"V-0001": B01_SLOT_A},
    )
    vehicles = [{"vehicle_id": "V-0001", "status": "IN_YARD"}]
    closure = ParsedConstraint(
        constraint_id="C-001",
        type="BLOCK_CLOSURE",
        target={"block_ids": ["B01"]},
        priority="HARD",
        confidence=0.99,
    )

    result = replan.compute_replan(None, [closure], yard_state, vehicles)

    assert result.placements["V-0001"] == B03_SLOT_A
    assert len(result.moves) == 1
    assert result.moves[0].from_slot == B01_SLOT_A
    assert result.moves[0].to_slot == B03_SLOT_A
    assert "폐쇄" in result.moves[0].reason


def test_unplaced_when_no_open_slot_exists():
    yard_state = _yard_state(
        blocks=[{"block_id": "B01", "closed": True}],
        slots=[_slot(B01_SLOT_A)],  # 유일한 빈 자리인데 블록 자체가 닫힘
    )
    vehicles = [{"vehicle_id": "V-0001", "status": "EXPECTED"}]

    result = replan.compute_replan(None, [], yard_state, vehicles)

    assert result.unplaced == ["V-0001"]
    assert result.kpi.hard_violations == 1
    assert "V-0001" not in result.placements


def test_departed_vehicles_are_ignored():
    yard_state = _yard_state(slots=[_slot(B01_SLOT_A)])
    vehicles = [{"vehicle_id": "V-0001", "status": "DEPARTED"}]

    result = replan.compute_replan(None, [], yard_state, vehicles)

    assert result.placements == {}
    assert result.moves == []
    assert result.unplaced == []


def test_vehicle_grouping_puts_matching_brands_in_the_same_block():
    yard_state = _yard_state(slots=[_slot(B01_SLOT_A), _slot(B01_SLOT_B), _slot(B03_SLOT_A)])
    vehicles = [
        {"vehicle_id": "V-0001", "status": "EXPECTED", "brand": "B"},
        {"vehicle_id": "V-0002", "status": "EXPECTED", "brand": "B"},
    ]
    grouping = ParsedConstraint(
        constraint_id="C-001",
        type="VEHICLE_GROUPING",
        target={"attribute": "brand", "values": ["B"]},
        priority="SOFT",
        confidence=0.9,
    )

    result = replan.compute_replan(None, [grouping], yard_state, vehicles)

    block1, _, _ = ids.parse_slot_id(result.placements["V-0001"])
    block2, _, _ = ids.parse_slot_id(result.placements["V-0002"])
    assert block1 == block2


def test_outbound_priority_prefers_shallow_slots_for_matching_vehicles():
    yard_state = _yard_state(slots=[_slot(B01_SLOT_DEEP), _slot(B01_SLOT_A)])
    vehicles = [{
        "vehicle_id": "V-0001",
        "status": "EXPECTED",
        "departure_cutoff_at": "2026-08-14T09:00:00",
    }]
    outbound = ParsedConstraint(
        constraint_id="C-001",
        type="OUTBOUND_PRIORITY",
        target={"filter": {"cutoff_date": "2026-08-14"}},
        priority="SOFT",
        confidence=0.8,
    )

    result = replan.compute_replan(None, [outbound], yard_state, vehicles)

    # depth 0 인 B01_SLOT_A 를 골라야 한다 - 도로(출구)에 더 가까운 얕은 자리.
    assert result.placements["V-0001"] == B01_SLOT_A


def test_plan_version_chains_from_base():
    yard_state = _yard_state(slots=[_slot(B01_SLOT_A)])
    result = replan.compute_replan("B0", [], yard_state, [])

    assert result.based_on_version == "B0"
    assert result.plan_version.startswith("B0-r")


def test_five_new_vehicles_get_contiguous_fifo_slots_and_staggered_paths():
    slots = [_slot(ids.make_slot_id(4, col)) for col in range(4, 10)]
    vehicles = [
        {
            "vehicle_id": f"V-{index:04d}",
            "status": "EXPECTED",
            "next_mode": "ROAD",
            "discharge_sequence": index,
        }
        for index in range(1, 6)
    ]

    result = replan.compute_replan(None, [], _yard_state(slots=slots), vehicles)

    moves = sorted(result.moves, key=lambda move: move.sequence)
    assigned = [ids.slot_cell(move.to_slot) for move in moves]
    assert len(moves) == 5
    assert len({row for row, _ in assigned}) == 1
    assert sorted(col for _, col in assigned) == list(range(5, 10))
    # 트럭 출구가 오른쪽이므로 선두 차량이 가장 오른쪽(출구 쪽) 슬롯을 받는다.
    assert assigned[0][1] == 9
    assert [move.path[0].t for move in moves] == [0, 1, 2, 3, 4]
    for move in moves:
        assert (move.path[-1].row, move.path[-1].col) == ids.slot_cell(move.to_slot)
        assert all(b.t == a.t + 1 for a, b in zip(move.path, move.path[1:]))


def test_parked_vehicle_blocks_the_slot_entry_corridor():
    """깊은 슬롯 앞에 주차된 차량이 있으면 그 차량을 관통해 배치하면 안 된다."""
    blocking_slot = ids.make_slot_id(4, 4)
    blocked_deep_slot = ids.make_slot_id(6, 4)
    yard_state = _yard_state(
        slots=[_slot(blocking_slot, "OCCUPIED"), _slot(blocked_deep_slot)],
        placements={"V-9000": blocking_slot},
    )
    vehicles = [
        {"vehicle_id": "V-9000", "status": "IN_YARD"},
        {"vehicle_id": "V-0001", "status": "EXPECTED"},
    ]

    result = replan.compute_replan(None, [], yard_state, vehicles)

    assert "V-0001" not in result.placements
    assert result.unplaced == ["V-0001"]


def test_path_uses_an_unblocked_lane_instead_of_crossing_a_parked_vehicle():
    blocking_slot = ids.make_slot_id(4, 4)
    blocked_deep_slot = ids.make_slot_id(6, 4)
    clear_deep_slot = ids.make_slot_id(6, 5)
    yard_state = _yard_state(
        slots=[
            _slot(blocking_slot, "OCCUPIED"),
            _slot(blocked_deep_slot),
            _slot(clear_deep_slot),
        ],
        placements={"V-9000": blocking_slot},
    )
    vehicles = [
        {"vehicle_id": "V-9000", "status": "IN_YARD"},
        {"vehicle_id": "V-0001", "status": "EXPECTED"},
    ]

    result = replan.compute_replan(None, [], yard_state, vehicles)

    move = result.moves[0]
    assert move.to_slot == clear_deep_slot
    assert ids.slot_cell(blocking_slot) not in {(step.row, step.col) for step in move.path}
