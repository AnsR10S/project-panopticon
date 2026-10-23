"""Read saved evidence incrementally and keep the notebook launch cell visible until completion."""

from __future__ import annotations

import asyncio
import html
import importlib
import json
import math
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import research_control as control


def optional_json(path: Path, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """Keep the last small status record while a worker is publishing its first bytes."""
    for attempt in range(6):
        try:
            return control.read_json(path)
        except (FileNotFoundError, json.JSONDecodeError):
            return previous or {}
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 5:
                raise
            time.sleep(0.01 * 2**attempt)
    raise AssertionError("Status read did not return")


def tail(path: Path, lines: int) -> list[str]:
    """Read only the end of the terminal log, not an ever-growing experiment transcript."""
    if not path.exists():
        return []
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - 16384))
        return stream.read().decode("utf8", errors="replace").splitlines()[-lines:]


def request_record(value: dict[str, Any]) -> dict[str, Any]:
    """Keep the recorded prompt for this firm and attempt, without reading credentials."""
    return {
        "call_id": value["call_id"],
        "round": value["round"],
        "firm": value["agent_id"],
        "messages": value.get("messages", []),
        "delivery_ids": value.get("delivery_ids", []),
        "transport_attempt": value.get("transport_attempt"),
        "requested_model": value.get("requested_model"),
        "status": "started; transmission not yet confirmed",
        "accepted": False,
    }


def finish_request(request: dict[str, Any], kind: str, value: dict[str, Any]) -> None:
    """Attach the actual reply or failure and the guarded request body when captured."""
    failed = kind == "model_call_failed"
    metadata = value.get("client_evidence" if failed else "usage_metadata", {})
    request.update(
        status="failed" if failed else "returned",
        response=value.get("response"),
        error_kind=value.get("error_kind"),
        network_requests=metadata.get("network_requests"),
        wire_request=metadata.get("wire_request"),
    )


def request_html(request: dict[str, Any]) -> str:
    """Show all message text literally; never render model content as executable markup."""

    def escape(value: Any) -> str:
        return html.escape(str(value))

    messages = "".join(
        f"<b>{index}. {escape(message['role'])}</b>"
        f"<pre style='white-space:pre-wrap'>{escape(message['content'])}</pre>"
        for index, message in enumerate(request["messages"], 1)
    )
    body = request.get("wire_request")
    wire = (
        "<details><summary>Guard-captured outgoing JSON body (no authorization headers)</summary>"
        f"<pre style='white-space:pre-wrap'>{escape(json.dumps(body, indent=2, ensure_ascii=False))}</pre></details>"
        if body is not None
        else "<p>No outgoing body captured for this attempt; these are the recorded input messages.</p>"
    )
    response = (
        f"<b>Returned text</b><pre style='white-space:pre-wrap'>{escape(request['response'])}</pre>"
        if request.get("response") is not None
        else ""
    )
    return (
        f"<p>Call {escape(request['call_id'])} | {escape(request['status'])} | "
        f"accepted decision: {escape(request['accepted'])} | model: {escape(request['requested_model'])}<br>"
        f"Notice IDs: {escape(request['delivery_ids'])} | "
        f"Error: {escape(request.get('error_kind') or 'none')}</p>" + messages + response + wire
    )


def saved_requests(
    root: Path, plan_path: Path, run_id: str, round_number: int, firm: int
) -> list[dict[str, Any]]:
    """Read one decision's saved attempts without loading a full transcript or running a model."""
    reader = ProgressReader(root, plan_path)
    if not 1 <= round_number <= reader.cfg["rounds"] or not 0 <= firm < reader.cfg["n_agents"]:
        raise ValueError("Choose a round and firm inside this frozen study")
    assignments = optional_json_array(reader.directory / "assignment.json")
    if run_id not in {row["run_id"] for row in assignments}:
        raise ValueError("Choose a run assigned to this frozen plan")
    directory = reader.run_directory(run_id)
    path = directory / "events.jsonl"
    requests: dict[str, dict[str, Any]] = {}
    committed = False
    if not path.exists():
        return []
    with path.open("rb") as stream:
        for line in stream:
            if not line.endswith(b"\n"):
                break
            event = json.loads(line)
            kind, value = event["type"], event["payload"]
            if kind == "round_committed" and value["round"] == round_number:
                committed = True
            if value.get("round") != round_number or value.get("agent_id") != firm:
                continue
            if kind == "model_call_started":
                requests[value["call_id"]] = request_record(value)
            elif kind in {"model_call_completed", "model_call_failed"}:
                request = requests.get(value["call_id"])
                if request is not None:
                    finish_request(request, kind, value)
            elif kind == "decision" and value.get("call_ids"):
                request = requests.get(value["call_ids"][-1])
                if request is not None:
                    request["accepted"] = True
    return [dict(request, round_committed=committed) for request in requests.values()]


