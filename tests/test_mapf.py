"""space_time_astar 순수 로직 — 격자/예약 장부만 있으면 되고 스프링·AI 호출 없이 돈다."""

from __future__ import annotations

import itertools

import pytest

from autoyard import mapf, yard_grid


def test_finds_a_path_between_two_road_cells():
    reservation = mapf.ReservationTable()
    path = mapf.space_time_astar(reservation, (0, 0), (0, 3), start_time=0, vehicle_id="V-0001")

    assert path[0] == (0, 0, 0)
    assert path[-1][:2] == (0, 3)
    # 도로 칸만 지나야 한다.
    for row, col, _ in path:
        assert yard_grid.is_road(row, col)


def test_consecutive_timesteps_are_adjacent_or_wait():
    reservation = mapf.ReservationTable()
    path = mapf.space_time_astar(reservation, (0, 0), (3, 0), start_time=0, vehicle_id="V-0001")

    for (r1, c1, t1), (r2, c2, t2) in itertools.pairwise(path):
        assert t2 == t1 + 1
        assert (r2, c2) == (r1, c1) or abs(r2 - r1) + abs(c2 - c1) == 1


def test_waits_instead_of_colliding_head_on():
    reservation = mapf.ReservationTable()
    # (0,2) 는 t=1 에 이미 V-0002 가 예약해 뒀다.
    reservation.reserve((0, 2), 1, "V-0002")

    # V-0001 은 (0,1) 에서 출발해서 (0,2) 로 가야 한다.
    path = mapf.space_time_astar(reservation, (0, 1), (0, 2), start_time=0, vehicle_id="V-0001")

    # t=1 에는 못 들어가고(자리 있음), 기다렸다가 나중에 들어가야 한다.
    step_at_t1 = next((r, c) for r, c, t in path if t == 1)
    assert step_at_t1 != (0, 2)
    assert path[-1][:2] == (0, 2)


def test_rejects_swap_conflict():
    reservation = mapf.ReservationTable()
    # V-0002 가 t=0 에 (0,5) 에 있다가 t=1 에 (0,4) 로 온다.
    reservation.reserve((0, 5), 0, "V-0002")
    reservation.reserve((0, 4), 1, "V-0002")

    # V-0001 이 반대로 t=0 (0,4) -> t=1 (0,5) 로 가려 하면, 좁은 도로에서 스쳐 지나가는 셈이라 막혀야 한다.
    path = mapf.space_time_astar(reservation, (0, 4), (0, 5), start_time=0, vehicle_id="V-0001")

    step_to_0_5_at_1 = any(r == 0 and c == 5 and t == 1 for r, c, t in path)
    assert not step_to_0_5_at_1


def test_start_equals_goal_returns_single_step():
    reservation = mapf.ReservationTable()
    path = mapf.space_time_astar(reservation, (0, 0), (0, 0), start_time=5, vehicle_id="V-0001")
    assert path == [(0, 0, 5)]


def test_raises_when_truly_boxed_in():
    reservation = mapf.ReservationTable()
    # (0,0) 의 도로 이웃 전부를 아주 오랫동안 V-0002 가 예약해서 빠져나갈 수 없게 만든다.
    for neighbor in yard_grid.neighbors(0, 0):
        reservation.reserve_hold(neighbor, 0, 500, "V-0002")

    with pytest.raises(mapf.PathNotFound):
        mapf.space_time_astar(
            reservation, (0, 0), (5, 5), start_time=0, vehicle_id="V-0001", max_horizon=50
        )


def test_reservation_table_swap_detection_ignores_self():
    reservation = mapf.ReservationTable()
    reservation.reserve((0, 0), 0, "V-0001")
    reservation.reserve((0, 1), 1, "V-0001")
    # 자기 자신과의 "스왑"은 충돌이 아니다.
    assert not reservation.would_swap((0, 1), (0, 0), 1, "V-0001")
