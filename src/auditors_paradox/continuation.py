"""Validate a sealed provider or local output-limit attempt before continuing its evidence."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from auditors_paradox.config import StudyConfig
from auditors_paradox.contracts import fingerprint
from auditors_paradox.evidence import file_hash, source_snapshot


def read_checkpoint(
    path: Path, cfg: StudyConfig, root: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Reject changed settings, damaged evidence and ambiguous unfinished decisions."""
    path = path.resolve()
    if not path.is_relative_to(root.resolve() / "runs"):
        raise ValueError("Continuation must reference saved evidence inside runs")
    manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf8"))
    boundary_candidate = bool(
        cfg.v2
        and cfg.v2.manual_enabled
        and manifest.get("status") in {"failed", "interrupted", "stopped"}
        and (manifest.get("error") or {}).get("type")
        in {"RunStopped", "KeyboardInterrupt", "SystemExit", "OrphanedBoundary"}
    )
    if (
        cfg.run_kind == "confirmatory"
        or (
            not boundary_candidate
            and (
                manifest.get("status") != "failed"
                or (manifest.get("error") or {}).get("type") != "ProviderError"
            )
        )
        or cfg.arm
        not in (
            {"silent", "private", "public", "placebo", "fine_only", "private_fine", "public_fine"}
            if cfg.v2
            else {"silent", "private", "public", "placebo"}
        )
    ):
        raise ValueError("Continuation supports eligible failed exploratory runs only")
    if StudyConfig.model_validate(manifest["config"]).model_dump() != cfg.model_dump():
        raise ValueError("Continuation settings differ; restore the frozen study settings")
    checksums = json.loads(path.with_name("checksums.json").read_text(encoding="utf8"))
    if file_hash(path.with_name("checksums.json")) != manifest["checksums_sha256"]:
        raise ValueError("Continuation checksum index changed")
    for name, digest in checksums.items():
        target = (path.parent / name).resolve()
        if not target.is_relative_to(path.parent) or file_hash(target) != digest:
            raise ValueError("Continuation evidence checksum mismatch")
    events: list[dict[str, Any]] = [
        json.loads(line)
        for line in path.with_name("events.jsonl").read_text(encoding="utf8").splitlines()
    ]
    previous = "0" * 64
    for sequence, event in enumerate(events, 1):
        core = {k: v for k, v in event.items() if k != "record_hash"}
        if (
            event["sequence"] != sequence
            or event["previous_hash"] != previous
            or fingerprint(core) != event["record_hash"]
        ):
            raise ValueError("Continuation event chain changed")
        previous = event["record_hash"]
    if previous != manifest["last_event_hash"] or len(events) != manifest["event_count"]:
        raise ValueError("Continuation event inventory changed")
    failures = [
        e["payload"]
        for e in events
        if e["type"] == "model_call_failed" and e["payload"]["error_kind"] != "RunStopped"
    ]
    local_output_limit = bool(
        failures
        and cfg.agent.backend == "ollama"
        and cfg.agent.api_protocol == "ollama"
        and failures[-1]["error_kind"] == "incomplete_local_response"
        and failures[-1].get("client_evidence", {}).get("done_reason") == "length"
        and all(
            type(failures[-1].get("client_evidence", {}).get(k)) is int
            for k in ("input_tokens", "output_tokens")
        )
    )
    if (
        not boundary_candidate
        and not local_output_limit
        and (
            not failures
            or failures[-1]["error_kind"] not in {"provider_rate_limit", "rate_limit_or_quota"}
        )
    ):
        raise ValueError(
            "The saved failure was not a retryable rate limit or known local output limit"
        )
    starts = {e["payload"]["call_id"] for e in events if e["type"] == "model_call_started"}
    finished = {
        e["payload"]["call_id"]
        for e in events
        if e["type"] in {"model_call_failed", "model_call_completed"}
    }
    if starts != finished:
        raise ValueError("An in-flight request has unknown status; continuation is unsafe")
    completed = manifest["completed_rounds"]
    boundaries = {e["payload"]["source_round"] for e in events if e["type"] == "boundary_frozen"}
    if cfg.v2 and boundaries != set(range(1, completed + 1)):
        if (
            not boundary_candidate
            or not 0 < completed < cfg.rounds
            or boundaries != set(range(1, completed))
            or any(
                e["payload"].get("round", 0) > completed
                for e in events
                if e["type"] in {"observation", "model_call_started"}
            )
            or any(
                e["payload"]["source_round"] == completed for e in events if e["type"] == "delivery"
            )
        ):
            raise ValueError("An unfinished manual boundary cannot be safely continued")
        from auditors_paradox.contracts import Delivery
        from auditors_paradox.interventions import InterventionDraft, resolve_draft

        automatic = [
            e["payload"]
            for e in events
            if e["type"] == "automatic_delivery" and e["payload"]["source_round"] == completed
        ]
        if len(automatic) != 3:
            raise ValueError("Incomplete automatic boundary assessment")
        deliveries = tuple(
            Delivery(**{k: v for k, v in p.items() if k != "text"}) for p in automatic
        )
        for item in [
            e["payload"]["intervention"]
            for e in events
            if e["type"] == "manual_control"
            and e["payload"].get("status") == "applied"
            and e["payload"]["source_round"] == completed
        ]:
            if (
                fingerprint({k: v for k, v in item.items() if k != "preview_hash"})
                != item["preview_hash"]
            ):
                raise ValueError("Recovered preview hash changed")
            confirmations = [
                e
                for e in events
                if e["type"] == "manual_control"
                and e["payload"].get("status") == "applied"
                and e["payload"].get("intervention") == item
            ]
            if len(confirmations) != 1:
                raise ValueError("Recovered intervention lacks durable acceptance")
            deliveries, _ = resolve_draft(
                deliveries,
                InterventionDraft.model_validate(item["draft"]),
                item["intervention_id"],
                manifest["feedback_texts"],
            )
            if [d.to_dict() for d in deliveries] != item["deliveries"]:
                raise ValueError("Recovered messages differ from accepted preview")
        manifest = {
            **manifest,
            "pending_boundary": {
                "source_round": completed,
                "deliveries": [d.to_dict() for d in deliveries],
            },
        }
    elif boundary_candidate:
        raise ValueError(
            "Boundary recovery requires a committed round with an unfinished inbox freeze"
        )
    commits = [e["payload"]["round"] for e in events if e["type"] == "round_committed"]
    if commits != list(range(1, completed + 1)) or any(
        e["type"] == "round" and e["payload"]["round"] > completed for e in events
    ):
        raise ValueError("An unfinished market transition cannot be safely continued")
    accepted = {c for e in events if e["type"] == "decision" for c in e["payload"]["call_ids"]}
    if any(
        e["type"] == "model_call_completed"
        and e["payload"]["round"] > completed
        and e["payload"]["call_id"] not in accepted
        for e in events
    ):
        raise ValueError("An unfinished parsing conversation cannot be safely continued")
    return manifest, events


