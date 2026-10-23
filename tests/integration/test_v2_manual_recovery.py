"""Inject a stop after durable acceptance and restore the exact accepted inbox."""

import json
import threading
import time

import pytest

from auditors_paradox.adapters.model_calls import RunStopped, ScriptedBackend
from auditors_paradox.config import StudyConfig, load_config
from auditors_paradox.evaluation.validate import validate_run
from auditors_paradox.evidence import EvidenceWriter
from auditors_paradox.interventions import read_state, submit_command
from auditors_paradox.runner import RunFailed, run_experiment


def await_state(directory, predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = read_state(directory)
        if predicate(state):
            return state
        time.sleep(0.01)
    raise AssertionError("Boundary acknowledgement timed out")


@pytest.mark.parametrize("crash", [False, True])
def test_real_runner_manual_boundary_and_recovery(root, tmp_path, monkeypatch, crash):
    data = load_config(root / "configs/v2/warning.yaml").model_dump(mode="json")
    data.update(rounds=4, analysis_last_rounds=2)
    data["feedback"]["schedule_rounds"] = [2]
    data["v2"].update(manual_enabled=True, index_enabled=False)
    data["budget"]["max_elapsed_seconds"] = 40
    cfg = StudyConfig.model_validate(data)
    gate = threading.Event()
    outcomes = []
    failures = []

    class WaitingBackend(ScriptedBackend):
        def complete(self, messages):
            assert gate.wait(10)
            return super().complete(messages)

    original_append = EvidenceWriter.append

    def fault(writer, kind, payload, **kwargs):
        if crash and kind == "boundary_frozen" and payload["source_round"] == 1:
            raise RunStopped("Injected stop after accepted intervention")
        return original_append(writer, kind, payload, **kwargs)

    monkeypatch.setattr(EvidenceWriter, "append", fault)

    def execute():
        try:
            outcomes.append(
                run_experiment(
                    cfg,
                    root,
                    output_root=tmp_path,
                    run_id="manual-original",
                    backend_factory=lambda c, m, i: WaitingBackend(["PRICE: 137"] * 4),
                )
            )
        except RunFailed as error:
            failures.append(error)

    thread = threading.Thread(target=execute, daemon=True)
    thread.start()
    directory = tmp_path / cfg.study_id / "manual-original"
    state = await_state(directory, lambda s: s.get("phase") == "collecting")
    submit_command(directory, "prepare", source_round=state["source_round"])
    gate.set()
    state = await_state(directory, lambda s: s.get("phase") == "paused")
    submit_command(
        directory,
        "preview",
        source_round=1,
        revision=state["revision"],
        draft={
            "operation": "append",
            "audience": "public",
            "recipients": [0, 1, 2],
            "text": "Diagnostic common receipt only.",
        },
    )
    state = await_state(directory, lambda s: bool(s.get("preview")))
    preview = state["preview"]
    submit_command(
        directory,
        "confirm",
        source_round=1,
        revision=state["revision"],
        preview_hash=preview["preview_hash"],
    )
    thread.join(15)
    assert not thread.is_alive()
    if crash:
        assert len(failures) == 1
        monkeypatch.setattr(EvidenceWriter, "append", original_append)
        resumed = []
        worker = threading.Thread(
            target=lambda: resumed.append(
                run_experiment(
                    cfg,
                    root,
                    output_root=tmp_path,
                    run_id="manual-restored",
                    resume_from=failures[0].manifest,
                )
            ),
            daemon=True,
        )
        worker.start()
        new_directory = tmp_path / cfg.study_id / "manual-restored"
        state = await_state(new_directory, lambda s: s.get("phase") == "paused")
        submit_command(new_directory, "resume", source_round=1, revision=state["revision"])
        worker.join(15)
        assert not worker.is_alive() and resumed
        path = resumed[0]
    else:
        assert not failures and outcomes
        path = outcomes[0]
    checked = validate_run(path)
    assert checked["valid"], checked
    manifest = json.loads(path.read_text())
    assert manifest["researcher_intervened"]
    events = [json.loads(line) for line in path.with_name("events.jsonl").read_text().splitlines()]
    notices = [
        e["payload"]
        for e in events
        if e["type"] == "delivery" and e["payload"].get("source_kind") == "manual"
    ]
    assert len(notices) == 3
    assert sum(e["type"] == "delivery_consumed" for e in events) == 3
    assert all(e["effective_round"] == 2 for e in notices)
