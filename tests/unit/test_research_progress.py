"""Check progress truthfulness, safe rendering and stop/failure behavior without inference."""

import asyncio
import importlib
import json
import os
import threading

import pytest

from auditors_paradox.config import StudyConfig
from auditors_paradox.evidence import atomic_json


@pytest.fixture
def progress(root, monkeypatch):
    monkeypatch.syspath_prepend(str(root / "scripts"))
    return importlib.import_module("research_progress")


@pytest.fixture
def saved_plan(tmp_path):
    cfg = StudyConfig(rounds=6, analysis_last_rounds=3, feedback={"schedule_rounds": [2]})
    folder = tmp_path / "plan"
    folder.mkdir()
    atomic_json(folder / "plan.json", {"id": "live-test"})
    atomic_json(folder / "study.json", cfg.model_dump(mode="json"))
    atomic_json(folder / "controls.json", {"replicates": 1})
    base = tmp_path / "runs" / cfg.run_kind / cfg.study_id
    sweep = base / "sweeps/live-test"
    sweep.mkdir(parents=True)
    assignment = [
        {"arm": arm, "replicate_id": "r0000", "run_id": "live-" + arm} for arm in cfg.batch_arms
    ]
    atomic_json(sweep / "assignment.json", assignment)
    atomic_json(sweep / "manifest.json", {"status": "running", "runs": []})
    run = base / "live-silent"
    run.mkdir()
    atomic_json(run / "manifest.json", {"status": "running", "completed_rounds": 0})
    return tmp_path, folder / "plan.json", run


def event(kind, **payload):
    return json.dumps({"type": kind, "payload": payload}) + "\n"


def test_active_first_arm_visible_and_only_committed_rounds_count(progress, saved_plan):
    root, plan, run = saved_plan
    events = run / "events.jsonl"
    events.write_text(
        event("model_call_started", call_id="one", round=1, agent_id=0)
        + event(
            "model_call_completed",
            call_id="one",
            round=1,
            agent_id=0,
            input_tokens=10,
            output_tokens=3,
            response="PRICE: 137",
        )
        + event("decision", call_ids=["one"], round=1, agent_id=0),
        encoding="utf8",
    )
    reader = progress.ProgressReader(root, plan)
    first = reader.read()
    assert len(first["runs"]) == 4
    assert first["runs"][0]["status"] == "running"
    assert first["totals"]["accepted_decisions"] == 1
    assert first["percent_complete"] == 0
    assert first["recent_responses"][0]["accepted"] is True
    assert reader.read()["totals"] == first["totals"]
    with events.open("a", encoding="utf8") as stream:
        stream.write(event("round_committed", round=1))
        stream.write('{"type":')
    updated = reader.read()
    assert updated["percent_complete"] == pytest.approx(100 / 24)
    assert updated["totals"]["completed_rounds"] == 1
    with events.open("a", encoding="utf8") as stream:
        stream.write('"round_committed","payload":{"round":2}}\n')
    assert reader.read()["totals"]["completed_rounds"] == 2
    atomic_json(run / "manifest.json", {"status": "complete", "completed_rounds": 2, "error": None})
    assert reader.read()["runs"][0]["failure_type"] is None


def test_failures_unknown_usage_and_returned_text_are_separate(progress):
    cursor = progress.EventCursor()
    cursor.consume(
        {"type": "model_call_started", "payload": {"call_id": "x", "round": 1, "agent_id": 0}}
    )
    cursor.consume(
        {
            "type": "model_call_failed",
            "payload": {"call_id": "x", "client_evidence": {"network_requests": 1}},
        }
    )
    assert not cursor.pending and cursor.unknown_failures == 1
    cursor.consume(
        {
            "type": "model_call_completed",
            "payload": {
                "call_id": "y",
                "round": 1,
                "agent_id": 0,
                "response": "bad format",
                "input_tokens": 11,
                "output_tokens": 4,
            },
        }
    )
    assert cursor.counts["decision"] == 0 and not cursor.replies[0]["accepted"]
    assert cursor.input_tokens == 11


def test_display_escapes_responses_and_never_counts_retries_as_completion(progress, saved_plan):
    root, plan, _ = saved_plan
    state = progress.ProgressReader(root, plan).read()
    state["recent_responses"] = [
        {
            "arm": "public",
            "round": 1,
            "firm": 0,
            "response": "<script>bad()</script>" * 20,
            "accepted": False,
        }
    ]
    state["totals"]["failed_requests"] = 99
    output = progress.dashboard_html(state, 10, response_characters=60)
    assert "<script>" not in output and "&lt;script&gt;" in output
    assert "0.0%" in output and "full response saved" in output
    assert "Unknown-usage failures" in output


