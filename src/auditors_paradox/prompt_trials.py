"""Versioned qualitative prompt strategies; trial labels never enter firm messages."""

from __future__ import annotations

import copy
import re
from typing import Any

TRIALS = ("prompt_1_v1", "prompt_2_v1", "prompt_3_v1")


def apply_trial(study: dict[str, Any], trial: str) -> dict[str, Any]:
    """Select both templates and the same output contract through existing configuration."""
    if trial not in TRIALS:
        raise ValueError("Choose one of the three frozen qualitative prompt trials")
    result = copy.deepcopy(study)
    number = TRIALS.index(trial) + 1
    agent = result["agent"]
    if agent.get("prompt_variables") and set(agent["prompt_variables"]) != {"custom_response_format"}:
        raise ValueError("Prompt trials cannot silently discard custom prompt variables")
    response = (
        'Return exactly one JSON object: {"price": <integer>}. Replace <integer> with your chosen valid integer price. No additional keys, explanations, strategy notes, Markdown, or other text.'
        if agent.get("action_parser") == "json_price_v1"
        else "Return exactly one line: PRICE: <integer>. Replace <integer> with your chosen valid integer price. No explanations, strategy notes, Markdown, or additional text."
    )
    agent.update(
        prompt_profile="price_only",
        system_prompt_path=f"prompts/pricing/trial-{number}-system-v1.txt",
        action_prompt_path=f"prompts/pricing/trial-{number}-action-v1.txt",
        prompt_variables={"custom_response_format": response},
    )
    return result


def trial_metadata(study: dict[str, Any], assignment: str | None = None) -> dict[str, Any]:
    """Derive identity from frozen template paths, independent of human-facing study names."""
    agent = study["agent"]
    path = agent.get("system_prompt_path") or ""
    match = re.fullmatch(r"prompts/pricing/trial-([123])-system-v1\.txt", path)
    if not match:
        return {}
    number = int(match[1])
    if agent.get("action_prompt_path") != f"prompts/pricing/trial-{number}-action-v1.txt":
        raise ValueError("System and action prompt trial versions do not match")
    cycle = re.fullmatch(r"nb-c(\d{4})-v2-(?:warning|event|continuing)-[a-z0-9]+", assignment or "")
    return {
        "prompt_trial": TRIALS[number - 1],
        "prompt_number": number,
        "prompt_version": 1,
        **({"cycle": int(cycle[1])} if cycle else {}),
    }
