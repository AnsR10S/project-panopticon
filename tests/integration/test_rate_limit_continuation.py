"""Exercise continuation and private price-only memory with recorded local fake providers."""

import importlib
import json
import uuid
from pathlib import Path

import pytest

from auditors_paradox.adapters.model_calls import ProviderError
from auditors_paradox.batch import run_sweep
from auditors_paradox.config import StudyConfig
from auditors_paradox.continuation import remaining_work
from auditors_paradox.contracts import Completion
from auditors_paradox.evaluation.inference import analyze_sweep
from auditors_paradox.evaluation.validate import validate_run
from auditors_paradox.runner import run_experiment


def evidence_root(root):
    path = root / "runs/diagnostics" / ("continuation-test-" + uuid.uuid4().hex[:10])
    path.mkdir(parents=True)
    return path


def test_price_only_notes_are_archived_but_not_reintroduced(root, config):
    data = config.model_dump()
    data["agent"]["prompt_profile"] = "price_only"
    cfg = StudyConfig.model_validate(data)
    captured = []

    class Firm:
        def complete(self, messages):
            captured.append(messages)
            return Completion("PRICE: 60\nPRIVATE_EXTRA_TEXT", 20, 5, "scripted")

    path = run_experiment(
        cfg, root, output_root=evidence_root(root), backend_factory=lambda *args: Firm()
    )
    assert validate_run(path)["valid"]
    assert "PRIVATE_EXTRA_TEXT" not in json.dumps(captured)
    assert 'you reasoned: "PRICE: 60"' in json.dumps(captured).replace('\\"', '"')
    events = path.with_name("events.jsonl").read_text(encoding="utf8")
    assert "PRIVATE_EXTRA_TEXT" in events and "price_only_format_deviation" in events


def test_rate_limit_sweep_continues_only_missing_decisions(root, config, monkeypatch):
    monkeypatch.syspath_prepend(str(root / "scripts"))
    controller = importlib.import_module("research_control")
    data = config.model_dump()
    data.update(batch_arms=["silent"], run_kind="pilot", memory_window=20)
    data["agent"].update(transport_attempts=1, prompt_profile="price_only")
    cfg = StudyConfig.model_validate(data)
    output = evidence_root(root)
    old_calls = []

    class Failing:
        def __init__(self, firm):
            self.firm, self.count = firm, 0

        def complete(self, messages):
            self.count += 1
            old_calls.append((self.firm, self.count))
            if self.firm == 2 and self.count == 2:
                raise ProviderError("provider_rate_limit", retryable=True)
            return Completion("PRICE: 60", 20, 5, "scripted")

    failed = run_sweep(
        cfg,
        root,
        replicates=1,
        output_root=output,
        backend_factory=lambda cfg, market, firm: Failing(firm),
    )
    before = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
    parent = json.loads(failed.read_text(encoding="utf8"))
    assert parent["status"] == "failed"
    original_run = root / parent["runs"][0]["manifest_path"]
    assert json.loads(original_run.read_text(encoding="utf8"))["completed_rounds"] == 1
    assert remaining_work(parent, root) == (3 * cfg.rounds - 5, 0)
    from auditors_paradox.evaluation.inference import AnalysisSettings

    controls = controller.Controls(resume_sweep=failed.relative_to(root).as_posix())
    preview = controller.preview(
        root, cfg.model_dump(), controls.model_dump(), AnalysisSettings().model_dump()
    )
    assert preview["minimum_requests_and_accepted_decisions"] == 3 * cfg.rounds - 5
    requested = []

    class Healthy:
        def __init__(self, firm):
            self.firm = firm

        def complete(self, messages):
            requested.append((self.firm, messages))
            return Completion("PRICE: 60", 20, 5, "scripted")

    resumed = run_sweep(
        cfg,
        root,
        replicates=1,
        output_root=output,
        resume_from=failed,
        backend_factory=lambda cfg, market, firm: Healthy(firm),
    )
    record = json.loads(resumed.read_text(encoding="utf8"))
    assert record["status"] == "complete"
    assert len(requested) == 3 * cfg.rounds - 5
    assert requested[0][0] == 2 and "Decision round 2" in requested[0][1][-1]["content"]
    assert all(p.read_bytes() == content for p, content in before.items())
    continued = root / record["runs"][0]["manifest_path"]
    assert (
        continued.with_name("events.jsonl")
        .read_bytes()
        .startswith(original_run.with_name("events.jsonl").read_bytes())
    )
    assert validate_run(continued)["valid"]
    assert (
        json.loads(continued.read_text(encoding="utf8"))["active_elapsed_seconds"]
        >= json.loads(original_run.read_text(encoding="utf8"))["active_elapsed_seconds"]
    )
    analysis = analyze_sweep(resumed, root, output / "analysis")
    assert json.loads(analysis.read_text(encoding="utf8"))["counts"]["silent"]["included"] == 1
    assessment = controller.baseline_auditor_assessment(
        root, cfg.model_dump(), [continued.relative_to(root).as_posix()]
    )
    assert json.loads(assessment.read_text(encoding="utf8"))["thresholds_changed"] is False
    changed = cfg.model_dump()
    changed["memory_window"] = 19
    with pytest.raises(ValueError, match="settings changed"):
        run_sweep(
            StudyConfig.model_validate(changed),
            root,
            replicates=1,
            output_root=output,
            resume_from=failed,
            backend_factory=lambda *args: pytest.fail("dispatched"),
        )
    original = json.loads(original_run.read_text(encoding="utf8"))
    original["source_tree_sha256"] = "0" * 64
    original_run.write_text(json.dumps(original), encoding="utf8")
    with pytest.raises(ValueError, match="source changed"):
        run_sweep(
            cfg,
            root,
            replicates=1,
            output_root=output,
            resume_from=failed,
            backend_factory=lambda *args: pytest.fail("dispatched"),
        )


