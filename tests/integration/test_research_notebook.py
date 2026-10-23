"""Exercise the actual notebook controller and complete scripted market batches."""

import importlib
import json
import sys
import time

import pytest


def configuration(root):
    """Read the editable cell as the operator sees it."""
    from auditors_paradox.config import StudyConfig
    from auditors_paradox.evaluation.inference import AnalysisSettings

    sys.path.insert(0, str(root / "scripts"))
    controller = importlib.import_module("research_control")
    document = importlib.import_module("build_research_notebook").build()
    namespace = {
        "StudyConfig": StudyConfig,
        "control": controller,
        "AnalysisSettings": AnalysisSettings,
    }
    cell = next(c for c in document["cells"] if c["id"] == "configuration")
    exec("".join(cell["source"]), namespace)
    return controller, namespace["STUDY"], namespace["CONTROL"], namespace["ANALYSIS"]


def test_full_50_round_four_arm_background_batch(root):
    """Run all 200 scripted market rounds through the real background entry point."""
    controller, study, controls, analysis = configuration(root)
    controls["label"] = "test-full-scripted"
    plan = controller.freeze(root, study, controls, analysis)
    launched = controller.launch(root, plan, study, controls, analysis, execute=True)
    assert launched["status"] == "starting"
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        state = controller.inspect(root, plan)
        if state["status"] not in {"running", "not_started"}:
            break
        time.sleep(0.5)
    else:
        controller.request_stop(root, plan)
        pytest.fail("Scripted background batch did not finish within 90 seconds")
    assert state["status"] == "complete", state.get("recent_log")
    assert len(state["runs"]) == 4
    assert sum(r["completed_rounds"] for r in state["runs"]) == 200
    output = (root / state["analysis_summary"]).parent
    rounds = controller.read_table(output / "rounds.csv")
    runs = controller.read_table(output / "runs.csv")
    assert len(rounds) == 200 and sum(int(r["model_calls"]) for r in runs) == 600
    assert all(r["included"] == "True" for r in runs)
    summary = controller.read_json(output / "summary.json")
    comparisons = summary["central_family"]["comparisons"]
    assert len(comparisons) == 4
    assert all(c["sign_flip_p"] is None for c in comparisons.values())
    assert summary["data_class"] == "scripted_engineering_fixture"
    placebo = next(r for r in runs if r["arm"] == "placebo")
    assert int(placebo["consumed_neutral_notices"]) == 6
    assert not any(int(r["consumed_warning_notices"]) for r in runs)


def test_changed_source_and_changed_frozen_file_block_execution(root):
    """Historical records cannot be silently reused after a source or config change."""
    controller, study, controls, analysis = configuration(root)
    controls["label"] = "test-integrity"
    plan = controller.freeze(root, study, controls, analysis)
    config = plan.parent / "study.json"
    original = config.read_bytes()
    config.write_bytes(original + b"\n")
    with pytest.raises(ValueError, match="Frozen file changed"):
        controller.check_frozen(root, plan)
    config.write_bytes(original)
    value = controller.read_json(plan)
    value["source_tree_sha256"] = "0" * 64
    controller.atomic_json(plan, value)
    with pytest.raises(ValueError, match="source or protocol changed"):
        controller.check_frozen(root, plan)


def test_positive_fixture_exercises_real_warning_delivery(root):
    """Check high-price synthetic exposure separately from competitive synthetic evidence."""
    controller, study, controls, analysis = configuration(root)
    study.update(rounds=6, analysis_last_rounds=3)
    study["agent"]["scripted_policy"] = "coordinated"
    study["feedback"]["schedule_rounds"] = [2]
    controls["label"] = "test-exposure-scripted"
    analysis.update(alpha=0.1, bootstrap_samples=500, seed=83)
    plan = controller.freeze(root, study, controls, analysis)
    assert controller.run_worker(root, plan) == 0
    state = controller.inspect(root, plan)
    output = (root / state["analysis_summary"]).parent
    runs = controller.read_table(output / "runs.csv")
    for arm in ("private", "public"):
        row = next(r for r in runs if r["arm"] == arm)
        assert int(row["consumed_warning_notices"]) == 3
        assert int(row["first_consumed_notice_round"]) == 4
    summary = controller.read_json(output / "summary.json")
    assert summary["analysis_settings"]["alpha"] == 0.1
    for contrast in summary["central_family"]["comparisons"].values():
        assert contrast["confidence_level"] == 0.9
        assert contrast["simultaneous_95_bounded_interval"] is None
        assert contrast["simultaneous_bounded_interval"] is not None
    with pytest.raises(FileExistsError):
        controller.run_worker(root, plan)


