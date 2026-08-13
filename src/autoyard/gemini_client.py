"""Gemini 로 선하증권 이미지를 구조화된 데이터로 뽑는다.

호출 계약(HANDOFF_AI.md 7번 폴백):
    - API 호출(네트워크/서버 오류) 실패 시 1회 재시도, 그래도 안 되면 실패로 본다.
    - 응답은 받았지만 Pydantic 검증에 걸리면, 에러 메시지를 모델에 되돌려주는 1회 repair
      재시도. 그래도 안 되면 실패로 본다.
    - 실패를 최종 판정하는 건 이 모듈이 아니다. `ExtractionFailed` 를 던지기만 하고,
      폴백으로 대체할지는 호출부(라우터)가 정한다 — 여기서 폴백을 숨기면 "실제 추출 결과"와
      "폴백"을 구분할 수 없게 된다.

AI활용방안 2·3절 원칙(문서에 없는 건 만들지 않는다)을 프롬프트로 강제한다. Pydantic 이 잡는 건
형식·교차검증뿐이고, "지어내지 마라"는 모델에게 직접 말해야 한다.

`response_schema` 를 안 쓰는 이유: Gemini 의 구조화 출력은 OpenAPI 3.0 의 제한된 부분집합만
받는데, `BillOfLadingExtraction` 이 쓰는 `Field(gt=0)`(→ exclusiveMinimum)과
`extra="forbid"`(→ additionalProperties) 가 둘 다 Gemini API 에서 400 으로 거부된다.
그래서 JSON 형식만 강제(`response_mime_type`)하고, 정확한 필드 모양은 프롬프트로 명시한 뒤
Pydantic 으로 사후 검증 + repair 재시도한다.
"""

from __future__ import annotations

import logging

from google import genai
from google.genai import types
from pydantic import ValidationError

from autoyard.config import settings
from autoyard.schemas import BillOfLadingExtraction

logger = logging.getLogger(__name__)


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

_REPAIR_PROMPT_TEMPLATE = """\
{original}

방금 네가 낸 답이 스키마 검증에 실패했다.

네 응답:
{previous}

에러:
{error}

같은 이미지를 다시 보고, 위 에러만 고쳐서 다시 답하라. 에러와 무관한 필드는 그대로 두고,
이번에도 문서에 없는 값은 지어내지 마라.
"""


def _client() -> genai.Client:
    return genai.Client(api_key=settings.gemini_api_key)


def _call_once(client: genai.Client, image_bytes: bytes, mime_type: str, prompt: str) -> str:
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )
    return response.text


def _call_with_retry(client: genai.Client, image_bytes: bytes, mime_type: str, prompt: str) -> str:
    """API 호출 자체(네트워크/서버 오류)가 실패하면 1회만 재시도한다."""
    # SDK 가 던지는 예외 타입이 네트워크 오류부터 API 오류까지 다양해 넓게 잡는다.
    try:
        return _call_once(client, image_bytes, mime_type, prompt)
    except Exception as first_exc:  # noqa: BLE001
        logger.warning("Gemini 호출 실패, 재시도: %s", first_exc)
        try:
            return _call_once(client, image_bytes, mime_type, prompt)
        except Exception as second_exc:
            raise ExtractionFailed(f"Gemini 호출이 재시도까지 실패했습니다: {second_exc}") from second_exc


def extract_bill_of_lading(image_bytes: bytes, mime_type: str) -> BillOfLadingExtraction:
    """선하증권 이미지 → BillOfLadingExtraction.

    호출부는 `settings.gemini_enabled` 를 먼저 확인해야 한다 — 이 함수는 키가 있다고 가정한다.
    """
    client = _client()

    raw = _call_with_retry(client, image_bytes, mime_type, _EXTRACTION_PROMPT)

    try:
        return BillOfLadingExtraction.model_validate_json(raw)
    except ValidationError as first_error:
        logger.warning("추출 결과가 스키마 검증 실패, repair 재시도: %s", first_error)
        repair_prompt = _REPAIR_PROMPT_TEMPLATE.format(
            original=_EXTRACTION_PROMPT, previous=raw, error=first_error
        )
        raw2 = _call_with_retry(client, image_bytes, mime_type, repair_prompt)
        try:
            return BillOfLadingExtraction.model_validate_json(raw2)
        except ValidationError as second_error:
            raise ExtractionFailed(
                f"스키마 검증이 repair 재시도까지 실패했습니다: {second_error}"
            ) from second_error
