"""Pause at an explicit boundary and record exact next-decision interventions."""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from auditors_paradox.config import StrictModel, StudyConfig
from auditors_paradox.contracts import Delivery, fingerprint
from auditors_paradox.evidence import EvidenceWriter, atomic_json, utc_now


class InterventionDraft(StrictModel):
    """One custom notice or automatic-message operation and its recipients."""

    operation: Literal["append", "replace", "suppress"] = "append"
    recipients: list[int] = Field(default_factory=lambda: [0, 1, 2])
    audience: Literal["private", "public"] = "private"
    text: str = ""

    @model_validator(mode="after")
    def valid(self) -> InterventionDraft:
        """A public common-receipt statement must describe all three recipients."""
        if (
            not self.recipients
            or self.recipients != sorted(set(self.recipients))
            or any(i not in range(3) for i in self.recipients)
        ):
            raise ValueError("Choose sorted unique firm IDs from 0, 1, 2")
        if self.audience == "public" and self.recipients != [0, 1, 2]:
            raise ValueError("Public common delivery requires all three firms")
        if self.operation != "suppress" and not self.text.strip():
            raise ValueError("Write the custom notice before previewing")
        if self.operation == "suppress" and self.text:
            raise ValueError("Suppression has no replacement text")
        return self


@contextmanager
def control_lock(directory: Path) -> Iterator[None]:
    """Share an OS-owned lock between notebook sessions and the worker."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "control.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + 5
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import importlib

                    fcntl = importlib.import_module("fcntl")

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Manual boundary is busy; refresh and retry") from None
                time.sleep(0.01)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read_state(run_directory: Path) -> dict[str, Any]:
    """Read the worker's boundary state without changing the experiment."""
    path = run_directory / "control/state.json"
    for attempt in range(6):
        try:
            return (
                json.loads(path.read_text(encoding="utf8"))
                if path.exists()
                else {"phase": "unavailable"}
            )
        except FileNotFoundError:
            return {"phase": "unavailable"}
        except PermissionError as error:
            if attempt == 5 or (
                getattr(error, "winerror", None) not in {5, 32, 33} and error.errno != 13
            ):
                raise
            time.sleep(0.01 * 2**attempt)
    raise AssertionError("Boundary read loop did not return")


def submit_command(
    run_directory: Path,
    command: str,
    *,
    source_round: int,
    revision: int | None = None,
    draft: dict[str, Any] | None = None,
    preview_hash: str | None = None,
    resume: bool = True,
) -> str:
    """Queue a command against the exact visible boundary; the worker accepts it."""
    if command not in {"prepare", "preview", "confirm", "cancel", "resume"}:
        raise ValueError("Unknown manual control command")
    directory = run_directory / "control"
    with control_lock(directory):
        state = read_state(run_directory)
        if not state.get("enabled") or state.get("phase") not in {"collecting", "paused"}:
            raise ValueError("This run has no available manual boundary")
        if source_round != state["source_round"]:
            raise ValueError("Boundary changed; refresh before submitting")
        if command != "prepare" and state["phase"] != "paused":
            raise ValueError("Prepare and wait for the boundary preview first")
        identity = uuid.uuid4().hex
        payload = {
            "command_id": identity,
            "run_id": state["run_id"],
            "kind": command,
            "source_round": source_round,
            "effective_round": source_round + 1,
            "submitted_at": utc_now(),
            "revision": revision,
            "draft": draft,
            "preview_hash": preview_hash,
            "resume": resume,
        }
        atomic_json(directory / f"request-{time.time_ns()}-{identity}.json", payload)
        return identity


