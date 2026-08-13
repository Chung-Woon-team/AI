"""브리핑 문장을 결정론적으로 조립한다. 여기 있는 함수는 전부 순수 함수다 — 네트워크를 타지 않는다.

이 모듈이 브리핑 설계의 핵심이다. **숫자가 들어가는 줄은 전부 여기서 만든다.**
Gemini 는 숫자가 없는 첫 문장(headline)과 부연 한 줄(note)만 만들고, 최종 문장 조립은
`assemble()` 이 한다. 즉 LLM 이 숫자를 지어낼 경로 자체가 구조적으로 없다
(docs 계약서 4.3, DOMAIN.md:231 "브리핑에 나오는 숫자는 전부 PlanKpi 에서만 나온다").

`build_briefing_draft()` 의 출력은 두 가지 역할을 한다.
    1. Gemini 를 못 쓸 때(키 없음·호출 실패·검증 실패) 그대로 내려보내는 **폴백 문장**
    2. Gemini 를 쓸 때 headline 만 갈아끼우고 note 를 덧붙이는 **뼈대**
그래서 AI 경로든 폴백 경로든 숫자 불릿 6줄은 바이트 단위로 같다.

반올림을 `round()` 로 하지 않는 이유: 파이썬 `round()` 는 은행가 반올림(0.5 를 짝수로)이라
Java 의 `Math.round()`(= floor(v+0.5))와 결과가 어긋난다. 같은 판을 두고 스프링이 만든
`delta_label` 과 여기서 만든 문장이 다르면 화면에서 바로 들통난다.
"""

from __future__ import annotations

import math
import re

from autoyard import ids
from autoyard.schemas import PlanKpi

# 지표 3종의 표시 순서·라벨·단위·좋은 방향. 계약서 2.2 표 그대로이고 순서가 고정이다.
_METRICS: tuple[tuple[str, str, str, str], ...] = (
    ("avg_move_distance", "평균 이동거리", "m", "LOWER"),
    ("rehandle_proxy", "재취급 Proxy", "", "LOWER"),
    ("plan_retention_rate", "계획 유지율", "%", "HIGHER"),
)

# replan.py:315-321 이 만드는 고정 문구. 이 접미사로 "폐쇄된 블록"을 역산한다.
_CLOSURE_SUFFIX = " 블록 폐쇄로 인한 재배치"

# 문장에서 ID 를 걷어낼 때 쓴다. 슬롯 ID 가 블록 ID 를 앞부분으로 품고 있어서
# 반드시 슬롯 → 블록 순으로 놓아야 한다(순서를 바꾸면 "B03-R12-C04" 가 "B03" 만 잘려나간다).
_ID_IN_TEXT = re.compile(r"V-\d{4}|B\d{2}-R\d{2}-C\d{2}|B\d{2}|C-\d{3}")
_NUMBER_IN_TEXT = re.compile(r"\d+(?:\.\d+)?")


def round1(value: float) -> float:
    """소수 첫째 자리 반올림. Java `Math.round(v*10.0)/10.0` 과 맞추려고 직접 계산한다."""
    return math.copysign(math.floor(abs(value) * 10.0 + 0.5) / 10.0, value)


def round_int(value: float) -> float:
    """정수 반올림. 라벨용 퍼센트에만 쓰므로 음수는 들어오지 않는다(호출부가 abs 를 씌운다)."""
    return math.floor(value + 0.5)


def fmt(value: float) -> str:
    """표시용 문자열. 소수 첫째 자리까지 반올림하고, 소수부가 0 이면 정수로 적는다.

    812.0 -> "812", 1.93 -> "1.9", 91.6 -> "91.6".
    """
    rounded = round1(value)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.1f}"


def delta_pct(before: float, after: float) -> float:
    """증감률(%). 반올림은 마지막에 한 번만 한다 — 표시값으로 먼저 반올림한 뒤 계산하면
    "1.9 → 1.2 인데 36% 감소" 같은 손계산 불일치가 아니라 진짜 오차가 생긴다."""
    return round1((after - before) / before * 100.0)


