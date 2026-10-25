"""The actual user-edited qualitative file works in a new replayable study."""

import json

import pytest

from auditors_paradox.config import StudyConfig, load_config
from auditors_paradox.contracts import Completion
from auditors_paradox.evaluation.validate import validate_run
from auditors_paradox.runner import run_experiment


@pytest.mark.parametrize("version", [1, 2])
def test_actual_edited_system_and_json_action_run_without_equation_extraction(root, tmp_path, version):
    raw = load_config(root / "configs/v2/warning.yaml").model_dump(mode="json")
    raw.update(schema_version=version, rounds=3, analysis_last_rounds=1)
    if version == 1:
        raw["v2"] = None
    else:
        raw["v2"].update(manual_enabled=False, index_enabled=False)
    raw["feedback"]["schedule_rounds"] = []
    raw["agent"].update(backend="scripted", model="scripted", prompt_profile="price_only",
        action_parser="json_price_v1", decision_workers=1,
        system_prompt_path="prompts/pricing/explicit-economics.txt",
        action_prompt_path="prompts/pricing/json-price-action-v1.txt")

    class FakeModel:
        def complete(self, messages):
            assert "The actual demand equation is:" not in messages[0]["content"]
            assert "you reasoned" not in json.dumps(messages)
            return Completion('{"price":150}', 50, 5, "scripted")

    path = run_experiment(StudyConfig.model_validate(raw), root, output_root=tmp_path,
                          run_id=f"approved-edited-prompt-v{version}", backend_factory=lambda *_: FakeModel())
    assert validate_run(path)["valid"]
    manifest = json.loads(path.read_text())
    assert manifest["pricing_templates"]["system"] == (root / "prompts/pricing/explicit-economics.txt").read_text()