def test_cursor_rejects_truncated_history(progress, tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(event("round_committed", round=1), encoding="utf8")
    cursor = progress.EventCursor()
    cursor.update(path)
    path.write_text("", encoding="utf8")
    with pytest.raises(ValueError, match="shrank"):
        cursor.update(path)


@pytest.mark.skipif(os.name != "nt", reason="Windows file sharing semantics")
def test_atomic_writer_recovers_from_short_lived_reader(tmp_path):
    path = tmp_path / "manifest.json"
    atomic_json(path, {"round": 1})
    stream = path.open(encoding="utf8")
    timer = threading.Timer(0.04, stream.close)
    timer.start()
    try:
        atomic_json(path, {"round": 2})
    finally:
        timer.join()
        stream.close()
    assert json.loads(path.read_text())["round"] == 2


def test_monitor_surfaces_dead_startup_and_does_not_advance(progress, saved_plan, monkeypatch):
    root, plan, _ = saved_plan

    class Dead:
        def poll(self):
            return 1

    monkeypatch.setattr(progress.control, "worker_process", lambda pid: Dead())
    snapshots = []
    with pytest.raises(RuntimeError, match="did not complete"):
        asyncio.run(
            progress.watch(
                root,
                plan,
                {"pid": 123},
                refresh_seconds=0.05,
                renderer=lambda state, elapsed: snapshots.append(state),
            )
        )
    assert snapshots[-1]["error"] == "WorkerExited"
    assert snapshots[-1]["percent_complete"] == 0


def test_monitor_waits_for_analysis_even_when_all_rounds_are_saved(progress, saved_plan):
    root, plan, _ = saved_plan
    atomic_json(plan.parent / "worker.json", {"status": "running"})

    async def exercise():
        frames = []

        async def finish():
            await asyncio.sleep(0.12)
            atomic_json(
                plan.parent / "worker.json", {"status": "complete", "analysis_summary": "done"}
            )

        task = asyncio.create_task(finish())
        state = await progress.watch(
            root,
            plan,
            {"pid": -1},
            refresh_seconds=0.05,
            renderer=lambda state, elapsed: frames.append(state["status"]),
        )
        await task
        assert frames[0] == "running" and frames[-1] == "complete"
        return state

    assert asyncio.run(exercise())["analysis_summary"] == "done"


def test_cancel_monitor_requests_frozen_stop(progress, saved_plan):
    root, plan, _ = saved_plan

    async def exercise():
        task = asyncio.create_task(
            progress.watch(
                root, plan, {"pid": -1}, refresh_seconds=0.05, renderer=lambda *args: None
            )
        )
        await asyncio.sleep(0.06)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert (root / ".stop").exists()
    assert (plan.parent / "stop-request.json").exists()


def test_notebook_upgrade_keeps_research_controls_and_launch_flags(root, monkeypatch):
    monkeypatch.syspath_prepend(str(root / "scripts"))
    builder = importlib.import_module("build_research_notebook")
    document = builder.build()
    config = next(c for c in document["cells"] if c["id"] == "configuration")
    config["source"] = (
        config["source"]
        .replace('CONNECTION = "scripted"', 'CONNECTION = "agentrouter"')
        .replace('"rounds": 50', '"rounds": 80')
    )
    source = config["source"]
    document["cells"].insert(
        1,
        {
            "cell_type": "code",
            "id": "custom-root",
            "source": "ROOT = ROOT",
            "metadata": {},
            "outputs": [],
            "execution_count": None,
        },
    )
    updated = builder.upgrade(document)
    assert next(c["source"] for c in updated["cells"] if c["id"] == "configuration").startswith(
        source
    )
    assert any(c["id"] == "custom-root" for c in updated["cells"])
    assert "await progress.watch" in next(
        c["source"] for c in updated["cells"] if c["id"] == "launch"
    )


def test_saved_prompt_selection_preserves_corrections_and_failed_attempts(progress, saved_plan):
    root, plan, run = saved_plan
    messages = [
        {"role": "system", "content": "Firm zero only"},
        {"role": "user", "content": "private <note>"},
    ]
    correction = messages + [
        {"role": "assistant", "content": "bad"},
        {"role": "user", "content": "Correct the format"},
    ]
    content = event("model_call_started", call_id="first", round=1, agent_id=0, messages=messages)
    content += event(
        "model_call_completed",
        call_id="first",
        round=1,
        agent_id=0,
        response="bad",
        input_tokens=10,
        output_tokens=1,
    )
    content += event(
        "model_call_started", call_id="second", round=1, agent_id=0, messages=correction
    )
    content += event(
        "model_call_completed",
        call_id="second",
        round=1,
        agent_id=0,
        response="137",
        input_tokens=12,
        output_tokens=1,
        usage_metadata={"network_requests": 1, "wire_request": {"input": correction}},
    )
    content += event("decision", call_ids=["first", "second"], round=1, agent_id=0)
    content += event(
        "model_call_started",
        call_id="other",
        round=1,
        agent_id=1,
        messages=[{"role": "user", "content": "firm one secret"}],
    )
    content += event(
        "model_call_failed",
        call_id="other",
        round=1,
        agent_id=1,
        error_kind="provider_rate_limit",
        client_evidence={"network_requests": 1},
    )
    content += event("round_committed", round=1)
    (run / "events.jsonl").write_text(content + '{"partial":', encoding="utf8")
    selected = progress.saved_requests(root, plan, "live-silent", 1, 0)
    assert len(selected) == 2 and selected[0]["messages"] == messages
    assert selected[1]["messages"] == correction and selected[1]["accepted"]
    assert not selected[0]["accepted"] and selected[1]["round_committed"]
    markup = progress.request_html(selected[1])
    assert "firm one secret" not in markup and "private &lt;note&gt;" in markup
    assert "Guard-captured outgoing" in markup and "Correct the format" in markup
    failed = progress.saved_requests(root, plan, "live-silent", 1, 1)
    assert failed[0]["status"] == "failed" and not failed[0]["accepted"]
    assert progress.saved_requests(root, plan, "live-silent", 2, 0) == []
    with pytest.raises(ValueError, match="assigned"):
        progress.saved_requests(root, plan, "../other-run", 1, 0)


def test_live_prompts_visible_before_reply_and_not_truncated(progress, saved_plan):
    root, plan, run = saved_plan
    prompt = "Own memory <script>" + "x" * 2000
    (run / "events.jsonl").write_text(
        event(
            "model_call_started",
            call_id="x",
            round=1,
            agent_id=0,
            messages=[{"role": "user", "content": prompt}],
        ),
        encoding="utf8",
    )
    state = progress.ProgressReader(root, plan).read()
    markup = progress.dashboard_html(state, 1, response_characters=50)
    assert "x" * 2000 in markup and "&lt;script&gt;" in markup
    assert "transmission not yet confirmed" in markup
    assert prompt not in progress.dashboard_html(state, 1, show_prompts=False)


def test_open_prompt_drawer_survives_updates_without_replacing_its_html(progress, monkeypatch):
    pytest.importorskip("ipywidgets")
    monkeypatch.setattr(
        importlib.import_module("IPython.display"), "display", lambda *args, **kwargs: None
    )
    panel = progress.RecentPromptsPanel()
    request = dict(
        progress.request_record(
            {
                "call_id": "first",
                "round": 1,
                "agent_id": 0,
                "messages": [{"role": "user", "content": "first private prompt"}],
            }
        ),
        arm="private",
    )
    panel.update({"recent_requests": [request]})
    panel.drawer.selected_index = 0
    content = panel.body.value
    replaced = []
    panel.body.observe(lambda change: replaced.append(change["new"]), names="value")
    for round_number in range(2, 7):
        updated = dict(
            request,
            call_id=f"round-{round_number}",
            round=round_number,
            messages=[{"role": "user", "content": f"new prompt {round_number}"}],
        )
        panel.update({"recent_requests": [updated]})
    assert panel.drawer.selected_index == 0 and panel.body.value == content
    assert replaced == []
    panel.button.click()
    assert panel.drawer.selected_index == 0 and "new prompt 6" in panel.body.value
    assert len(replaced) == 1
    panel.update({"recent_requests": [request]})
    assert "new prompt 6" in panel.body.value
    panel.drawer.selected_index = None
    panel.drawer.selected_index = 0
    assert "first private prompt" in panel.body.value


def test_live_progress_refresh_does_not_include_the_persistent_prompt_drawer(
    progress, saved_plan, monkeypatch
):
    pytest.importorskip("ipywidgets")
    root, plan, _ = saved_plan

    class Handle:
        def __init__(self):
            self.frames = []

        def update(self, content):
            self.frames.append(content.data)

    handle = Handle()
    monkeypatch.setattr(
        importlib.import_module("IPython.display"),
        "display",
        lambda *args, **kwargs: handle if kwargs.get("display_id") else None,
    )
    panel = progress.LivePanel(root, plan)
    state = progress.ProgressReader(root, plan).read()
    panel.render(state, 1)
    panel.recent_prompts.drawer.selected_index = 0
    drawer = panel.recent_prompts.drawer
    panel.render(state, 2)
    assert panel.recent_prompts.drawer is drawer and drawer.selected_index == 0
    assert len(handle.frames) == 2
    assert all(
        "Recent prompts by firm and request attempt" not in content for content in handle.frames
    )


def test_failed_worker_surfaces_provider_cause(progress, saved_plan):
    root, plan, run = saved_plan
    atomic_json(plan.parent / "worker.json", {"status": "failed"})
    atomic_json(run / "manifest.json", {"status": "failed", "error": {"type": "ProviderError"}})
    (run / "events.jsonl").write_text(
        event("model_call_started", call_id="x", round=3, agent_id=2)
        + event(
            "model_call_failed",
            call_id="x",
            round=3,
            agent_id=2,
            error_kind="provider_rate_limit",
            client_evidence={"provider_error": {"message": "upstream token rate exceeded"}},
        ),
        encoding="utf8",
    )
    with pytest.raises(RuntimeError, match="silent round 3, firm 2: provider_rate_limit"):
        asyncio.run(progress.watch(root, plan, {"pid": -1}, renderer=lambda *args: None))
    atomic_json(
        run / "manifest.json",
        {"status": "failed", "error": {"type": "RunStopped", "message": "Cost cap reached"}},
    )
    state = progress.ProgressReader(root, plan).read()
    assert "Cost cap reached" in state["message"] and "provider_rate_limit" not in state["message"]


def test_failed_worker_surfaces_parse_location_and_policy(progress, saved_plan):
    root, plan, run = saved_plan
    atomic_json(plan.parent / "worker.json", {"status": "failed"})
    atomic_json(run / "manifest.json", {"status": "failed", "error": {"type": "ActionParseError"}})
    (run / "events.jsonl").write_text(
        event(
            "decision_rejected",
            round=1,
            agent_id=0,
            reason="parsing_attempts_exhausted_no_action_executed",
            parsing={"strategy": "final_answer_v1", "method": "no_final_commitment"},
        ),
        encoding="utf8",
    )
    state = progress.ProgressReader(root, plan).read()
    assert "silent round 1, firm 0: action_parsing_exhausted" in state["message"]
    assert "final_answer_v1" in state["message"] and "no_final_commitment" in state["message"]


def test_prompt_widget_selects_saved_attempt_without_model_access(
    progress, saved_plan, monkeypatch
):
    pytest.importorskip("ipywidgets")
    root, plan, run = saved_plan
    (run / "events.jsonl").write_text(
        event(
            "model_call_started",
            call_id="x",
            round=1,
            agent_id=0,
            messages=[{"role": "user", "content": "saved firm zero"}],
        )
        + event(
            "model_call_started",
            call_id="y",
            round=1,
            agent_id=1,
            messages=[{"role": "user", "content": "saved firm one"}],
        ),
        encoding="utf8",
    )
    display_api = importlib.import_module("IPython.display")
    monkeypatch.setattr(display_api, "display", lambda *args: None)
    panel = progress.prompt_viewer(root, plan)
    assert "saved firm zero" in panel.children[2].value
    panel.children[0].children[2].value = 1
    panel.children[1].children[0].click()
    assert "saved firm one" in panel.children[2].value
    assert "saved firm zero" not in panel.children[2].value


def test_status_reader_retries_only_transient_windows_file_locks(progress, tmp_path, monkeypatch):
    attempts = []

    def locked_then_ready(path):
        attempts.append(path)
        if len(attempts) < 3:
            error = PermissionError("temporary replacement lock")
            error.winerror = 5
            raise error
        return {"status": "complete"}

    monkeypatch.setattr(progress.control, "read_json", locked_then_ready)
    monkeypatch.setattr(progress.time, "sleep", lambda seconds: None)
    assert progress.optional_json(tmp_path / "status.json") == {"status": "complete"}
    assert len(attempts) == 3

    def denied(path):
        raise PermissionError("persistent access problem")

    monkeypatch.setattr(progress.control, "read_json", denied)
    with pytest.raises(PermissionError):
        progress.optional_json(tmp_path / "status.json")
