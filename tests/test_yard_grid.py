"""도면(4 + 22 + 4 + 22 + 4)과 코드가 어긋나면 여기서 깨진다.

스프링 쪽 YardGridTest 와 같은 것을 본다. 두 테스트가 같이 초록이어야 연동이 맞는 것이다.
"""

from __future__ import annotations

import pytest

from autoyard import ids, yard_grid


def test_grid_counts():
    assert yard_grid.SIZE == 56
    assert (
        yard_grid.ROAD_WIDTH
        + yard_grid.BLOCK_SIZE
        + yard_grid.ROAD_WIDTH
        + yard_grid.BLOCK_SIZE
        + yard_grid.ROAD_WIDTH
    ) == yard_grid.SIZE

    assert len(yard_grid.BLOCKS) == yard_grid.BLOCK_COUNT
    assert yard_grid.SLOT_COUNT == 1936
    assert yard_grid.ROAD_CELL_COUNT == 1200
    assert len(yard_grid.slot_cells()) == yard_grid.SLOT_COUNT
    assert len(yard_grid.road_cells()) == yard_grid.ROAD_CELL_COUNT


def test_every_cell_is_either_road_or_slot():
    road = sum(
        1
        for row in range(yard_grid.SIZE)
        for col in range(yard_grid.SIZE)
        if yard_grid.is_road(row, col)
    )
    slot = sum(
        1
        for row in range(yard_grid.SIZE)
        for col in range(yard_grid.SIZE)
        if yard_grid.is_slot(row, col)
    )
    assert road == yard_grid.ROAD_CELL_COUNT
    assert slot == yard_grid.SLOT_COUNT
    assert road + slot == yard_grid.SIZE**2


def test_block_bounds_match_drawing():
    b01 = yard_grid.block_of("B01")
    assert (b01.origin_row, b01.origin_col) == (4, 4)
    assert (b01.last_row, b01.last_col) == (25, 25)

    b04 = yard_grid.block_of("B04")
    assert (b04.origin_row, b04.origin_col) == (30, 30)
    assert (b04.last_row, b04.last_col) == (51, 51)

    # 십자 통로와 바깥 테두리는 도로다.
    assert yard_grid.is_road(26, 10)
    assert yard_grid.is_road(10, 26)
    assert yard_grid.is_road(0, 0)
    assert yard_grid.is_road(55, 55)


def test_every_block_touches_road_on_all_four_sides():
    for block in yard_grid.BLOCKS:
        mid_row = block.origin_row + yard_grid.BLOCK_SIZE // 2
        mid_col = block.origin_col + yard_grid.BLOCK_SIZE // 2
        assert yard_grid.is_road(block.origin_row - 1, mid_col)
        assert yard_grid.is_road(block.last_row + 1, mid_col)
        assert yard_grid.is_road(mid_row, block.origin_col - 1)
        assert yard_grid.is_road(mid_row, block.last_col + 1)


def test_depth_is_measured_from_nearest_road():
    b = yard_grid.block_of("B01")

    assert b.depth(b.origin_row) == 0
    assert b.access_side(b.origin_row) == "NORTH"
    assert b.depth(b.last_row) == 0
    assert b.access_side(b.last_row) == "SOUTH"

    # 가운데 두 칸이 가장 깊다.
    assert b.depth(b.origin_row + 10) == 10
    assert b.access_side(b.origin_row + 10) == "NORTH"
    assert b.depth(b.origin_row + 11) == 10
    assert b.access_side(b.origin_row + 11) == "SOUTH"

    for row in range(b.origin_row, b.last_row + 1):
        assert 0 <= b.depth(row) <= yard_grid.DEPTH_PER_LANE - 1


def test_lane_position_is_unique():
    keys = set()
    for row, col in yard_grid.slot_cells():
        block = yard_grid.block_at(row, col)
        assert block is not None
        keys.add((block.block_id, block.lane(col), block.access_side(row), block.depth(row)))
    assert len(keys) == yard_grid.SLOT_COUNT


def test_slot_id_round_trip():
    seen = set()
    for row, col in yard_grid.slot_cells():
        slot_id = ids.make_slot_id(row, col)
        seen.add(slot_id)
        assert ids.slot_cell(slot_id) == (row, col)
    assert len(seen) == yard_grid.SLOT_COUNT

    assert ids.make_slot_id(4, 7) == "B01-R04-C07"
    assert ids.parse_slot_id("B01-R04-C07") == ("B01", 4, 7)
    assert ids.make_slot_id(51, 51) == "B04-R51-C51"


def test_road_cell_has_no_slot_id():
    with pytest.raises(ValueError):
        ids.make_slot_id(26, 26)
    with pytest.raises(ValueError):
        ids.parse_slot_id("B01-R26-C26")
    assert not ids.is_valid("slot", "B01-R26-C26")
    assert not ids.is_valid("slot", "B03-L02-D04")  # 옛 형식
    assert ids.is_valid("slot", "B01-R04-C07")


def test_access_road_cell_is_adjacent_road():
    b03 = yard_grid.block_of("B03")

    assert b03.access_side(30) == "NORTH"
    assert b03.access_road_cell(30, 12) == (29, 12)
    assert yard_grid.is_road(29, 12)

    assert b03.access_side(51) == "SOUTH"
    assert b03.access_road_cell(51, 12) == (52, 12)
    assert yard_grid.is_road(52, 12)


def test_neighbors_are_roads_only():
    # 십자 교차부 한가운데는 사방이 도로다.
    assert len(yard_grid.neighbors(27, 27)) == 4
    # 블록에 붙은 도로 칸은 블록 쪽으로 못 간다.
    assert (4, 12) not in yard_grid.neighbors(3, 12)
    # 야드 모서리는 바깥으로 못 나간다.
    assert len(yard_grid.neighbors(0, 0)) == 2
