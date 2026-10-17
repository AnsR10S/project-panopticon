"""Save the run evidence with exclusive folders, hashed events and atomic manifests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from threading import RLock
from typing import Any, TextIO

from auditors_paradox.contracts import canonical, fingerprint
from auditors_paradox.leases import RunLease


def utc_now() -> str:
    """Current time as a UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def file_hash(path: Path) -> str:
    """SHA-256 hash of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_revision(root: Path) -> str | None:
    """Record Git HEAD only for this checkout; source snapshots still identify dirty bytes."""
    try:
        top = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if Path(top).resolve() != root.resolve():
            return None
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        return None


def atomic_json(path: Path, value: Any) -> None:
    """Write and sync the JSON, then swap it in atomically so no file is half written."""
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    for attempt in range(6):
        try:
            temp.replace(path)
            break
        except PermissionError as error:
            if getattr(error, "winerror", None) not in {5, 32, 33} or attempt == 5:
                raise
            time.sleep(0.01 * 2**attempt)


def source_snapshot(root: Path, destination: Path) -> str:
    """Archive the study source, prompts, research docs and dependency locks for this run."""
    paths = [
        p
        for folder in ("src/auditors_paradox", "prompts", "docs/research")
        for p in (root / folder).rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix in {".py", ".txt", ".md", ".json", ".mjs", ".sql"}
    ]
    paths += [p for p in (root / "scripts").glob("*.py")]
    paths += [
        root / name
        for name in (
            "pyproject.toml",
            "uv.lock",
            "external/collusion-lab.lock.json",
            "tools/qwen-code/package.json",
            "tools/qwen-code/package-lock.json",
            "tools/qwen-code/qualification.json",
        )
        if (root / name).is_file()
    ]
    hashes = {p.relative_to(root).as_posix(): file_hash(p) for p in sorted(set(paths))}
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in hashes:
            archive.writestr(relative, (root / relative).read_bytes())
        archive.writestr("snapshot-hashes.json", canonical(hashes))
    return fingerprint(hashes)


class EvidenceWriter:
    """Keeps one event stream and one manifest inside a new run folder."""

    def __init__(self, directory: Path, manifest: dict[str, Any]) -> None:
        """Create the run folder exclusively and save the starting running status."""
        directory.mkdir(parents=True, exist_ok=False)
        self.directory = directory
        self.lease = RunLease(directory)
        self.manifest = {
            **manifest,
            "schema_version": 1,
            "artifact_kind": "run",
            "status": "running",
            "started_at": utc_now(),
            "completed_rounds": 0,
        }
        self.sequence = 0
        self.previous_hash = "0" * 64
        self.lock = RLock()
        self.append_seconds: list[float] = []
        self.stream: TextIO = (directory / "events.jsonl").open("x", encoding="utf-8", newline="\n")
        atomic_json(directory / "manifest.json", self.manifest)

    def append(self, kind: str, payload: dict[str, Any], *, durable: bool = False) -> str:
        """Append a hash linked event, and on round commits sync to disk and update progress."""
        started = time.perf_counter()
        with self.lock:
            result = self._append(kind, payload)
            if durable:
                os.fsync(self.stream.fileno())
            if self.manifest.get("config", {}).get("schema_version") == 2:
                self.append_seconds.append(time.perf_counter() - started)
            return result

    def _append(self, kind: str, payload: dict[str, Any]) -> str:
        """Write one complete event while holding the shared writer lock."""
        record = {
            "schema_version": 1,
            "sequence": self.sequence + 1,
            "type": kind,
            "payload": payload,
            "previous_hash": self.previous_hash,
        }
        record_hash = fingerprint(record)
        self.stream.write(canonical({**record, "record_hash": record_hash}) + "\n")
        self.stream.flush()
        if kind == "round_committed":
            os.fsync(self.stream.fileno())
            self.manifest["completed_rounds"] = payload["round"]
            atomic_json(self.directory / "manifest.json", self.manifest)
        self.sequence += 1
        self.previous_hash = record_hash
        return record_hash

    def finish(self, status: str, error: dict[str, str] | None = None) -> Path:
        """Close the event stream, save the checksums and record the final run status."""
        try:
            self.stream.close()
            if self.append_seconds:
                samples = sorted(self.append_seconds)
                self.manifest["recording_profile"] = {
                    "event_appends": len(samples),
                    "median_seconds": median(samples),
                    "p95_seconds": samples[min(len(samples) - 1, int(0.95 * len(samples)))],
                    "scope": "raw append including lock/flush; fsync on durable events; excludes source snapshot and final sealing",
                }
            checksums = {
                p.name: file_hash(p)
                for p in sorted(self.directory.iterdir())
                if p.is_file() and p.name not in {"manifest.json", "checksums.json"}
            }
            atomic_json(self.directory / "checksums.json", checksums)
            self.manifest.update(
                {
                    "status": status,
                    "ended_at": utc_now(),
                    "error": error,
                    "event_count": self.sequence,
                    "last_event_hash": self.previous_hash,
                    "checksums_sha256": file_hash(self.directory / "checksums.json"),
                }
            )
            atomic_json(self.directory / "manifest.json", self.manifest)
            return self.directory / "manifest.json"
        finally:
            self.lease.close()
