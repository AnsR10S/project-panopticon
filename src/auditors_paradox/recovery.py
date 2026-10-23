"""Snapshot an orphaned, fully committed V2 boundary without changing its evidence."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from auditors_paradox.config import StudyConfig, safe_identifier
from auditors_paradox.contracts import fingerprint
from auditors_paradox.evaluation.validate_v2 import check_v2
from auditors_paradox.evidence import EvidenceWriter, file_hash
from auditors_paradox.leases import RunLease


def recover_boundary(root: Path, source: Path, output: Path) -> Path:
    """Acquire the abandoned writer lease and create a new sealed recovery checkpoint."""
    root = root.resolve()
    source = (root / source).resolve()
    output = (root / output).resolve()
    if (
        not source.is_relative_to(root / "runs")
        or not output.is_relative_to(root / "runs")
        or output.exists()
        or output.is_relative_to(source.parent)
    ):
        raise ValueError("Choose a new recovery directory under runs, outside the original attempt")
    with RunLease(source.parent, existing=True):
        original = json.loads(source.read_text(encoding="utf8"))
        cfg = StudyConfig.model_validate(original["config"])
        if (
            original["status"] != "running"
            or cfg.v2 is None
            or not cfg.v2.manual_enabled
            or cfg.run_kind == "confirmatory"
        ):
            raise ValueError("Orphan recovery is only for a running exploratory V2 manual boundary")
        raw = source.with_name("events.jsonl").read_bytes()
        if not raw.endswith(b"\n"):
            raise ValueError(
                "Incomplete raw tail; quarantine it for inspection instead of dropping bytes"
            )
        records = [json.loads(line) for line in raw.decode("utf8").splitlines()]
        completed = int(original["completed_rounds"])
        boundaries = [
            e["payload"]["source_round"] for e in records if e["type"] == "boundary_frozen"
        ]
        if not 0 < completed < cfg.rounds or boundaries != list(range(1, completed)):
            raise ValueError("Recovery requires the final committed round to lack its inbox freeze")
        accepted = [
            e["payload"]["intervention"]
            for e in records
            if e["type"] == "manual_control" and e["payload"].get("status") == "applied"
        ]
        recovery = {
            "kind": "orphan_boundary_snapshot",
            "original_manifest": source.relative_to(root).as_posix(),
            "original_manifest_sha256": file_hash(source),
            "original_events_sha256": file_hash(source.with_name("events.jsonl")),
            "root_manifest": original.get("continuation", {}).get(
                "root_manifest", source.relative_to(root).as_posix()
            ),
            "root_run_id": original.get("continuation", {}).get("root_run_id", original["run_id"]),
            "lease": "exclusive abandoned writer lease acquired",
            "source_round": completed,
            "budget_elapsed": "conservative upper bound including downtime",
            "pending_controls": "Unaccepted commands are retained in the original directory and never replayed",
        }
        checked = {**original, "researcher_intervened": bool(accepted), "recovery": recovery}
        errors = check_v2(
            records, checked, completed_rounds=completed, allow_unfrozen_boundary=True
        )
        if errors:
            raise ValueError("Unsafe recovery prefix: " + "; ".join(errors))
        with zipfile.ZipFile(source.with_name("source.zip")) as archive:
            hashes = json.loads(archive.read("snapshot-hashes.json"))
            if (
                fingerprint(hashes) != original["source_tree_sha256"]
                or sorted(archive.namelist()) != sorted([*hashes, "snapshot-hashes.json"])
                or any(
                    hashlib.sha256(archive.read(name)).hexdigest() != digest
                    for name, digest in hashes.items()
                )
            ):
                raise ValueError("Original source snapshot changed")
        resolved = json.loads(source.with_name("resolved-config.json").read_text(encoding="utf8"))
        if resolved != original["config"] or fingerprint(resolved) != original["config_sha256"]:
            raise ValueError("Original resolved configuration changed")
        calls = [e["payload"] for e in records if e["type"] == "model_call_started"]
        successful = [e["payload"] for e in records if e["type"] == "model_call_completed"]
        failed = [
            e["payload"].get("client_evidence", {})
            for e in records
            if e["type"] == "model_call_failed"
        ]
        known_failed = [
            e
            for e in failed
            if all(type(e.get(k)) is int for k in ("input_tokens", "output_tokens"))
        ]
        paid = cfg.agent.backend not in {"scripted", "ollama"}
        observed = (
            sum(
                e["input_tokens"] * float(cfg.budget.input_usd_per_million or 0)
                + e["output_tokens"] * float(cfg.budget.output_usd_per_million or 0)
                for e in [*successful, *known_failed]
            )
            / 1e6
            if paid
            else 0
        )
        elapsed = max(
            0.0,
            (
                datetime.now(timezone.utc) - datetime.fromisoformat(original["started_at"])
            ).total_seconds(),
        )
        parent = source.with_name("parent-manifest.json")
        if original.get("continuation") and parent.is_file():
            elapsed += float(
                json.loads(parent.read_text(encoding="utf8")).get("active_elapsed_seconds", 0)
            )
        writer = EvidenceWriter(output, {**checked, "run_id": safe_identifier(output.name)})
        try:
            for name in ("source.zip", "resolved-config.json", "parent-manifest.json"):
                path = source.with_name(name)
                if path.is_file():
                    shutil.copyfile(path, output / name)
            shutil.copyfile(source, output / "orphan-manifest.json")
            writer.stream.write(raw.decode("utf8"))
            writer.stream.flush()
            os.fsync(writer.stream.fileno())
            writer.sequence = len(records)
            writer.previous_hash = records[-1]["record_hash"]
            writer.manifest.update(
                completed_rounds=completed,
                model_calls=len(calls),
                reserved_cost_usd=sum(c["reserved_cost_usd"] for c in calls),
                observed_cost_estimate_usd=observed,
                usage_incomplete=any(
                    e.get("network_requests", 1) and e not in known_failed for e in failed
                ),
                active_elapsed_seconds=elapsed,
                fallback_count=0,
            )
            return writer.finish(
                "interrupted",
                {
                    "type": "OrphanedBoundary",
                    "message": "Abandoned committed boundary preserved; resume requires the original source/config and operator acknowledgement",
                },
            )
        except BaseException:
            writer.stream.close()
            writer.lease.close()
            raise
