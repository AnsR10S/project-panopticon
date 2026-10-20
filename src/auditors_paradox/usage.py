"""Measure local requests and estimate a separately priced API workload."""

from __future__ import annotations

import importlib
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any

from auditors_paradox.evidence import atomic_json


def run_paths(path: Path, root: Path) -> list[Path]:
    """Follow a run, sweep or matrix without counting the same run twice."""
    path = path / "manifest.json" if path.is_dir() else path
    value = json.loads(path.read_text(encoding="utf-8"))
    kind = value.get("artifact_kind", "run")
    if kind == "run":
        return [path]
    field = "manifest_path" if kind == "sweep" else "sweep_manifest"
    entries = value["runs" if kind == "sweep" else "cells"]
    result = []
    for entry in entries:
        relative = entry.get(field)
        if relative:
            target = (root / relative).resolve()
            if not target.is_relative_to(root.resolve()):
                raise ValueError("Usage input escaped the project")
            result.extend(run_paths(target, root))
    return sorted(set(result))


def profile_usage(
    path: Path,
    root: Path,
    *,
    planned_runs: int = 1,
    planned_rounds: int = 100,
    input_rate: float | None = None,
    output_rate: float | None = None,
    input_factor: float = 1,
    output_factor: float = 1,
    safety_factor: float = 1.25,
    target_encoding: str | None = None,
    output: Path | None = None,
) -> dict[str, Any]:
    """Keep measured tokens separate from assumptions about another model's bill."""
    numbers = [input_factor, output_factor, safety_factor]
    if (
        planned_runs < 1
        or planned_rounds < 1
        or any(not math.isfinite(v) or v <= 0 for v in numbers)
        or ((input_rate is None) != (output_rate is None))
        or any(v is not None and (not math.isfinite(v) or v < 0) for v in (input_rate, output_rate))
    ):
        raise ValueError("Use positive workload/factors and both nonnegative token rates")
    encoding = None
    if target_encoding:
        try:
            encoding = importlib.import_module("tiktoken").get_encoding(target_encoding)
        except ImportError:
            raise ValueError("Target tokenization needs: uv sync --frozen --all-extras") from None
    runs = run_paths(path, root)
    calls: list[dict[str, Any]] = []
    failed = decisions = rejected = fallback = 0
    statuses: dict[str, int] = {}
    measured_rounds = 0
    for manifest_path in runs:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        statuses[manifest["status"]] = statuses.get(manifest["status"], 0) + 1
        records = [
            json.loads(line)
            for line in manifest_path.with_name("events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        for row in records:
            payload = row["payload"]
            if row["type"] == "model_call_completed":
                calls.append(
                    {
                        **payload,
                        "backend": manifest["config"]["agent"]["backend"],
                        "arm": manifest["config"]["arm"],
                        "data_class": manifest["data_class"],
                    }
                )
            elif row["type"] == "model_call_failed":
                failed += 1
            elif row["type"] == "decision":
                decisions += 1
                fallback += int(payload["fallback"])
            elif row["type"] == "decision_rejected":
                rejected += 1
            elif row["type"] == "round_committed":
                measured_rounds += 1
    if not calls:
        raise ValueError("No successful model calls were recorded; inspect failed events first")
    input_counts = [int(c["input_tokens"]) for c in calls]
    output_counts = [int(c["output_tokens"]) for c in calls]
    target_input, target_output = [], []
    if encoding:
        for call in calls:
            target_input.append(
                sum(
                    len(encoding.encode(m["content"], disallowed_special=()))
                    for m in call["messages"]
                )
            )
            target_output.append(len(encoding.encode(call["response"], disallowed_special=())))
    decisions_performed = decisions + rejected
    correction_factor = max(1.0, len(calls) / max(1, decisions_performed))
    planned_calls = math.ceil(3 * planned_rounds * planned_runs * correction_factor)
    mean_input = mean(target_input or input_counts)
    mean_output = mean(target_output or output_counts)
    result: dict[str, Any] = {
        "schema_version": 1,
        "statuses": statuses,
        "runs_profiled": len(runs),
        "committed_rounds": measured_rounds,
        "completed_requests": len(calls),
        "failed_requests_with_unknown_billing": failed,
        "accepted_decisions": decisions,
        "rejected_decisions": rejected,
        "fallback_decisions": fallback,
        "backends": sorted({c["backend"] for c in calls}),
        "reported_models": sorted({c["reported_model"] for c in calls}),
        "data_classes": sorted({c["data_class"] for c in calls}),
        "per_arm": {
            arm: {
                "requests": sum(c["arm"] == arm for c in calls),
                "input_tokens": sum(c["input_tokens"] for c in calls if c["arm"] == arm),
                "output_tokens": sum(c["output_tokens"] for c in calls if c["arm"] == arm),
            }
            for arm in sorted({c["arm"] for c in calls})
        },
        "input_tokens": {
            "total": sum(input_counts),
            "mean": mean(input_counts),
            "max": max(input_counts),
        },
        "output_tokens": {
            "total": sum(output_counts),
            "mean": mean(output_counts),
            "max": max(output_counts),
        },
        "mean_request_seconds": mean(c["elapsed_seconds"] for c in calls),
        "tokenization_basis": target_encoding or "source_model_reported_usage",
        "target_visible_text_input_mean": mean(target_input) if target_input else None,
        "target_visible_text_output_mean": mean(target_output) if target_output else None,
        "projection": {
            "planned_runs": planned_runs,
            "planned_rounds": planned_rounds,
            "estimated_successful_requests": planned_calls,
            "observed_correction_factor": correction_factor,
            "input_token_factor": input_factor,
            "output_token_factor": output_factor,
            "safety_factor": safety_factor,
            "input_usd_per_million": input_rate,
            "output_usd_per_million": output_rate,
            "estimated_usd": (
                planned_calls
                * (
                    mean_input * input_factor * float(input_rate)
                    + mean_output * output_factor * float(output_rate or 0)
                )
                / 1e6
                * safety_factor
                if input_rate is not None
                else None
            ),
        },
        "limitations": [
            "Scripted counts are synthetic; use a real local-model profile for API planning.",
            "Forecast only: target behavior, reasoning tokens, tokenizer and chat wrappers can differ.",
            "Short histories understate longer-run input costs; profile at the intended memory window.",
            "Failed requests may be billed; their unreported usage is not treated as known zero.",
            "Visible-text retokenization excludes target-specific wrappers, hidden reasoning and billing rules.",
            "Use actual AgentRouter account rates and a small later API calibration before a larger batch.",
        ],
    }
    if output:
        target = output.resolve()
        if not target.is_relative_to(root.resolve()) or target.exists():
            raise ValueError("Usage output must be a new file inside the project")
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(target, result)
    return result
