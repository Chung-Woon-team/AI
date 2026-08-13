"""Gemini 로 (1) 자연어 지시를 제약 조건으로, (2) 선하증권 이미지를 문서 데이터로 바꾼다.

호출 계약(docs/HANDOFF_AI.md 7번 폴백), 두 기능 공통:
    - API 호출(네트워크/서버 오류) 실패 시 1회 재시도, 그래도 안 되면 실패로 본다.
    - 응답은 받았지만 검증에 걸리면, 에러 메시지를 모델에 되돌려주는 1회 repair 재시도.
      그래도 안 되면 실패로 본다.
    - 실패를 최종 판정하는 건 이 모듈이 아니다. `ParsingFailed`/`ExtractionFailed` 를
      던지기만 하고, 폴백으로 대체할지는 호출부(라우터)가 정한다 — 여기서 폴백을 숨기면
      "실제 결과"와 "폴백"을 구분할 수 없게 된다.

AI활용방안 2·3절 원칙(미지원 Intent 거절, 없는 ID 통과 금지, 문서에 없는 건 만들지 않는다)을
프롬프트로 강제한다. Pydantic 이 잡는 건 형식·교차검증뿐이고, "지어내지 마라"는 모델에게
직접 말해야 한다.

`response_schema` 를 안 쓰는 이유(선하증권 추출에서 먼저 발견): Gemini 의 구조화 출력은
OpenAPI 3.0 의 제한된 부분집합만 받는데, `BillOfLadingExtraction` 이 쓰는 `Field(gt=0)`
(→ exclusiveMinimum)과 `extra="forbid"`(→ additionalProperties) 가 둘 다 Gemini API 에서
400 으로 거부된다. 그래서 JSON 형식만 강제(`response_mime_type`)하고, 정확한 필드 모양은
프롬프트로 명시한 뒤 Pydantic 으로 사후 검증 + repair 재시도한다. 제약 파서도 같은 이유로
`response_schema` 를 안 쓴다.

`constraint_id`/`instruction_id` 는 Gemini 에게 시키지 않는다. 모델이 중복되거나 형식이
어긋난 ID 를 만들 위험이 있어서, 코드가 순번으로 매긴다(AI활용방안 8절 ID 규칙 통일과 같은 이유로
차량 ID 를 코드가 매기는 것과 동일한 논리).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from autoyard import yard_grid
from autoyard.config import settings
from autoyard.schemas import BillOfLadingExtraction, GridCell, GridObservation, ObservationSource, ParseResult

logger = logging.getLogger(__name__)


def _client() -> genai.Client:
    return genai.Client(api_key=settings.gemini_api_key)


# --------------------------------------------------------------------------
# ① 제약 파서
# --------------------------------------------------------------------------


class ParsingFailed(Exception):
    """Gemini 호출 또는 스키마 검증이 재시도까지 실패했을 때."""


_PARSE_PROMPT_TEMPLATE = """\
너는 야드(완성차 주차장) 현장 관리자가 무전·메신저로 남긴 자연어 지시를 구조화된 제약 조건으로
바꾸는 변환기다.

지원하는 제약 타입은 셋뿐이다. 그 외의 의도는 거절한다 — 제약을 만들지 말고 unresolved 에
원문 표현만 남겨라.

| type | 지시 예시 | target 에 필요한 것 |
|---|---|---|
| BLOCK_CLOSURE | "3번 블록 폐쇄해" | target.block_ids |
| VEHICLE_GROUPING | "브랜드 B는 서쪽으로 모아줘" | target.attribute + target.values, 또는 target.vehicle_ids |
| OUTBOUND_PRIORITY | "내일 컷오프 차량은 게이트 가깝게" | target.filter |

절대 규칙:
1. 한 지시문에 여러 제약이 섞여 있을 수 있다. 문장을 쪼개서 각각 별도 constraint 로 뽑아라.
2. priority 는 HARD 또는 SOFT 다. 어기면 배치 자체가 거부돼야 하는 것(폐쇄 등)은 HARD,
   "하면 좋은" 수준(묶음 배치 등)은 SOFT 로 매겨라.
