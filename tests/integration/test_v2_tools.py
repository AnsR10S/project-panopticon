"""Verify offline CLI operations and actual parsing corrections through V2."""

import json
import sys

from auditors_paradox.batch import run_sweep
from auditors_paradox.cli import main
from auditors_paradox.config import StudyConfig, load_config
from auditors_paradox.diagnostics import warning_response_battery
from auditors_paradox.evaluation.validate import validate_run
from auditors_paradox.evaluation.validate_v2 import correction_text, equivalent
from auditors_paradox.runner import run_experiment


def short_cfg(root):
    data = load_config(root / "configs/v2/warning.yaml").model_dump(mode="json")
    data.update(rounds=4, analysis_last_rounds=2)
    data["feedback"]["schedule_rounds"] = [2]
    data["v2"]["index_enabled"] = False
    return StudyConfig.model_validate(data)


def test_actual_corrections_are_replayed_exactly(root, tmp_path):
    cfg = short_cfg(root)
    data = cfg.model_dump(mode="json")
    data["agent"]["scripted_replies"] = [
        [item for _ in range(4) for item in ["bad JSON", "PRICE: 137"]] for _ in range(3)
    ]
    cfg = StudyConfig.model_validate(data)
    path = run_experiment(cfg, root, run_id="correction", output_root=tmp_path)
    checked = validate_run(path)
    assert checked["valid"], checked
    assert json.loads(path.read_text())["model_calls"] == 24
    assert "empty action" in correction_text("", cfg)
    assert "out of grid" in correction_text("PRICE: 999", cfg)
    assert equivalent({"x": [0.1]}, {"x": [0.1 + 1e-12]})
    assert not equivalent({"x": True}, {"x": 1})
    assert not equivalent(0.1, 0.11)


def test_warning_diagnostic_is_truthful_synthetic_and_receipt_matched(root):
    cfg = load_config(root / "configs/v2/warning.yaml")
    cases = warning_response_battery(cfg)
    assert len(cases) == 6
    assert all(case["audit"]["flagged"] and case["expected"] is None for case in cases)
    assert not cases[0]["notices"]
    assert cases[1]["notices"][0]["body"] == cases[2]["notices"][0]["body"]
    assert cases[1]["notices"][0]["header"] != cases[2]["notices"][0]["header"]
    assert cases[0]["effective_round"] == cases[0]["source_round"] + 1


def test_cli_research_tools_never_need_credentials(root, tmp_path, capsys):
    cfg = short_cfg(root)
    config_path = tmp_path / "config.json"
    config_path.write_text(cfg.model_dump_json(), encoding="utf8")
    prefix = ["--project-root", str(root)]
    assert main([*prefix, "calibrate-sanctions", str(config_path)]) == 0
    capsys.readouterr()
    manifest = run_experiment(cfg, root, output_root=tmp_path, run_id="cli-v2")
    db = str(tmp_path / "cli.sqlite3")
    for arguments in [
        ["index", "ingest", "--database", db, "--source", str(manifest)],
        ["index", "check", "--database", db],
        ["index", "query", "--database", db, "--sql", "SELECT * FROM runs"],
        ["index", "export", "--database", db, "--output", str(tmp_path / "export.csv")],
        ["index", "backup", "--database", db, "--output", str(tmp_path / "backup.sqlite3")],
        ["diagnostic", str(config_path), "--limit", "2", "--output", str(tmp_path / "battery")],
        [
            "diagnostic",
            str(config_path),
            "--execute-local",
            "--kind",
            "warning-response",
            "--output",
            str(tmp_path / "warning-battery"),
        ],
        ["timings", str(manifest)],
    ]:
        assert main([*prefix, *arguments]) == 0, arguments
        capsys.readouterr()
    assert main([*prefix, "index", "query", "--database", db, "--sql", "DELETE FROM runs"]) == 1
    assert main([*prefix, "index", "export", "--database", db]) == 1
    assert (
        main(
            [
                *prefix,
                "diagnostic",
                str(config_path),
                "--limit",
                "0",
                "--output",
                str(tmp_path / "not-created"),
            ]
        )
        == 1
    )


def test_relative_analysis_and_v2_baseline_tools(root, tmp_path, monkeypatch):
    prefix = ["--project-root", str(root)]
    cfg = short_cfg(root)
    sweep = run_sweep(cfg, root, replicates=1, output_root=tmp_path, sweep_id="relative-analysis")
    monkeypatch.chdir(root)
    output = tmp_path / "relative-output"
    assert (
        main(
            [
                "--project-root",
                str(root),
                "analyze",
                sweep.relative_to(root).as_posix(),
                "--output",
                output.relative_to(root).as_posix(),
            ]
        )
        == 0
    )
    sys.path.insert(0, str(root / "scripts"))
    import research_control as control

    report = control.auditor_diagnostics(root, cfg.model_dump(mode="json"))
    data = json.loads(report.read_text())
    assert data["model_requests"] == 0
    assert (
        data["scenarios"]["sustained_joint_reference"]["audit_events"][-1]["assessment"] is not None
    )
    silent = next(row for row in json.loads(sweep.read_text())["runs"] if row["arm"] == "silent")
    baseline = control.baseline_auditor_assessment(
        root, cfg.model_dump(mode="json"), [silent["manifest_path"]]
    )
    assert json.loads(baseline.read_text())["thresholds_changed"] is False
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
                "pilot_blocks": [[0, 0, 0, 0]] * 3,
                "arm_means": [0, 0.1, 0.2, 0],
                "pairs": 8,
                "simulations": 20,
            }
        ),
        encoding="utf8",
    )
    assert (
        main([*prefix, "power-plan", str(scenario), "--output", str(tmp_path / "power.json")]) == 0
    )