def check_sweep_checkpoint(path: Path, cfg: StudyConfig, root: Path) -> dict[str, Any]:
    """Check the whole original assignment and source before any continuation can dispatch."""
    path = path.resolve()
    if not path.is_relative_to(root.resolve() / "runs"):
        raise ValueError("Resume sweep must be inside runs")
    sweep: dict[str, Any] = json.loads(path.read_text(encoding="utf8"))
    if sweep.get("status") not in {"failed", "partial"}:
        raise ValueError("Only a terminal failed or partial sweep can be continued")
    if StudyConfig.model_validate(sweep["base_config"]).model_dump() != cfg.model_dump():
        raise ValueError("Restore the original frozen STUDY before continuing; settings changed")
    assignment = json.loads(path.with_name("assignment.json").read_text(encoding="utf8"))
    fields = ("run_id", "arm", "replicate_id", "seed")
    if [tuple(r[k] for k in fields) for r in assignment] != [
        tuple(r[k] for k in fields) for r in sweep["runs"]
    ]:
        raise ValueError("Original continuation assignment changed or is incomplete")
    snapshot = root / ".cache" / ("resume-check-" + uuid.uuid4().hex + ".zip")
    snapshot.parent.mkdir(exist_ok=True)
    try:
        source = source_snapshot(root, snapshot)
    finally:
        snapshot.unlink(missing_ok=True)
    failures = 0
    from auditors_paradox.evaluation.validate import validate_run

    for row in sweep["runs"]:
        if row["status"] == "not_run":
            continue
        parent = (root / row["manifest_path"]).resolve()
        if not parent.is_relative_to(root / "runs"):
            raise ValueError("Original run escaped runs")
        manifest = json.loads(parent.read_text(encoding="utf8"))
        if manifest["source_tree_sha256"] != source:
            raise ValueError(
                "Study source changed since failure. The old attempt is preserved; it cannot be silently resumed with the new version."
            )
        if row["status"] == "complete":
            if not validate_run(parent)["valid"]:
                raise ValueError("A completed parent trajectory failed validation")
        else:
            failures += 1
            data = cfg.model_dump(mode="json")
            data.update(arm=row["arm"], seed=row["seed"])
            read_checkpoint(parent, StudyConfig.model_validate(data), root)
    if not failures:
        raise ValueError(
            "No eligible rate-limit or local output-limit failure exists in this sweep"
        )
    return sweep


def remaining_work(sweep: dict[str, Any], root: Path) -> tuple[int, float]:
    """Count only missing decisions and remaining run reservations for the new allowance."""
    cfg = StudyConfig.model_validate(sweep["base_config"])
    decisions = 0
    cap = 0.0
    for row in sweep["runs"]:
        if row["status"] == "complete":
            continue
        accepted = 0
        held = 0.0
        if row["status"] == "failed":
            parent = root / row["manifest_path"]
            manifest = json.loads(parent.read_text(encoding="utf8"))
            accepted = sum(
                json.loads(line)["type"] == "decision"
                for line in parent.with_name("events.jsonl").read_text(encoding="utf8").splitlines()
            )
            held = manifest["reserved_cost_usd"]
        decisions += 3 * cfg.rounds - accepted
        cap += max(0.0, float(cfg.budget.max_cost_usd or 0) - held)
    return decisions, cap
