"""Execute V2 rounds, manual boundaries and reconstructible research indexing offline."""

import json
import threading
import time

import pytest

from auditors_paradox.adapters.collusionlab import Market
from auditors_paradox.auditing import BehavioralAuditor
from auditors_paradox.batch import run_sweep
from auditors_paradox.config import StudyConfig, load_config
from auditors_paradox.diagnostics import run_battery
from auditors_paradox.evaluation.inference import analyze_sweep
from auditors_paradox.evaluation.metrics import run_metrics
from auditors_paradox.evaluation.validate import validate_run
from auditors_paradox.evidence import EvidenceWriter
from auditors_paradox.feedback.composer import load_feedback
from auditors_paradox.feedback.router import route
from auditors_paradox.interventions import ManualController, read_state, submit_command
from auditors_paradox.research_index import IndexIntegrityError, ResearchIndex, safe_data
from auditors_paradox.runner import run_experiment


@pytest.fixture
def v2(root, tmp_path):
    data = load_config(root / "configs/v2/warning.yaml").model_dump(mode="json")
    data.update(rounds=8, analysis_last_rounds=4)
    data["feedback"]["schedule_rounds"] = [2, 4]
    data["agent"]["scripted_policy"] = "coordinated"
    data["v2"]["index_path"] = (tmp_path / "index.sqlite3").relative_to(root).as_posix()
    return StudyConfig.model_validate(data)


def test_four_arm_index_and_analysis_roundtrip(root, tmp_path, v2):
    sweep = run_sweep(v2, root, replicates=2, output_root=tmp_path, sweep_id="v2-full")
    result = analyze_sweep(sweep, root, tmp_path / "analysis")
    summary = json.loads(result.read_text())
    assert summary["central_family"]["family_id"] == "warning"
    assert len(summary["central_family"]["comparisons"]) == 4
    with ResearchIndex(root, root / v2.v2.index_path) as index:
        assert index.check()["valid"]
        assert index.ingest_sweep(sweep)["assignments"] == 8
        runs = index.query("SELECT * FROM runs ORDER BY run_id")
        assert len(runs) == 8
        assert index.ingest_run(root / runs[0]["relative_path"])["new_events"] == 0
        assert len(index.query("SELECT * FROM v_analysis_run_outcomes WHERE included=1")) == 8
        assert len(index.query("SELECT * FROM contrasts")) == 4
        assert (
            index.export("SELECT run_id,status FROM runs ORDER BY run_id", tmp_path / "export.csv")
            == 8
        )
        index.backup(tmp_path / "backup.sqlite3")
        with ResearchIndex(root, tmp_path / "backup.sqlite3") as backup:
            assert backup.check()["valid"]
        with pytest.raises(ValueError):
            index.query("DELETE FROM runs")
    manifest = json.loads((root / runs[0]["relative_path"]).read_text())
    assert validate_run(root / runs[0]["relative_path"])["valid"]
    assert manifest["config"]["schema_version"] == 2


def test_sanction_family_actual_rounds(root, tmp_path, v2):
    data = v2.model_dump(mode="json")
    data.update(
        study_id="v2-fines",
        arm="fine_only",
        batch_arms=["silent", "fine_only", "private_fine", "public_fine"],
    )
    data["v2"].update(hypothesis_family="sanctions_continuing", sanction_timing="continuing")
    cfg = StudyConfig.model_validate(data)
    sweep = run_sweep(cfg, root, replicates=1, output_root=tmp_path, sweep_id="financial")
    result = analyze_sweep(sweep, root, tmp_path / "financial-analysis")
    summary = json.loads(result.read_text())
    assert summary["central_family"]["family_id"] == "sanctions_continuing"
    for row in json.loads(sweep.read_text())["runs"]:
        path = root / row["manifest_path"]
        assert validate_run(path)["valid"]
        metrics = run_metrics(path)
        assert (
            metrics["total_penalties"] > 0
            if row["arm"] != "silent"
            else metrics["total_penalties"] == 0
        )


def wait_for(predicate, seconds=8):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("Worker did not reach the expected boundary state")


def test_manual_exact_preview_and_stale_confirmation(root, tmp_path, v2):
    cfg = StudyConfig.model_validate(
        {**v2.model_dump(mode="json"), "v2": {**v2.v2.model_dump(), "manual_enabled": True}}
    )
    directory = tmp_path / "manual"
    writer = EvidenceWriter(
        directory, {"run_id": "manual", "protocol_sha256": "a" * 64, "source_tree_sha256": "b" * 64}
    )
    controller = ManualController(cfg, writer, load_feedback(root, cfg))
    auditor = BehavioralAuditor(cfg.auditor, Market.build(cfg).benchmarks, v2=True)
    event = auditor.evaluate(1, [137] * 3, [0.12] * 3)
    automatic = route(cfg, event, load_feedback(root, cfg))
    controller.collecting(1)
    submit_command(directory, "prepare", source_round=1)
    result = []
    thread = threading.Thread(
        target=lambda: result.append(controller.boundary(1, automatic, lambda: None)), daemon=True
    )
    thread.start()
    state = wait_for(lambda: (s if (s := read_state(directory)).get("phase") == "paused" else None))
    submit_command(
        directory,
        "preview",
        source_round=1,
        revision=state["revision"],
        draft={
            "operation": "append",
            "audience": "public",
            "recipients": [0, 1, 2],
            "text": "Hypothetical diagnostic notice",
        },
    )
    state = wait_for(lambda: (s if (s := read_state(directory)).get("preview") else None))
    preview = state["preview"]
    submit_command(
        directory,
        "confirm",
        source_round=1,
        revision=state["revision"] - 1,
        preview_hash=preview["preview_hash"],
    )
    wait_for(lambda: any(x["status"] == "rejected" for x in read_state(directory)["history"]))
    assert thread.is_alive()
    submit_command(
        directory,
        "confirm",
        source_round=1,
        revision=state["revision"],
        preview_hash=preview["preview_hash"],
    )
    thread.join(8)
    assert not thread.is_alive()
    assert writer.manifest["researcher_intervened"]
    assert [d.to_dict() for d in result[0]] == preview["deliveries"]
    assert all(d.effective_round == 2 for d in result[0])
    controller.close("complete")
    writer.finish("complete")
    with pytest.raises(ValueError):
        submit_command(directory, "prepare", source_round=1)


def test_running_prefix_and_integrity_conflict(root, tmp_path, v2):
    path = run_experiment(v2, root, output_root=tmp_path, run_id="indexed")
    with ResearchIndex(root, tmp_path / "fresh.sqlite3") as index:
        first = index.ingest_run(path)
        assert first["new_events"] > 0
        assert index.ingest_run(path)["new_events"] == 0
        events = path.with_name("events.jsonl")
        original = events.read_bytes()
        events.write_bytes(original.replace(b'"type":"observation"', b'"type":"observatipn"', 1))
        with pytest.raises(IndexIntegrityError):
            index.ingest_run(path)
        events.write_bytes(original)
    assert safe_data(
        {"AGENTROUTER_API_KEY": "never-save", "authorization": "secret", "usage": None}
    ) == {"usage": None}


def test_scripted_competence_evidence_is_labeled(root, tmp_path, v2):
    path = run_battery(v2, root, tmp_path / "competence", execute=True)
    data = json.loads(path.read_text())
    assert data["data_class"] == "scripted_engineering_fixture"
    assert len(data["results"]) == 56
    assert all(r["correct"] for r in data["results"][:24])
