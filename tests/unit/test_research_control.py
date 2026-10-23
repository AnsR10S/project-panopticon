"""Check notebook control boundaries without credentials or paid inference."""

import ast
import copy
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from auditors_paradox.evaluation.inference import AnalysisSettings, paired_estimate


@pytest.fixture
def controller(root, monkeypatch):
    """Load the same helpers the notebook uses."""
    monkeypatch.syspath_prepend(str(root / "scripts"))
    return importlib.import_module("research_control")


@pytest.fixture
def notebook_values(root):
    """Execute just the central configuration cell, which cannot make requests."""
    from auditors_paradox.config import StudyConfig

    sys.path.insert(0, str(root / "scripts"))
    import research_control
    from build_research_notebook import build

    document = build()
    namespace = {
        "StudyConfig": StudyConfig,
        "control": research_control,
        "AnalysisSettings": AnalysisSettings,
    }
    source = next(cell["source"] for cell in document["cells"] if cell["id"] == "configuration")
    exec("".join(source), namespace)
    return namespace["STUDY"], namespace["CONTROL"], namespace["ANALYSIS"]


def test_every_runner_and_analysis_field_has_an_explanation(controller, notebook_values):
    """No exposed knob can silently disappear from the manual."""
    import research_parameters

    rows = research_parameters.parameter_rows(*notebook_values)
    assert len(rows) == len(research_parameters.EXPLANATIONS)
    assert len({row["parameter"] for row in rows}) == len(rows)
    assert all(row["meaning_and_change"] and row["validated_values"] for row in rows)


def test_preview_counts_actual_decisions_and_blocks(root, controller, notebook_values):
    """Four 50-round trajectories are 600 decisions and one paired block."""
    result = controller.preview(root, *notebook_values)
    assert result["planned_market_rounds"] == 200
    assert result["minimum_requests_and_accepted_decisions"] == 600
    assert result["independent_blocks"] == 1
    assert result["benchmarks"]["nash_price"] == 137
    assert not result["paid"]


def test_questions_follow_actual_assigned_arms_and_notice_mechanism(
    root, controller, notebook_values
):
    """A single-arm smoke or neutral-notification test must not advertise the four-arm question."""
    study, controls, analysis = copy.deepcopy(notebook_values)
    core = controller.preview(root, study, controls, analysis)
    assert len(core["assigned_supported_contrasts"]) == 4
    study["batch_arms"] = ["silent"]
    smoke = controller.preview(root, study, controls, analysis)
    assert not smoke["assigned_supported_contrasts"]
    assert "no supported between-arm contrast" in smoke["research_question"]
    study["batch_arms"] = ["private", "public"]
    study["feedback"]["warning_source"] = "fixed"
    scheduled = controller.preview(root, study, controls, analysis)
    assert scheduled["assigned_supported_contrasts"] == ["public_minus_private"]
    assert "neutral-notification" in scheduled["research_question"]


def test_paid_preview_preserves_settings_and_reports_insufficient_allowance(
    root, controller, notebook_values, monkeypatch
):
    """Research settings are never silently replaced to fit the money."""
    study, controls, analysis = copy.deepcopy(notebook_values)
    study["agent"].update(
        backend="agentrouter",
        client="qwen_code",
        model="gpt-6.1-sol",
        base_url="https://agentrouter.org/v1",
        api_protocol="responses",
        reasoning_effort="low",
    )
    controls["replicates"] = 2
    study["budget"]["enforce_spending_caps"] = True
    controls["new_cost_limit_usd"] = 44
    before = copy.deepcopy(study)
    monkeypatch.setattr(
        controller, "read_json", lambda path: {"reserved_usd": 5, "reserved_attempts": 58}
    )
    result = controller.preview(root, study, controls, analysis)
    assert study == before
    assert result["paid_launch_blockers"]
    assert result["minimum_requests_and_accepted_decisions"] == 1200
    assert result["minimum_dispatch_minutes_at_selected_pacing"] == 1199


def test_analysis_settings_change_intervals_and_keep_seeded_results():
    """Alpha and numeric settings really reach the inference procedure."""
    differences = [i / 20 for i in range(1, 12)]
    defaults = paired_estimate(differences)
    options = AnalysisSettings(
        alpha=0.2,
        bootstrap_samples=2000,
        exact_sign_flip_max_pairs=4,
        monte_carlo_samples=3000,
        seed=71,
    )
    altered = paired_estimate(differences, settings=options)
    assert altered == paired_estimate(differences, settings=options)
    assert altered["confidence_level"] == 0.8
    assert altered["bootstrap_95_interval"] is None
    assert altered["bootstrap_interval"] is not None
    assert defaults["bootstrap_95_interval"] == defaults["bootstrap_interval"]
    assert altered["test"] == "monte_carlo_sign_flips"
    assert altered["analysis_seed"] == 71
    assert paired_estimate([0.2], settings=options)["sign_flip_p"] is None


def test_launch_requires_current_frozen_controls_and_explicit_switch(
    root, controller, notebook_values, monkeypatch
):
    """A notebook edit invalidates the frozen plan before any process starts."""
    from auditors_paradox.config import StudyConfig

    study, controls, analysis = copy.deepcopy(notebook_values)
    plan = {"draft_digest": controller.design_digest(study, controls, analysis)}
    monkeypatch.setattr(
        controller,
        "check_frozen",
        lambda *args: (
            plan,
            StudyConfig.model_validate(study),
            controller.Controls.model_validate(controls),
            AnalysisSettings.model_validate(analysis),
        ),
    )
    monkeypatch.setattr(
        controller.subprocess, "Popen", lambda *a, **k: pytest.fail("unexpected worker")
    )
    result = controller.launch(root, root / "unused", study, controls, analysis)
    assert result["status"] == "preview_only"
    study["auditor"]["elevation_threshold"] = 0.3
    with pytest.raises(ValueError, match="changed after freezing"):
        controller.launch(root, root / "unused", study, controls, analysis)