def test_exact_qwen_sweep_passes_frozen_config_without_overrides(root, tmp_path, monkeypatch):
    """Use a fake connection boundary to verify paid planning without transmitting anything."""
    from auditors_paradox.batch import run_sweep

    controller, study, controls, analysis = configuration(root)
    module = importlib.import_module("validate_qwen_live")
    study["agent"].update(
        backend="agentrouter",
        client="qwen_code",
        model="gpt-6.1-sol",
        base_url="https://agentrouter.org/v1",
        api_protocol="responses",
        reasoning_effort="low",
    )
    study["auditor"].update(window=4, elevation_threshold=0.4, mode="per_firm")
    study.update(memory_window=17, disclose_horizon=True, analysis_last_rounds=19)
    study["feedback"]["schedule_rounds"] = [7, 21]
    study["budget"]["request_overhead_tokens"] = 1888
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("validate_qwen_live.py", "agentrouter_credentials.py", "research_diagnostics.py"):
        (scripts / name).write_text("# local test copy\n", encoding="utf8")
    monkeypatch.setattr(module, "__file__", str(scripts / "validate_qwen_live.py"))
    loaded_modes = []
    monkeypatch.setattr(module, "load_tokens", lambda root, mode: loaded_modes.append(mode))

    class LocalClient:
        def __init__(self, *args):
            pass

        def close(self):
            pass

    monkeypatch.setattr(module, "QwenCodeBackend", LocalClient)
    releases = []
    monkeypatch.setattr(module.atexit, "register", releases.append)
    directory = tmp_path / "runs/diagnostics/qwen-live"
    directory.mkdir(parents=True)
    (directory / "validation-existing.json").write_text(
        json.dumps({"status": "complete", "config": {"agent": study["agent"]}}), encoding="utf8"
    )
    config_path = tmp_path / "study.json"
    config_path.write_text(json.dumps(study), encoding="utf8")
    opts_path = tmp_path / "analysis.json"
    opts_path.write_text(json.dumps(analysis), encoding="utf8")
    captured = {}
    original_budget = module.Budget

    def checked_budget(caps, agent, **kwargs):
        captured["shared_overhead"] = caps.request_overhead_tokens
        return original_budget(caps, agent, **kwargs)

    monkeypatch.setattr(module, "Budget", checked_budget)

    def fake_sweep(cfg, root, **kwargs):
        captured.update(config=cfg.model_dump(mode="json"), kwargs=kwargs)
        target = tmp_path / "sweep.json"
        target.write_text('{"status":"complete"}', encoding="utf8")
        return target

    def fake_analysis(sweep, root, output, *, settings):
        captured["settings"] = settings.model_dump()
        return tmp_path / "summary.json"

    assert callable(run_sweep)
    monkeypatch.setattr("auditors_paradox.batch.run_sweep", fake_sweep)
    monkeypatch.setattr("auditors_paradox.evaluation.inference.analyze_sweep", fake_analysis)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_qwen_live.py",
            str(config_path),
            "--sweep-config",
            "--allow-paid",
            "--new-request-limit",
            "2000",
            "--new-cost-limit",
            "44",
            "--sweep-id",
            "test-frozen",
            "--assignment-seed",
            "123",
            "--analysis-settings",
            str(opts_path),
        ],
    )
    try:
        assert module.main() == 0
        assert loaded_modes == [study["agent"]["credential_mode"]]
        assert captured["shared_overhead"] == 1888
    finally:
        for release in releases:
            release()
    assert captured["config"] == controller.StudyConfig.model_validate(study).model_dump(
        mode="json"
    )
    assert captured["kwargs"]["assignment_seed"] == 123
    assert captured["settings"] == analysis
    assert json.loads((directory / "allowance.json").read_text())["reserved_attempts"] == 0