def optional_json_array(path: Path) -> list[dict[str, Any]]:
    """Handle a plan whose assignment has not been written yet."""
    try:
        value: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf8"))
        return value
    except FileNotFoundError:
        return []


def prompt_viewer(root: Path, plan_path: Path, *, collapsed: bool = False) -> Any:
    """Select a run, round, firm and attempt in saved evidence; this panel sends no requests."""
    widgets = importlib.import_module("ipywidgets")
    display_api = importlib.import_module("IPython.display")
    reader = ProgressReader(root, plan_path)
    assignments = optional_json_array(reader.directory / "assignment.json")
    run = widgets.Dropdown(
        options=[(f"{row['arm']} / {row['replicate_id']}", row["run_id"]) for row in assignments],
        description="Run:",
    )
    round_input = widgets.BoundedIntText(
        value=1, min=1, max=reader.cfg["rounds"], description="Round:"
    )
    firm = widgets.Dropdown(options=list(range(reader.cfg["n_agents"])), description="Firm:")
    attempt = widgets.Dropdown(options=[], description="Attempt:")
    button = widgets.Button(description="Load saved prompts", icon="search")
    output = widgets.HTML()
    records: list[dict[str, Any]] = []

    def render(change: Any = None) -> None:
        """Display only the selected saved attempt, including corrections and failures."""
        if attempt.value is None:
            output.value = (
                "<p>No saved request for this selection yet. Firms are numbered 0, 1, 2.</p>"
            )
            return
        record = records[attempt.value]
        output.value = (
            "<div style='max-height:600px;overflow:auto'>"
            f"<p>Market round committed: {record['round_committed']}. "
            "A returned or accepted decision alone is not a complete market round.</p>"
            + request_html(record)
            + "</div>"
        )

    def load(button: Any = None) -> None:
        """Rescan only when asked, including newly appended evidence from an active run."""
        assignments = optional_json_array(reader.directory / "assignment.json")
        options = [(f"{row['arm']} / {row['replicate_id']}", row["run_id"]) for row in assignments]
        if list(run.options) != options:
            previous = run.value
            run.options = options
            if previous in {row["run_id"] for row in assignments}:
                run.value = previous
        records[:] = (
            saved_requests(root, plan_path, run.value, round_input.value, firm.value)
            if run.value is not None
            else []
        )
        attempt.options = [(record["call_id"], index) for index, record in enumerate(records)]
        attempt.value = 0 if records else None
        render()

    attempt.observe(render, names="value")
    button.on_click(load)
    panel = widgets.VBox(
        [widgets.HBox([run, round_input, firm]), widgets.HBox([button, attempt]), output]
    )
    if collapsed:
        wrapper = widgets.Accordion(children=[panel], selected_index=None)
        wrapper.set_title(0, "Browse saved prompts by run, round, firm and attempt")
        display_api.display(wrapper)
    else:
        display_api.display(panel)
    load()
    return panel


