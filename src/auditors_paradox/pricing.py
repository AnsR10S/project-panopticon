"""Select frozen prompt versions without rewriting historical templates."""

from __future__ import annotations

import re
from collections.abc import Callable
from string import Formatter

from auditors_paradox.config import StudyConfig

NEW_PRICE_PROFILES = {"qualitative_v2", "explicit_economics_v2"}
PRICE_ONLY_PROFILES = {"price_only", "economics_price_only", *NEW_PRICE_PROFILES}


def template_fields(text: str) -> set[str]:
    """Inspect formatted and nested placeholders; escaped literal braces are not fields."""
    fields: set[str] = set()
    pending = [text]
    for _ in range(10):
        if not pending:
            return fields
        current, pending = pending, []
        for part in current:
            for _, name, spec, _ in Formatter().parse(part):
                if name is not None:
                    fields.add(re.split(r"[.\[]", name, maxsplit=1)[0])
                    if spec:
                        pending.append(spec)
    raise ValueError("Prompt formatting nesting is too deep")


def pricing_templates(cfg: StudyConfig, read: Callable[[str], str]) -> tuple[str, str]:
    """Use the same selector for execution and archived-source validation."""
    if cfg.agent.system_prompt_path:
        system = read(cfg.agent.system_prompt_path)
        action = action_template(cfg.agent.prompt_profile, read)
    else:
        system, action = profile_templates(cfg, read)
    if cfg.agent.action_parser == "json_price_v1":
        action = read("prompts/pricing/json-price-action-v1.txt")
        system = system.replace(
            "Return exactly one line: PRICE: <integer>\nReplace <integer> with your chosen valid price. No additional text.",
            'Return exactly one JSON object: {{"price": <integer>}}\nNo additional keys or text.',
        )
    if cfg.agent.action_prompt_path:
        action = read(cfg.agent.action_prompt_path)
        if any(
            "{" + key + "}" not in action
            for key in ("round_number", "memory_context", "recent_messages")
        ):
            raise ValueError(
                "Custom action prompt must retain round_number, memory_context and recent_messages"
            )
    if not cfg.disclose_horizon and (cfg.agent.system_prompt_path or cfg.agent.action_prompt_path):
        safe_system = system.replace("across {n_rounds} rounds", "")
        safe_action = action.replace(" of {n_rounds}", "")
        if "n_rounds" in template_fields(safe_system + safe_action):
            raise ValueError(
                "Hidden-horizon custom prompts must omit n_rounds except the standard managed horizon phrases"
            )
    names = set(re.findall(r"{(custom_[a-z][a-z0-9_]*)}", system + action))
    if names != set(cfg.agent.prompt_variables):
        raise ValueError("Custom prompt placeholders and agent.prompt_variables must match exactly")

    def substitute(match: re.Match[str]) -> str:
        return cfg.agent.prompt_variables[match.group(1)].replace("{", "{{").replace("}", "}}")

    system = re.sub(r"{(custom_[a-z][a-z0-9_]*)}", substitute, system)
    action = re.sub(r"{(custom_[a-z][a-z0-9_]*)}", substitute, action)
    return system, action


def profile_templates(cfg: StudyConfig, read: Callable[[str], str]) -> tuple[str, str]:
    """Load historical or versioned defaults before applying explicit custom templates."""
    profile = cfg.agent.prompt_profile
    if profile in NEW_PRICE_PROFILES:
        system = read("prompts/pricing/alternatives-v2-system.txt")
        equations = ""
        if profile == "explicit_economics_v2":
            existing = read("prompts/pricing/explicit-economics.txt")
            start = "The actual demand equation is:\n"
            end = "The outside option is included in total demand."
            if existing.count(start) != 1 or existing.count(end) != 1:
                raise ValueError(
                    "The selected explicit_economics_v2 profile requires its equation block. "
                    "For your edited qualitative text, select a numbered/qualitative prompt or set "
                    "agent.system_prompt_path explicitly. New plans accept edited custom prompts; "
                    "this is a profile-content mismatch, not a hash approval requirement."
                )
            equations = start + existing.split(start, 1)[1].split(end, 1)[0]
        return system.replace("{market_equations}", equations), action_template(profile, read)
    system = read("prompts/pricing/system.txt")
    if profile in {"explicit_economics", "economics_price_only", "economics_private_note"}:
        system = read("prompts/pricing/explicit-economics.txt")
    return system, action_template(profile, read)


def action_template(profile: str, read: Callable[[str], str]) -> str:
    """Select the output instructions without reading an overridden system prompt."""
    if profile in NEW_PRICE_PROFILES:
        return read("prompts/pricing/alternatives-v2-action.txt")
    if profile in PRICE_ONLY_PROFILES:
        return read("prompts/pricing/price-only.txt")
    if profile == "economics_private_note":
        return read("prompts/pricing/private-note.txt")
    if profile == "legacy":
        return read("prompts/pricing/action.txt")
    action = read("prompts/pricing/price-first.txt")
    if profile == "bounded_price_first":
        action += "\n" + read("prompts/pricing/bounded-guidance.txt")
    return action