def delta_label(before: float, after: float, better: str | None = None) -> str:
    """"38% 감소" / "5% 증가" / "변화 없음".

    `better` 는 계약서 2.3 시그니처에 있어서 받아두지만 문구를 바꾸지 않는다 — 좋아졌는지
    나빠졌는지는 색(tone)으로만 표현하고, 문장은 세 지표 모두 퍼센트 하나로 통일한다.
    지표별 특례를 만들면 자바 구현과 반드시 어긋난다.
    """
    pct = delta_pct(before, after)
    point = int(round_int(abs(pct)))
    if pct < 0:
        return f"{point}% 감소"
    if pct > 0:
        return f"{point}% 증가"
    return "변화 없음"


def _reason_of(move: dict) -> str:
    """이동 1건의 사유 문자열. 문자열이 아니면 빈 문자열로 본다.

    `moves` 는 `list[dict]` 라 값의 타입이 보장되지 않는다(`Move` 로 조이면 스프링이 여분
    키를 하나만 보내도 422 가 난다 - 계약서 4.1). 여기서 타입을 안 보면 사유에 숫자가 실려
    왔을 때 AttributeError 가 그대로 올라가 500 이 된다. /brief 는 어떤 경우에도 5xx 를
    내지 않는다(계약서 4.4).
    """
    reason = move.get("reason")
    return reason if isinstance(reason, str) else ""


def _closed_blocks(moves: list[dict]) -> list[str]:
    """이동 사유에서 폐쇄된 블록 ID 를 뽑는다. 정렬해서 돌려주므로 출력이 결정론적이다."""
    found = set()
    for move in moves:
        reason = _reason_of(move)
        if reason.endswith(_CLOSURE_SUFFIX):
            found.add(reason[: -len(_CLOSURE_SUFFIX)])
    return sorted(found)


def build_headline(kpi: PlanKpi, moves: list[dict]) -> str:
    """1줄차. 숫자는 `kpi.changed_vehicles` 하나만 쓴다.

    `len(moves)` 를 쓰지 않는 이유: 스프링이 요청이 비대해지지 않게 moves 를 앞에서부터
    200 건만 잘라서 보낸다. 개수를 여기서 세면 500 대 판에서 "200대가 재배치되었습니다" 가 된다.
    """
    if kpi.changed_vehicles == 0:
        return "이번 판에서 이동한 차량은 없습니다."
    closed = _closed_blocks(moves)
    if closed:
        return f"{', '.join(closed)} 블록 폐쇄로 {kpi.changed_vehicles}대가 재배치되었습니다."
    return f"{kpi.changed_vehicles}대가 재배치되었습니다."


def build_briefing_draft(
    kpi: PlanKpi, kpi_before: PlanKpi | None, moves: list[dict]
) -> str:
    """결정론적 초안. 이 문자열이 폴백 문장이자 AI 경로의 뼈대다."""
    lines = [build_headline(kpi, moves)]

    for key, label, suffix, better in _METRICS:
        after = getattr(kpi, key)
        before = getattr(kpi_before, key) if kpi_before is not None else None
        if before is not None and before != 0.0:
            lines.append(
                f"- {label}: {fmt(before)} → {fmt(after)}{suffix}"
                f" ({delta_label(before, after, better)})"
            )
        else:
            # 비교할 이전 판이 없으면 전후 비교를 생략한다 — 없는 숫자를 지어내지 않는다.
            lines.append(f"- {label}: {fmt(after)}{suffix}")

    lines.append(f"- Hard 제약 위반: {kpi.hard_violations}건")
    lines.append(f"- 이동 대상 차량: {kpi.changed_vehicles}대")
    lines.append(f"- 계산 소요: {kpi.calc_millis}ms")

    if kpi.hard_violations > 0:
        lines.append("")
        lines.append("Hard 제약 위반이 남아 있어 승인 전 확인이 필요합니다.")

    return "\n".join(lines)


def assemble(draft: str, headline: str | None, note: str | None) -> str:
    """초안의 1줄차를 LLM headline 으로 갈아끼우고, 숫자 없는 note 를 불릿 뒤에 붙인다.

    불릿 6줄은 초안에서 **그대로** 옮긴다. LLM 이 손댈 수 있는 건 첫 줄과 마지막 문단뿐이다.
    """
    lines = draft.split("\n")
    bullets = [line for line in lines[1:] if line.startswith("- ")]
    # 꼬리(Hard 제약 경고)는 불릿도 빈 줄도 아닌 줄이다.
    tail = [line for line in lines[1:] if line and not line.startswith("- ")]

    text = "\n".join([(headline or lines[0]).strip(), *bullets])
    if note:
        text += "\n\n" + note.strip()
    for line in tail:
        text += "\n\n" + line
    return text


