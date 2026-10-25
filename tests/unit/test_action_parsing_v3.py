"""Keep repeated final declarations consistent without guessing considered prices."""

import pytest

from auditors_paradox.action_parsing import parse_action, parser_identity
from auditors_paradox.config import AgentConfig


@pytest.mark.parametrize(
    "text,expected",
    [
        ("**Decision:**\nSet the price at 135, as it is profitable.\n**Answer:**\nPRICE: 135", 135),
        (
            "**Decision:**\nMaintain the price at 135 to stay competitive.\n**Answer:**\nPRICE: 135",
            135,
        ),
        (
            "**Decision:**\nSet my price at 137 to maintain stability and balance in the market.",
            137,
        ),
        ("Decision: PRICE: 135\nAnswer: PRICE: 140", None),
        ("Decision: Set my price at 135.\nAnswer: PRICE: 140", None),
        ("Decision: I may choose 135 or 140.\nAnswer: PRICE: 140", None),
        ("Decision:\nAnswer: PRICE: 140", 140),
        ("I might choose 130.\nFinal answer: PRICE: 140", 140),
        ("<think>PRICE: 130</think>\nDecision: PRICE: 140\nAnswer: PRICE: 140", 140),
        ("<think>PRICE: 140", None),
        ("> Decision: PRICE: 130\nPRICE: 140", 140),
        ("If rivals hold, set my price at 137", None),
        ("I may set my price at 137", None),
        ("Do not set my price at 137", None),
        ("Set my price at 137.5", None),
        ("Set my price at 137 or 140", None),
        ("Set my price at 99", None),
        ("Set my price at 251", None),
        ("PRICE: 130\nPRICE: 140", None),
        ("Decision: I choose 130. I select 140.", None),
        ("My previous price was 137; my cost was 100.", None),
        ("DECISION PRICE: 137", 137),
        ("Decision: PRICE: 137\nAnswer: PRICE: 137.5", None),
        ("Decision: PRICE: 137\nAnswer: PRICE: 251", None),
        ("Decision: PRICE: 137\nAnswer: I choose 137\nFinal price: 137", 137),
    ],
)
@pytest.mark.parametrize("strategy", ["final_answer_v3", "final_answer_v4"])
def test_v3_final_commitments(text, expected, strategy):
    assert parse_action(text, 100, 250, strategy).price == expected


def test_v3_is_explicit_and_does_not_rewrite_historical_strategies():
    assert AgentConfig(action_parser="final_answer_v3").action_parser == "final_answer_v3"
    text = "Decision: PRICE: 135\nAnswer: PRICE: 135"
    assert parse_action(text, 100, 250, "final_answer_v2").price is None
    assert parse_action(text, 100, 250, "final_answer_v3").price == 135
    assert parser_identity("auto", "deepseek-r1:7b") == "final_answer_v1"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("The chosen action is: PRICE: 185", 185),
        ("Previous trends varied.\nThe chosen action is: PRICE: 185", 185),
        ("My selected action is: PRICE: 137", 137),
        ("If stable, the chosen action is: PRICE: 185", None),
        ("The chosen action is: PRICE: 185 or 190", None),
        ("The chosen action is: PRICE: 185\nPRICE: 190", None),
        ("<think>The chosen action is: PRICE: 185", None),
        ("The chosen action is: PRICE: 251", None),
    ],
)
def test_v4_chosen_action_wrapper(text, expected):
    assert parse_action(text, 100, 250, "final_answer_v4").price == expected


def test_v4_does_not_reinterpret_v3():
    text = "The chosen action is: PRICE: 185"
    assert parse_action(text, 100, 250, "final_answer_v3").price is None
