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
import time
from datetime import datetime

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from autoyard import briefing, yard_grid
from autoyard.config import settings
from autoyard.schemas import (
    BillOfLadingExtraction,
    GridCell,
    GridObservation,
    ObservationSource,
    ParseResult,
    PlanKpi,
)

logger = logging.getLogger(__name__)


def _client() -> genai.Client:
    # 타임아웃을 명시하지 않으면 SDK 기본값에 끌려가 사실상 무제한이다. 과부하(503) 때
    # 78초까지 매달린 적이 있고, 그러면 스프링이 60초에 먼저 끊어서 폴백이 사용자에게
    # 도달하지 못한다. 상한을 걸어 그 전에 폴백으로 떨어뜨린다.
    return genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=settings.gemini_timeout_ms),
    )


def _is_quota_error(exc: Exception) -> bool:
    """429 RESOURCE_EXHAUSTED 인가. 할당량은 재시도해도 같은 답이 온다.

    SDK 예외 타입이 버전마다 달라 문자열로 판정한다. 넓게 잡아 오판해도 손해는
    '재시도 1회를 건너뛴다' 뿐이고, 그 경우도 폴백 경로로 정상 응답이 나간다.
    """
    text = str(exc)
    return "RESOURCE_EXHAUSTED" in text or "429" in text


def _guard_retry(exc: Exception, started: float, failure_cls: type[Exception]) -> None:
    """재시도가 무의미하거나 시간 예산을 넘겼으면 여기서 실패로 끝낸다.

    재시도를 무조건 하면 느린 실패에서 대기 시간이 두 배가 되고, 스프링(read timeout 60초)이
    먼저 끊어버린다. 빨리 폴백으로 떨어지는 쪽이 사용자에게 이득이다.
    """
    if _is_quota_error(exc):
        raise failure_cls(f"Gemini 할당량 초과라 재시도하지 않는다: {exc}") from exc

    elapsed = time.monotonic() - started
    if elapsed > settings.gemini_retry_budget_seconds:
        raise failure_cls(
            f"첫 호출이 {elapsed:.1f}초 걸려 재시도하지 않는다(스프링 타임아웃 보호): {exc}"
        ) from exc


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
    """API 호출 자체(네트워크/서버 오류)가 실패하면 1회만 재시도한다.

    단 할당량 초과이거나 첫 호출이 이미 오래 걸렸으면 재시도하지 않는다(_guard_retry).
    """
    started = time.monotonic()
    try:
        return _call_once_text(client, prompt)
    except Exception as first_exc:  # noqa: BLE001 - SDK 예외 타입이 다양해 넓게 잡는다
        _guard_retry(first_exc, started, ParsingFailed)
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
    """API 호출 자체(네트워크/서버 오류)가 실패하면 1회만 재시도한다.

    단 할당량 초과이거나 첫 호출이 이미 오래 걸렸으면 재시도하지 않는다(_guard_retry).
    이미지 호출은 정상일 때도 30초 넘게 걸려서, 재시도까지 하면 스프링이 먼저 끊는다.
    """
    # SDK 가 던지는 예외 타입이 네트워크 오류부터 API 오류까지 다양해 넓게 잡는다.
    started = time.monotonic()
    try:
        return _call_once_image(client, image_bytes, mime_type, prompt)
    except Exception as first_exc:  # noqa: BLE001
        _guard_retry(first_exc, started, ExtractionFailed)
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


# --------------------------------------------------------------------------
# ④ 브리핑 (초안 → 숫자 없는 headline + note)
# --------------------------------------------------------------------------


class BriefingFailed(Exception):
    """Gemini 호출 또는 숫자/ID 검증이 재시도까지 실패했을 때."""


# 완성된 브리핑을 시키지 않는다. 숫자가 들어가는 줄은 briefing.py 가 이미 다 만들어 놨고,
# 여기서는 숫자가 없는 두 조각만 받는다 — 환각이 끼어들 자리를 구조적으로 없앤 것이다.
_BRIEF_PROMPT = """\
너는 완성차 야드(주차장) 재배치 결과를 현장 담당자에게 설명하는 사람이다.

아래는 시스템이 이미 계산해 둔 브리핑 초안이다. 이 초안의 숫자는 전부 검증된 값이다.

--- 초안 시작 ---
<<DRAFT>>
--- 초안 끝 ---

너는 두 조각만 쓴다.
- headline: 초안의 1줄차를 더 자연스러운 한 문장으로 다듬은 것
- note: 왜 이런 결과가 나왔는지 설명하는 부연

절대 규칙:
1. 초안의 숫자를 바꾸지 마라. 새 숫자를 만들지 마라. 계산하지 마라.
2. headline 에는 초안 1줄차에 이미 나온 숫자와 ID 외의 어떤 수치도 쓰지 마라.
3. note 는 **숫자를 하나도 쓰지 말고** 1~2문장으로 써라. 왜 이런 결과가 나왔는지 인과만
   설명하라. 지표 이름은 말해도 되지만 값은 말하지 마라.
4. 재취급 Proxy 는 슬롯 깊이로 추정한 값이지 실측 재취급 횟수가 아니다. "재취급이 N번
   줄었다" 같은 단정 표현을 쓰지 마라. "재취급 부담이 줄었다" 정도로만 말하라.
5. 초안에 없는 차량 ID·슬롯 ID·블록 ID 를 지어내지 마라.

아래 JSON 형식으로만 응답하라. 다른 텍스트를 덧붙이지 마라. 여기 없는 키를 추가하지 마라.

{
  "headline": "B02 블록 폐쇄로 42대가 재배치되었습니다.",
  "note": "폐쇄 구역에 있던 차량을 인접 블록의 얕은 자리로 옮기면서 평균 이동거리와 재취급 부담이 함께 줄었습니다."
}
"""

