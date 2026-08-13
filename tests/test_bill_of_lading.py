"""선하증권 샘플 3장이 스키마를 통과하는지, 그리고 교차 검증이 실제로 잡아내는지."""

import pytest
from pydantic import ValidationError

from autoyard.schemas import BillOfLadingExtraction

# 샘플 문서 3장의 핵심 숫자. 셋 다 UNITS == DRIVEABLE+TOW == SEQ 구간 크기 가 성립한다.
DOC01 = dict(
    bl_number="NXR-USN-NTD-26081101",
    document_type="BILL_OF_LADING",
    booking_number="NXR-SP2608E-001",
    lot_code="EV-A-0811",
    vessel_name="MV SYNTH PACIFIC",
    voyage_number="SP2608E",
    port_of_loading="KRUSN",
    port_of_discharge="USNTD",
    unit_count=60,
    vin_range_from="SYNT26E0000000001",
    vin_range_to="SYNT26E0000000060",
    powertrain="BATTERY_EV",
    driveable_count=60,
    tow_count=0,
    unloading_priority="P2",
    target_yard_zone="EV-A / ROWS 01-06",
    discharge_seq_from=41,
    discharge_seq_to=100,
)

DOC02 = dict(
    bl_number="NXR-USN-NTD-26081102",
    document_type="SEA_WAYBILL",
    unit_count=42,
    powertrain="GASOLINE",
    driveable_count=40,
    tow_count=2,
    tow_unit_numbers=[17, 31],
    unloading_priority="P3",
    target_yard_zone="QC-HOLD / BAYS 17 & 31",
    discharge_seq_from=101,
    discharge_seq_to=142,
)

DOC03 = dict(
    bl_number="NXR-USN-NTD-26081103",
    document_type="STRAIGHT_BILL_OF_LADING",
    unit_count=18,
    cargo_lines=[
        {"description": "ELECTRIC CITY BUSES", "unit_count": 10, "height_meters": 3.25},
        {"description": "HEAVY ELECTRIC TRUCKS", "unit_count": 8, "height_meters": 3.80},
    ],
    powertrain="BATTERY_EV",
    driveable_count=18,
    tow_count=0,
    unloading_priority="P1",
    target_yard_zone="HVY-D / OVERSIZE LANES",
    discharge_seq_from=1,
    discharge_seq_to=18,
)


@pytest.mark.parametrize("doc", [DOC01, DOC02, DOC03], ids=["doc01", "doc02", "doc03"])
def test_sample_documents_pass(doc):
    bl = BillOfLadingExtraction(**doc)
    assert bl.unit_count == bl.driveable_count + bl.tow_count


def test_doc03_keeps_two_cargo_lines():
    """한 문서에 품목이 두 줄인 경우 - 높이가 서로 다르므로 뭉개면 안 된다."""
    bl = BillOfLadingExtraction(**DOC03)
    assert [line.height_meters for line in bl.cargo_lines] == [3.25, 3.80]


def test_unit_count_mismatch_is_rejected():
    """OCR 이 60을 6으로 잘못 읽으면 여기서 걸린다."""
    bad = DOC01 | {"unit_count": 6}
    with pytest.raises(ValidationError, match="대수 불일치"):
        BillOfLadingExtraction(**bad)


def test_seq_span_mismatch_is_rejected():
    bad = DOC01 | {"discharge_seq_to": 99}
    with pytest.raises(ValidationError, match="대수 불일치"):
        BillOfLadingExtraction(**bad)


def test_tow_unit_numbers_must_match_tow_count():
    bad = DOC02 | {"tow_unit_numbers": [17]}
    with pytest.raises(ValidationError, match="견인 대수 불일치"):
        BillOfLadingExtraction(**bad)