def resolve_draft(
    automatic: tuple[Delivery, ...], draft: InterventionDraft, identity: str, texts: dict[str, str]
) -> tuple[tuple[Delivery, ...], list[str]]:
    """Render the preview without touching prices, rewards or another manual notice."""
    changed: list[str] = []
    results = list(automatic)
    affected = [
        d
        for d in results
        if d.recipient in draft.recipients
        and d.source_kind != "manual"
        and d.status == "scheduled_for_next_round"
    ]
    if draft.operation in {"replace", "suppress"}:
        if {d.recipient for d in affected} != set(draft.recipients):
            raise ValueError(
                "Every selected recipient needs a scheduled automatic notice; use Append otherwise"
            )
        changed = [d.delivery_id for d in affected]
        results = [
            (
                replace(
                    d,
                    status=(
                        "replaced_by_manual"
                        if draft.operation == "replace"
                        else "suppressed_by_manual"
                    ),
                )
                if d in affected
                else d
            )
            for d in results
        ]
    if draft.operation != "suppress":
        reference = automatic[0]
        results.extend(
            Delivery(
                f"manual-{identity}-a{i}",
                i,
                reference.source_round,
                reference.effective_round,
                reference.arm,
                identity,
                "scheduled_for_next_round",
                draft.text,
                texts[draft.audience],
                "manual",
                "custom-v2",
                identity,
            )
            for i in draft.recipients
        )
    for body in {
        d.body
        for d in results
        if d.header == texts["public"] and d.status == "scheduled_for_next_round"
    }:
        recipients = sorted(
            {
                d.recipient
                for d in results
                if d.body == body
                and d.header == texts["public"]
                and d.status == "scheduled_for_next_round"
            }
        )
        if recipients != [0, 1, 2]:
            header = (
                "Public receipt: this identical message is delivered to "
                + ", ".join(f"Firm {i}" for i in recipients)
                + ". Only these recipients are announced."
            )
            results = [
                (
                    replace(d, header=header)
                    if d.body == body
                    and d.header == texts["public"]
                    and d.status == "scheduled_for_next_round"
                    else d
                )
                for d in results
            ]
    return tuple(results), changed