_REPAIR_PROMPT_TEMPLATE_BRIEF = """\
{original}

방금 네가 낸 답이 검증에 실패했다.

네 응답:
{previous}

에러:
{error}

같은 초안을 다시 보고, 위 에러만 고쳐서 다시 답하라. 초안에 없는 숫자나 ID 는 빼고,
설명이 필요하면 숫자 없이 말로만 풀어라.
"""


def _call_text_for_brief(client: genai.Client, prompt: str) -> str:
    """`_call_with_retry_text` 재사용. 다만 예외 타입은 브리핑용으로 바꿔서 올려보낸다 —
    라우터가 `except gemini_client.BriefingFailed` 하나로 폴백 분기를 잡을 수 있게."""
    try:
        return _call_with_retry_text(client, prompt)
    except ParsingFailed as exc:
        raise BriefingFailed(str(exc)) from exc


def _brief_text_field(data: dict, key: str) -> str:
    """headline/note 를 문자열로 꺼낸다. 값이 문자열이 아니면 검증 실패로 본다.

    `(data.get(key) or "").strip()` 로 두면 모델이 숫자나 리스트를 넣었을 때 AttributeError 가
    올라가 repair/폴백 분기를 빠져나간다.
    """
    value = data.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{key} 가 문자열이 아닙니다")  # noqa: TRY004
    return value.strip()


def _to_headline_note(
    raw: str, kpi: PlanKpi, kpi_before: PlanKpi | None, moves: list[dict]
) -> tuple[str, str | None]:
    """Gemini 의 raw JSON → 검증된 (headline, note). 검증 실패는 ValueError."""
    if not isinstance(raw, str):
        # SDK 의 response.text 는 응답이 막혔을 때(safety block · MAX_TOKENS) None 이다.
        # 그대로 json.loads 에 넣으면 TypeError 가 나서 호출부의 repair/폴백 분기를 빠져나가고
        # /brief 가 500 을 낸다 — "어떤 경우에도 5xx 를 내지 않는다"(계약서 4.4) 위반.
        raise ValueError("Gemini 응답이 비어 있습니다")  # noqa: TRY004
    data = json.loads(raw)
    if not isinstance(data, dict):
        # TRY004(TypeError 를 쓰라)를 억제하는 이유: 여기서 TypeError 를 던지면 호출부의
        # repair 재시도 분기(ValueError/ValidationError)를 그냥 빠져나간다. LLM 이 형식을
        # 어긴 건 파이썬 타입 오류가 아니라 "검증 실패" 라서 다른 검증들과 같은 줄에 세운다.
        raise ValueError("응답이 JSON 객체가 아닙니다")  # noqa: TRY004

    headline = _brief_text_field(data, "headline")
    note = _brief_text_field(data, "note") or None
    if not headline:
        raise ValueError("headline 이 비어 있습니다")

    briefing._validate_llm_text(f"{headline}\n{note or ''}", kpi, kpi_before, moves)
    return headline, note


def generate_briefing(
    draft: str, kpi: PlanKpi, kpi_before: PlanKpi | None, moves: list[dict]
) -> tuple[str, str | None]:
    """결정론적 초안 → (headline, note). 최종 문장 조립은 호출부가 briefing.assemble 로 한다.

    호출부는 `settings.gemini_enabled` 를 먼저 확인해야 한다 — 이 함수는 키가 있다고 가정한다.
    다른 기능과 마찬가지로 여기서 폴백하지 않는다. 실패하면 BriefingFailed 를 던진다.
    """
    client = _client()
    prompt = _BRIEF_PROMPT.replace("<<DRAFT>>", draft)

    raw = _call_text_for_brief(client, prompt)

    try:
        return _to_headline_note(raw, kpi, kpi_before, moves)
    except (ValueError, ValidationError) as first_error:
        logger.warning("브리핑 문장이 검증 실패, repair 재시도: %s", first_error)
        repair_prompt = _REPAIR_PROMPT_TEMPLATE_BRIEF.format(
            original=prompt, previous=raw, error=first_error
        )
        raw2 = _call_text_for_brief(client, repair_prompt)
        try:
            return _to_headline_note(raw2, kpi, kpi_before, moves)
        except (ValueError, ValidationError) as second_error:
            raise BriefingFailed(
                f"숫자/ID 검증이 repair 재시도까지 실패했습니다: {second_error}"
            ) from second_error
