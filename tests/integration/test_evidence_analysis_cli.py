"""Here we test the saved evidence, the failed assignments and the CLI commands."""

import json

import pytest

from auditors_paradox import cli
from auditors_paradox.adapters.model_calls import ScriptedBackend
from auditors_paradox.batch import run_sweep
from auditors_paradox.config import StudyConfig
from auditors_paradox.contracts import canonical, fingerprint
from auditors_paradox.evaluation.inference import analyze_sweep, paired_estimate
from auditors_paradox.evaluation.validate import validate_run
from auditors_paradox.evidence import atomic_json, file_hash
from auditors_paradox.runner import default_backend, run_experiment


def rewrite_records(path, mutate):
    """Change the events with mutate, then rehash the whole chain so only the meaning is off."""
    directory = path.parent
    rows = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    mutate(rows)
    previous = "0" * 64
    for index, row in enumerate(rows, 1):
        row.update(sequence=index, previous_hash=previous)
        core = {key: value for key, value in row.items() if key != "record_hash"}
        row["record_hash"] = fingerprint(core)
        previous = row["record_hash"]
    (directory / "events.jsonl").write_text("\n".join(canonical(row) for row in rows) + "\n")
    checksums = json.loads((directory / "checksums.json").read_text())
    checksums["events.jsonl"] = file_hash(directory / "events.jsonl")
    atomic_json(directory / "checksums.json", checksums)
    manifest = json.loads(path.read_text())
    manifest.update(
        last_event_hash=previous,
        event_count=len(rows),
        checksums_sha256=file_hash(directory / "checksums.json"),
    )
    atomic_json(path, manifest)


@pytest.mark.parametrize(
    "mutation",
    [
        "economic",
        "recipient",
        "prompt",
        "commit",
        "routing",
        "consumption",
        "decision",
        "audit",
        "response",
    ],
)
def test_BR_007_semantic_tampering_detected_after_rehash(root, config, tmp_path, mutation):
    """Even if someone rehashes the events after editing them, validation still has to catch it."""
    cfg = StudyConfig.model_validate({**config.model_dump(), "arm": "public"})
    path = run_experiment(cfg, root, run_id="tamper", output_root=tmp_path)

    def mutate(rows):
        if mutation == "economic":
            next(r for r in rows if r["type"] == "round")["payload"]["net_rewards"][0] = -1
        elif mutation == "recipient":
            next(r for r in rows if r["type"] == "delivery")["payload"]["recipient"] = 9
        elif mutation == "prompt":
            next(r for r in rows if r["type"] == "model_call_completed")["payload"]["messages"][1][
                "content"
            ] += "hidden disclosure"
        elif mutation == "commit":
            rows[:] = [
                r
                for r in rows
                if not (r["type"] == "round_committed" and r["payload"]["round"] == 2)
            ]
        elif mutation == "routing":
            next(r for r in rows if r["type"] == "delivery" and r["payload"]["text"])["payload"][
                "effective_round"
            ] = 1
        elif mutation == "consumption":
            next(r for r in rows if r["type"] == "delivery_consumed")["payload"]["agent_id"] = 2
        elif mutation == "decision":
            next(r for r in rows if r["type"] == "decision")["payload"]["price"] = 60
        elif mutation == "response":
            next(r for r in rows if r["type"] == "model_call_completed")["payload"][
                "response"
            ] = "PRICE: 60"
        else:
            next(r for r in rows if r["type"] == "audit")["payload"]["flagged"] = True

    rewrite_records(path, mutate)
    assert not validate_run(path)["valid"]


def test_BR_007_checksum_chain_and_malformed_data(root, config, tmp_path):
    """Broken checksums, a broken event chain or malformed data all have to fail."""
    path = run_experiment(config, root, output_root=tmp_path, run_id="checksums")
    log = path.with_name("events.jsonl")
    raw = log.read_text()
    log.write_text(raw.replace('"previous_hash":"000', '"previous_hash":"100', 1))
    assert not validate_run(path)["valid"]
    log.write_text("not json")
    assert not validate_run(path)["valid"]
    assert not validate_run(tmp_path / "missing")["valid"]


