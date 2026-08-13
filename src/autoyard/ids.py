"""ID 규칙 한 곳.

AI활용방안 8절 "ID 규칙 통일"을 코드로 강제한다. 파서가 지어낸 ID(환각)를 여기서 거른다.
스프링 쪽 PK 형식과 반드시 같아야 한다 — 한쪽만 고치면 연동이 조용히 깨진다.
"""

from __future__ import annotations

import re

from autoyard import yard_grid

VEHICLE_ID = re.compile(r"^V-\d{4}$")
BLOCK_ID = re.compile(r"^B\d{2}$")
# 슬롯 ID 는 야드 절대 좌표를 품는다. 사진 인식 결과 (row, col) 를 그대로 뒤집을 수 있게 한 것.
SLOT_ID = re.compile(r"^B\d{2}-R\d{2}-C\d{2}$")
CONSTRAINT_ID = re.compile(r"^C-\d{3}$")
INSTRUCTION_ID = re.compile(r"^INS-\d{3}$")


def make_slot_id(row: int, col: int) -> str:
    """(4, 7) -> "B01-R04-C07". 도로 칸이면 ValueError."""
    block = yard_grid.block_at(row, col)
    if block is None:
        raise ValueError(f"({row}, {col}) 은(는) 도로 칸이라 슬롯이 없습니다")
    return f"{block.block_id}-R{row:02d}-C{col:02d}"


def parse_slot_id(slot_id: str) -> tuple[str, int, int]:
    """"B01-R04-C07" -> ("B01", 4, 7)"""
    if not SLOT_ID.match(slot_id):
        raise ValueError(f"슬롯 ID 형식이 아닙니다: {slot_id!r}")
    block_id, row_part, col_part = slot_id.split("-")
    row, col = int(row_part[1:]), int(col_part[1:])
    block = yard_grid.block_at(row, col)
    if block is None or block.block_id != block_id:
        raise ValueError(f"격자에 없는 슬롯입니다: {slot_id!r}")
    return block_id, row, col


def slot_cell(slot_id: str) -> yard_grid.Cell:
    """슬롯 ID 를 야드 절대 좌표로. 경로·시각화가 쓴다."""
    _, row, col = parse_slot_id(slot_id)
    return (row, col)


def is_valid(kind: str, value: str) -> bool:
    patterns = {
        "vehicle": VEHICLE_ID,
        "block": BLOCK_ID,
        "slot": SLOT_ID,
        "constraint": CONSTRAINT_ID,
        "instruction": INSTRUCTION_ID,
    }
    if kind not in patterns:
        raise KeyError(f"모르는 ID 종류: {kind}")
    if not patterns[kind].match(value):
        return False
    if kind == "slot":
        # 형식이 맞아도 격자 밖(예: 도로 칸)이면 가짜 ID 다.
        try:
            parse_slot_id(value)
        except ValueError:
            return False
    return True
