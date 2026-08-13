"""야드 격자. 팀 도면 그대로다.

스프링의 ``domain/yard/YardGrid.java`` 와 ``BlockLayout.java`` 의 거울이다.
한쪽만 고치면 배치 결과가 조용히 어긋나므로 숫자를 바꿀 때는 양쪽을 같이 고칠 것.

::

    행: 4 +  5 + 4 +  5 + 4 = 22
    열: 4 + 17 + 4 + 17 + 4 = 46

    열 →  0    4              21   25              42  46
    행 0 ┌───────────────────────────────────────────┐
         │            외곽 도로 (폭 4)                │
       4 ├────┬──────────────┬────┬──────────────┬───┤
         │도로│  B01  5×17   │통로│  B02  5×17   │도로│
       9 ├────┼──────────────┼────┼──────────────┼───┤
         │            십자 통로 (폭 4)                │
      13 ├────┼──────────────┼────┼──────────────┼───┤
         │도로│  B03  5×17   │통로│  B04  5×17   │도로│
      18 ├────┴──────────────┴────┴──────────────┴───┤
         │            외곽 도로 (폭 4)                │
      22 └───────────────────────────────────────────┘

주차칸 5×17×4 = 340, 도로칸 22×46 − 340 = 672.

**정사각형이 아니다.** 블록이 가로로 길다(폭 17 × 깊이 5) — 차 한 대가 폭 2.5m · 길이 5m 라
항공사진에서도 블록이 가로로 긴 직사각형으로 보인다. 그래서 행 수와 열 수를 따로 둔다.

블록은 네 면이 모두 도로에 닿는다. 그래서 한 레인(세로 5칸)은 위·아래 양쪽에서 진입하고,
depth 는 "가장 가까운 도로에서 몇 번째"(0~2)로 센다. 어느 쪽인지는 access_side 가 가린다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Literal

ROAD_WIDTH = 4
BLOCK_ROWS = 5
BLOCK_COLS = 17
BLOCK_COUNT = 4

ROW_COUNT = ROAD_WIDTH * 3 + BLOCK_ROWS * 2
COL_COUNT = ROAD_WIDTH * 3 + BLOCK_COLS * 2

FIRST_BLOCK_ORIGIN = ROAD_WIDTH
SECOND_BLOCK_ORIGIN_ROW = ROAD_WIDTH + BLOCK_ROWS + ROAD_WIDTH
SECOND_BLOCK_ORIGIN_COL = ROAD_WIDTH + BLOCK_COLS + ROAD_WIDTH

LANES_PER_BLOCK = BLOCK_COLS
# 5칸을 위 3 / 아래 2 로 가른다(홀수라 위쪽이 한 칸 더 깊다).
DEPTH_PER_LANE = (BLOCK_ROWS + 1) // 2

SLOTS_PER_BLOCK = BLOCK_ROWS * BLOCK_COLS
SLOT_COUNT = BLOCK_COUNT * SLOTS_PER_BLOCK
ROAD_CELL_COUNT = ROW_COUNT * COL_COUNT - SLOT_COUNT

AccessSide = Literal["NORTH", "SOUTH"]

Cell = tuple[int, int]
"""야드 절대 좌표 (row, col)."""


@dataclass(frozen=True)
class BlockLayout:
    """도면에 그려진 블록 하나의 고정 위치."""

    block_id: str
    zone_code: str
    origin_row: int
    origin_col: int

    @property
    def last_row(self) -> int:
        return self.origin_row + BLOCK_ROWS - 1

    @property
    def last_col(self) -> int:
        return self.origin_col + BLOCK_COLS - 1

    def contains(self, row: int, col: int) -> bool:
        return (
            self.origin_row <= row <= self.last_row
            and self.origin_col <= col <= self.last_col
        )

    def lane(self, col: int) -> int:
        """레인 = 블록 안의 세로 열. 0 ~ 16."""
        self._require_col(col)
        return col - self.origin_col

    def access_side(self, row: int) -> AccessSide:
        self._require_row(row)
        return "NORTH" if (row - self.origin_row) < DEPTH_PER_LANE else "SOUTH"

    def depth(self, row: int) -> int:
        """진입 도로에서 몇 번째 자리인지. 0 ~ 2, 0 이 도로에 바로 붙은 칸."""
        self._require_row(row)
        offset = row - self.origin_row
        return offset if offset < DEPTH_PER_LANE else BLOCK_ROWS - 1 - offset

    def access_road_cell(self, row: int, col: int) -> Cell:
        """그 자리로 드나드는 도로 칸. 경로 알고리즘의 출발·도착점."""
        side = self.access_side(row)
        self._require_col(col)
        return (self.origin_row - 1 if side == "NORTH" else self.last_row + 1, col)

    def cells(self) -> Iterator[Cell]:
        for row in range(self.origin_row, self.last_row + 1):
            for col in range(self.origin_col, self.last_col + 1):
                yield (row, col)

    def _require_row(self, row: int) -> None:
        if not self.origin_row <= row <= self.last_row:
            raise ValueError(f"행 {row} 은(는) 블록 {self.block_id} 밖입니다")

    def _require_col(self, col: int) -> None:
        if not self.origin_col <= col <= self.last_col:
            raise ValueError(f"열 {col} 은(는) 블록 {self.block_id} 밖입니다")


# zone_code 는 아직 팀에서 확정하지 않은 임시값이다. 스프링 BlockLayout 과 같은 순서·같은 값.
BLOCKS: tuple[BlockLayout, ...] = (
    BlockLayout("B01", "EV-A", FIRST_BLOCK_ORIGIN, FIRST_BLOCK_ORIGIN),
    BlockLayout("B02", "GEN-B", FIRST_BLOCK_ORIGIN, SECOND_BLOCK_ORIGIN_COL),
    BlockLayout("B03", "QC-HOLD", SECOND_BLOCK_ORIGIN_ROW, FIRST_BLOCK_ORIGIN),
    BlockLayout("B04", "HVY-D", SECOND_BLOCK_ORIGIN_ROW, SECOND_BLOCK_ORIGIN_COL),
)


def block_of(block_id: str) -> BlockLayout:
    for block in BLOCKS:
        if block.block_id == block_id:
            return block
    raise ValueError(f"도면에 없는 블록: {block_id!r}")


def is_inside(row: int, col: int) -> bool:
    return 0 <= row < ROW_COUNT and 0 <= col < COL_COUNT


def block_at(row: int, col: int) -> BlockLayout | None:
    """이 칸이 속한 블록. 도로면 None."""
    if not is_inside(row, col):
        return None
    for block in BLOCKS:
        if block.contains(row, col):
            return block
    return None


def is_road(row: int, col: int) -> bool:
    """차가 지나다닐 수 있는 칸인지. 외곽 도로와 십자 통로 둘 다 True."""
    return is_inside(row, col) and block_at(row, col) is None


def is_slot(row: int, col: int) -> bool:
    return block_at(row, col) is not None


def road_cells() -> list[Cell]:
    """경로 알고리즘이 그래프로 쓸 도로 칸 전부."""
    return [
        (row, col)
        for row in range(ROW_COUNT)
        for col in range(COL_COUNT)
        if is_road(row, col)
    ]


def slot_cells() -> list[Cell]:
    """슬롯 칸 전부. 블록 순서 → 행 → 열."""
    return [cell for block in BLOCKS for cell in block.cells()]


def neighbors(row: int, col: int) -> list[Cell]:
    """상하좌우로 붙어 있는 도로 칸. 경로 탐색의 간선."""
    candidates = [(row - 1, col), (row + 1, col), (row, col - 1), (row, col + 1)]
    return [(r, c) for r, c in candidates if is_road(r, c)]
