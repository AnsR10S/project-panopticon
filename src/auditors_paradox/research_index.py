"""Rebuild a local SQLite research index from authoritative evidence."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from auditors_paradox.contracts import canonical, fingerprint
from auditors_paradox.evidence import file_hash, utc_now


class IndexIntegrityError(ValueError):
    """An existing indexed identity no longer matches its evidence."""


def safe_data(value: Any) -> Any:
    """Exclude credential headers and token values from every database projection."""
    if isinstance(value, dict):
        return {
            k: safe_data(v)
            for k, v in value.items()
            if k.lower()
            not in {
                "authorization",
                "api_key",
                "api-key",
                "cookie",
                "set-cookie",
                "password",
                "secret",
                "access_token",
            }
            and not k.lower().endswith("_api_key")
        }
    if isinstance(value, list):
        return [safe_data(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"\bsk-[A-Za-z0-9_-]{16,}", "[REDACTED]", value)
    return value


class ResearchIndex:
    """One worker-owned connection writes; separate read-only connections browse."""

    def __init__(self, root: Path, path: Path | None = None) -> None:
        self.root = root.resolve()
        self.path = (path or self.root / "runs/research.sqlite3").resolve()
        if not self.path.is_relative_to(self.root):
            raise ValueError("Research database must stay inside the project")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        migration = Path(__file__).with_name("migrations") / "0001_research_index.sql"
        checksum = file_hash(migration)
        exists = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone()
        if not exists:
            try:
                self.connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + migration.read_text(encoding="utf8")
                    + "\nINSERT INTO schema_migrations VALUES(1,'"
                    + checksum
                    + "','"
                    + utc_now()
                    + "');\nCOMMIT;"
                )
            except Exception:
                self.connection.close()
                raise
        else:
            applied = [
                tuple(row)
                for row in self.connection.execute("SELECT version,checksum FROM schema_migrations")
            ]
            if applied != [(1, checksum)]:
                self.connection.close()
                raise IndexIntegrityError(
                    "Unknown schema version or applied migration checksum changed"
                )

    def __enter__(self) -> ResearchIndex:
        return self

    def __exit__(self, *_: Any) -> None:
        self.connection.close()

    def _put(self, table: str, values: dict[str, Any], keys: tuple[str, ...]) -> None:
        """Update reconstructible projections inside the current transaction."""
        columns = list(values)
        updates = [c for c in columns if c not in keys]
        action = (
            "DO UPDATE SET " + ",".join(f"{c}=excluded.{c}" for c in updates)
            if updates
            else "DO NOTHING"
        )
        self.connection.execute(
            f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) ON CONFLICT({','.join(keys)}) {action}",
            list(values.values()),
        )

    def ingest_run(self, manifest_path: Path) -> dict[str, Any]:
        """Verify the immutable prefix and commit its projections and cursor together."""
        manifest_path = manifest_path.resolve()
        if manifest_path.is_dir():
            manifest_path = manifest_path / "manifest.json"
        if not manifest_path.is_relative_to(self.root):
            raise ValueError("Evidence escaped the project")
        manifest = json.loads(manifest_path.read_text(encoding="utf8"))
        cfg = manifest["config"]
        run_id = manifest_path.parent.relative_to(self.root).as_posix()
        raw = manifest_path.with_name("events.jsonl").read_bytes()
        cursor = self.connection.execute(
            "SELECT * FROM import_cursors WHERE run_id=?", (run_id,)
        ).fetchone()
        offset, sequence, previous = (
            (cursor["byte_offset"], cursor["sequence"], cursor["record_hash"])
            if cursor
            else (0, 0, "0" * 64)
        )
        if cursor and hashlib.sha256(raw[:offset]).hexdigest() != cursor["prefix_sha256"]:
            raise IndexIntegrityError("Indexed raw prefix changed")
        terminal = manifest["status"] != "running"
        if terminal:
            checks = json.loads(manifest_path.with_name("checksums.json").read_text())
            if file_hash(manifest_path.with_name("checksums.json")) != manifest["checksums_sha256"]:
                raise IndexIntegrityError("Checksum index changed")
            for name, digest in checks.items():
                target = (manifest_path.parent / name).resolve()
                if not target.is_relative_to(manifest_path.parent) or file_hash(target) != digest:
                    raise IndexIntegrityError("Terminal artifact failed its checksum")
        parsed = []
        for line in raw[offset:].splitlines(keepends=True):
            if not line.endswith(b"\n"):
                if terminal:
                    raise IndexIntegrityError("Terminal event stream has an incomplete tail")
                break
            event = json.loads(line)
            core = {k: v for k, v in event.items() if k != "record_hash"}
            if (
                event["sequence"] != sequence + 1
                or event["previous_hash"] != previous
                or fingerprint(core) != event["record_hash"]
            ):
                raise IndexIntegrityError("Raw event chain failed before indexing")
            sequence += 1
            previous = event["record_hash"]
            offset += len(line)
            parsed.append(event)
        if terminal and (
            sequence != manifest["event_count"] or previous != manifest["last_event_hash"]
        ):
            raise IndexIntegrityError("Terminal manifest tail differs from evidence")
        study_id = manifest["study_id"] + "@" + manifest["protocol_sha256"][:12]
        plan = manifest.get("assignment_block", "standalone")
        root_manifest = manifest.get("continuation", {}).get("root_manifest")
        if plan == "standalone" and root_manifest:
            original = (self.root / root_manifest).resolve()
            if not original.is_relative_to(self.root):
                raise ValueError("Parent evidence escaped the project")
            plan = "standalone-" + original.parent.relative_to(self.root).as_posix()
        if plan == "standalone":
            plan = (
                "standalone-"
                + manifest_path.parent.with_name(
                    manifest.get("continuation", {}).get("root_run_id", manifest["run_id"])
                )
                .relative_to(self.root)
                .as_posix()
            )
        block = fingerprint([plan, manifest["replicate_id"]])
        assignment = fingerprint([block, manifest["arm"]])
        model = fingerprint(cfg["agent"])
        comparable = {
            k: v for k, v in cfg.items() if k not in {"arm", "seed", "run_kind", "study_id"}
        }
        design = fingerprint(comparable)

        def encode(data: Any) -> str:
            return canonical(safe_data(data))

        with self.connection:
            existing_plan = self.connection.execute(
                "SELECT design_hash FROM plans WHERE plan_id=?", (plan,)
            ).fetchone()
            if existing_plan and existing_plan[0] != design:
                raise IndexIntegrityError("Frozen plan identity has conflicting designs")
            self._put(
                "studies",
                {
                    "study_id": study_id,
                    "protocol_hash": manifest["protocol_sha256"],
                    "data_json": encode(
                        {"recorded_study_id": manifest["study_id"], "protocol": cfg["protocol"]}
                    ),
                },
                ("study_id",),
            )
            if not existing_plan:
                self._put(
                    "plans",
                    {
                        "plan_id": plan,
                        "study_id": study_id,
                        "design_hash": design,
                        "data_json": encode(
                            {"source": manifest["source_tree_sha256"], "config": cfg}
                        ),
                    },
                    ("plan_id",),
                )
            self._put(
                "blocks",
                {
                    "block_id": block,
                    "plan_id": plan,
                    "replicate_id": manifest["replicate_id"],
                    "seed": manifest["replicate_seed"],
                },
                ("block_id",),
            )
            if not self.connection.execute(
                "SELECT 1 FROM assignments WHERE assignment_id=?", (assignment,)
            ).fetchone():
                self._put(
                    "assignments",
                    {
                        "assignment_id": assignment,
                        "block_id": block,
                        "arm": manifest["arm"],
                        "data_json": encode({"logical_assignment": assignment}),
                    },
                    ("assignment_id",),
                )
            self._put(
                "runs",
                {
                    "run_id": run_id,
                    "assignment_id": assignment,
                    "parent_run_id": manifest.get("continuation", {}).get("parent_manifest"),
                    "arm": manifest["arm"],
                    "status": manifest["status"],
                    "relative_path": manifest_path.relative_to(self.root).as_posix(),
                    "researcher_intervened": int(manifest.get("researcher_intervened", False)),
                    "data_json": encode(manifest),
                },
                ("run_id",),
            )
            self._put(
                "models",
                {
                    "model_id": model,
                    "requested_model": cfg["agent"]["model"],
                    "data_json": encode(cfg["agent"]),
                },
                ("model_id",),
            )
            for event in parsed:
                existing = self.connection.execute(
                    "SELECT record_hash FROM events WHERE run_id=? AND sequence=?",
                    (run_id, event["sequence"]),
                ).fetchone()
                if existing and existing[0] != event["record_hash"]:
                    raise IndexIntegrityError("Event identity has a conflicting hash")
                self._put(
                    "events",
                    {
                        "run_id": run_id,
                        "sequence": event["sequence"],
                        "record_hash": event["record_hash"],
                        "previous_hash": event["previous_hash"],
                        "event_type": event["type"],
                        "data_json": encode(event["payload"]),
                    },
                    ("run_id", "sequence"),
                )
                self._project(run_id, event, model)
            for name, digest in manifest.get("prompt_hashes", {}).items():
                self._put(
                    "artifacts",
                    {
                        "artifact_id": fingerprint([run_id, name]),
                        "run_id": run_id,
                        "relative_path": name,
                        "sha256": digest,
                        "role": "frozen_prompt_in_source_zip",
                        "verified": int(terminal),
                    },
                    ("artifact_id",),
                )
            self._put(
                "import_cursors",
                {
                    "run_id": run_id,
                    "byte_offset": offset,
                    "sequence": sequence,
                    "record_hash": previous,
                    "prefix_sha256": hashlib.sha256(raw[:offset]).hexdigest(),
                },
                ("run_id",),
            )
        return {
            "run_id": run_id,
            "new_events": len(parsed),
            "sequence": sequence,
            "status": manifest["status"],
        }

    def _project(self, run_id: str, event: dict[str, Any], model: str) -> None:
        """Project one validated event while keeping unavailable usage unknown."""
        p = event["payload"]
        kind = event["type"]
        data = canonical(safe_data(p))
        base = {"run_id": run_id}
        if kind == "round":
            self._put(
                "rounds",
                {**base, "round_no": p["round"], "committed": 0, "data_json": data},
                ("run_id", "round_no"),
            )
            for i, amount in enumerate(p.get("penalties", [])):
                gross = p.get("gross_rewards", p.get("net_rewards", []))
                rate = amount / gross[i] if len(gross) > i and gross[i] > 0 else None
                self._put(
                    "penalties",
                    {
                        **base,
                        "round_no": p["round"],
                        "firm_id": i,
                        "severity": "legacy_unknown",
                        "rate": rate,
                        "amount": amount,
                        "data_json": data,
                    },
                    ("run_id", "round_no", "firm_id"),
                )
        elif kind == "round_committed":
            self.connection.execute(
                "UPDATE rounds SET committed=1 WHERE run_id=? AND round_no=?", (run_id, p["round"])
            )
        elif kind == "observation":
            self._put(
                "observations",
                {**base, "round_no": p["round"], "firm_id": p["agent_id"], "data_json": data},
                ("run_id", "round_no", "firm_id"),
            )
        elif kind.startswith("model_call_"):
            key = (run_id, p["call_id"])
            old = self.connection.execute(
                "SELECT data_json FROM requests WHERE run_id=? AND call_id=?", key
            ).fetchone()
            combined = {**(json.loads(old[0]) if old else {}), **p}
            usage = p if kind == "model_call_completed" else p.get("client_evidence", {})
            known = all(type(usage.get(k)) is int for k in ("input_tokens", "output_tokens"))
            self._put(
                "requests",
                {
                    **base,
                    "call_id": p["call_id"],
                    "round_no": combined.get("round"),
                    "firm_id": combined.get("agent_id"),
                    "model_id": model,
                    "reported_model": usage.get("reported_model"),
                    "status": kind.removeprefix("model_call_"),
                    "input_tokens": usage.get("input_tokens") if known else None,
                    "output_tokens": usage.get("output_tokens") if known else None,
                    "usage_status": "known" if known else "unknown",
                    "elapsed_seconds": p.get("elapsed_seconds"),
                    "data_json": canonical(safe_data(combined)),
                },
                ("run_id", "call_id"),
            )
        elif kind == "decision":
            self._put(
                "decisions",
                {
                    **base,
                    "round_no": p["round"],
                    "firm_id": p["agent_id"],
                    "price": p["price"],
                    "data_json": data,
                },
                ("run_id", "round_no", "firm_id"),
            )
        elif kind == "audit":
            self._put(
                "audit_events",
                {
                    **base,
                    "audit_id": p["event_id"],
                    "round_no": p["source_round"],
                    "flagged": int(p["flagged"]),
                    "data_json": data,
                },
                ("run_id", "audit_id"),
            )
        elif kind == "penalty_assessment":
            self._put(
                "penalties",
                {
                    **base,
                    "round_no": p["round"],
                    "firm_id": p["agent_id"],
                    "severity": p["severity"],
                    "rate": p["rate"],
                    "amount": p["deduction"],
                    "data_json": data,
                },
                ("run_id", "round_no", "firm_id"),
            )
        elif kind == "delivery":
            self._put(
                "notices",
                {
                    **base,
                    "notice_id": p["delivery_id"],
                    "source_kind": p.get("source_kind", "legacy"),
                    "source_round": p["source_round"],
                    "rendered_text": p["text"],
                    "data_json": data,
                },
                ("run_id", "notice_id"),
            )
            self._put(
                "deliveries",
                {
                    **base,
                    "delivery_id": p["delivery_id"],
                    "firm_id": p["recipient"],
                    "effective_round": p["effective_round"],
                    "status": p["status"],
                    "consumed_call_id": None,
                    "intervention_id": p.get("intervention_id"),
                    "data_json": data,
                },
                ("run_id", "delivery_id"),
            )
        elif kind == "delivery_consumed":
            self.connection.execute(
                "UPDATE deliveries SET consumed_call_id=? WHERE run_id=? AND delivery_id=?",
                (p["call_id"], run_id, p["delivery_id"]),
            )
        elif kind in {"manual_control", "manual_intervention"}:
            intervention = p.get("intervention", p.get("preview", p))
            draft = intervention.get("draft") or p.get("draft") or {}
            self._put(
                "interventions",
                {
                    **base,
                    "intervention_id": intervention.get("intervention_id", p.get("command_id")),
                    "source_round": p["source_round"],
                    "status": p.get("status", "applied"),
                    "original_text": draft.get("text"),
                    "data_json": data,
                },
                ("run_id", "intervention_id"),
            )

    def query(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Browse through a separate read-only connection with a bounded result size."""
        if not sql.lstrip().lower().startswith(("select", "with")):
            raise ValueError("Research queries must be read-only SELECT statements")
        with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as reader:
            reader.row_factory = sqlite3.Row
            return [dict(row) for row in reader.execute(sql, parameters).fetchmany(1000)]

    def ingest_analysis(self, summary_path: Path) -> dict[str, Any]:
        """Index a sealed derived analysis without counting attempts as extra replicates."""
        summary_path = summary_path.resolve()
        if not summary_path.is_relative_to(self.root):
            raise ValueError("Analysis escaped the project")
        summary = json.loads(summary_path.read_text(encoding="utf8"))
        if summary.get("source_sweep"):
            source = self.root / summary["source_sweep"]
            if file_hash(source) != summary["source_sweep_sha256"]:
                raise IndexIntegrityError("Analysis sweep bytes changed")
            self.ingest_sweep(source)
        with summary_path.with_name("runs.csv").open(encoding="utf8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            if row.get("manifest_path"):
                self.ingest_run(self.root / row["manifest_path"])
        identity = summary_path.parent.relative_to(self.root).as_posix()
        settings_hash = fingerprint(summary)
        with self.connection:
            old = self.connection.execute(
                "SELECT settings_hash FROM analyses WHERE analysis_id=?", (identity,)
            ).fetchone()
            if old and old[0] != settings_hash:
                raise IndexIntegrityError("Derived analysis identity changed")
            self._put(
                "analyses",
                {
                    "analysis_id": identity,
                    "plan_id": None,
                    "settings_hash": settings_hash,
                    "data_json": canonical(safe_data(summary)),
                },
                ("analysis_id",),
            )
            for row in rows:
                key = (
                    (
                        (self.root / row["manifest_path"])
                        .resolve()
                        .parent.relative_to(self.root)
                        .as_posix()
                    )
                    if row.get("manifest_path")
                    else None
                )
                assignment = (
                    self.connection.execute(
                        "SELECT assignment_id FROM runs WHERE run_id=?", (key,)
                    ).fetchone()[0]
                    if key
                    else fingerprint(
                        [fingerprint([summary["sweep_id"], row["replicate_id"]]), row["arm"]]
                    )
                )
                if not self.connection.execute(
                    "SELECT 1 FROM assignments WHERE assignment_id=?", (assignment,)
                ).fetchone():
                    raise ValueError("Import the frozen sweep assignments before this analysis")
                included = row["included"].lower() == "true"
                self._put(
                    "run_outcomes",
                    {
                        "analysis_id": identity,
                        "assignment_id": assignment,
                        "run_id": key,
                        "metric": "primary_outcome_value",
                        "value": float(row["primary_outcome_value"]) if included else None,
                        "included": int(included),
                        "data_json": canonical(safe_data(row)),
                    },
                    ("analysis_id", "assignment_id", "metric"),
                )
            for name, value in summary.get("central_family", {}).get("comparisons", {}).items():
                self._put(
                    "contrasts",
                    {"analysis_id": identity, "contrast_id": name, "data_json": canonical(value)},
                    ("analysis_id", "contrast_id"),
                )
            for path in sorted(summary_path.parent.iterdir()):
                if path.is_file():
                    self._put(
                        "artifacts",
                        {
                            "artifact_id": fingerprint([identity, path.name]),
                            "run_id": None,
                            "relative_path": path.relative_to(self.root).as_posix(),
                            "sha256": file_hash(path),
                            "role": "derived_analysis",
                            "verified": 1,
                        },
                        ("artifact_id",),
                    )
        return {"analysis_id": identity, "indexed_rows": len(rows)}

    def ingest_sweep(self, path: Path) -> dict[str, Any]:
        """Keep every frozen assignment, including conditions that never started."""
        path = path.resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Sweep escaped the project")
        sweep = json.loads(path.read_text(encoding="utf8"))
        indexed = []
        for row in sweep["runs"]:
            if row.get("manifest_path"):
                indexed.append(self.ingest_run(self.root / row["manifest_path"]))
        cfg = sweep["base_config"]
        protocol = self.root / cfg["protocol"]
        protocol_hash = cfg.get("protocol_sha256") or file_hash(protocol)
        study = sweep["study_id"] + "@" + protocol_hash[:12]
        plan = sweep["sweep_id"]
        comparable = {
            k: v for k, v in cfg.items() if k not in {"arm", "seed", "run_kind", "study_id"}
        }
        design = fingerprint(comparable)
        with self.connection:
            old = self.connection.execute(
                "SELECT design_hash FROM plans WHERE plan_id=?", (plan,)
            ).fetchone()
            if old and old[0] != design:
                raise IndexIntegrityError("Sweep design conflicts with indexed runs")
            self._put(
                "studies",
                {
                    "study_id": study,
                    "protocol_hash": protocol_hash,
                    "data_json": canonical(
                        safe_data(
                            {
                                "recorded_study_id": sweep["study_id"],
                                "protocol": cfg["protocol"],
                                "current_protocol_text": protocol.read_text(encoding="utf8"),
                                "text_status": (
                                    "current_bytes_match"
                                    if file_hash(protocol) == protocol_hash
                                    else "current_bytes_differ"
                                ),
                            }
                        )
                    ),
                },
                ("study_id",),
            )
            self._put(
                "plans",
                {
                    "plan_id": plan,
                    "study_id": study,
                    "design_hash": design,
                    "data_json": canonical(safe_data(sweep)),
                },
                ("plan_id",),
            )
            assignment_path = path.with_name("assignment.json")
            assignments = json.loads(assignment_path.read_text(encoding="utf8"))
            for slot, row in enumerate(assignments):
                block = fingerprint([plan, row["replicate_id"]])
                identity = fingerprint([block, row["arm"]])
                self._put(
                    "blocks",
                    {
                        "block_id": block,
                        "plan_id": plan,
                        "replicate_id": row["replicate_id"],
                        "seed": row["seed"],
                    },
                    ("block_id",),
                )
                self._put(
                    "assignments",
                    {
                        "assignment_id": identity,
                        "block_id": block,
                        "arm": row["arm"],
                        "data_json": canonical(safe_data({**row, "execution_slot": slot})),
                    },
                    ("assignment_id",),
                )
        return {"sweep_id": plan, "assignments": len(assignments), "indexed_attempts": len(indexed)}

    def check(self) -> dict[str, Any]:
        """Check SQLite integrity and relational consistency without model calls."""
        integrity = [r[0] for r in self.connection.execute("PRAGMA integrity_check")]
        foreign = [tuple(r) for r in self.connection.execute("PRAGMA foreign_key_check")]
        return {
            "valid": integrity == ["ok"] and not foreign,
            "integrity": integrity,
            "foreign_key_errors": foreign,
        }

    def backup(self, destination: Path) -> None:
        """Create a consistent new backup through SQLite's backup API."""
        destination = destination.resolve()
        if not destination.is_relative_to(self.root) or destination.exists():
            raise ValueError("Choose a new backup inside the project")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(destination) as target:
            self.connection.backup(target)

    def export(self, sql: str, destination: Path) -> int:
        """Write a new deterministic CSV from a read-only ordered query."""
        destination = destination.resolve()
        if not destination.is_relative_to(self.root):
            raise ValueError("Export must stay inside the project")
        rows = self.query(sql)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf8", newline="") as stream:
            if rows:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        return len(rows)