def test_BR_010_011_four_arm_sweep_and_independent_analysis(root, config, tmp_path):
    """Run a full four arm sweep and make sure the analysis checks every run by itself."""
    path = run_sweep(
        config, root, sweep_id="batch", replicates=2, assignment_seed=17, output_root=tmp_path
    )
    data = json.loads(path.read_text())
    assert data["status"] == "complete" and data["completed_runs"] == 8
    order = [row["arm"] for row in data["runs"][:4]]
    assert len(set(order)) == 4
    output = tmp_path / "analysis"
    result = json.loads(analyze_sweep(path, root, output).read_text())
    assert result["estimate"]["paired_runs"] == 2
    assert result["estimate"]["mean_difference"] == 0
    assert result["estimate"]["sign_flip_p"] == 1
    assert result["data_class"] == "scripted_engineering_fixture"
    assert all(count["included"] == 2 for count in result["counts"].values())
    assert (output / "run-outcomes.svg").exists() and (output / "runs.csv").exists()
    assert len((output / "runs.csv").read_text().splitlines()) == 9
    with pytest.raises(FileExistsError):
        run_sweep(config, root, sweep_id="batch", output_root=tmp_path)
    with pytest.raises(ValueError):
        run_sweep(config, root, replicates=0, output_root=tmp_path)


@pytest.mark.parametrize("replicates", [1, 2])
def test_core_contrasts_use_profit_scores_and_keep_single_pairs_descriptive(
    root, config, tmp_path, replicates
):
    """Compare each core arm with silence without inventing p-values for one pair."""
    prices = {"silent": 60, "private": 75, "public": 90, "placebo": 45}
    cfg = StudyConfig.model_validate({**config.model_dump(), "primary_outcome": "gross_profit"})

    def factory(study, market, agent_id):
        return ScriptedBackend([f"PRICE: {prices[study.arm]}"] * study.rounds)

    path = run_sweep(
        cfg,
        root,
        sweep_id="core-contrasts",
        replicates=replicates,
        output_root=tmp_path,
        backend_factory=factory,
    )
    output = tmp_path / "analysis"
    summary = json.loads(analyze_sweep(path, root, output).read_text())
    assert summary["comparison"] == "public_minus_private"
    assert summary["estimate"]["mean_difference"] == pytest.approx(0.25)
    for name, expected in {
        "public_minus_silent": 1.0,
        "private_minus_silent": 0.75,
        "placebo_minus_silent": -1.25,
    }.items():
        result = summary["secondary_contrasts"][name]
        assert result["mean_difference"] == pytest.approx(expected)
        assert result["paired_runs"] == replicates
        assert name in (output / "report.md").read_text()
        if replicates == 1:
            assert result["status"] == "insufficient_pairs"
            assert result["sign_flip_p"] is None and result["bootstrap_95_interval"] is None
        else:
            assert result["status"] == "estimated" and result["sign_flip_p"] == 0.5
    assert summary["secondary_inference_status"].startswith("Exploratory")


def test_BR_010_partial_and_failed_sweeps_retain_all_assignments(root, config, tmp_path):
    """Partial and failed sweeps still keep every assignment in the record."""

    class Broken:
        def complete(self, messages):
            raise RuntimeError("synthetic transport failure")

    def factory(cfg, market, agent_id):
        return Broken() if cfg.arm == "private" else default_backend(cfg, market, agent_id)

    partial = run_sweep(
        config,
        root,
        sweep_id="partial",
        replicates=1,
        output_root=tmp_path,
        backend_factory=factory,
    )
    data = json.loads(partial.read_text())
    assert data["status"] == "partial" and data["failed_runs"] == 1 and len(data["runs"]) == 4
    summary = json.loads(analyze_sweep(partial, root, tmp_path / "partial-analysis").read_text())
    assert summary["estimate"]["status"] == "insufficient_pairs"
    assert summary["secondary_contrasts"]["public_minus_silent"]["paired_runs"] == 1
    assert summary["secondary_contrasts"]["private_minus_silent"]["paired_runs"] == 0
    assert (
        summary["counts"]["private"]["assigned"] == 1
        and summary["counts"]["private"]["included"] == 0
    )
    failed = run_sweep(
        config,
        root,
        sweep_id="failed",
        replicates=1,
        output_root=tmp_path,
        backend_factory=lambda *args: Broken(),
    )
    assert json.loads(failed.read_text())["status"] == "failed"
    result = json.loads(analyze_sweep(failed, root, tmp_path / "failed-analysis").read_text())
    assert result["data_class"] == "no_valid_runs"


