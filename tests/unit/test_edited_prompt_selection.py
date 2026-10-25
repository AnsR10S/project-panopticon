"""A custom system prompt must not load a replaced equation-based default."""

import pytest

from auditors_paradox.config import StudyConfig, load_config
from auditors_paradox.pricing import pricing_templates


@pytest.mark.parametrize("parser", ["auto", "json_price_v1"])
@pytest.mark.parametrize("both", [False, True])
def test_custom_system_skips_unselected_equation_source(root, parser, both):
    custom = "prompts/pricing/approved-edited-system.txt"
    action = "prompts/pricing/approved-edited-action.txt"
    data = load_config(root / "configs/v2/warning.yaml").model_dump(mode="json")
    data["agent"].update({
        "prompt_profile": "explicit_economics_v2", "action_parser": parser,
        "system_prompt_path": custom, "action_prompt_path": action if both else None,
    })
    cfg = StudyConfig.model_validate(data)
    accessed = []

    def read(name):
        accessed.append(name)
        if name == custom:
            return "APPROVED_EDITED_SYSTEM. No equation is supplied."
        if name == action:
            return "Round {round_number}\n{memory_context}\n{recent_messages}\nAPPROVED_EDITED_ACTION"
        if name in {"prompts/pricing/explicit-economics.txt", "prompts/pricing/alternatives-v2-system.txt"}:
            raise AssertionError("An overridden system template was accessed")
        return (root / name).read_text(encoding="utf8")

    system, selected = pricing_templates(cfg, read)
    assert system == "APPROVED_EDITED_SYSTEM. No equation is supplied."
    assert "prompts/pricing/explicit-economics.txt" not in accessed
    assert ("APPROVED_EDITED_ACTION" in selected) == both
    if not both and parser == "json_price_v1":
        assert '"price"' in selected


def test_genuine_explicit_profile_mismatch_is_actionable(root):
    data = load_config(root / "configs/v2/warning.yaml").model_dump(mode="json")
    data["agent"]["prompt_profile"] = "explicit_economics_v2"
    cfg = StudyConfig.model_validate(data)

    def read(name):
        if name == "prompts/pricing/explicit-economics.txt":
            return "A deliberately edited qualitative prompt without an equation."
        return (root / name).read_text(encoding="utf8")

    with pytest.raises(ValueError, match="profile-content mismatch"):
        pricing_templates(cfg, read)


def test_json_custom_price_only_prompts_allowed_without_weakening_other_parsers():
    cfg = StudyConfig.model_validate({"agent": {
        "prompt_profile": "price_only", "action_parser": "json_price_v1",
        "system_prompt_path": "prompts/pricing/my-system.txt",
        "action_prompt_path": "prompts/pricing/my-action.txt",
    }})
    assert cfg.agent.action_parser == "json_price_v1"
    with pytest.raises(ValueError, match="price-only"):
        StudyConfig.model_validate({"agent": {"prompt_profile": "legacy", "action_parser": "json_price_v1"}})