3. target.block_ids 는 반드시 아래 valid_block_ids 목록 안에서만 골라라. target.values 를 쓸 때도
   목록(valid_brands 또는 valid_zones) 안에서만 골라라. 목록에 없는 표현이 나오면 그 제약을
   만들지 말고, 대신 unresolved 에 원문 표현을 그대로 넣어라.
4. "가까이", "많이", "적당히" 처럼 정량화 안 된 애매한 표현은 confidence 를 낮추고(0.7 이하 권장)
   unresolved 에도 그 표현을 넣어라.
5. 확신 없는 값은 지어내지 마라. 차라리 그 제약을 만들지 말고 unresolved 로 넘겨라.
6. constraint_id 와 instruction_id 는 넣지 마라 — 다른 시스템이 채운다.
7. "오늘"/"내일" 같은 상대 시각은 아래 reference_datetime 을 기준으로 절대 시각(ISO-8601,
   초 단위까지)으로 바꿔라.

reference_datetime: <<REFERENCE_DATETIME>>
valid_block_ids: <<VALID_BLOCK_IDS>>
valid_brands: <<VALID_BRANDS>>
valid_zones: <<VALID_ZONES>>

아래 JSON 형식으로만 응답하라. 다른 텍스트를 덧붙이지 마라. 이 타입에 필요 없는 target 필드는
null 로 둬라.

{
  "constraints": [
    {
      "type": "BLOCK_CLOSURE | VEHICLE_GROUPING | OUTBOUND_PRIORITY",
      "target": {
        "block_ids": ["B02"],
        "attribute": "brand | null",
        "values": ["..."],
        "vehicle_ids": ["V-0001"],
        "filter": {"...": "..."}
      },
      "value": null,
      "time_window": {"start": "2026-08-13T14:00:00", "end": null},
      "priority": "HARD | SOFT",
      "confidence": 0.9
    }
  ],
  "unresolved": ["가까이"],
  "requires_confirmation": true
}

지시문: "<<RAW_TEXT>>"
"""

_REPAIR_PROMPT_TEMPLATE_PARSE = """\
{original}

방금 네가 낸 답이 스키마 검증에 실패했다.

네 응답:
{previous}

에러:
{error}