def test_BR_010_sweep_preflight_and_interrupt(root, config, tmp_path):
    """Live sweeps need permission and a total budget, and an interrupt never counts as success."""
    live = StudyConfig.model_validate(
        {
            **config.model_dump(),
            "agent": {"backend": "openai", "model": "chosen"},
            "budget": {"max_cost_usd": 1, "input_usd_per_million": 1, "output_usd_per_million": 1},
        }
    )
    with pytest.raises(ValueError, match="Live sweeps"):
        run_sweep(live, root, output_root=tmp_path)
    with pytest.raises(ValueError, match="total"):
        run_sweep(live, root, allow_paid=True, max_total_cost_usd=1, output_root=tmp_path)

    class Interrupted:
        def complete(self, messages):
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        run_sweep(
            config,
            root,
            sweep_id="interrupted",
            replicates=1,
            output_root=tmp_path,
            backend_factory=lambda *args: Interrupted(),
        )
    path = tmp_path / config.study_id / "sweeps/interrupted/manifest.json"
    assert json.loads(path.read_text())["status"] == "interrupted"
    with pytest.raises(ValueError, match="terminal"):
        analyze_sweep(path, root, tmp_path / "invalid-analysis")


def test_BR_011_run_level_inference():
    """The statistics treat each run as one sample and give the same answer every time."""
    assert paired_estimate([])["status"] == "insufficient_pairs"
    assert paired_estimate([1])["mean_difference"] == 1
    estimate = paired_estimate([1, 2, 3])
    assert estimate["mean_difference"] == 2 and estimate["paired_runs"] == 3
    assert estimate == paired_estimate([1, 2, 3])
    assert paired_estimate([1.0] * 17)["test"] == "monte_carlo_sign_flips"


def test_BR_012_public_cli_workflow(root, config, tmp_path, capsys):
    """Go through the public CLI workflow end to end like a normal user would."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(config.model_dump_json())
    assert cli.main(["doctor"]) == 0
    expected_sources = 2 if cli.verify_sources(root)["status"] == "not_available" else 0
    assert cli.main(["verify-sources"]) == expected_sources
    assert cli.main(["validate-config", str(cfg_path)]) == 0
    assert (
        cli.main(["run", str(cfg_path), "--run-id", "cli-run", "--output-root", str(tmp_path)]) == 0
    )
    path = tmp_path / config.study_id / "cli-run/manifest.json"
    assert cli.main(["verify", str(path)]) == 0
    assert cli.main(["inspect", str(path)]) == 0
    assert cli.main(["compare-scripted", str(path), str(path)]) == 0
    assert (
        cli.main(["freeze-config", str(cfg_path), "--output", str(tmp_path / "frozen.json")]) == 0
    )
    frozen = json.loads((tmp_path / "frozen.json").read_text())
    assert frozen["run_kind"] == "confirmatory" and len(frozen["protocol_sha256"]) == 64
    assert (
        cli.main(
            [
                "sweep",
                str(cfg_path),
                "--sweep-id",
                "cli-batch",
                "--replicates",
                "1",
                "--output-root",
                str(tmp_path),
            ]
        )
        == 0
    )
    sweep = tmp_path / config.study_id / "sweeps/cli-batch/manifest.json"
    assert cli.main(["analyze", str(sweep), "--output", str(tmp_path / "cli-analysis")]) == 0
    assert cli.main(["verify", str(tmp_path / "missing")]) == 1
    assert cli.main(["inspect", str(tmp_path / "missing")]) == 1
    assert cli.main(["compare-scripted", str(path), str(tmp_path / "missing")]) == 1
    assert (
        cli.main(["run", str(cfg_path), "--run-id", "cli-run", "--output-root", str(tmp_path)]) == 1
    )
    assert cli.main(["validate-config", str(tmp_path / "missing-config")]) == 1
