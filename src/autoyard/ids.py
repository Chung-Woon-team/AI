"""ID 규칙 한 곳.

AI활용방안 8절 "ID 규칙 통일"을 코드로 강제한다. 파서가 지어낸 ID(환각)를 여기서 거른다.
스프링 쪽 PK 형식과 반드시 같아야 한다 — 한쪽만 고치면 연동이 조용히 깨진다.
"""

from __future__ import annotations

import re

VEHICLE_ID = re.compile(r"^V-\d{4}$")
BLOCK_ID = re.compile(r"^B\d{2}$")
SLOT_ID = re.compile(r"^B\d{2}-L\d{2}-D\d{2}$")
CONSTRAINT_ID = re.compile(r"^C-\d{3}$")
INSTRUCTION_ID = re.compile(r"^INS-\d{3}$")


def make_slot_id(block_id: str, lane: int, depth: int) -> str:
    """B03, 2, 4 -> "B03-L02-D04" """
    return f"{block_id}-L{lane:02d}-D{depth:02d}"


def parse_slot_id(slot_id: str) -> tuple[str, int, int]:
    """"B03-L02-D04" -> ("B03", 2, 4)"""
    if not SLOT_ID.match(slot_id):
        raise ValueError(f"슬롯 ID 형식이 아닙니다: {slot_id!r}")
    block, lane, depth = slot_id.split("-")
    return block, int(lane[1:]), int(depth[1:])


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
    return bool(patterns[kind].match(value))
