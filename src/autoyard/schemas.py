"""Pydantic 스키마 — AI 출력의 통과 관문.

AI활용방안 8절: "모든 AI 출력은 Pydantic 검증을 통과해야 다음 단계로 전달됨."
그래서 여기 정의는 문서의 JSON 예시와 필드명·값까지 1:1로 맞춰져 있다.
스프링 엔티티(PlanConstraint, Vehicle, ObservationSnapshot …)와도 같은 이름을 쓴다.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autoyard import ids


class ConstraintType(str, Enum):
    BLOCK_CLOSURE = "BLOCK_CLOSURE"
    VEHICLE_GROUPING = "VEHICLE_GROUPING"
    OUTBOUND_PRIORITY = "OUTBOUND_PRIORITY"


class ConstraintPriority(str, Enum):
    HARD = "HARD"
    SOFT = "SOFT"


class ConstraintStatus(str, Enum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class NextMode(str, Enum):
    TRUCK = "TRUCK"
    RAIL = "RAIL"
    SHIP = "SHIP"


class DocumentType(str, Enum):
    BILL_OF_LADING = "BILL_OF_LADING"
    SEA_WAYBILL = "SEA_WAYBILL"
    STRAIGHT_BILL_OF_LADING = "STRAIGHT_BILL_OF_LADING"


class Powertrain(str, Enum):
    BATTERY_EV = "BATTERY_EV"
    GASOLINE = "GASOLINE"
    DIESEL = "DIESEL"
    HYBRID = "HYBRID"


class UnloadingPriority(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class VehicleStatus(str, Enum):
    EXPECTED = "EXPECTED"
    IN_YARD = "IN_YARD"
    DEPARTED = "DEPARTED"


class ObservationSource(str, Enum):
    FIXED_CAMERA = "FIXED_CAMERA"
    DRONE = "DRONE"
    MANUAL = "MANUAL"


# --------------------------------------------------------------------------
# ① 제약 파서 출력
# --------------------------------------------------------------------------


class TimeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime | None = None
    end: datetime | None = None

    @model_validator(mode="after")
    def _order(self) -> TimeWindow:
        if self.start and self.end and self.end <= self.start:
            raise ValueError("time_window.end 가 start 보다 앞서거나 같습니다")
        return self


class ConstraintTarget(BaseModel):
    """무엇에 적용되는 제약인지. type 에 따라 채워지는 필드가 다르다."""

    model_config = ConfigDict(extra="forbid")

    block_ids: list[str] | None = None
    attribute: str | None = None
    values: list[str] | None = None
    vehicle_ids: list[str] | None = None
    filter: dict[str, Any] | None = None

    @field_validator("block_ids")
    @classmethod
    def _block_format(cls, v: list[str] | None) -> list[str] | None:
        for b in v or []:
            if not ids.is_valid("block", b):
                raise ValueError(f"블록 ID 형식이 아닙니다: {b!r} (예: B03)")
        return v

    @field_validator("vehicle_ids")
    @classmethod
    def _vehicle_format(cls, v: list[str] | None) -> list[str] | None:
        for x in v or []:
            if not ids.is_valid("vehicle", x):
                raise ValueError(f"차량 ID 형식이 아닙니다: {x!r} (예: V-0001)")
        return v


class ParsedConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    constraint_id: str
    type: ConstraintType
    target: ConstraintTarget
    value: dict[str, Any] | None = None
    time_window: TimeWindow | None = None
    priority: ConstraintPriority
    confidence: float = Field(ge=0.0, le=1.0)
    status: ConstraintStatus = ConstraintStatus.PENDING_REVIEW

    @field_validator("constraint_id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not ids.is_valid("constraint", v):
            raise ValueError(f"제약 ID 형식이 아닙니다: {v!r} (예: C-001)")
        return v

    @model_validator(mode="after")
    def _target_matches_type(self) -> ParsedConstraint:
        """타입별로 반드시 있어야 할 target 필드를 강제한다."""
        if self.type is ConstraintType.BLOCK_CLOSURE and not self.target.block_ids:
            raise ValueError("BLOCK_CLOSURE 는 target.block_ids 가 필요합니다")
        if self.type is ConstraintType.VEHICLE_GROUPING and not (
            self.target.attribute or self.target.vehicle_ids
        ):
            raise ValueError("VEHICLE_GROUPING 은 target.attribute 또는 vehicle_ids 가 필요합니다")
        return self


class ParseResult(BaseModel):
    """자연어 지시 한 건의 파싱 결과 전체."""

    model_config = ConfigDict(extra="forbid")

    instruction_id: str
    constraints: list[ParsedConstraint] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False

    @field_validator("instruction_id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not ids.is_valid("instruction", v):
            raise ValueError(f"지시 ID 형식이 아닙니다: {v!r} (예: INS-001)")
        return v

    @model_validator(mode="after")
    def _confirmation_flag(self) -> ParseResult:
        """미해결 표현이 남았는데 확인 불필요로 나오는 건 파서 버그다. 여기서 바로잡는다."""
        if self.unresolved and not self.requires_confirmation:
            object.__setattr__(self, "requires_confirmation", True)
        return self


# --------------------------------------------------------------------------
# ② 선하증권 → 차량 입고 데이터
# --------------------------------------------------------------------------


class CargoLine(BaseModel):
    """품목 한 줄. 한 문서에 여러 줄이 올 수 있다.

    샘플 DOC 03 이 그렇다 — "전기버스 10대(3.25m) + 대형 전기트럭 8대(3.80m) = 18 UNITS".
    평평한 구조로 받으면 이 문서를 표현할 수 없다.
    """

    model_config = ConfigDict(extra="forbid")

    description: str
    unit_count: int = Field(gt=0)
    brand: str | None = None
    model: str | None = None
    height_meters: float | None = None
    driveable: bool | None = None


class BillOfLadingExtraction(BaseModel):
    """Gemini 가 선하증권 이미지에서 뽑아내는 것 = **문서에 적힌 그대로**.

    개별 차량 60행은 여기 없다. 문서에 없기 때문이다(VIN 은 범위, 자력주행은 집계).
    차량 행 전개는 이 객체를 받아 결정론적 코드가 한다 — AI 에게 시키면 없는 VIN 을 지어낸다.
    """

    model_config = ConfigDict(extra="forbid")

    # 코드 번호
    bl_number: str
    document_type: DocumentType
    booking_number: str | None = None
    lot_code: str | None = None
    linked_route_code: str | None = None

    # 선박 / 항로
    vessel_name: str | None = None
    voyage_number: str | None = None
    port_of_loading: str | None = None
    port_of_discharge: str | None = None
    issue_date: date | None = None

    # 당사자
    shipper_name: str | None = None
    consignee_name: str | None = None
    notify_party: str | None = None

    # 화물
    cargo_lines: list[CargoLine] = Field(default_factory=list)
    unit_count: int = Field(gt=0)
    gross_weight_kg: int | None = None
    measurement_cbm: float | None = None

    # VIN 은 범위로만 온다. 예: ("SYNT26E0000000001", "SYNT26E0000000060")
    vin_range_from: str | None = None
    vin_range_to: str | None = None

    # PCTC DISCHARGE DATA (최적화 입력)
    powertrain: Powertrain | None = None
    driveable_count: int | None = Field(default=None, ge=0)
    tow_count: int | None = Field(default=None, ge=0)
    # 견인이 필요한 개별 단위 번호. DOC 02 의 "UNITS 017 & 031" 처럼 명시된 경우만 채운다.
    tow_unit_numbers: list[int] = Field(default_factory=list)
    unloading_priority: UnloadingPriority | None = None
    target_yard_zone: str | None = None
    discharge_seq_from: int | None = Field(default=None, ge=0)
    discharge_seq_to: int | None = Field(default=None, ge=0)
    special_handling: str | None = None

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _cross_check_counts(self) -> BillOfLadingExtraction:
        """대수가 세 군데서 교차 검증된다. 하나라도 어긋나면 추출 실패로 본다.

        샘플 3장 모두 UNITS == DRIVEABLE+TOW == SEQ 구간 크기 가 성립한다.
        정답지 없이 추출 정확도를 재는 장치이므로 느슨하게 두지 않는다.
        """
        if self.driveable_count is not None and self.tow_count is not None:
            total = self.driveable_count + self.tow_count
            if total != self.unit_count:
                raise ValueError(
                    f"대수 불일치: UNITS={self.unit_count} 인데 "
                    f"DRIVEABLE({self.driveable_count})+TOW({self.tow_count})={total}"
                )

        if self.discharge_seq_from is not None and self.discharge_seq_to is not None:
            span = self.discharge_seq_to - self.discharge_seq_from + 1
            if span != self.unit_count:
                raise ValueError(
                    f"대수 불일치: UNITS={self.unit_count} 인데 "
                    f"SEQ {self.discharge_seq_from}-{self.discharge_seq_to} 는 {span}대"
                )

        if self.cargo_lines:
            line_total = sum(line.unit_count for line in self.cargo_lines)
            if line_total != self.unit_count:
                raise ValueError(
                    f"대수 불일치: UNITS={self.unit_count} 인데 품목 줄 합계는 {line_total}"
                )

        if self.tow_unit_numbers and self.tow_count is not None:
            if len(self.tow_unit_numbers) != self.tow_count:
                raise ValueError(
                    f"견인 대수 불일치: TOW={self.tow_count} 인데 "
                    f"명시된 단위는 {len(self.tow_unit_numbers)}개"
                )
        return self


class ExpandedVehicle(BaseModel):
    """전개 결과 = 차량 1대 1행. 코드가 만든다(AI 아님).

    스프링의 Vehicle 엔티티와 필드가 대응한다.
    """

    model_config = ConfigDict(extra="forbid")

    vehicle_id: str
    vin: str | None = None
    bl_number: str
    brand: str | None = None
    model: str | None = None
    destination: str | None = None
    discharge_sequence: int | None = None
    unloading_priority: UnloadingPriority | None = None
    powertrain: Powertrain | None = None
    driveable: bool = True
    energy_level_pct: int | None = None
    height_meters: float | None = None
    status: VehicleStatus = VehicleStatus.EXPECTED
    arrived_at: datetime | None = None
    departed_at: datetime | None = None
    # 선하증권에 없는 항목 — 억지로 채우지 않고 비워 둔다(AI활용방안 3절).
    next_mode: NextMode | None = None
    departure_cutoff_at: datetime | None = None

    @field_validator("vehicle_id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not ids.is_valid("vehicle", v):
            raise ValueError(f"차량 ID 형식이 아닙니다: {v!r} (예: V-0001)")
        return v


# --------------------------------------------------------------------------
# ③ 주차장 사진 → 슬롯 점유상태
# --------------------------------------------------------------------------


class GridCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row: int = Field(ge=0)
    col: int = Field(ge=0)
    occupied: bool


class GridObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: ObservationSource
    captured_at: datetime
    block_id: str
    grid: list[GridCell]
    confidence: float = Field(ge=0.0, le=1.0)
    requires_confirmation: bool = False

    @field_validator("block_id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not ids.is_valid("block", v):
            raise ValueError(f"블록 ID 형식이 아닙니다: {v!r} (예: B03)")
        return v

    @model_validator(mode="after")
    def _no_duplicate_cells(self) -> GridObservation:
        seen = {(c.row, c.col) for c in self.grid}
        if len(seen) != len(self.grid):
            raise ValueError("같은 (row, col) 이 두 번 나왔습니다")
        return self


# --------------------------------------------------------------------------
# 배치 결과 / KPI / 브리핑
# --------------------------------------------------------------------------


class Move(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vehicle_id: str
    from_slot: str | None
    to_slot: str
    sequence: int
    reason: str
    distance_meters: float


class PlanKpi(BaseModel):
    model_config = ConfigDict(extra="forbid")

    avg_move_distance: float
    rehandle_proxy: float
    hard_violations: int
    changed_vehicles: int
    plan_retention_rate: float
    calc_millis: int


class ReplanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_version: str
    based_on_version: str | None
    placements: dict[str, str]  # vehicle_id -> slot_id
    moves: list[Move]
    kpi: PlanKpi
    kpi_before: PlanKpi | None = None
    unplaced: list[str] = Field(default_factory=list)


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    constraint_id: str | None
    code: str
    message: str
    fatal: bool


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: list[ParsedConstraint] = Field(default_factory=list)
    rejected: list[ParsedConstraint] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(i.fatal for i in self.issues)