def _kpi_values(kpi: PlanKpi) -> tuple[float, ...]:
    return (
        kpi.avg_move_distance,
        kpi.rehandle_proxy,
        float(kpi.hard_violations),
        float(kpi.changed_vehicles),
        kpi.plan_retention_rate,
        float(kpi.calc_millis),
    )


def allowed_number_tokens(kpi: PlanKpi, kpi_before: PlanKpi | None = None) -> set[str]:
    """LLM 문장에 나와도 되는 숫자 토큰 전부. 여기 없는 숫자가 나오면 환각으로 본다."""
    tokens = {fmt(v) for v in _kpi_values(kpi)}
    if kpi_before is not None:
        tokens |= {fmt(v) for v in _kpi_values(kpi_before)}
    # 정수 지표는 fmt 를 거치지 않은 원표기도 허용한다(불릿에 "0건"/"42대"/"1740ms" 로 나간다).
    tokens |= {
        str(kpi.hard_violations),
        str(kpi.changed_vehicles),
        str(kpi.calc_millis),
    }
    if kpi_before is not None:
        for key, _label, _suffix, _better in _METRICS:
            before = getattr(kpi_before, key)
            if before is None or before == 0.0:
                continue
            pct = delta_pct(before, getattr(kpi, key))
            tokens.add(str(int(round_int(abs(pct)))))
    return tokens


def allowed_id_tokens(moves: list[dict]) -> set[str]:
    """입력 moves 에 실제로 등장한 ID 전부. 슬롯 ID 의 블록 부분과 사유 속 블록 ID 도 포함한다."""
    tokens: set[str] = set()
    for move in moves:
        for key in ("vehicle_id", "from_slot", "to_slot"):
            value = move.get(key)
            if not isinstance(value, str) or not value:
                continue
            tokens.add(value)
            if key != "vehicle_id":
                # "B03-R12-C04" 를 언급하려면 "B03" 도 말할 수 있어야 한다.
                tokens.add(value.split("-")[0])
        head = _reason_of(move).split(" ")[0]
        if ids.BLOCK_ID.match(head):
            tokens.add(head)
    return tokens


def _id_kind(token: str) -> str:
    if token.startswith("V-"):
        return "vehicle"
    if token.startswith("C-"):
        return "constraint"
    if "-R" in token:
        return "slot"
    return "block"


def _validate_llm_text(
    text: str, kpi: PlanKpi, kpi_before: PlanKpi | None, moves: list[dict]
) -> None:
    """LLM 이 만든 headline/note 에 지어낸 숫자·ID 가 없는지 검사한다. 실패하면 ValueError.

    ID 를 먼저 걷어내고 숫자를 뽑는 순서가 중요하다 — "B02" 의 02, "V-0182" 의 0182 를
    숫자 토큰으로 세면 멀쩡한 문장이 전부 환각으로 걸린다.
    """
    allowed_ids = allowed_id_tokens(moves)
    for token in _ID_IN_TEXT.findall(text):
        # 입력에 있었는지가 1차 관문이다. 환각 ID 는 여기서 전부 걸린다.
        if token not in allowed_ids:
            raise ValueError(f"입력(moves)에 없는 ID 를 썼습니다: {token}")
        # 2차는 격자 대조. 입력에 있어도 격자에 없는 슬롯이면 화면에서 클릭이 안 되는
        # 죽은 ID 라 문장에 넣지 않는다(그 경우 호출부가 폴백 문장으로 떨어진다).
        if not ids.is_valid(_id_kind(token), token):
            raise ValueError(f"실재하지 않는 ID 를 썼습니다: {token}")

    allowed = allowed_number_tokens(kpi, kpi_before)
    for number in _NUMBER_IN_TEXT.findall(_ID_IN_TEXT.sub(" ", text)):
        if number not in allowed:
            raise ValueError(f"입력 KPI 에 없는 숫자를 썼습니다: {number}")
