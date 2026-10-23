"""Kill an actual scripted worker at a durable boundary and recover without rewriting it."""

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from auditors_paradox.config import StudyConfig, load_config
from auditors_paradox.contracts import canonical, fingerprint
from auditors_paradox.evaluation.validate import validate_run
from auditors_paradox.evidence import EvidenceWriter, file_hash
from auditors_paradox.interventions import read_state, submit_command
from auditors_paradox.leases import RunLease
from auditors_paradox.recovery import recover_boundary
from auditors_paradox.runner import run_experiment


def wait_state(directory, predicate, process=None):
    until = time.monotonic() + 15
    while time.monotonic() < until:
        state = read_state(directory)
        if predicate(state):
            return state
        if process is not None and process.poll() is not None:
            raise AssertionError("Worker exited before the requested boundary")
        time.sleep(0.01)
    raise AssertionError("Boundary did not become available")


@pytest.mark.parametrize("point", ["manual_intervention", "boundary_frozen", "unfinished", "tail"])
def test_process_death_preserves_accepted_messages(root, tmp_path, point):
    data = load_config(root / "configs/v2/warning.yaml").model_dump(mode="json")
    data.update(rounds=4, analysis_last_rounds=2)
    data["feedback"]["schedule_rounds"] = [2]
    data["v2"].update(manual_enabled=True, index_enabled=False)
    data["budget"]["max_elapsed_seconds"] = 120
    cfg = StudyConfig.model_validate(data)
    config_file = tmp_path / "config.json"
    config_file.write_text(cfg.model_dump_json(), encoding="utf8")
    script = tmp_path / "actor.py"
    script.write_text(
        """import os,sys,time
from pathlib import Path
root=Path(sys.argv[1]);base=Path(sys.argv[2])
sys.path.insert(0,str(root/'src'))
from auditors_paradox.config import load_config
from auditors_paradox.evidence import EvidenceWriter
from auditors_paradox.runner import run_experiment
from auditors_paradox.adapters.model_calls import ScriptedBackend
cfg=load_config(base/'config.json')
original=EvidenceWriter.append
def crash(writer,kind,payload,**kwargs):
    if kind==sys.argv[3]:os._exit(73)
    return original(writer,kind,payload,**kwargs)
EvidenceWriter.append=crash
class Waiting(ScriptedBackend):
    def complete(self,messages):
        until=time.monotonic()+15
        while not (base/'go').exists():
            if time.monotonic()>until:raise TimeoutError('Fixture gate timed out')
            time.sleep(.01)
        return super().complete(messages)
run_experiment(cfg,root,run_id='orphan',output_root=base,backend_factory=lambda c,m,i:Waiting(['PRICE: 137']*4))
""",
        encoding="utf8",
    )
    crash_at = (
        point if point in {"manual_intervention", "boundary_frozen"} else "manual_intervention"
    )
    process = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(script), str(root), str(tmp_path), crash_at],
        cwd=root,
        env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    directory = tmp_path / cfg.study_id / "orphan"
    try:
        state = wait_state(directory, lambda s: s.get("phase") == "collecting", process)
        submit_command(directory, "prepare", source_round=1)
        (tmp_path / "go").write_text("go")
        state = wait_state(directory, lambda s: s.get("phase") == "paused", process)
        submit_command(
            directory,
            "preview",
            source_round=1,
            revision=state["revision"],
            draft={
                "operation": "append",
                "audience": "public",
                "recipients": [0, 1, 2],
                "text": "Synthetic crash-recovery notice.",
            },
        )
        state = wait_state(directory, lambda s: bool(s.get("preview")), process)
        submit_command(
            directory,
            "confirm",
            source_round=1,
            revision=state["revision"],
            preview_hash=state["preview"]["preview_hash"],
        )
        assert process.wait(timeout=15) == 73
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.communicate(timeout=5)
    source = directory / "manifest.json"
    assert json.loads(source.read_text())["status"] == "running"
    raw = directory / "events.jsonl"
    if point == "tail":
        raw.write_bytes(raw.read_bytes() + b'{"incomplete":')
        with pytest.raises(ValueError, match="Incomplete raw tail"):
            recover_boundary(root, source, tmp_path / "unsafe-tail")
        return
    if point == "unfinished":
        rows = [json.loads(line) for line in raw.read_text().splitlines()]
        start = next(e["payload"] for e in rows if e["type"] == "model_call_started")
        core = {
            "schema_version": 1,
            "sequence": len(rows) + 1,
            "type": "model_call_started",
            "payload": {**start, "call_id": "unknown-attempt"},
            "previous_hash": rows[-1]["record_hash"],
        }
        with raw.open("a", encoding="utf8") as stream:
            stream.write(canonical({**core, "record_hash": fingerprint(core)}) + "\n")
        with pytest.raises(ValueError, match="Unsafe recovery prefix"):
            recover_boundary(root, source, tmp_path / "unsafe-call")
        return
    original_hashes = [file_hash(source), file_hash(raw)]
    checkpoint = recover_boundary(root, source, tmp_path / "recovered-checkpoint")
    assert checkpoint.with_name("events.jsonl").read_bytes() == raw.read_bytes()
    assert json.loads(checkpoint.read_text())["researcher_intervened"]
    results = []
    errors = []

    def resume():
        try:
            results.append(
                run_experiment(
                    cfg, root, run_id="resumed-orphan", output_root=tmp_path, resume_from=checkpoint
                )
            )
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=resume, daemon=True)
    worker.start()
    resumed_directory = tmp_path / cfg.study_id / "resumed-orphan"
    state = wait_state(resumed_directory, lambda s: s.get("phase") == "paused")
    submit_command(resumed_directory, "resume", source_round=1, revision=state["revision"])
    worker.join(15)
    assert not errors and not worker.is_alive() and results, errors
    checked = validate_run(results[0])
    assert checked["valid"], checked
    assert original_hashes == [file_hash(source), file_hash(raw)]
    assert json.loads(results[0].read_text())["model_calls"] == 12


def test_active_writer_and_missing_lease_are_rejected(root, tmp_path):
    writer = EvidenceWriter(tmp_path / "active", {"run_id": "active"})
    try:
        with pytest.raises(ValueError, match="active writer"):
            recover_boundary(root, writer.directory / "manifest.json", tmp_path / "not-created")
    finally:
        writer.finish("failed")
    with pytest.raises(ValueError, match="no worker lease"):
        RunLease(tmp_path / "older", existing=True)