같은 지시문을 다시 보고, 위 에러만 고쳐서 다시 답하라. 에러와 무관한 부분은 그대로 두고,
이번에도 목록에 없는 값이나 확신 없는 값은 지어내지 마라.
"""


def _build_prompt(
    raw_text: str,
    valid_block_ids: list[str],
    valid_brands: list[str],
    valid_zones: list[str],
    reference_datetime: datetime,
) -> str:
    return (
        _PARSE_PROMPT_TEMPLATE.replace("<<REFERENCE_DATETIME>>", reference_datetime.isoformat())
        .replace("<<VALID_BLOCK_IDS>>", json.dumps(valid_block_ids, ensure_ascii=False))
        .replace("<<VALID_BRANDS>>", json.dumps(valid_brands, ensure_ascii=False))
        .replace("<<VALID_ZONES>>", json.dumps(valid_zones, ensure_ascii=False))
        .replace("<<RAW_TEXT>>", raw_text)
    )


def _call_once_text(client: genai.Client, prompt: str) -> str:
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=[prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return response.text


def _call_with_retry_text(client: genai.Client, prompt: str) -> str:
    """API 호출 자체(네트워크/서버 오류)가 실패하면 1회만 재시도한다."""
    try:
        return _call_once_text(client, prompt)
    except Exception as first_exc:  # noqa: BLE001 - SDK 예외 타입이 다양해 넓게 잡는다
        logger.warning("Gemini 호출 실패, 재시도: %s", first_exc)
        try:
            return _call_once_text(client, prompt)
        except Exception as second_exc:
            raise ParsingFailed(f"Gemini 호출이 재시도까지 실패했습니다: {second_exc}") from second_exc


def _to_parse_result(raw: str, instruction_id: str) -> ParseResult:
    """Gemini 의 raw JSON(제약 ID 없음) + 서버가 정한 instruction_id → 검증된 ParseResult.

    constraint_id 는 여기서 순번으로 매긴다(모델에게 안 시킨 이유는 모듈 docstring 참고).
    """
    data = json.loads(raw)
    constraints = data.get("constraints") or []
    numbered = [
        {**c, "constraint_id": f"C-{i:03d}"} for i, c in enumerate(constraints, start=1)
    ]
    return ParseResult(
        instruction_id=instruction_id,
        constraints=numbered,
        unresolved=data.get("unresolved") or [],
        requires_confirmation=data.get("requires_confirmation") or False,
    )


def parse_instruction(
    raw_text: str,
    instruction_id: str,
    valid_block_ids: list[str],
    valid_brands: list[str],
    valid_zones: list[str],
    reference_datetime: datetime,
) -> ParseResult:
    client = _client()
    prompt = _build_prompt(raw_text, valid_block_ids, valid_brands, valid_zones, reference_datetime)

    raw = _call_with_retry_text(client, prompt)

    try:
        return _to_parse_result(raw, instruction_id)
    except (ValueError, ValidationError) as first_error:
        logger.warning("파싱 결과가 스키마 검증 실패, repair 재시도: %s", first_error)
        repair_prompt = _REPAIR_PROMPT_TEMPLATE_PARSE.format(
            original=prompt, previous=raw, error=first_error
        )
        raw2 = _call_with_retry_text(client, repair_prompt)
        try:
            return _to_parse_result(raw2, instruction_id)
        except (ValueError, ValidationError) as second_error:
            raise ParsingFailed(
                f"스키마 검증이 repair 재시도까지 실패했습니다: {second_error}"
            ) from second_error


# --------------------------------------------------------------------------
# ② 선하증권 추출
# --------------------------------------------------------------------------


class ExtractionFailed(Exception):
    """Gemini 호출 또는 스키마 검증이 재시도까지 실패했을 때."""


_EXTRACTION_PROMPT = """\
너는 선하증권(Bill of Lading / Sea Waybill) 이미지에서 문서에 적힌 값만 그대로 옮기는 추출기다.

절대 규칙:
1. 문서에 적히지 않은 값은 만들지 마라. 모르면 그 필드를 비워라(null). 추정하거나 계산하지 마라.
2. VIN 은 문서에 범위("...0001 TO ...0060")로만 적혀 있다. vin_range_from/vin_range_to 에
   그 범위의 시작/끝만 넣어라. 개별 VIN 60개를 나열하지 마라 — 그건 이 시스템에서 하지 않는다.
3. tow_unit_numbers 는 "UNITS 017 & 031 REQUIRE TOW" 처럼 개별 번호가 문서에 콕 집어 적힌
   경우에만 채워라. TOW 대수만 적혀 있고 번호가 없으면 비워 둬라.
4. cargo_lines 는 한 문서 안에 서로 다른 품목(설명·전고 등이 다른 화물)이 여러 줄로 적힌
   경우에만 채워라. 단일 품목이면 비워 둬라.
5. UNITS, DRIVEABLE/TOW 대수, SEQ 구간처럼 서로 관련된 숫자들이 있다면, 각각 문서에 적힌
   그대로만 옮겨라. 숫자끼리 안 맞아 보여도 네가 하나를 고쳐서 맞추지 마라 — 불일치 여부는
   이 시스템의 다른 단계에서 검증한다.
6. document_type 은 문서 제목/표기를 보고 BILL_OF_LADING / SEA_WAYBILL /
   STRAIGHT_BILL_OF_LADING 중 하나로 판정하라.
7. "PCTC DISCHARGE DATA" 구획(있다면)에 하선 관련 값(powertrain, driveable/tow, priority,
   target zone, seq, special handling)이 모여 있다.
8. confidence 는 네가 이 이미지를 얼마나 확신 있게 읽었는지 0.0~1.0 으로 매겨라. 흐릿하거나
   해석이 애매한 부분이 있으면 낮춰라.

아래 JSON 형식으로만 응답하라. 다른 텍스트를 덧붙이지 마라. 여기 없는 키를 추가하지 마라.
모르는 값은 필드를 생략하지 말고 null 로 넣어라.

