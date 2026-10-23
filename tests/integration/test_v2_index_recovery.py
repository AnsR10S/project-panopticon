"""Inject index failures while preserving the authoritative event stream."""

import json
import sqlite3

import pytest

from auditors_paradox.config import StudyConfig, load_config
from auditors_paradox.evidence import file_hash
from auditors_paradox.research_index import IndexIntegrityError, ResearchIndex
from auditors_paradox.runner import run_experiment


@pytest.fixture
def evidence(root, tmp_path):
    data = load_config(root / "configs/v2/warning.yaml").model_dump(mode="json")
    data.update(rounds=4, analysis_last_rounds=2)
    data["feedback"]["schedule_rounds"] = [2]
    data["v2"]["index_enabled"] = False
    return run_experiment(
        StudyConfig.model_validate(data), root, output_root=tmp_path, run_id="index-crash"
    )


def test_transaction_failure_rolls_back_projection_and_cursor(
    root, tmp_path, evidence, monkeypatch
):
    digest = file_hash(evidence.with_name("events.jsonl"))
    with ResearchIndex(root, tmp_path / "transaction.sqlite3") as index:
        original = index._project
        calls = []

        def failed(run, event, model):
            original(run, event, model)
            calls.append(1)
            if len(calls) == 5:
                raise sqlite3.OperationalError("Injected disk failure")

        monkeypatch.setattr(index, "_project", failed)
        with pytest.raises(sqlite3.OperationalError):
            index.ingest_run(evidence)
        assert index.query("SELECT * FROM import_cursors") == []
        assert index.query("SELECT * FROM events") == []
        monkeypatch.setattr(index, "_project", original)
        result = index.ingest_run(evidence)
        assert result["new_events"] > 0 and index.check()["valid"]
    assert file_hash(evidence.with_name("events.jsonl")) == digest


def test_incomplete_running_tail_waits_then_terminal_reconciliation(root, tmp_path, evidence):
    raw = evidence.with_name("events.jsonl")
    original_raw = raw.read_bytes()
    original_manifest = evidence.read_bytes()
    data = json.loads(original_manifest)
    data["status"] = "running"
    evidence.write_text(json.dumps(data), encoding="utf8")
    raw.write_bytes(original_raw + b'{"partial":')
    with ResearchIndex(root, tmp_path / "tail.sqlite3") as index:
        first = index.ingest_run(evidence)
        assert first["status"] == "running" and first["new_events"] > 0
        raw.write_bytes(original_raw)
        evidence.write_bytes(original_manifest)
        final = index.ingest_run(evidence)
        assert final["new_events"] == 0 and final["status"] == "complete"
        assert index.check()["valid"]


def test_unknown_schema_is_rejected_without_rewriting(root, tmp_path):
    path = tmp_path / "schema.sqlite3"
    with ResearchIndex(root, path) as index:
        with index.connection:
            index.connection.execute("UPDATE schema_migrations SET version=99")
    with pytest.raises(IndexIntegrityError, match="schema version"):
        ResearchIndex(root, path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM schema_migrations").fetchone()[0] == 99
