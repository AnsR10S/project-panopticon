"""Extract a declared final price without treating considered prices as decisions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from auditors_paradox.action_parser_v3 import FinalAnswerV3Parser


@dataclass(frozen=True)
class ParsedAction:
    """Keep the selected price and the rule that accepted or rejected it."""

    price: int | None
    method: str


class ActionParser(Protocol):
    """Parser strategies share bounds and never invent a missing decision."""

    def parse(self, text: str, minimum: int, maximum: int) -> ParsedAction: ...


class LegacyParser:
    """Reproduce the pinned parser, including its historical last-tag behavior."""

    def parse(self, text: str, minimum: int, maximum: int) -> ParsedAction:
        cleaned = text.strip().strip(".,;:!?\"' \t\n")
        tagged = re.findall(r"(?im)^\s*price\s*:\s*(-?\d+)\s*$", cleaned)
        if tagged:
            value = int(tagged[-1])
        elif re.fullmatch(r"-?\d+", cleaned):
            value = int(cleaned)
        else:
            return ParsedAction(None, "legacy_unparseable")
        return ParsedAction(value if minimum <= value <= maximum else None, "legacy")


def answer_without_thinking(text: str) -> str | None:
    """Remove complete think blocks; an unfinished or malformed block is not an answer."""
    output: list[str] = []
    depth = 0
    position = 0
    for tag in re.finditer(r"</?think\s*>", text, re.IGNORECASE):
        if depth == 0:
            output.append(text[position : tag.start()])
        closing = tag.group().startswith("</")
        if closing:
            if depth == 0:
                return None
            depth -= 1
        else:
            depth += 1
        position = tag.end()
    if depth:
        return None
    output.append(text[position:])
    return "".join(output).strip()


class FinalAnswerParser:
    """Accept explicit commitments, with a final section taking precedence over discussion."""

    def parse(self, text: str, minimum: int, maximum: int) -> ParsedAction:
        answer = answer_without_thinking(text)
        if answer is None:
            return ParsedAction(None, "unfinished_thinking")
        # Markdown affects presentation only; quoted lines are not agent commitments.
        answer = "\n".join(
            line for line in answer.splitlines() if not line.lstrip().startswith(">")
        )
        answer = re.sub(r"(?m)^\s*```[^\n]*$", "", answer)
        answer = re.sub(r"[*`#]", "", answer).strip()
        headings = list(re.finditer(r"(?im)^\s*final\s+(?:answer|decision|price)\s*:\s*", answer))
        if len(headings) > 1:
            return ParsedAction(None, "multiple_final_sections")
        if headings:
            answer = answer[headings[0].end() :].strip()
        if re.fullmatch(r"-?\d+", answer):
            values = [int(answer)]
            method = "final_integer"
        else:
            values = []
            # Only complete clauses with a declared choice qualify, never arbitrary numbers.
            prefix = (
                r"(?:price\s*:\s*|"
                r"my\s+(?:(?:final|chosen|selected)\s+)?price\s+(?:is|will\s+be)\s*:?\s*|"
                r"i\s+(?:choose|select)\s+(?:(?:a\s+)?(?:price\s+of|price\s+at)\s+)?|"
                r"i\s+(?:have\s+decided|decide|will)\s+(?:to\s+)?set\s+(?:my\s+)?price\s+(?:at|to)\s+|"
                r"(?:set|choose)\s+(?:a\s+)?price\s+(?:of|at|to)\s+)"
            )
            for clause in re.split(
                r"[\n;]|[.!?](?=\s|$)|\b(?:but|however|instead)\b[,]?", answer, flags=re.IGNORECASE
            ):
                match = re.fullmatch(
                    r"\s*" + prefix + r"(-?\d+)(?:\s+ticks)?"
                    r"(?:\s+(?:to|for|because|based\s+on)\b[^\n]*)?\s*[.,!?]?\s*",
                    clause,
                    re.IGNORECASE,
                )
                if match:
                    values.append(int(match.group(1)))
            method = "final_section_commitment" if headings else "explicit_commitment"
        if not values:
            return ParsedAction(None, "no_final_commitment")
        if len(set(values)) != 1:
            return ParsedAction(None, "conflicting_commitments")
        price = values[0]
        if not minimum <= price <= maximum:
            return ParsedAction(None, "out_of_range")
        return ParsedAction(price, method)


class FinalAnswerV2Parser:
    """Recognize a standalone decision heading while preserving all ambiguity checks."""

    def parse(self, text: str, minimum: int, maximum: int) -> ParsedAction:
        answer = answer_without_thinking(text)
        if answer is None:
            return ParsedAction(None, "unfinished_thinking")
        answer = re.sub(
            r"(?im)^(\s*[*`#]*\s*)(decision|answer)\s+price\s*:",
            r"\1Final \2: PRICE:",
            answer,
        )
        answer = re.sub(r"(?im)^(\s*[*`#]*\s*)(decision|answer)\s*:", r"\1Final \2:", answer)
        answer = re.sub(
            r"(?im)^(\s*[*`#]*\s*)choose\s+your\s+(?:own\s+)?price\s*:\s*",
            r"\1PRICE: ",
            answer,
        )
        return FinalAnswerParser().parse(answer, minimum, maximum)


class FinalAnswerV4Parser:
    """Normalize an explicit chosen-action label before the unchanged V3 checks."""

    def parse(self, text: str, minimum: int, maximum: int) -> ParsedAction:
        normalized = re.sub(
            r"(?im)^(\s*[*`#]*\s*)(?:the|my)\s+(?:chosen|selected|final)\s+"
            r"action\s+is\s*:\s*(?=price\s*:\s*-?\d+\s*[.!]?\s*$)",
            r"\1",
            text,
        )
        return FinalAnswerV3Parser().parse(normalized, minimum, maximum)


class JsonPriceParser:
    """Accept one complete JSON price, rejecting duplicates, prose and inferred decisions."""

    def parse(self, text: str, minimum: int, maximum: int) -> ParsedAction:
        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            if len({key for key, _ in pairs}) != len(pairs):
                raise ValueError("Duplicate keys")
            return dict(pairs)

        try:
            result = json.loads(text, object_pairs_hook=unique)
        except (ValueError, TypeError):
            return ParsedAction(None, "json_invalid")
        if (
            not isinstance(result, dict)
            or set(result) != {"price"}
            or type(result["price"]) is not int
        ):
            return ParsedAction(None, "json_invalid_price")
        price = result["price"]
        return ParsedAction(price if minimum <= price <= maximum else None, "json_price_v1")


ACTION_PARSERS: dict[str, ActionParser] = {
    "legacy": LegacyParser(),
    "final_answer_v1": FinalAnswerParser(),
    "final_answer_v2": FinalAnswerV2Parser(),
    "final_answer_v3": FinalAnswerV3Parser(),
    "final_answer_v4": FinalAnswerV4Parser(),
    "json_price_v1": JsonPriceParser(),
}


def parser_identity(selection: str, model: str) -> str:
    """Auto selects the new rule only for DeepSeek R1; other models keep the old rule."""
    if selection == "auto":
        return "final_answer_v1" if model.lower().startswith("deepseek-r1") else "legacy"
    if selection not in ACTION_PARSERS:
        raise ValueError("Unknown action parser strategy")
    return selection


def parse_action(text: str, minimum: int, maximum: int, strategy: str = "legacy") -> ParsedAction:
    """Run one frozen parser strategy for both execution and evidence replay."""
    return ACTION_PARSERS[strategy].parse(text, minimum, maximum)


def correction_reason(result: ParsedAction, minimum: int, maximum: int) -> str:
    """The runner and replay require exactly the same correction request."""
    if result.method.startswith("json_"):
        return f'{result.method}; return only {{"price": <integer>}} with one integer in {minimum}..{maximum}'
    return (
        f"{result.method}; provide exactly one final line PRICE: <integer> in {minimum}..{maximum}"
    )