@dataclass
class EventCursor:
    """Keep bounded display state while reading only newly appended complete event lines."""

    offset: int = 0
    counts: Counter[str] = field(default_factory=Counter)
    rounds: set[int] = field(default_factory=set)
    pending: dict[str, dict[str, Any]] = field(default_factory=dict)
    replies: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=100))
    requests: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=12))
    input_tokens: int = 0
    output_tokens: int = 0
    unknown_failures: int = 0
    notice_count: int = 0
    last_event: str = ""
    last_round: int = 0
    warning: str = ""
    last_failure: dict[str, Any] = field(default_factory=dict)
    latest_accounting: dict[str, Any] = field(default_factory=dict)
    latest_audit: dict[str, Any] = field(default_factory=dict)
    phase_timings: dict[str, Any] = field(default_factory=dict)

    def update(self, path: Path) -> None:
        """Leave an incomplete trailing line unread until the next refresh."""
        if not path.exists():
            return
        if path.stat().st_size < self.offset:
            raise ValueError("The live evidence file shrank; refusing to silently reset progress")
        with path.open("rb") as stream:
            stream.seek(self.offset)
            while True:
                position = stream.tell()
                line = stream.readline()
                if not line or not line.endswith(b"\n"):
                    break
                event = json.loads(line)
                self.consume(event)
                self.offset = stream.tell()
                if self.offset <= position:
                    raise RuntimeError("Live event cursor did not advance")

    def consume(self, event: dict[str, Any]) -> None:
        """Count committed outcomes separately from returned text and failed attempts."""
        kind, value = event["type"], event["payload"]
        self.counts[kind] += 1
        self.last_event = kind
        self.last_round = value.get("round", self.last_round)
        if kind == "round_committed":
            self.rounds.add(value["round"])
        elif kind == "round":
            self.latest_accounting = value
        elif kind == "audit":
            self.latest_audit = value
        elif kind == "phase_timing":
            self.phase_timings.update(value)
        elif kind == "model_call_started":
            self.pending[value["call_id"]] = {
                "round": value["round"],
                "firm": value["agent_id"],
                "started_at": value.get("started_at"),
            }
            self.requests.append(request_record(value))
        elif kind in {"model_call_completed", "model_call_failed"}:
            self.pending.pop(value["call_id"], None)
            for request in reversed(self.requests):
                if request["call_id"] == value["call_id"]:
                    finish_request(request, kind, value)
                    break
            usage = value if kind == "model_call_completed" else value.get("client_evidence", {})
            if kind == "model_call_failed":
                self.last_failure = {
                    "call_id": value["call_id"],
                    "round": value.get("round"),
                    "firm": value.get("agent_id"),
                    "error_kind": value.get("error_kind", "unknown_provider_failure"),
                    "provider_message": usage.get("provider_error", {}).get("message", ""),
                    "done_reason": usage.get("done_reason"),
                    "output_tokens": usage.get("output_tokens"),
                    "configured_output_tokens": usage.get("configured_output_tokens"),
                }
            if all(type(usage.get(key)) is int for key in ("input_tokens", "output_tokens")):
                self.input_tokens += usage["input_tokens"]
                self.output_tokens += usage["output_tokens"]
            elif kind == "model_call_failed" and usage.get("network_requests", 1):
                self.unknown_failures += 1
            if kind == "model_call_completed":
                self.replies.append(
                    {
                        "round": value["round"],
                        "firm": value["agent_id"],
                        "response": value["response"],
                        "call_id": value["call_id"],
                        "accepted": False,
                    }
                )
        elif kind == "decision_rejected":
            self.last_failure = {
                "round": value.get("round"),
                "firm": value.get("agent_id"),
                "error_kind": "action_parsing_exhausted",
                "provider_message": (
                    "No accepted final price after the configured parsing attempts. "
                    f"Parser={value.get('parsing', {}).get('strategy', 'see frozen config')}; "
                    f"reason={value.get('parsing', {}).get('method', value.get('reason', 'unparseable'))}. "
                    "Inspect the saved responses; select a tested parser/prompt in a new study, "
                    "rather than substituting a previous price."
                ),
            }
        elif kind == "decision":
            identifiers = value.get("call_ids", [])
            if identifiers:
                for request in reversed(self.requests):
                    if request["call_id"] == identifiers[-1]:
                        request["accepted"] = True
                        break
                for reply in reversed(self.replies):
                    if reply["call_id"] == identifiers[-1]:
                        reply["accepted"] = True
                        break
        elif kind == "delivery_consumed":
            self.notice_count += 1


