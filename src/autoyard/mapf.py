"""시공간(Multi-Agent Path Finding) 경로 탐색.

`mapf.ipynb`(알고리즘 파트가 준 원본 시뮬레이션)를 이 프로젝트의 야드 모델에 맞게 옮긴 것이다.
바뀐 것과 그대로인 것:

- **그대로**: 시간 축이 있는 A*("제자리 대기"도 이동으로 취급), 시공간 예약 장부로 정면충돌·
  교차충돌(스왑) 방지.
- **바뀐 것**: 원본은 56×56 칸 전체가 이동 가능하고 "남의 구역을 밟으면 비용 100배" 페널티로
  우회시켰다. 이 프로젝트의 `yard_grid.neighbors()`는 애초에 **도로 칸만** 이웃으로 내준다
  (슬롯은 이웃 그래프에 아예 없음) — 그래서 페널티가 필요 없다. 슬롯을 가로지르는 경로 자체가
  후보에 안 들어오기 때문에 "남의 구역 침범 방지"가 공짜로 딸려온다.

차량 한 대의 이동은 항상 "슬롯의 진입 도로 칸 ↔ 슬롯의 진입 도로 칸"으로 계산한다. 슬롯 자체는
경로에 포함하지 않는다 — `distance_meters` 계산 시 양 끝에 1칸씩 더해주는 걸로 충분하다.
"""

from __future__ import annotations

import heapq

from autoyard import yard_grid

Cell = tuple[int, int]
"""야드 절대 좌표 (row, col)."""

TimedCell = tuple[int, int, int]
"""(row, col, t)."""


class ReservationTable:
    """"이 시각, 이 칸은 이 차량이 쓴다"를 기록하는 시공간 예약 장부.

    두 가지 충돌을 막는다.
    - 정면충돌: 같은 (row, col, t) 를 두 차량이 동시에 예약하려는 경우.
    - 교차충돌(스왑): A 는 t-1→t 에 X→Y 로 가려 하고, B 는 같은 구간에 Y→X 로 가려는 경우
      (좁은 도로에서 서로 자리를 맞바꾸는 건 불가능하다).
    """

    def __init__(self) -> None:
        self._by_time: dict[int, dict[Cell, str]] = {}

    def occupant_at(self, cell: Cell, t: int) -> str | None:
        return self._by_time.get(t, {}).get(cell)

    def is_free(self, cell: Cell, t: int, vehicle_id: str) -> bool:
        occupant = self.occupant_at(cell, t)
        return occupant is None or occupant == vehicle_id

    def would_swap(self, from_cell: Cell, to_cell: Cell, t: int, vehicle_id: str) -> bool:
        mover = self.occupant_at(to_cell, t - 1)
        if mover is None or mover == vehicle_id:
            return False
        return self.occupant_at(from_cell, t) == mover

    def reserve(self, cell: Cell, t: int, vehicle_id: str) -> None:
        self._by_time.setdefault(t, {})[cell] = vehicle_id

    def reserve_path(self, path: list[TimedCell], vehicle_id: str) -> None:
        for row, col, t in path:
            self.reserve((row, col), t, vehicle_id)

    def reserve_hold(self, cell: Cell, start_t: int, end_t: int, vehicle_id: str) -> None:
        """차가 그 칸에 머무는 동안(주차 대기 등) 계속 예약해 둔다."""
        for t in range(start_t, end_t + 1):
            self.reserve(cell, t, vehicle_id)


class PathNotFound(Exception):
    """예약된 경로들 때문에, 또는 격자 구조상 목적지에 닿을 수 없을 때."""


def space_time_astar(
    reservation: ReservationTable,
    start: Cell,
    goal: Cell,
    start_time: int,
    vehicle_id: str,
    max_horizon: int = 400,
) -> list[TimedCell]:
    """도로 칸(`yard_grid.neighbors`)만 지나서 start → goal 로 가는 시공간 경로.

    막다른 예약 때문에 `max_horizon` 스텝 안에 못 찾으면 `PathNotFound`.
    """
    if start == goal:
        return [(start[0], start[1], start_time)]

    h_start = _manhattan(start, goal)
    open_set: list[tuple[int, int, int, int, int]] = [(h_start, 0, start[0], start[1], start_time)]
    came_from: dict[TimedCell, TimedCell] = {}
    g_score: dict[TimedCell, int] = {(start[0], start[1], start_time): 0}
    deadline = start_time + max_horizon

    while open_set:
        _, g, cx, cy, ct = heapq.heappop(open_set)

        if (cx, cy) == goal:
            return _reconstruct(came_from, (cx, cy, ct))
        if ct >= deadline:
            continue

        candidates = yard_grid.neighbors(cx, cy)
        candidates.append((cx, cy))  # 제자리 대기도 이동의 하나다.

        for nx, ny in candidates:
            nt = ct + 1
            if not reservation.is_free((nx, ny), nt, vehicle_id):
                continue
            if reservation.would_swap((cx, cy), (nx, ny), nt, vehicle_id):
                continue

            new_g = g + 1
            key = (nx, ny, nt)
            if key not in g_score or new_g < g_score[key]:
                g_score[key] = new_g
                h = _manhattan((nx, ny), goal)
                heapq.heappush(open_set, (new_g + h, new_g, nx, ny, nt))
                came_from[key] = (cx, cy, ct)

    raise PathNotFound(
        f"{vehicle_id}: {start} -> {goal} (t={start_time}) 경로를 {max_horizon}스텝 안에 못 찾음"
    )


def _manhattan(a: Cell, b: Cell) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _reconstruct(came_from: dict[TimedCell, TimedCell], end: TimedCell) -> list[TimedCell]:
    path = [end]
    cur = end
    while cur in came_from:
        cur = came_from[cur]
        path.append(cur)
    path.reverse()
    return path
