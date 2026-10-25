"""Notebook controls submit durable commands; only the worker accepts interventions."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from auditors_paradox.interventions import read_state, submit_command
from auditors_paradox.research_index import ResearchIndex


def manual_availability(
    study: dict[str, Any], run: Path | None, boundary: dict[str, Any]
) -> dict[str, Any]:
    """Explain which frozen prerequisite prevents manual intervention."""
    if study.get("schema_version", 1) != 2 or not study.get("v2"):
        return {
            "phase": "unavailable",
            "enabled": False,
            "reason": "This frozen plan uses V1. Start a new V2 exploratory plan with v2.manual_enabled=True; the running study cannot be upgraded mid-run.",
        }
    if not study["v2"].get("manual_enabled"):
        return {
            "phase": "unavailable",
            "enabled": False,
            "reason": "Manual Auditor was disabled when this plan was frozen. Enable v2.manual_enabled before freezing a new exploratory plan.",
        }
    if run is None:
        return {
            "phase": "unavailable",
            "enabled": False,
            "reason": "No running trajectory is available. Controls attach to an active V2 worker, not a preview or completed run.",
        }
    if boundary.get("phase") == "unavailable" or not boundary:
        return {
            **boundary,
            "phase": "unavailable",
            "enabled": False,
            "reason": "The V2 worker has not published a boundary state yet. Wait for startup; a stale kernel may require restart before a new launch.",
        }
    return boundary


class V2Panel:
    """Keep manual controls and exact previews stable across progress refreshes."""

    def __init__(self, root: Path, plan_path: Path) -> None:
        import ipywidgets as w
        from IPython.display import display

        self.root = root.resolve()
        self.plan = json.loads(plan_path.read_text(encoding="utf8"))
        self.study = json.loads(plan_path.with_name("study.json").read_text(encoding="utf8"))
        self.run: Path | None = None
        self.boundary: dict[str, Any] = {}
        self.status = w.HTML("No V2 worker boundary available.")
        self.preview = w.HTML()
        self.operation = w.Dropdown(
            options=["append", "replace", "suppress"], description="Operation"
        )
        self.audience = w.Dropdown(options=["private", "public"], description="Receipt")
        self.recipients = w.SelectMultiple(
            options=[("Firm 0", 0), ("Firm 1", 1), ("Firm 2", 2)], value=(0,)
        )
        self.audience.observe(self.receipt_changed, names="value")
        self.text = w.Textarea(
            description="Text",
            placeholder="Exact custom body; factual claims remain your responsibility",
            layout=w.Layout(width="95%"),
        )
        self.buttons = {}
        for kind, label in [
            ("prepare", "Prepare boundary"),
            ("preview", "Preview"),
            ("confirm", "Confirm & Resume"),
            ("cancel", "Cancel preview"),
            ("resume", "Resume unchanged"),
        ]:
            button = w.Button(description=label, disabled=True)
            button.on_click(lambda _, action=kind: self.command(action))
            self.buttons[kind] = button
        self.log = w.HTML()
        self.sql = w.Textarea(
            value="SELECT arm,status,relative_path FROM runs ORDER BY run_id LIMIT 100",
            layout=w.Layout(width="95%"),
        )
        query = w.Button(description="Read index")
        query.on_click(self.query)
        self.query_result = w.HTML()
        self.system = w.HTML()
        self.plan_display = w.HTML(
            "<pre>" + html.escape(json.dumps(self.plan, indent=2)) + "</pre>"
        )
        self.panels = w.Accordion(
            children=[
                self.plan_display,
                self.system,
                w.VBox(
                    [
                        self.status,
                        self.operation,
                        self.audience,
                        self.recipients,
                        self.text,
                        w.HBox(list(self.buttons.values())),
                        self.preview,
                        self.log,
                    ]
                ),
                w.VBox([self.sql, query, self.query_result]),
                w.HTML(
                    "Raw events and sealed manifests are authoritative. Accepted manual edits are exploratory and excluded from untouched comparisons. SQL results are bounded to 1,000 rows. Index lag does not change agent information."
                ),
            ]
        )
        for i, title in enumerate(
            [
                "Frozen configuration",
                "Execution and accounting",
                "Manual Auditor",
                "Evidence index",
                "Interpretation and provenance",
            ]
        ):
            self.panels.set_title(i, title)
        self.panels.selected_index = None
        display(self.panels)

    def receipt_changed(self, change: dict[str, Any]) -> None:
        """A public receipt statement must actually include every firm."""
        public = change["new"] == "public"
        if public:
            self.recipients.value = (0, 1, 2)
        self.recipients.disabled = public

    def command(self, kind: str) -> None:
        """Bind each action to the displayed run, source round and preview revision."""
        try:
            if self.run is None:
                raise ValueError("No active V2 run")
            state = self.boundary
            extra: dict[str, Any] = {}
            if kind == "preview":
                extra["draft"] = {
                    "operation": self.operation.value,
                    "audience": self.audience.value,
                    "recipients": list(self.recipients.value),
                    "text": self.text.value,
                }
            if kind == "confirm":
                current_preview = state.get("preview") or {}
                current_draft = {
                    "operation": self.operation.value,
                    "audience": self.audience.value,
                    "recipients": sorted(self.recipients.value),
                    "text": self.text.value,
                }
                if current_draft != current_preview.get("draft"):
                    raise ValueError("The draft changed; request and review a new preview")
                extra["preview_hash"] = current_preview.get("preview_hash")
                if not extra["preview_hash"]:
                    raise ValueError("Wait for a worker-generated preview")
            identity = submit_command(
                self.run,
                kind,
                source_round=state["source_round"],
                revision=state["revision"],
                **extra,
            )
            self.log.value = "Command submitted; wait for worker acknowledgement: " + html.escape(
                str(identity)
            )
        except (ValueError, OSError, KeyError) as error:
            self.log.value = "Command rejected: " + html.escape(str(error))

    def update(self, state: dict[str, Any]) -> None:
        """Refresh readouts without replacing widgets or changing the operator's draft."""
        candidates = [
            row
            for row in state.get("runs", [])
            if row.get("status") == "running" and row.get("manifest_path")
        ]
        self.run = (self.root / candidates[0]["manifest_path"]).parent if candidates else None
        self.boundary = manual_availability(
            self.study, self.run, read_state(self.run) if self.run else {}
        )
        current = self.boundary
        self.status.value = "<pre>" + html.escape(json.dumps(current, indent=2)) + "</pre>"
        preview = current.get("preview") or {}
        self.preview.value = (
            "<b>Exact worker preview</b><pre>"
            + html.escape(json.dumps(preview, indent=2))
            + "</pre>"
        )
        for kind, button in self.buttons.items():
            button.disabled = not (
                current.get("enabled")
                and current.get("phase")
                in ({"collecting", "paused"} if kind == "prepare" else {"paused"})
            )
            if kind == "confirm" and not preview:
                button.disabled = True
        self.system.value = (
            "<pre>"
            + html.escape(
                json.dumps(
                    {
                        **{
                            key: state.get(key)
                            for key in ("status", "totals", "gateway_pacing", "message")
                        },
                        "recent_run_accounting": [
                            {
                                key: row.get(key)
                                for key in (
                                    "arm",
                                    "status",
                                    "latest_accounting",
                                    "latest_audit",
                                    "phase_timings",
                                    "index_state",
                                )
                            }
                            for row in state.get("runs", [])
                            if row.get("latest_accounting")
                        ],
                    },
                    indent=2,
                )
            )
            + "</pre>"
        )

    def query(self, _: Any = None) -> None:
        """Read the configured local index; this never dispatches model requests."""
        try:
            path = (self.study.get("v2") or {}).get("index_path", "runs/research.sqlite3")
            if not (self.root / path).is_file():
                raise ValueError(
                    "Index does not exist; enable V2 indexing or ingest evidence first"
                )
            with ResearchIndex(self.root, self.root / path) as index:
                result = {"check": index.check(), "rows": index.query(self.sql.value)}
            self.query_result.value = "<pre>" + html.escape(json.dumps(result, indent=2)) + "</pre>"
        except Exception as error:
            self.query_result.value = html.escape(type(error).__name__ + ": " + str(error))