class ManualController:
    """The worker alone owns acceptance, preview versions and the final inbox."""

    def __init__(self, cfg: StudyConfig, writer: EvidenceWriter, texts: dict[str, str]) -> None:
        self.cfg, self.writer, self.texts = cfg, writer, texts
        self.directory = writer.directory / "control"
        self.seen: set[str] = set()
        self.revision = 0
        self.preview: dict[str, Any] | None = None
        self.preview_deliveries: tuple[Delivery, ...] = ()
        self.state: dict[str, Any] = {
            "run_id": writer.manifest["run_id"],
            "enabled": bool(cfg.v2 and cfg.v2.manual_enabled),
            "phase": "starting",
            "source_round": 0,
            "revision": 0,
            "history": [],
        }

    def save(self) -> None:
        """Publish a worker-owned state snapshot for the interface."""
        self.state["revision"] = self.revision
        atomic_json(self.directory / "state.json", self.state)

    def collecting(self, round_number: int) -> None:
        """Name the next possible boundary without exposing another firm's decision."""
        with control_lock(self.directory):
            self.state.update(
                phase="collecting",
                source_round=round_number,
                effective_round=round_number + 1,
                preview=None,
            )
            self.save()

    def record(self, request: dict[str, Any], status: str, **details: Any) -> None:
        """Durably record submissions and resolutions before acknowledging them."""
        payload = {**request, "status": status, "resolved_at": utc_now(), **details}
        digest = self.writer.append("manual_control", payload, durable=True)
        self.state["history"].append(
            {
                "command_id": request["command_id"],
                "kind": request["kind"],
                "status": status,
                **details,
            }
        )
        atomic_json(
            self.directory / f"reply-{request['command_id']}.json",
            {**payload, "event_hash": digest},
        )

    def pending(self) -> list[dict[str, Any]]:
        """Read complete atomic requests once in submission order."""
        requests = []
        for path in sorted(self.directory.glob("request-*.json")):
            if path.name not in self.seen:
                self.seen.add(path.name)
                requests.append(json.loads(path.read_text(encoding="utf8")))
        return requests

    def boundary(
        self,
        source_round: int,
        deliveries: tuple[Delivery, ...],
        check_stop: Callable[[], None],
        *,
        initially_paused: bool = False,
    ) -> tuple[Delivery, ...]:
        """Pause only when requested, then freeze exactly the confirmed next-round inbox."""
        current = deliveries
        paused = initially_paused
        while True:
            check_stop()
            with control_lock(self.directory):
                for request in self.pending():
                    try:
                        if (
                            not self.state["enabled"]
                            or request["run_id"] != self.state["run_id"]
                            or request["source_round"] != source_round
                            or source_round >= self.cfg.rounds
                        ):
                            raise ValueError(
                                "Boundary unavailable or missed; prepare the next available round"
                            )
                        kind = request["kind"]
                        if kind == "prepare":
                            paused = True
                            self.record(request, "accepted_hold")
                        elif not paused:
                            raise ValueError("Boundary was not prepared")
                        elif request["revision"] != self.revision:
                            raise ValueError(
                                "Boundary revision changed; refresh the displayed state"
                            )
                        elif kind == "preview":
                            if request["revision"] != self.revision:
                                raise ValueError("Preview revision changed")
                            draft = InterventionDraft.model_validate(request["draft"])
                            assert self.cfg.v2 is not None
                            if len(draft.text) > self.cfg.v2.manual_max_chars:
                                raise ValueError("Notice exceeds manual_max_chars")
                            candidate, changed = resolve_draft(
                                current, draft, request["command_id"], self.texts
                            )
                            self.revision += 1
                            preview = {
                                "intervention_id": request["command_id"],
                                "draft": draft.model_dump(),
                                "source_round": source_round,
                                "effective_round": source_round + 1,
                                "revision": self.revision,
                                "changed_automatic_ids": changed,
                                "deliveries": [d.to_dict() for d in candidate],
                                "protocol_sha256": self.writer.manifest["protocol_sha256"],
                                "source_tree_sha256": self.writer.manifest["source_tree_sha256"],
                            }
                            preview["preview_hash"] = fingerprint(preview)
                            self.preview, self.preview_deliveries = preview, candidate
                            self.record(request, "previewed", preview=preview)
                        elif kind == "confirm":
                            if (
                                not self.preview
                                or request["preview_hash"] != self.preview["preview_hash"]
                                or request["revision"] != self.revision
                            ):
                                raise ValueError("Stale preview; review the exact messages again")
                            self.record(request, "applied", intervention=self.preview)
                            self.writer.manifest["researcher_intervened"] = True
                            self.writer.append("manual_intervention", self.preview, durable=True)
                            current = self.preview_deliveries
                            self.preview = None
                            self.revision += 1
                            if request["resume"]:
                                paused = False
                        elif kind == "cancel":
                            self.record(
                                request,
                                "canceled",
                                intervention_id=(self.preview or {}).get("intervention_id"),
                            )
                            self.preview = None
                            self.revision += 1
                        elif kind == "resume":
                            self.preview = None
                            self.record(request, "resumed")
                            paused = False
                        else:
                            raise ValueError("Unknown manual command")
                    except (ValueError, KeyError, TypeError) as error:
                        self.record(request, "rejected", reason=str(error))
                self.state.update(
                    phase="paused" if paused else "frozen",
                    source_round=source_round,
                    effective_round=source_round + 1,
                    preview=self.preview,
                    deliveries=[d.to_dict() for d in current],
                )
                if not paused:
                    self.writer.append(
                        "boundary_frozen",
                        {
                            "source_round": source_round,
                            "effective_round": source_round + 1,
                            "deliveries_sha256": fingerprint([d.to_dict() for d in current]),
                        },
                        durable=True,
                    )
                self.save()
                if not paused:
                    return current
            time.sleep(0.05)

    def close(self, status: str) -> None:
        """Reject pending commands when no further decision can be frozen."""
        with control_lock(self.directory):
            for request in self.pending():
                self.record(request, "rejected", reason="Run ended before this command was applied")
            self.state.update(phase="terminal", run_status=status, preview=None)
            self.save()
