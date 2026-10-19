"""Here we test the run labels, the assignments and the output paths."""

import json

import pytest

from auditors_paradox.batch import run_sweep
from auditors_paradox.config import StudyConfig
from auditors_paradox.contracts import Completion
from auditors_paradox.evaluation.inference import analyze_sweep
from auditors_paradox.evaluation.validate import validate_run
from auditors_paradox.evidence import atomic_json
from auditors_paradox.runner import run_experiment


def test_BR_007_manifest_relabeling_and_template_tampering(root, config, tmp_path):
    """Relabeling a manifest or editing its templates has to break validation."""
    path = run_experiment(config, root, output_root=tmp_path, run_id="metadata")
    manifest = json.loads(path.read_text())
    changed = {**manifest, "comparison_key": "incorrect"}
    atomic_json(path, changed)
    assert "Comparison identity" in str(validate_run(path)["errors"])
    changed = {
        **manifest,
        "pricing_templates": {**manifest["pricing_templates"], "system": "changed"},
    }
    atomic_json(path, changed)
    assert not validate_run(path)["valid"]
    atomic_json(path, manifest)
    assert validate_run(path)["valid"]
    assert not validate_run(path.with_name("nonexistent.json"))["valid"]


def test_BR_010_analysis_refuses_reused_run_as_new_assignment(root, config, tmp_path):
    """The analysis refuses an old run passed off as a new assignment."""
    path = run_sweep(config, root, replicates=1, sweep_id="guard", output_root=tmp_path)
    manifest = json.loads(path.read_text())
    manifest["runs"][0]["manifest_path"] = manifest["runs"][1]["manifest_path"]
    atomic_json(path, manifest)
    with pytest.raises(ValueError, match="identity"):
        analyze_sweep(path, root, tmp_path / "refused")


def test_BR_010_analysis_retains_corrupt_and_missing_attempts(root, config, tmp_path):
    """Corrupt runs stay in the counts and a missing assignment stops the analysis."""
    path = run_sweep(config, root, replicates=1, sweep_id="corrupt", output_root=tmp_path)
    manifest = json.loads(path.read_text())
    first = root / manifest["runs"][0]["manifest_path"]
    first.with_name("events.jsonl").write_text("corrupted")
    result = json.loads(analyze_sweep(path, root, tmp_path / "analyzed").read_text())
    assert sum(c["included"] for c in result["counts"].values()) == 3
    manifest["runs"].pop()
    atomic_json(path, manifest)
    with pytest.raises(ValueError, match="omits"):
        analyze_sweep(path, root, tmp_path / "missing-assignment")


def test_BR_001_output_paths_cannot_escape_project(root, config, tmp_path):
    """Outputs can never be written outside the project folder."""
    outside = root.parent / "not-a-project-output"
    with pytest.raises(ValueError, match="inside"):
        run_experiment(config, root, output_root=outside)
    with pytest.raises(ValueError, match="inside"):
        run_sweep(config, root, output_root=outside)
    with pytest.raises(ValueError):
        run_sweep(config, root, sweep_id="x" * 95, output_root=tmp_path)


def test_BR_009_negative_usage_is_invalid():
    """Negative token usage is rejected."""
    with pytest.raises(ValueError):
        Completion("PRICE: 60", -1, 4, "invalid")


@pytest.mark.parametrize("total", [float("nan"), float("inf"), -1, 0])
def test_BR_009_total_live_budget_must_be_finite(root, config, tmp_path, total):
    """A live sweep needs a real finite total budget above zero."""
    live = StudyConfig.model_validate(
        {
            **config.model_dump(),
            "agent": {"backend": "openai", "model": "chosen"},
            "budget": {"max_cost_usd": 1, "input_usd_per_million": 1, "output_usd_per_million": 1},
        }
    )
    with pytest.raises(ValueError, match="Live sweeps"):
        run_sweep(live, root, allow_paid=True, max_total_cost_usd=total, output_root=tmp_path)