def test_paid_execution_stays_off_without_operator_switch(
    root, controller, notebook_values, monkeypatch
):
    """Drafting a paid config or setting EXECUTE alone never permits spending."""
    from auditors_paradox.config import StudyConfig

    study, controls, analysis = copy.deepcopy(notebook_values)
    study["agent"].update(
        backend="agentrouter",
        client="qwen_code",
        model="gpt-6.1-sol",
        base_url="https://agentrouter.org/v1",
        api_protocol="responses",
        reasoning_effort="low",
    )
    plan = {"draft_digest": controller.design_digest(study, controls, analysis)}
    monkeypatch.setattr(
        controller,
        "check_frozen",
        lambda *args: (
            plan,
            StudyConfig.model_validate(study),
            controller.Controls.model_validate(controls),
            AnalysisSettings.model_validate(analysis),
        ),
    )
    with pytest.raises(ValueError, match="Paid execution is off"):
        controller.launch(root, root / "unused", study, controls, analysis, execute=True)


def test_stop_uses_frozen_filename(controller, tmp_path):
    """Stopping cannot follow an edited draft to the wrong file."""
    from auditors_paradox.config import StudyConfig

    saved = StudyConfig().model_dump()
    saved["budget"]["stop_file"] = "stops/notebook.stop"
    (tmp_path / "study.json").write_text(json.dumps(saved), encoding="utf8")
    result = controller.request_stop(tmp_path, tmp_path / "plan.json")
    assert result == "stops/notebook.stop"
    assert (tmp_path / result).is_file()


def test_detector_diagnostic_preserves_real_rule_semantics(root, controller, notebook_values):
    """Defaults reject spikes/deviations and schedule sustained-elevation warnings at t+1."""
    study, _, _ = notebook_values
    path = controller.auditor_diagnostics(root, study)
    evidence = controller.read_json(path)
    assert evidence["model_requests"] == 0
    cases = evidence["scenarios"]
    assert cases["competitive_reference"]["flagged_rounds"] == []
    assert cases["one_round_spike"]["flagged_rounds"] == []
    assert cases["unilateral_elevation"]["flagged_rounds"] == []
    assert cases["unaligned_elevated_prices"]["flagged_rounds"] == []
    sustained = cases["sustained_joint_reference"]
    assert sustained["flagged_rounds"] == [3, 9]
    assert sustained["public_scheduled_effective_rounds"] == [4, 10]


def test_exact_sweep_rejects_overrides_before_client_construction(root, monkeypatch):
    """The new launcher cannot claim exact configuration while applying old pilot overrides."""
    monkeypatch.syspath_prepend(str(root / "scripts"))
    module = importlib.import_module("validate_qwen_live")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_qwen_live.py",
            "unused.json",
            "--sweep-config",
            "--allow-paid",
            "--new-request-limit",
            "100",
            "--new-cost-limit",
            "1",
            "--pilot-rounds",
            "10",
        ],
    )
    monkeypatch.setattr(module, "QwenCodeBackend", lambda *a: pytest.fail("client constructed"))
    with pytest.raises(ValueError, match="no overrides"):
        module.main()


def test_notebook_is_clean_and_deterministically_generated(root, monkeypatch):
    """The shareable notebook contains no saved outputs or local machine paths."""
    monkeypatch.syspath_prepend(str(root / "scripts"))
    module = importlib.import_module("build_research_notebook")
    path = root / "notebooks/00_research_control.ipynb"
    document = json.loads(path.read_text(encoding="utf8"))
    assert module.build() == module.build()
    document = module.build()
    code = "\n".join("".join(c["source"]) for c in document["cells"] if c["cell_type"] == "code")
    assert "EXECUTE = False" in code and "ALLOW_PAID = False" in code
    assert "A:\\" not in code and "C:\\Users" not in code and "sk-" not in code
    assert all(not c.get("outputs") and c.get("execution_count") is None for c in document["cells"])


@pytest.mark.parametrize("state", ["current", "stale", "wrong_installation"])
def test_notebook_setup_identifies_cached_or_wrong_schema(root, monkeypatch, state):
    """An old kernel gets a restart instruction before any controller or client loads."""
    monkeypatch.syspath_prepend(str(root / "scripts"))
    from build_research_notebook import build

    from auditors_paradox.config import StudyConfig

    source = next(c["source"] for c in build()["cells"] if c["id"] == "setup")
    guards = [
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.If) and "configuration_schema" in ast.unparse(node.test)
    ]
    assert len(guards) == 2
    tree = ast.fix_missing_locations(ast.Module(body=guards, type_ignores=[]))
    schema = SimpleNamespace(
        __file__=str(root / "src/auditors_paradox/config.py"), StudyConfig=StudyConfig
    )
    if state == "stale":
        schema.StudyConfig = SimpleNamespace(
            model_fields={"agent": SimpleNamespace(annotation=SimpleNamespace(model_fields={}))}
        )
    if state == "wrong_installation":
        schema.__file__ = str(root / "another-checkout/config.py")
    namespace = {"ROOT": root, "Path": Path, "configuration_schema": schema}
    if state == "current":
        exec(compile(tree, "<setup guards>", "exec"), namespace)
    else:
        with pytest.raises(RuntimeError, match="[Rr]estart the kernel"):
            exec(compile(tree, "<setup guards>", "exec"), namespace)
    assert not any(k in namespace for k in ("control", "STUDY", "EXECUTE"))