class ProgressReader:
    """Follow assignment.json so the first running arm is visible before it finishes."""

    def __init__(self, root: Path, plan_path: Path) -> None:
        self.root, self.plan_path = root, plan_path
        self.plan = control.read_json(plan_path)
        self.cfg = control.read_json(plan_path.parent / "study.json")
        self.controls = control.read_json(plan_path.parent / "controls.json")
        self.base = control.inside(root, f"runs/{self.cfg['run_kind']}/{self.cfg['study_id']}")
        self.directory = self.base / "sweeps" / self.plan["id"]
        self.cursors: dict[str, EventCursor] = {}
        self.worker: dict[str, Any] = {}
        self.report: dict[str, Any] = {}
        self.pacing: dict[str, Any] = {}

    def run_directory(self, identity: str, disposition: dict[str, Any] | None = None) -> Path:
        """Follow a continuation's evidence location instead of reopening the failed original."""
        if disposition is None:
            sweep = optional_json(self.directory / "manifest.json")
            disposition = next((r for r in sweep.get("runs", []) if r["run_id"] == identity), {})
        if disposition.get("manifest_path"):
            return control.inside(
                self.root, str(disposition["manifest_path"]).replace("\\", "/")
            ).parent
        directory = (
            self.directory / "continued-runs" / self.cfg["study_id"] / identity
            if self.controls.get("resume_sweep")
            else self.base / identity
        )
        return control.inside(self.root, directory.relative_to(self.root).as_posix())

    def read(
        self, *, log_lines: int = 8, response_limit: int = 9, prompt_limit: int = 3
    ) -> dict[str, Any]:
        """Return a read-only snapshot; no provider, credentials or model client is touched."""
        self.worker = optional_json(self.plan_path.parent / "worker.json", self.worker)
        report_path = self.root / "runs/diagnostics/qwen-live" / (self.plan["id"] + ".json")
        self.report = optional_json(report_path, self.report)
        state = {**self.report, **self.worker}
        self.pacing = optional_json(
            self.root / "runs/diagnostics/qwen-live/pacing.json", self.pacing
        )
        if self.pacing.get("validation") == self.plan["id"]:
            state["gateway_pacing"] = self.pacing.get("pacing", {})
        state.setdefault("status", "not_started")
        sweep_path = self.directory / "manifest.json"
        sweep = optional_json(sweep_path)
        if sweep:
            state.update(
                sweep_manifest=sweep_path.relative_to(self.root).as_posix(),
                sweep_status=sweep["status"],
            )
        assignment_path = self.directory / "assignment.json"
        assignments = (
            json.loads(assignment_path.read_text(encoding="utf8"))
            if assignment_path.exists()
            else []
        )
        dispositions = {row["run_id"]: row for row in sweep.get("runs", [])}
        runs: list[dict[str, Any]] = []
        replies: list[dict[str, Any]] = []
        requests: list[dict[str, Any]] = []
        totals: Counter[str] = Counter()
        for assigned in assignments:
            identity = assigned["run_id"]
            disposition = dispositions.get(identity, {})
            run_directory = self.run_directory(identity, disposition)
            manifest_path = run_directory / "manifest.json"
            manifest = optional_json(manifest_path)
            cursor = self.cursors.setdefault(identity, EventCursor())
            cursor.update(manifest_path.with_name("events.jsonl"))
            disposition = dispositions.get(identity, {})
            status = manifest.get("status", disposition.get("status", "queued"))
            failure_record = manifest.get("error") or {}
            completed = max(len(cursor.rounds), manifest.get("completed_rounds", 0))
            run = {
                **assigned,
                **disposition,
                "status": status,
                "completed_rounds": completed,
                "target_rounds": self.cfg["rounds"],
                "model_calls": max(
                    cursor.counts["model_call_started"], manifest.get("model_calls", 0)
                ),
                "accepted_decisions": cursor.counts["decision"],
                "completed_requests": cursor.counts["model_call_completed"],
                "failed_requests": cursor.counts["model_call_failed"],
                "pending_requests": len(cursor.pending),
                "notices_consumed": cursor.notice_count,
                "current_round": cursor.last_round,
                "current_firm": next(iter(cursor.pending.values()), {}).get("firm"),
                "last_event": cursor.last_event,
                "last_failure": dict(cursor.last_failure),
                "latest_accounting": dict(cursor.latest_accounting),
                "latest_audit": dict(cursor.latest_audit),
                "phase_timings": dict(cursor.phase_timings),
                "index_state": optional_json(
                    run_directory / "control/index-warning.json", {"status": "no_failure_reported"}
                ),
                "manifest_path": manifest_path.relative_to(self.root).as_posix(),
                "failure_type": failure_record.get("type"),
                "failure_message": failure_record.get("message", ""),
            }
            runs.append(run)
            totals.update(
                {
                    "completed_rounds": completed,
                    "accepted_decisions": run["accepted_decisions"],
                    "started_requests": run["model_calls"],
                    "completed_requests": run["completed_requests"],
                    "failed_requests": run["failed_requests"],
                    "pending_requests": run["pending_requests"],
                    "input_tokens": cursor.input_tokens,
                    "output_tokens": cursor.output_tokens,
                    "unknown_usage_failures": cursor.unknown_failures,
                    "notices_consumed": cursor.notice_count,
                }
            )
            replies.extend(
                {"arm": assigned["arm"], "replicate_id": assigned["replicate_id"], **reply}
                for reply in list(cursor.replies)[-response_limit:]
            )
            requests.extend(
                {
                    "arm": assigned["arm"],
                    "replicate_id": assigned["replicate_id"],
                    "run_id": identity,
                    **request,
                }
                for request in list(cursor.requests)[-prompt_limit:]
            )
        planned = len(self.cfg["batch_arms"]) * self.controls["replicates"]
        target = planned * self.cfg["rounds"]
        budget = self.cfg["budget"]
        paid = self.cfg["agent"]["backend"] not in {"scripted", "ollama"}
        cost = (
            (
                totals["input_tokens"] * float(budget.get("input_usd_per_million") or 0)
                + totals["output_tokens"] * float(budget.get("output_usd_per_million") or 0)
            )
            / 1e6
            if paid
            else 0.0
        )
        state.update(
            runs=runs,
            recent_responses=replies[-response_limit:],
            recent_requests=requests[-prompt_limit:],
            totals=dict(totals),
            target_rounds=target,
            target_decisions=target * self.cfg["n_agents"],
            planned_runs=planned,
            percent_complete=min(100.0, 100 * totals["completed_rounds"] / target),
            known_estimated_cost_usd=cost,
            optional_spending_caps_enforced=budget.get("enforce_spending_caps", True),
            lifetime_cost_limit_usd=self.controls.get("lifetime_cost_limit_usd", 1000),
            paid=paid,
            stop_requested=(self.root / budget["stop_file"]).exists(),
            operator_stop_requested=(self.plan_path.parent / "stop-request.json").exists(),
            recent_log=tail(self.plan_path.parent / "worker.log", log_lines),
        )
        if state["status"] in {"failed", "partial"} and not state.get("message"):
            failed = next((run for run in runs if run["status"] == "failed"), None)
            if (
                failed
                and failed["last_failure"]
                and failed["failure_type"] in {"ProviderError", "ActionParseError"}
            ):
                failure = failed["last_failure"]
                state["message"] = (
                    f"{failed['arm']} round {failure['round']}, firm {failure['firm']}: "
                    f"{failure['error_kind']}. {failure['provider_message']}"
                )
                if failure["error_kind"] == "incomplete_local_response":
                    state["message"] += (
                        f" Local generation ended with {failure.get('done_reason')!r}; "
                        f"output usage={failure.get('output_tokens', 'unknown')} tokens. "
                        "The unfinished answer was not accepted. For a new local study, review "
                        "agent.max_output_tokens, context_tokens and the bounded "
                        "local_truncation_retries/local_retry_output_tokens settings. "
                        "Committed rounds are preserved; do not edit the failed run."
                    )
            elif failed and failed["failure_type"]:
                state["message"] = (
                    f"{failed['arm']}: {failed['failure_type']}. {failed['failure_message']}"
                )
        return state


