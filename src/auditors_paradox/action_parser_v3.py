"""Recognize explicit final commitments without treating repeated headings as ambiguity."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auditors_paradox.action_parsing import ParsedAction


class FinalAnswerV3Parser:
    """Require agreement across final sections and accept explicit first-person price commands."""

    def parse(self, text: str, minimum: int, maximum: int) -> ParsedAction:
        from auditors_paradox.action_parsing import (
            FinalAnswerParser,
            ParsedAction,
            answer_without_thinking,
        )

        answer = answer_without_thinking(text)
        if answer is None:
            return ParsedAction(None, "unfinished_thinking")
        answer = "\n".join(
            line for line in answer.splitlines() if not line.lstrip().startswith(">")
        )
        answer = re.sub(r"(?m)^\s*```[^\n]*$", "", answer)
        answer = re.sub(r"[*`#]", "", answer).strip()
        answer = re.sub(r"(?im)^(\s*)(decision|answer)\s+price\s*:", r"\1\2: PRICE:", answer)
        answer = re.sub(r"(?im)^(\s*)choose\s+your\s+(?:own\s+)?price\s*:\s*", r"\1PRICE: ", answer)
        headings = list(
            re.finditer(
                r"(?im)^\s*(?:final\s+(?:answer|decision|price)|decision|answer)\s*:\s*", answer
            )
        )
        if headings:
            sections = [
                answer[
                    h.end() : headings[i + 1].start() if i + 1 < len(headings) else len(answer)
                ].strip()
                for i, h in enumerate(headings)
            ]
            sections = [section for section in sections if section]
        else:
            sections = [answer]
        if not sections:
            return ParsedAction(None, "no_final_commitment")
        decisions = []
        for section in sections:
            # Only a complete imperative clause changes form; considered/conditional prices do not.
            normalized = re.sub(
                r"(?im)^(\s*)(?:set|maintain|keep)\s+(?:my|the)\s+price\s+(?:at|to)\s+"
                r"(-?\d+)(?:\s+ticks)?(?P<reason>(?:\s+(?:to|for|because|based\s+on)\b[^\n]*|"
                r",\s*(?:as|since)\b[^\n]*)?)\s*[.!]?\s*$",
                lambda match: "I choose "
                + match.group(2)
                + (
                    " because " + re.sub(r"^,\s*(?:as|since)\s*", "", match.group("reason"))
                    if match.group("reason").startswith(",")
                    else match.group("reason")
                ),
                section,
            )
            decision = FinalAnswerParser().parse(normalized, minimum, maximum)
            if decision.price is None:
                return ParsedAction(None, decision.method)
            decisions.append(decision.price)
        if len(set(decisions)) != 1:
            return ParsedAction(None, "conflicting_final_sections")
        method = (
            "consistent_final_sections" if len(sections) > 1 else "explicit_final_commitment_v3"
        )
        return ParsedAction(decisions[0], method)
