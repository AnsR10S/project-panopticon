"""Rebuild the repair evidence summary from saved runs, without making model requests."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from auditors_paradox.config import MarketConfig, project_root
from auditors_paradox.contracts import fingerprint
from auditors_paradox.economics import rewards
from auditors_paradox.evaluation.inference import analyze_sweep
from auditors_paradox.evaluation.metrics import market_trajectory, run_metrics
from auditors_paradox.evidence import atomic_json, file_hash
from auditors_paradox.usage import profile_usage

SWEEPS = {
    "original": ("local-calvano-core-qwen35", "qwen35-four-arm-20261022-010023"),
    "natural": ("repair-natural-20261022", "repair-natural-r1"),
    "neutral": ("repair-neutral-20261022", "repair-neutral-r1"),
    "explicit": ("repair-explicit-20261022", "repair-explicit-r1"),
}


def read(path: Path) -> Any:
    """Read one preserved JSON artifact."""
    return json.loads(path.read_text(encoding="utf-8"))


def checked_probes(path: Path) -> Any:
    """Check probe files and the event chain before trusting the exported answers."""
    manifest = read(path / "manifest.json")
    if (
        manifest["status"] != "complete"
        or file_hash(path / "checksums.json") != manifest["checksums_sha256"]
    ):
        raise ValueError("Incomplete or changed probe manifest")
    for name, digest in read(path / "checksums.json").items():
        if Path(name).name != name or file_hash(path / name) != digest:
            raise ValueError("Changed diagnostic artifact")
    previous = "0" * 64
    events = [json.loads(line) for line in (path / "events.jsonl").read_text().splitlines()]
    for sequence, event in enumerate(events, 1):
        if (
            event["sequence"] != sequence
            or event["previous_hash"] != previous
            or event["record_hash"]
            != fingerprint({k: v for k, v in event.items() if k != "record_hash"})
        ):
            raise ValueError("Changed diagnostic event chain")
        previous = event["record_hash"]
    results = read(path / "results.json")
    if (
        previous != manifest["last_event_hash"]
        or len(events) != manifest["event_count"]
        or results != [e["payload"] for e in events if e["type"] == "probe_result"]
    ):
        raise ValueError("Diagnostic results disagree with preserved events")
    return results


def main() -> None:
    """Revalidate the saved studies and keep a separate report and plots for each design."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = project_root()
    output = args.output.resolve()
    if not output.is_relative_to(root) or output.exists():
        raise ValueError("Use a new output directory inside this project")
    output.mkdir(parents=True)
    baseline = read(root / "runs/diagnostics/repair-baseline-20261022/baseline.json")
    changed = [p for p, digest in baseline["raw_sha256"].items() if file_hash(root / p) != digest]
    if changed:
        raise ValueError(f"Historical evidence changed: {changed}")
    result: dict[str, Any] = {
        "historical_files_checked": len(baseline["raw_sha256"]),
        "historical_files_changed": changed,
        "historical_sha256": baseline["raw_sha256"],
        "studies": {},
        "auditor_cases": baseline["auditor_cases"],
        "terminal_notice_check": baseline["terminal_notice_check"],
        "cost_rates_status": "Illustrative input $3/M, output $15/M, not verified current provider rates",
    }
    for name, (study, identity) in SWEEPS.items():
        relative = Path("runs/pilot") / study / "sweeps" / identity / "manifest.json"
        sweep = read(root / relative)
        target = output / name
        analysis = read(analyze_sweep(root / relative, root, target))
        usage = profile_usage(
            root / relative,
            root,
            planned_runs=4,
            planned_rounds=100,
            input_rate=3,
            output_rate=15,
            output=target / "usage.json",
        )
        runs = []
        for assignment in sweep["runs"]:
            path = root / assignment["manifest_path"]
            manifest = read(path)
            trajectory = market_trajectory(path)
            config = manifest["config"]
            benchmark = manifest["benchmarks"]
            reasons = []
            for window_end in range(config["auditor"]["window"], len(trajectory) + 1):
                window = trajectory[window_end - config["auditor"]["window"] : window_end]
                profit_ok = all(
                    (sum(r["gross_rewards"]) / 3 - benchmark["nash_reward"])
                    / (benchmark["joint_reward"] - benchmark["nash_reward"])
                    > config["auditor"]["elevation_threshold"]
                    for r in window
                )
                spread_ok = all(
                    max(r["prices"]) - min(r["prices"]) <= config["auditor"]["maximum_spread"]
                    for r in window
                )
                reasons.append(
                    {"end_round": window_end, "profit_ok": profit_ok, "spread_ok": spread_ok}
                )
            runs.append(
                {
                    "manifest": Path(assignment["manifest_path"]).as_posix(),
                    "manifest_sha256": file_hash(path),
                    "arm": assignment["arm"],
                    "metrics": run_metrics(path),
                    "prices": [r["prices"] for r in trajectory] if name != "original" else None,
                    "window_checks": reasons,
                    "elapsed_seconds": (read_elapsed(manifest)),
                }
            )
        result["studies"][name] = {
            "sweep": relative.as_posix(),
            "sweep_sha256": file_hash(root / relative),
            "config": sweep["base_config"],
            "analysis": analysis,
            "usage": usage,
            "runs": runs,
        }
    probes = checked_probes(root / "runs/diagnostics/repair-probes-20261022/probes")
    comprehension = checked_probes(root / "runs/diagnostics/repair-comprehension-20261022/probes")
    probe_cfg = MarketConfig.model_validate(result["studies"]["original"]["config"]["market"])
    for probe in probes:
        if probe["kind"] != "competence":
            continue
        rival = int(probe["id"].split("-")[1])
        chosen = probe["parsed_price"]
        if chosen is None:
            match = re.match(r"^(\d+)\b", probe["response"])
            chosen = int(match.group(1)) if match else None
        best = rewards(probe_cfg, [probe["expected_price"], rival, rival])[0]
        chosen_profit = (
            rewards(probe_cfg, [chosen, rival, rival])[0] if chosen is not None else None
        )
        probe.update(
            economic_choice=chosen,
            economic_regret=None if chosen_profit is None else best - chosen_profit,
            maximum_candidate_profit=best,
        )
    result["standalone_probes"] = {"first": probes, "comprehension": comprehension}
    atomic_json(output / "evidence-summary.json", result)
    print(output / "evidence-summary.json")


def read_elapsed(manifest: dict[str, Any]) -> float:
    """Use recorded start and end times, including provider waiting time."""
    from datetime import datetime

    return (
        datetime.fromisoformat(manifest["ended_at"])
        - datetime.fromisoformat(manifest["started_at"])
    ).total_seconds()


if __name__ == "__main__":
    main()