def duration(seconds: float | None) -> str:
    """Show elapsed and observed-rate estimates without pretending an ETA is guaranteed."""
    if seconds is None or not math.isfinite(seconds):
        return "estimating"
    minutes, secs = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def phase(state: dict[str, Any]) -> str:
    """Distinguish waiting for a model, paced dispatch, analysis and terminal failure."""
    status = state["status"]
    if status in {"complete", "partial", "failed", "interrupted"}:
        return "Stopped" if state.get("operator_stop_requested") else status.capitalize()
    if state.get("stop_requested"):
        return "Stopping safely"
    if state.get("sweep_status") in {"complete", "partial", "failed"}:
        return "Analyzing saved evidence"
    if state.get("totals", {}).get("pending_requests", 0):
        return "Waiting for model response"
    if state.get("runs"):
        return "Running / waiting for paced dispatch"
    return "Starting worker"


def recent_prompts_html(requests: list[dict[str, Any]]) -> str:
    """Show a saved prompt snapshot without changing the agent inputs."""
    if not requests:
        return "<p>No recorded requests yet.</p>"
    return "".join(
        "<details><summary>"
        + html.escape(
            f"{row['arm']} | round {row['round']} | firm {row['firm']} | {row['call_id']} | {row['status']}"
        )
        + "</summary>"
        + request_html(row)
        + "</details>"
        for row in requests
    )


