"""브리핑 초안 조립 — 네트워크를 타지 않는 순수 로직만 검증한다.

여기서 문자열 전문을 통째로 비교하는 이유: 이 문자열이 곧 폴백 응답이고, 동시에 AI 경로의
불릿 6줄이기도 하다. 한 글자라도 흔들리면 같은 판에 대해 두 벌의 문장이 생긴다.
"""

from __future__ import annotations

import pytest

from autoyard import briefing
from autoyard.schemas import PlanKpi

KPI_AFTER = PlanKpi(
    avg_move_distance=502.0,
    rehandle_proxy=1.24,
    hard_violations=0,
    changed_vehicles=42,
    plan_retention_rate=91.6,
    calc_millis=1740,
)

KPI_BEFORE = PlanKpi(
    avg_move_distance=812.0,
    rehandle_proxy=1.93,
    hard_violations=2,
    changed_vehicles=61,
    plan_retention_rate=86.9,
    calc_millis=2110,
)

# ⚠️ 계약서 예시의 슬롯 ID("B03-R12-C04" 등)는 이 격자에 실재하지 않는다 — B03 은 13~17행이라
# R12 가 없다. 여기서는 ids.is_valid 를 통과하는 진짜 슬롯을 쓴다(replan.py 가 만드는 값과 같다).
MOVES = [
    {
        "vehicle_id": "V-0182",
        "from_slot": "B02-R04-C25",
        "to_slot": "B03-R13-C04",
        "sequence": 1,
        "reason": "B02 블록 폐쇄로 인한 재배치",
        "distance_meters": 1284.0,
    },
    {
        "vehicle_id": "V-0183",
        "from_slot": "B02-R04-C26",
        "to_slot": "B03-R13-C05",
        "sequence": 2,
        "reason": "B02 블록 폐쇄로 인한 재배치",
        "distance_meters": 468.0,
    },
    {
        "vehicle_id": "V-0411",
        "from_slot": None,
        "to_slot": "B01-R04-C11",
        "sequence": 3,
        "reason": "신규 배치",
        "distance_meters": 312.0,
    },
]


def test_draft_matches_contract_example_exactly():
    draft = briefing.build_briefing_draft(KPI_AFTER, KPI_BEFORE, MOVES)

    assert draft == (
        "B02 블록 폐쇄로 42대가 재배치되었습니다.\n"
        "- 평균 이동거리: 812 → 502m (38% 감소)\n"
        "- 재취급 Proxy: 1.9 → 1.2 (36% 감소)\n"
        "- 계획 유지율: 86.9 → 91.6% (5% 증가)\n"
        "- Hard 제약 위반: 0건\n"
        "- 이동 대상 차량: 42대\n"
        "- 계산 소요: 1740ms"
    )


def test_draft_omits_comparison_without_kpi_before():
    draft = briefing.build_briefing_draft(KPI_AFTER, None, MOVES)

    assert draft == (
        "B02 블록 폐쇄로 42대가 재배치되었습니다.\n"
        "- 평균 이동거리: 502m\n"
        "- 재취급 Proxy: 1.2\n"
        "- 계획 유지율: 91.6%\n"
        "- Hard 제약 위반: 0건\n"
        "- 이동 대상 차량: 42대\n"
        "- 계산 소요: 1740ms"
    )
    assert "→" not in draft


def test_draft_skips_comparison_when_before_is_zero():
    """0 나눗셈을 피한다. 그 지표만 비교를 생략하고 나머지는 그대로 비교한다."""
    zero_before = KPI_BEFORE.model_copy(update={"avg_move_distance": 0.0})

    draft = briefing.build_briefing_draft(KPI_AFTER, zero_before, MOVES)

    assert "- 평균 이동거리: 502m" in draft
    assert "- 재취급 Proxy: 1.9 → 1.2 (36% 감소)" in draft


def test_headline_uses_changed_vehicles_not_len_moves():
    """스프링이 moves 를 200건으로 잘라 보내도 대수가 틀어지면 안 된다."""
    draft = briefing.build_briefing_draft(KPI_AFTER, None, MOVES[:1])

    assert draft.startswith("B02 블록 폐쇄로 42대가 재배치되었습니다.")


def test_headline_when_nothing_moved():
    idle = KPI_AFTER.model_copy(update={"changed_vehicles": 0})

    draft = briefing.build_briefing_draft(idle, None, [])

    assert draft.startswith("이번 판에서 이동한 차량은 없습니다.")


def test_headline_without_closure_reason():
    draft = briefing.build_briefing_draft(KPI_AFTER, None, [MOVES[2]])

    assert draft.startswith("42대가 재배치되었습니다.")


def test_headline_lists_multiple_closed_blocks_in_order():
    moves = [
        {**MOVES[0], "reason": "B04 블록 폐쇄로 인한 재배치"},
        {**MOVES[1], "reason": "B02 블록 폐쇄로 인한 재배치"},
    ]

    draft = briefing.build_briefing_draft(KPI_AFTER, None, moves)

    assert draft.startswith("B02, B04 블록 폐쇄로 42대가 재배치되었습니다.")


def test_tail_line_appears_when_hard_violations_remain():
    violated = KPI_AFTER.model_copy(update={"hard_violations": 3})

    draft = briefing.build_briefing_draft(violated, None, MOVES)

    assert draft.endswith(
        "- 계산 소요: 1740ms\n\nHard 제약 위반이 남아 있어 승인 전 확인이 필요합니다."
    )