{
  "bl_number": "string",
  "document_type": "BILL_OF_LADING | SEA_WAYBILL | STRAIGHT_BILL_OF_LADING",
  "booking_number": "string | null",
  "lot_code": "string | null",
  "linked_route_code": "string | null",
  "vessel_name": "string | null",
  "voyage_number": "string | null",
  "port_of_loading": "string | null (UN/LOCODE)",
  "port_of_discharge": "string | null (UN/LOCODE)",
  "issue_date": "YYYY-MM-DD | null",
  "shipper_name": "string | null",
  "consignee_name": "string | null",
  "notify_party": "string | null",
  "cargo_lines": [
    {"description": "string", "unit_count": 1, "brand": "string | null",
      "model": "string | null", "height_meters": 0.0, "driveable": true}
  ],
  "unit_count": 1,
  "gross_weight_kg": 0,
  "measurement_cbm": 0.0,
  "vin_range_from": "string | null",
  "vin_range_to": "string | null",
  "powertrain": "BATTERY_EV | GASOLINE | DIESEL | HYBRID | null",
  "driveable_count": 0,
  "tow_count": 0,
  "tow_unit_numbers": [17, 31],
  "unloading_priority": "P1 | P2 | P3 | null",
  "target_yard_zone": "string | null",
  "discharge_seq_from": 0,
  "discharge_seq_to": 0,
  "special_handling": "string | null",
  "confidence": 0.9
}
"""

_REPAIR_PROMPT_TEMPLATE_BL = """\
{original}

방금 네가 낸 답이 스키마 검증에 실패했다.

네 응답:
{previous}

에러:
{error}

같은 이미지를 다시 보고, 위 에러만 고쳐서 다시 답하라. 에러와 무관한 필드는 그대로 두고,
이번에도 문서에 없는 값은 지어내지 마라.
"""


def _call_once_image(client: genai.Client, image_bytes: bytes, mime_type: str, prompt: str) -> str:
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return response.text


def _call_with_retry_image(
    client: genai.Client, image_bytes: bytes, mime_type: str, prompt: str
) -> str:
    """API 호출 자체(네트워크/서버 오류)가 실패하면 1회만 재시도한다."""
    # SDK 가 던지는 예외 타입이 네트워크 오류부터 API 오류까지 다양해 넓게 잡는다.
    try:
        return _call_once_image(client, image_bytes, mime_type, prompt)
    except Exception as first_exc:  # noqa: BLE001
        logger.warning("Gemini 호출 실패, 재시도: %s", first_exc)
        try:
            return _call_once_image(client, image_bytes, mime_type, prompt)
        except Exception as second_exc:
            raise ExtractionFailed(f"Gemini 호출이 재시도까지 실패했습니다: {second_exc}") from second_exc


def extract_bill_of_lading(image_bytes: bytes, mime_type: str) -> BillOfLadingExtraction:
    """선하증권 이미지 → BillOfLadingExtraction.

    호출부는 `settings.gemini_enabled` 를 먼저 확인해야 한다 — 이 함수는 키가 있다고 가정한다.
    """
    client = _client()

    raw = _call_with_retry_image(client, image_bytes, mime_type, _EXTRACTION_PROMPT)

    try:
        return BillOfLadingExtraction.model_validate_json(raw)
    except ValidationError as first_error:
        logger.warning("추출 결과가 스키마 검증 실패, repair 재시도: %s", first_error)
        repair_prompt = _REPAIR_PROMPT_TEMPLATE_BL.format(
            original=_EXTRACTION_PROMPT, previous=raw, error=first_error
        )
        raw2 = _call_with_retry_image(client, image_bytes, mime_type, repair_prompt)
        try:
            return BillOfLadingExtraction.model_validate_json(raw2)
        except ValidationError as second_error:
            raise ExtractionFailed(
                f"스키마 검증이 repair 재시도까지 실패했습니다: {second_error}"
            ) from second_error


# --------------------------------------------------------------------------
# ③ 야드 격자 관측 (사진 → 슬롯별 점유 여부)
# --------------------------------------------------------------------------


class _GridRecognition(BaseModel):
    """Gemini 가 이미지에서 직접 알 수 있는 것만. source_type/captured_at 은 업로드 맥락이지
    사진 내용이 아니라서 여기 안 넣는다 - 호출부(라우터)가 채운다.
    """

    model_config = ConfigDict(extra="forbid")

    grid: list[GridCell]
    confidence: float = Field(ge=0.0, le=1.0)
    requires_confirmation: bool = False

    @model_validator(mode="after")
    def _cells_are_valid_slots(self) -> _GridRecognition:
        for cell in self.grid:
            if not yard_grid.is_slot(cell.row, cell.col):
                raise ValueError(
                    f"({cell.row}, {cell.col}) 은(는) 주차칸이 아닙니다(도로 칸이거나 격자 밖)"
                )
        seen = {(c.row, c.col) for c in self.grid}
        if len(seen) != len(self.grid):
            raise ValueError("같은 (row, col) 이 두 번 나왔습니다")
        return self


_GRID_PROMPT = """\
너는 완성차 야드(주차장) 항공사진 한 장을 보고 주차칸마다 비었는지 찼는지 판정하는 인식기다.