class RecentPromptsPanel:
    """Keep the prompt drawer and its contents fixed while the researcher is reading."""

    def __init__(self) -> None:
        widgets = importlib.import_module("ipywidgets")
        self.latest: list[dict[str, Any]] = []
        self.body = widgets.HTML(value=recent_prompts_html([]))
        self.button = widgets.Button(description="Refresh prompts", icon="refresh")
        self.button.on_click(self.refresh)
        content = widgets.VBox(
            [
                widgets.HTML(
                    value="Prompts stay fixed while this drawer is open. Close/reopen it or click Refresh prompts to load the latest snapshot."
                ),
                self.button,
                self.body,
            ]
        )
        self.drawer = widgets.Accordion(children=[content], selected_index=None)
        self.drawer.set_title(0, "Recent prompts by firm and request attempt")
        self.drawer.observe(self.opened, names="selected_index")
        importlib.import_module("IPython.display").display(self.drawer)

    def refresh(self, button: Any = None) -> None:
        """Replace prompt text only when closed or when the researcher asks for a refresh."""
        content = recent_prompts_html(self.latest)
        if self.body.value != content:
            self.body.value = content

    def opened(self, change: Any) -> None:
        """Load the current snapshot when opening or closing without rebuilding the drawer."""
        self.refresh()

    def update(self, state: dict[str, Any]) -> None:
        """Continue collecting the latest prompts without replacing an expanded view."""
        self.latest = list(state.get("recent_requests", []))
        if self.drawer.selected_index is None:
            self.refresh()