def test_assemble_replaces_headline_and_appends_note():
    draft = briefing.build_briefing_draft(KPI_AFTER, KPI_BEFORE, MOVES)

    text = briefing.assemble(draft, "새 헤드라인입니다.", "인과 설명 한 줄.")

    assert text == (
        "새 헤드라인입니다.\n"
        "- 평균 이동거리: 812 → 502m (38% 감소)\n"
        "- 재취급 Proxy: 1.9 → 1.2 (36% 감소)\n"
        "- 계획 유지율: 86.9 → 91.6% (5% 증가)\n"
        "- Hard 제약 위반: 0건\n"
        "- 이동 대상 차량: 42대\n"
        "- 계산 소요: 1740ms\n"
        "\n"
        "인과 설명 한 줄."
    )


def test_assemble_keeps_tail_after_note():
    violated = KPI_AFTER.model_copy(update={"hard_violations": 3})
    draft = briefing.build_briefing_draft(violated, None, MOVES)

    text = briefing.assemble(draft, "새 헤드라인입니다.", "인과 설명 한 줄.")

    assert text.endswith(
        "인과 설명 한 줄.\n\nHard 제약 위반이 남아 있어 승인 전 확인이 필요합니다."
    )


def test_assemble_without_note_keeps_bullets_identical():
    draft = briefing.build_briefing_draft(KPI_AFTER, KPI_BEFORE, MOVES)

    text = briefing.assemble(draft, "새 헤드라인입니다.", None)

    assert text.split("\n")[1:] == draft.split("\n")[1:]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, 0.0),
        (38.24, 38.2),
        (-38.24, -38.2),
        (2.25, 2.3),  # 파이썬 기본 round() 는 은행가 반올림이라 2.2 를 준다 - 그래서 안 쓴다
        (-0.05, -0.1),
    ],
)
def test_round1(value, expected):
    assert briefing.round1(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(812.0, "812"), (1.93, "1.9"), (91.6, "91.6"), (0.0, "0"), (1740.0, "1740")],
)
def test_fmt(value, expected):
    assert briefing.fmt(value) == expected


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (812.0, 502.0, "38% 감소"),
        (1.93, 1.24, "36% 감소"),
        (86.9, 91.6, "5% 증가"),
        (100.0, 100.0, "변화 없음"),
        # 반올림하면 -0.0 이 된다. 부호만 남은 0 을 "0% 감소" 로 쓰지 않는다.
        (1000.0, 999.6, "변화 없음"),
    ],
)
def test_delta_label(before, after, expected):
    assert briefing.delta_label(before, after, "LOWER") == expected


def test_allowed_number_tokens_covers_both_kpis_and_delta_percents():
    tokens = briefing.allowed_number_tokens(KPI_AFTER, KPI_BEFORE)

    assert {"502", "1.2", "0", "42", "91.6", "1740"} <= tokens  # 이번 판
    assert {"812", "1.9", "2", "61", "86.9", "2110"} <= tokens  # 이전 판
    assert {"38", "36", "5"} <= tokens  # 라벨용 정수 퍼센트


def test_validate_llm_text_accepts_draft_numbers_and_real_ids():
    briefing._validate_llm_text(
        "B02 블록 폐쇄로 42대가 재배치되었습니다.\nV-0182 를 B03-R13-C04 로 옮겼습니다.",
        KPI_AFTER,
        KPI_BEFORE,
        MOVES,
    )


def test_validate_llm_text_rejects_invented_number():
    with pytest.raises(ValueError, match="숫자"):
        briefing._validate_llm_text(
            "재취급이 7번 줄었습니다.", KPI_AFTER, KPI_BEFORE, MOVES
        )


def test_validate_llm_text_rejects_id_not_in_moves():
    with pytest.raises(ValueError, match="ID"):
        briefing._validate_llm_text(
            "V-9999 를 옮겼습니다.", KPI_AFTER, KPI_BEFORE, MOVES
        )


def test_validate_llm_text_rejects_slot_outside_grid():
    """형식은 맞지만 격자에 없는 슬롯(도로 칸)은 가짜 ID 다.

    입력 moves 에 실려 왔더라도 통과시키지 않는다 — 화면에서 클릭이 안 되는 죽은 ID 라
    문장에 넣을 이유가 없다. 이 경우 호출부(gemini_client)는 폴백 문장으로 떨어진다.
    """
    dead_slot_moves = [{**MOVES[0], "to_slot": "B03-R12-C04"}]

    with pytest.raises(ValueError, match="실재하지 않는 ID"):
        briefing._validate_llm_text(
            "B03-R12-C04 로 옮겼습니다.", KPI_AFTER, KPI_BEFORE, dead_slot_moves
        )


def test_validate_llm_text_rejects_slot_not_in_input():
    with pytest.raises(ValueError, match="입력\\(moves\\)에 없는 ID"):
        briefing._validate_llm_text(
            "B01-R04-C12 로 옮겼습니다.", KPI_AFTER, KPI_BEFORE, MOVES
        )


# --------------------------------------------------------------------------
# 5xx 방지 — moves 는 list[dict] 라 값의 타입이 보장되지 않는다(계약서 4.1/4.4)
# --------------------------------------------------------------------------


def test_draft_survives_non_string_reason():
    """사유가 문자열이 아니어도 500 이 아니라 그냥 폐쇄 블록이 없는 걸로 본다."""
    weird = [{**MOVES[0], "reason": 123}, {**MOVES[1], "reason": None}]

    draft = briefing.build_briefing_draft(KPI_AFTER, None, weird)

    assert draft.startswith("42대가 재배치되었습니다.")


def test_allowed_id_tokens_survives_non_string_values():
    tokens = briefing.allowed_id_tokens(
        [{"vehicle_id": "V-0182", "to_slot": 404, "reason": 123}]
    )

    assert tokens == {"V-0182"}