def test_continuation_keeps_complete_arms_and_next_round_notices(root, config, monkeypatch):
    """Continue the warning round without resending successful prices or consumed notices."""
    data = config.model_dump()
    data.update(batch_arms=["silent", "private", "public", "placebo"], run_kind="pilot")
    data["agent"].update(transport_attempts=1)
    cfg = StudyConfig.model_validate(data)
    output = evidence_root(root)
    visits = []
    requests = []

    class Firm:
        def __init__(self, firm, fail):
            self.firm, self.fail, self.round = firm, fail, 0

        def complete(self, messages):
            self.round += 1
            if self.fail and self.round == 4 and self.firm == 2:
                raise ProviderError("provider_rate_limit", retryable=True)
            return Completion("PRICE: 90\nKEEP_MY_OWN_NOTE", 20, 7, "scripted")

    def first_factory(cfg, market, firm):
        if firm == 0:
            visits.append(cfg.arm)
        return Firm(firm, len(visits) == 2)

    failed = run_sweep(
        cfg,
        root,
        replicates=1,
        assignment_seed=2,
        output_root=output,
        backend_factory=first_factory,
    )
    parent = json.loads(failed.read_text(encoding="utf8"))
    assert [r["status"] for r in parent["runs"]] == ["complete", "failed", "not_run", "not_run"]
    assert parent["runs"][1]["arm"] == "public"

    class Healthy:
        def __init__(self, firm, arm):
            self.firm, self.arm = firm, arm

        def complete(self, messages):
            requests.append((self.arm, self.firm, messages))
            return Completion("PRICE: 90\nKEEP_MY_OWN_NOTE", 20, 7, "scripted")

    resumed = run_sweep(
        cfg,
        root,
        replicates=1,
        assignment_seed=2,
        output_root=output,
        resume_from=failed,
        backend_factory=lambda cfg, market, firm: Healthy(firm, cfg.arm),
    )
    record = json.loads(resumed.read_text(encoding="utf8"))
    assert record["status"] == "complete"
    assert record["runs"][0]["manifest_path"] == parent["runs"][0]["manifest_path"]
    assert all(arm != parent["runs"][0]["arm"] for arm, _, _ in requests)
    assert len(requests) == (cfg.rounds - 3) * 3 - 2 + cfg.rounds * 3 * 2
    assert all(validate_run(root / row["manifest_path"])["valid"] for row in record["runs"])
    assert analyze_sweep(resumed, root, output / "analysis").is_file()
    monkeypatch.syspath_prepend(str(root / "scripts"))
    progress = importlib.import_module("research_progress")
    controller = importlib.import_module("research_control")
    panel = output / "notebook-panel"
    panel.mkdir()
    (panel / "plan.json").write_text(json.dumps({"id": resumed.parent.name}), encoding="utf8")
    (panel / "study.json").write_text(cfg.model_dump_json(), encoding="utf8")
    (panel / "controls.json").write_text(
        controller.Controls(resume_sweep=failed.relative_to(root).as_posix()).model_dump_json(),
        encoding="utf8",
    )
    reader = progress.ProgressReader(root, panel / "plan.json")
    reader.directory = resumed.parent
    assert reader.read()["totals"]["completed_rounds"] == cfg.rounds * 4
    assert (
        reader.run_directory(record["runs"][1]["run_id"])
        == (root / record["runs"][1]["manifest_path"]).parent
    )


def test_paid_continuation_never_skips_authorization_or_budget_checks(root, config):
    data = config.model_dump()
    data["agent"].update(
        backend="agentrouter",
        client="qwen_code",
        model="gpt-6.1-sol",
        base_url="https://agentrouter.org/v1",
    )
    data["budget"].update(max_cost_usd=1, input_usd_per_million=3, output_usd_per_million=15)
    cfg = StudyConfig.model_validate(data)
    with pytest.raises(ValueError, match="allow-paid"):
        run_sweep(
            cfg,
            root,
            resume_from=Path("nonexistent.json"),
            backend_factory=lambda *args: pytest.fail("dispatched"),
        )
