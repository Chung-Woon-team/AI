"""Gemini 로 자연어 지시를 구조화된 제약 조건으로 바꾼다.

호출 계약(docs/HANDOFF_AI.md 7번 폴백):
    - API 호출(네트워크/서버 오류) 실패 시 1회 재시도, 그래도 안 되면 실패로 본다.
    - 응답은 받았지만 검증에 걸리면, 에러 메시지를 모델에 되돌려주는 1회 repair 재시도.
      그래도 안 되면 실패로 본다.
    - 실패를 최종 판정하는 건 이 모듈이 아니다. `ParsingFailed` 를 던지기만 하고,
      폴백으로 대체할지는 호출부(라우터)가 정한다.

AI활용방안 2절 원칙(미지원 Intent 거절, 없는 ID 통과 금지)을 프롬프트로 강제한다. Pydantic 이
잡는 건 형식뿐이고, "목록에 없는 블록은 쓰지 마라" 같은 건 모델에게 직접 말해야 한다.

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
from pydantic import ValidationError

from autoyard.config import settings
from autoyard.schemas import ParseResult

logger = logging.getLogger(__name__)


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

_REPAIR_PROMPT_TEMPLATE = """\
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


def _client() -> genai.Client:
    return genai.Client(api_key=settings.gemini_api_key)


def _call_once(client: genai.Client, prompt: str) -> str:
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=[prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    return response.text


def _call_with_retry(client: genai.Client, prompt: str) -> str:
    """API 호출 자체(네트워크/서버 오류)가 실패하면 1회만 재시도한다."""
    try:
        return _call_once(client, prompt)
    except Exception as first_exc:  # noqa: BLE001 - SDK 예외 타입이 다양해 넓게 잡는다
        logger.warning("Gemini 호출 실패, 재시도: %s", first_exc)
        try:
            return _call_once(client, prompt)
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

    raw = _call_with_retry(client, prompt)

    try:
        return _to_parse_result(raw, instruction_id)
    except (ValueError, ValidationError) as first_error:
        logger.warning("파싱 결과가 스키마 검증 실패, repair 재시도: %s", first_error)
        repair_prompt = _REPAIR_PROMPT_TEMPLATE.format(original=prompt, previous=raw, error=first_error)
        raw2 = _call_with_retry(client, repair_prompt)
        try:
            return _to_parse_result(raw2, instruction_id)
        except (ValueError, ValidationError) as second_error:
            raise ParsingFailed(
                f"스키마 검증이 repair 재시도까지 실패했습니다: {second_error}"
            ) from second_error