def dashboard_html(
    state: dict[str, Any],
    elapsed: float,
    *,
    response_characters: int = 1500,
    show_prompts: bool = True,
) -> str:
    """Escape model text and render clean progress without exposing credentials or request headers."""

    def escape(value: Any) -> str:
        return html.escape(str(value))

    percent = state.get("percent_complete", 0)
    totals = state.get("totals", {})
    completed = totals.get("completed_rounds", 0)
    eta = elapsed * (state["target_rounds"] - completed) / completed if completed else None
    terminal = state["status"] in {"complete", "partial", "failed", "interrupted"}
    if terminal:
        eta = 0 if state["status"] == "complete" else None
    bar = "#" * int(percent / 4) + "-" * (25 - int(percent / 4))
    heading = (
        f"<b>Auditor's Paradox · {escape(phase(state))}</b>"
        f"<pre style='white-space:pre-wrap'>[{bar}] {percent:.1f}% saved market rounds\n"
        f"Rounds {completed}/{state['target_rounds']} · Accepted decisions {totals.get('accepted_decisions', 0)}/{state['target_decisions']}\n"
        f"Requests: {totals.get('completed_requests', 0)} completed · {totals.get('failed_requests', 0)} failed · {totals.get('pending_requests', 0)} in flight\n"
        f"Elapsed {duration(elapsed)} · Observed-rate ETA {duration(eta)}\n"
        f"Known usage estimate ${state.get('known_estimated_cost_usd', 0):.4f} · Unknown-usage failures {totals.get('unknown_usage_failures', 0)}\n"
        f"Optional spending caps: {'on' if state.get('optional_spending_caps_enforced', True) else 'off'} · Cumulative research ceiling ${state.get('lifetime_cost_limit_usd', 1000):,.2f}</pre>"
        f"<progress value='{percent:.4f}' max='100' style='width:100%;height:18px'></progress>"
    )
    pacing = state.get("gateway_pacing", {})
    if pacing:
        heading += (
            f"<p>Shared gateway recovery: cooldown {max(0, pacing.get('cooldown_until', 0) - time.time()):.0f}s"
            f" · adaptive request spacing {pacing.get('adaptive_interval_seconds', 0):.0f}s"
            f" · provider limits {escape(pacing.get('provider_limits') or 'unknown (not reported)')}</p>"
        )
    rows = "".join(
        "<tr>"
        + "".join(
            f"<td style='padding:6px'>{escape(value)}</td>"
            for value in (
                row["arm"],
                row["replicate_id"],
                row["status"],
                f"{row['completed_rounds']}/{row['target_rounds']}",
                row["accepted_decisions"],
                row["model_calls"],
                row["notices_consumed"],
            )
        )
        + "</tr>"
        for row in state.get("runs", [])
    )
    table = (
        "<table style='width:100%;text-align:left'><thead><tr>"
        "<th>Condition</th><th>Block</th><th>Status</th><th>Rounds</th><th>Decisions</th><th>Requests</th><th>Notices consumed</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )
    replies = []
    for row in state.get("recent_responses", []):
        text = row["response"]
        if len(text) > response_characters:
            text = (
                text[:response_characters]
                + "\n[Display shortened; full response saved in events.jsonl]"
            )
        label = f"{row['arm']} · round {row['round']} · firm {row['firm']} · {'accepted' if row['accepted'] else 'returned / parsing'}"
        replies.append(
            f"<div style='padding:5px;border-top:1px solid #ddd'><b>{escape(label)}</b><pre style='white-space:pre-wrap;margin:3px 0'>{escape(text)}</pre></div>"
        )
    logs = "\n".join(state.get("recent_log", []))
    prompts = ""
    if show_prompts:
        prompts = "<h4>Recent prompts by firm and request attempt</h4>" + recent_prompts_html(
            state.get("recent_requests", [])
        )
    error = ""
    if state.get("error") or state.get("message"):
        error = f"<p style='color:#b91c1c'>{escape(state.get('error', ''))}: {escape(state.get('message', ''))}</p>"
    return (
        "<div style='border:1px solid #9ca3af;border-radius:10px;padding:16px;font:14px system-ui'>"
        + heading
        + error
        + table
        + prompts
        + "<h4>Recent agent responses</h4>"
        + "".join(replies)
        + "<details><summary>Worker terminal</summary><pre style='white-space:pre-wrap'>"
        + escape(logs)
        + "</pre></details><small>Percent counts committed rounds, not retries. Known usage is not guaranteed billing. ETA changes with pacing and retries.</small></div>"
    )


class LivePanel:
    """A notebook display with an explicit cooperative stop button and terminal-style fallback."""

    def __init__(
        self,
        root: Path,
        plan_path: Path,
        response_characters: int = 1500,
        show_prompts: bool = True,
    ) -> None:
        self.display_api = importlib.import_module("IPython.display")
        self.root, self.plan_path = root, plan_path
        self.response_characters = response_characters
        self.show_prompts = show_prompts
        self.button: Any = None
        self.handle: Any = None
        self.recent_prompts: RecentPromptsPanel | None = None
        self.v2_panel: Any = None
        try:
            widgets = importlib.import_module("ipywidgets")

            self.button = widgets.Button(
                description="Stop experiment", button_style="danger", icon="stop"
            )
            self.button.on_click(self.stop)
            self.display_api.display(self.button)
            from research_v2_ui import V2Panel

            self.v2_panel = V2Panel(root, plan_path)
        except ImportError:
            pass
        self.handle = self.display_api.display(
            self.display_api.HTML(
                "Starting progress display. Use Stop or interrupt this cell to stop safely."
            ),
            display_id=True,
        )
        if self.button is not None and show_prompts:
            self.recent_prompts = RecentPromptsPanel()
            self.prompt_browser = prompt_viewer(root, plan_path, collapsed=True)

    def stop(self, button: Any = None) -> None:
        """Stop only the frozen plan's experiment through its existing stop control."""
        control.request_stop(self.root, self.plan_path)
        if self.button is not None:
            self.button.disabled = True
            self.button.description = "Stop requested"

    def render(self, state: dict[str, Any], elapsed: float) -> None:
        """Update one display rather than accumulating hundreds of notebook output blocks."""
        content = dashboard_html(
            state,
            elapsed,
            response_characters=self.response_characters,
            show_prompts=self.show_prompts and self.recent_prompts is None,
        )
        if self.handle is not None:
            self.handle.update(self.display_api.HTML(content))
        if self.recent_prompts is not None:
            self.recent_prompts.update(state)
        if self.v2_panel is not None:
            self.v2_panel.update(state)
        if (
            state["status"] in {"complete", "partial", "failed", "interrupted"}
            and self.button is not None
        ):
            self.button.disabled = True


async def watch(
    root: Path,
    plan_path: Path,
    launched: dict[str, Any],
    *,
    refresh_seconds: float = 2,
    response_limit: int = 9,
    response_characters: int = 1500,
    show_prompts: bool = True,
    prompt_limit: int = 3,
    terminal_lines: int = 8,
    startup_timeout_seconds: float = 60,
    stop_grace_seconds: float = 30,
    renderer: Callable[[dict[str, Any], float], None] | None = None,
) -> dict[str, Any]:
    """Keep Run All at the launch cell until execution and analysis reach a terminal state."""
    if launched.get("status") == "preview_only":
        return launched
    if (
        not 0.05 <= refresh_seconds <= 60
        or not 1 <= response_limit <= 100
        or response_characters < 50
        or not 1 <= prompt_limit <= 12
        or terminal_lines < 1
        or startup_timeout_seconds <= 0
        or stop_grace_seconds <= 0
    ):
        raise ValueError("Use positive bounded display controls; refresh must be 0.05–60 seconds")
    reader = ProgressReader(root, plan_path)
    panel = (
        LivePanel(root, plan_path, response_characters, show_prompts) if renderer is None else None
    )
    draw = panel.render if panel is not None else renderer
    assert draw is not None
    process = control.worker_process(launched["pid"])
    started = time.monotonic()
    terminal = {"complete", "partial", "failed", "interrupted"}
    try:
        while True:
            state = reader.read(
                log_lines=terminal_lines, response_limit=response_limit, prompt_limit=prompt_limit
            )
            elapsed = time.monotonic() - started
            code = process.poll() if process is not None else None
            if code is not None and state["status"] not in terminal:
                state.update(
                    status="failed",
                    error="WorkerExited",
                    message=f"Worker exited with code {code}; inspect the terminal below.",
                )
            if state["status"] == "not_started" and elapsed > startup_timeout_seconds:
                control.request_stop(root, plan_path)
                state.update(
                    status="failed",
                    error="StartupTimeout",
                    message="Worker never published its status; a stop was requested. Inspect the terminal and Python environment.",
                )
                draw(state, elapsed)
                raise TimeoutError(state["message"])
            draw(state, elapsed)
            if state["status"] in terminal:
                if process is not None and process.poll() is None:
                    await asyncio.sleep(min(refresh_seconds, 0.2))
                    continue
                if state.get("operator_stop_requested"):
                    return {**state, "raw_status": state["status"], "status": "stopped"}
                if state["status"] != "complete":
                    raise RuntimeError(
                        "Experiment did not complete. "
                        + state.get("message", "Inspect the saved worker.log.")
                        + " Later research stages were not run."
                    )
                return state
            await asyncio.sleep(refresh_seconds)
    except (KeyboardInterrupt, asyncio.CancelledError):
        control.request_stop(root, plan_path)
        deadline = time.monotonic() + stop_grace_seconds
        while process is not None and process.poll() is None and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        raise
    except Exception:
        if process is not None and process.poll() is None:
            control.request_stop(root, plan_path)
        raise
    finally:
        if process is None or process.poll() is not None:
            control.forget_worker(launched["pid"])