야드는 고정된 격자다. 전체 22행 × 46열이고, 주차칸은 아래 네 블록 안에만 있다(그 외는 전부 도로다):
- B01: 행 4~8, 열 4~20
- B02: 행 4~8, 열 25~41
- B03: 행 13~17, 열 4~20
- B04: 행 13~17, 열 25~41

절대 규칙:
1. 위 네 블록 안의 주차칸 340개(블록당 5행 × 17열) 전부를 하나씩 판정해서 빠짐없이 담아라.
2. 블록 범위 밖(도로 칸)은 절대 결과에 넣지 마라.
3. 사진이 흐리거나 각도가 애매해 확신이 안 서는 칸이 있어도 가장 그럴듯한 값으로 채우되,
   confidence 를 낮추고(0.7 이하 권장) requires_confirmation 을 true 로 켜라.
4. confidence 는 이 사진 전체를 얼마나 확신 있게 읽었는지 0.0~1.0.

아래 JSON 형식으로만 응답하라. 다른 텍스트를 덧붙이지 마라. 여기 없는 키를 추가하지 마라.

{
  "grid": [
    {"row": 4, "col": 4, "occupied": true}
  ],
  "confidence": 0.9,
  "requires_confirmation": false
}
"""

_REPAIR_PROMPT_TEMPLATE_GRID = """\
{original}

방금 네가 낸 답이 스키마 검증에 실패했다.

네 응답:
{previous}

에러:
{error}

같은 사진을 다시 보고, 위 에러만 고쳐서 다시 답하라. 에러와 무관한 칸은 그대로 두고,
이번에도 블록 범위 밖의 칸은 넣지 마라.
"""


def extract_grid_observation(
    image_bytes: bytes, mime_type: str, source_type: ObservationSource
) -> GridObservation:
    """야드 전체 사진 한 장 → GridObservation.

    호출부는 `settings.gemini_enabled` 를 먼저 확인해야 한다 — 이 함수는 키가 있다고 가정한다.
    """
    client = _client()

    raw = _call_with_retry_image(client, image_bytes, mime_type, _GRID_PROMPT)

    try:
        recognition = _GridRecognition.model_validate_json(raw)
    except ValidationError as first_error:
        logger.warning("격자 인식 결과가 스키마 검증 실패, repair 재시도: %s", first_error)
        repair_prompt = _REPAIR_PROMPT_TEMPLATE_GRID.format(
            original=_GRID_PROMPT, previous=raw, error=first_error
        )
        raw2 = _call_with_retry_image(client, image_bytes, mime_type, repair_prompt)
        try:
            recognition = _GridRecognition.model_validate_json(raw2)
        except ValidationError as second_error:
            raise ExtractionFailed(
                f"스키마 검증이 repair 재시도까지 실패했습니다: {second_error}"
            ) from second_error

    return GridObservation(
        source_type=source_type,
        captured_at=datetime.now(),
        grid=recognition.grid,
        confidence=recognition.confidence,
        requires_confirmation=recognition.requires_confirmation,
    )
