"""Recheck the saved 400- and 50-round Qwen batches and export detailed report data."""

from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any

from auditors_paradox.evaluation.inference import analyze_sweep
from auditors_paradox.evaluation.metrics import run_metrics
from auditors_paradox.evaluation.validate import parsed_price, validate_run
from auditors_paradox.evidence import atomic_json, file_hash

ARMS = ("silent", "private", "public", "placebo")
STUDIES = {
    "original": ("400", "local-calvano-core-qwen35", "qwen35-four-arm-20261022-010023"),
    "natural": ("50", "repair-natural-20261022", "repair-natural-r1"),
    "neutral": ("50", "repair-neutral-20261022", "repair-neutral-r1"),
}


def read(path: Path) -> Any:
    """Read a saved artifact without editing it."""
    return json.loads(path.read_text(encoding="utf-8"))


def export_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write a flat derived table with one record per row and no rounded source values."""
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def cairo_time(value: str) -> str:
    """Use the recorded Cairo offset for the October 22 experiment date."""
    return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=3))).isoformat()


def equation_profit(prices: list[int], market: dict[str, Any]) -> list[float]:
    """Independently reproduce the saved Calvano quantities and currency profit."""
    values = [math.exp((market["a"] - p) / market["mu"]) for p in prices]
    denominator = math.exp(market["a_0"] / market["mu"]) + sum(values)
    return [
        (p - market["c"]) / market["price_scale"] * q / denominator for p, q in zip(prices, values)
    ]


def run_data(root: Path, path: Path, phase: str, batch: str, order: int) -> dict[str, Any]:
    """Revalidate a trajectory and reconstruct round, call, decision and notice tables."""
    validation = validate_run(path)
    if not validation["valid"]:
        raise ValueError(f"Invalid source run {path.name}: {validation['errors']}")
    manifest = read(path)
    cfg, benchmark = manifest["config"], manifest["benchmarks"]
    records = [
        json.loads(line)
        for line in path.with_name("events.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    def group(kind: str) -> list[dict[str, Any]]:
        """Select one recorded event type."""
        return [r for r in records if r["type"] == kind]

    calls = {r["payload"]["call_id"]: r for r in group("model_call_completed")}
    starts = {r["payload"]["call_id"]: r["payload"] for r in group("model_call_started")}
    decisions = [r["payload"] for r in group("decision")]
    consumed = [r["payload"] for r in group("delivery_consumed")]
    audits = {r["payload"]["source_round"]: r for r in group("audit")}
    neutral = cfg["arm"] == "placebo" or cfg["feedback"]["warning_source"] == "fixed"
    base = {
        "batch_round_count": batch,
        "study_component": phase,
        "run_id": manifest["run_id"],
        "arm": cfg["arm"],
        "replicate_id": manifest["replicate_id"],
    }
    round_rows, call_rows, decision_rows, notice_rows, windows = [], [], [], [], []
    for record in group("round"):
        value, number = record["payload"], record["payload"]["round"]
        expected = equation_profit(value["prices"], cfg["market"])
        if any(
            not math.isclose(a, b, abs_tol=1e-12) for a, b in zip(expected, value["gross_rewards"])
        ):
            raise ValueError("Independent profit recomputation failed")
        current_calls = [r["payload"] for r in calls.values() if r["payload"]["round"] == number]
        current_decisions = [d for d in decisions if d["round"] == number]
        current_consumption = [d for d in consumed if d["round"] == number]
        gross = mean(value["gross_rewards"])
        row = {
            **base,
            "round": number,
            **{f"price_firm_{i}_ticks": p for i, p in enumerate(value["prices"])},
            "mean_price_ticks": mean(value["prices"]),
            "price_spread_ticks": max(value["prices"]) - min(value["prices"]),
            "price_sd_ticks": pstdev(value["prices"]),
            **{f"quantity_firm_{i}": q for i, q in enumerate(value["quantities"])},
            "outside_option_share": 1 - sum(value["quantities"]),
            **{f"gross_profit_firm_{i}_currency": p for i, p in enumerate(value["gross_rewards"])},
            **{f"net_profit_firm_{i}_currency": p for i, p in enumerate(value["net_rewards"])},
            "mean_gross_profit_currency": gross,
            "total_gross_profit_currency": sum(value["gross_rewards"]),
            "gross_profit_elevation": (gross - benchmark["nash_reward"])
            / (benchmark["joint_reward"] - benchmark["nash_reward"]),
            "price_elevation": (mean(value["prices"]) - benchmark["nash_price"])
            / (benchmark["joint_price"] - benchmark["nash_price"]),
            "total_penalties_currency": sum(value["penalties"]),
            "auditor_triggered_after_round": audits[number]["payload"]["flagged"],
            "recipient_notices_consumed_before_decision": len(current_consumption),
            "real_warnings_consumed": 0 if neutral else len(current_consumption),
            "neutral_notices_consumed": len(current_consumption) if neutral else 0,
            "model_requests": len(current_calls),
            "format_correction_requests": sum(len(d["call_ids"]) - 1 for d in current_decisions),
            "length_limited_responses": sum(
                c["usage_metadata"].get("done_reason") == "length" for c in current_calls
            ),
            "input_tokens": sum(c["input_tokens"] for c in current_calls),
            "output_tokens": sum(c["output_tokens"] for c in current_calls),
            "source_event_sequence": record["sequence"],
            "source_events_file": path.with_name("events.jsonl").relative_to(root).as_posix(),
        }
        round_rows.append(row)
        size = cfg["auditor"]["window"]
        if number >= size:
            window = round_rows[-size:]
            windows.append(
                {
                    **base,
                    "window_end_round": number,
                    "window_start_round": number - size + 1,
                    "minimum_round_profit_elevation": min(
                        r["gross_profit_elevation"] for r in window
                    ),
                    "maximum_round_spread_ticks": max(r["price_spread_ticks"] for r in window),
                    "every_profit_condition_passed": all(
                        r["gross_profit_elevation"] > cfg["auditor"]["elevation_threshold"]
                        for r in window
                    ),
                    "every_spread_condition_passed": all(
                        r["price_spread_ticks"] <= cfg["auditor"]["maximum_spread"] for r in window
                    ),
                    "triggered": row["auditor_triggered_after_round"],
                }
            )
    for call in calls.values():
        p = call["payload"]
        call_rows.append(
            {
                **base,
                "round": p["round"],
                "agent_id": p["agent_id"],
                "call_id": p["call_id"],
                "input_tokens": p["input_tokens"],
                "output_tokens": p["output_tokens"],
                "elapsed_seconds": p["elapsed_seconds"],
                "done_reason": p["usage_metadata"].get("done_reason"),
                "valid_price_in_reply": parsed_price(
                    p["response"], cfg["market"]["price_min"], cfg["market"]["price_max"]
                ),
                "visible_reply_characters": len(p["response"]),
                "reported_model": p["reported_model"],
                "source_event_sequence": call["sequence"],
                "started_at_utc": starts[p["call_id"]]["started_at"],
            }
        )
    for d in decisions:
        last = calls[d["call_ids"][-1]]
        decision_rows.append(
            {
                **base,
                "round": d["round"],
                "agent_id": d["agent_id"],
                "accepted_price_ticks": d["price"],
                "model_requests": len(d["call_ids"]),
                "format_correction_requests": len(d["call_ids"]) - 1,
                "fallback": d["fallback"],
                "final_call_id": d["call_ids"][-1],
                "final_response_event_sequence": last["sequence"],
                "input_tokens": sum(calls[c]["payload"]["input_tokens"] for c in d["call_ids"]),
                "output_tokens": sum(calls[c]["payload"]["output_tokens"] for c in d["call_ids"]),
                "source_events_file": path.with_name("events.jsonl").relative_to(root).as_posix(),
            }
        )
    for item in group("delivery"):
        d = item["payload"]
        if d["status"] != "scheduled_for_next_round":
            continue
        notice_rows.append(
            {
                **base,
                "delivery_id": d["delivery_id"],
                "recipient": d["recipient"],
                "source_round": d["source_round"],
                "effective_round": d["effective_round"],
                "kind": "neutral" if neutral else "warning",
                "consumed": any(c["delivery_id"] == d["delivery_id"] for c in consumed),
                "body": d["body"],
                "header": d["header"],
                "source_event_sequence": item["sequence"],
            }
        )
    metrics = run_metrics(path)
    trajectory = round_rows
    first_notice = min((d["round"] for d in consumed), default=None)
    first_deviation = min(
        (
            r["round"]
            for r in trajectory
            if any(r[f"price_firm_{i}_ticks"] != benchmark["nash_price"] for i in range(3))
        ),
        default=None,
    )
    settle_round = next(
        (
            r["round"]
            for i, r in enumerate(trajectory)
            if all(
                tuple(x[f"price_firm_{j}_ticks"] for j in range(3))
                == tuple(trajectory[-1][f"price_firm_{j}_ticks"] for j in range(3))
                for x in trajectory[i:]
            )
        ),
        None,
    )
    summary = {
        **base,
        "execution_order": order,
        "status": manifest["status"],
        "valid": True,
        "started_at_cairo": cairo_time(manifest["started_at"]),
        "ended_at_cairo": cairo_time(manifest["ended_at"]),
        "elapsed_seconds": (
            datetime.fromisoformat(manifest["ended_at"])
            - datetime.fromisoformat(manifest["started_at"])
        ).total_seconds(),
        "rounds": len(trajectory),
        "decisions": len(decisions),
        **metrics,
        "whole_mean_price": mean(r["mean_price_ticks"] for r in trajectory),
        "whole_mean_gross_profit": mean(r["mean_gross_profit_currency"] for r in trajectory),
        "whole_profit_elevation": mean(r["gross_profit_elevation"] for r in trajectory),
        "whole_mean_price_sd": mean(r["price_sd_ticks"] for r in trajectory),
        "total_gross_profit_all_firms": sum(r["total_gross_profit_currency"] for r in trajectory),
        "first_deviation_round": first_deviation,
        "first_consumed_notice_round_or_none": first_notice,
        "terminal_profile_constant_from_round": settle_round,
        "terminal_profile": [trajectory[-1][f"price_firm_{i}_ticks"] for i in range(3)],
        "input_tokens": sum(c["input_tokens"] for c in call_rows),
        "output_tokens": sum(c["output_tokens"] for c in call_rows),
        "mean_request_seconds": mean(c["elapsed_seconds"] for c in call_rows),
        "median_request_seconds": median(c["elapsed_seconds"] for c in call_rows),
        "max_request_seconds": max(c["elapsed_seconds"] for c in call_rows),
        "max_prompt_tokens": max(c["input_tokens"] for c in call_rows),
        "number_corrected_decisions": sum(d["model_requests"] > 1 for d in decision_rows),
        "valid_first_reply_decisions": sum(
            parsed_price(calls[d["call_ids"][0]]["payload"]["response"], 100, 250) is not None
            for d in decisions
        ),
        "full_windows": len(windows),
        "profit_passing_windows": sum(w["every_profit_condition_passed"] for w in windows),
        "spread_passing_windows": sum(w["every_spread_condition_passed"] for w in windows),
        "profit_threshold_passing_rounds": sum(
            r["gross_profit_elevation"] > cfg["auditor"]["elevation_threshold"] for r in trajectory
        ),
        "maximum_round_profit_elevation": max(r["gross_profit_elevation"] for r in trajectory),
        "manifest": path.relative_to(root).as_posix(),
        "config_sha256": manifest["config_sha256"],
        "source_tree_sha256": manifest["source_tree_sha256"],
        "protocol_sha256": manifest["protocol_sha256"],
    }
    examples = []
    for number in sorted({1, first_deviation, first_notice} - {None}):
        for agent in range(3):
            d = next(x for x in decisions if x["round"] == number and x["agent_id"] == agent)
            p = calls[d["call_ids"][-1]]["payload"]
            examples.append(
                {
                    **base,
                    "round": number,
                    "agent_id": agent,
                    "call_id": p["call_id"],
                    "response": p["response"],
                    "accepted_price": d["price"],
                }
            )
    return {
        "summary": summary,
        "manifest": manifest,
        "rounds": round_rows,
        "calls": call_rows,
        "decisions": decision_rows,
        "notices": notice_rows,
        "windows": windows,
        "examples": examples,
    }


def figures(output: Path, studies: dict[str, Any]) -> None:
    """Show each firm's price separately and compare outcomes without pooling designs."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    palette = ["#1768ac", "#de7b28", "#26866a"]
    for phase, study in studies.items():
        figure, axes = plt.subplots(2, 2, figsize=(12, 8), squeeze=False)
        for axis, run in zip(axes.flat, study["runs"]):
            values = run["rounds"]
            for agent, color in enumerate(palette):
                axis.plot(
                    [v["round"] for v in values],
                    [v[f"price_firm_{agent}_ticks"] for v in values],
                    marker="." if phase != "original" else None,
                    color=color,
                    alpha=0.8,
                    label=f"Firm {agent}",
                )
            axis.axhline(137, color="#777", ls="--", lw=0.9, label="Competitive 137")
            axis.axhline(200, color="#444", ls=":", lw=0.9, label="Joint reference 200")
            for number in sorted({n["effective_round"] for n in run["notices"]}):
                axis.axvline(number, color="#b25288", alpha=0.4, lw=1)
            axis.set_title(run["summary"]["arm"].title())
            axis.set_ylim(95, 255)
            axis.set_xlabel("Decision round")
            axis.set_ylabel("Price in ticks")
        for axis in list(axes.flat)[len(study["runs"]) :]:
            axis.axis("off")
        axes[0, 0].legend(fontsize=8, ncol=2)
        figure.suptitle(
            f"{phase.title()} design: one trajectory per condition\nVertical purple lines mark neutral-notice consumption; no real warnings occurred"
        )
        figure.tight_layout()
        figure.savefig(output / f"{phase}-firm-prices.png", dpi=170)
        plt.close(figure)
    figure, axes = plt.subplots(1, 3, figsize=(14, 5), sharey=True)
    for axis, (phase, study) in zip(axes, studies.items()):
        summaries = [run["summary"] for run in study["runs"]]
        x = list(range(len(summaries)))
        axis.bar(
            [i - 0.18 for i in x],
            [s["whole_profit_elevation"] for s in summaries],
            width=0.36,
            label="Whole trajectory",
            color="#1768ac",
        )
        axis.bar(
            [i + 0.18 for i in x],
            [s["gross_profit_elevation"] for s in summaries],
            width=0.36,
            label="Configured tail",
            color="#de7b28",
        )
        axis.set_xticks(x, [s["arm"].title() for s in summaries], rotation=30)
        axis.axhline(0, color="#999", lw=0.7)
        axis.set_title(phase.title())
    axes[0].set_ylabel("Gross-profit elevation")
    axes[0].legend(fontsize=8)
    figure.suptitle(
        "Descriptive outcomes: different durations and tail windows; no uncertainty inferred from n=1"
    )
    figure.tight_layout()
    figure.savefig(output / "whole-versus-tail.png", dpi=170)
    plt.close(figure)
    figure, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    for run in studies["natural"]["runs"]:
        values = run["rounds"]
        label = run["summary"]["arm"].title()
        axes[0].plot(
            [v["round"] for v in values],
            [v["gross_profit_elevation"] for v in values],
            marker="o",
            label=label,
        )
        axes[1].plot(
            [v["round"] for v in values],
            [v["price_spread_ticks"] for v in values],
            marker="o",
            label=label,
        )
    axes[0].axhline(0.25, color="#9f3545", ls="--", label="Profit cutoff .25")
    axes[1].axhline(5, color="#9f3545", ls="--", label="Spread cutoff 5")
    axes[0].set_ylabel("Gross-profit elevation")
    axes[1].set_ylabel("Max minus min price (ticks)")
    axes[1].set_xlabel("Completed round; all three consecutive rounds must satisfy both criteria")
    for axis in axes:
        axis.legend(fontsize=8, ncol=3)
    figure.suptitle("Why the natural 32-round calibration never triggered the auditor")
    figure.tight_layout()
    figure.savefig(output / "natural-trigger-criteria.png", dpi=170)
    plt.close(figure)


def main() -> None:
    """Build new local outputs exclusively and confirm every source file remains unchanged."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root) or output.exists():
        raise ValueError("Use a new output directory inside this repository")
    sources: dict[str, str] = {}
    for _, study_id, _ in STUDIES.values():
        sources.update(
            {
                p.relative_to(root).as_posix(): file_hash(p)
                for p in (root / "runs/pilot" / study_id).rglob("*")
                if p.is_file()
            }
        )
    baseline = read(root / "runs/diagnostics/repair-baseline-20261022/baseline.json")
    if any(file_hash(root / p) != h for p, h in baseline["raw_sha256"].items()):
        raise ValueError("The original evidence differs from the pre-repair inventory")
    output.mkdir(parents=True)
    studies = {}
    for phase, (batch, study_id, identity) in STUDIES.items():
        sweep_path = root / "runs/pilot" / study_id / "sweeps" / identity / "manifest.json"
        sweep = read(sweep_path)
        analysis = read(analyze_sweep(sweep_path, root, output / phase))
        runs = [
            run_data(root, root / a["manifest_path"], phase, batch, a["execution_order"])
            for a in sweep["runs"]
        ]
        runs.sort(key=lambda run: ARMS.index(run["summary"]["arm"]))
        studies[phase] = {"sweep": sweep, "analysis": analysis, "runs": runs}
    all_runs = [r for s in studies.values() for r in s["runs"]]
    for batch in ("400", "50"):
        rows = [
            row for run in all_runs for row in run["rounds"] if row["batch_round_count"] == batch
        ]
        if len(rows) != int(batch) or len({(r["run_id"], r["round"]) for r in rows}) != len(rows):
            raise ValueError("Round table does not match the requested batch")
        export_csv(output / f"rounds-{batch}.csv", rows)
    export_csv(output / "runs.csv", [r["summary"] for r in all_runs])
    for field in ("calls", "decisions", "notices", "windows"):
        export_csv(output / f"{field}.csv", [row for run in all_runs for row in run[field]])
    contrasts = []
    for phase, study in studies.items():
        for name, estimate in study["analysis"]["central_family"]["comparisons"].items():
            contrasts.append(
                {
                    "study_component": phase,
                    "contrast": name,
                    "status": estimate["status"],
                    "paired_runs": estimate["paired_runs"],
                    "assigned_blocks": estimate["assigned_blocks"],
                    "missing_complete_pairs": estimate["missing_complete_pairs"],
                    "mean_difference": estimate["mean_difference"],
                    "raw_p_value": estimate["sign_flip_p"],
                    "holm_p_value": estimate["holm_p"],
                    "bootstrap_95_interval": estimate["bootstrap_95_interval"],
                    "bounded_95_lower": (
                        None
                        if estimate["simultaneous_95_bounded_interval"] is None
                        else estimate["simultaneous_95_bounded_interval"][0]
                    ),
                    "bounded_95_upper": (
                        None
                        if estimate["simultaneous_95_bounded_interval"] is None
                        else estimate["simultaneous_95_bounded_interval"][1]
                    ),
                }
            )
    export_csv(output / "contrasts.csv", contrasts)
    figures(output, studies)
    old = read(root / "docs/reviews/2026-10-22-study-repair/evidence-summary.json")
    appendix = {
        "explicit_economics": old["studies"]["explicit"],
        "standalone_probes": old["standalone_probes"],
        "scripted_positive_control": read(
            root / "analysis/repair-scripted-positive-20261022/summary.json"
        ),
    }
    summary = {
        "report_date": "2026-10-22",
        "main_scope": "400 original plus 32 natural and 18 neutral diagnostic rounds; not pooled",
        "studies": studies,
        "appendix_outside_main_comparison": appendix,
    }
    atomic_json(output / "report-data.json", summary)
    if any(file_hash(root / p) != h for p, h in sources.items()):
        raise ValueError("A saved input changed while generating this report")
    counts = {
        field: sum(len(r[field]) for r in all_runs)
        for field in ("rounds", "calls", "decisions", "notices", "windows")
    }
    if counts["rounds"] != 450 or counts["decisions"] != 1350 or counts["calls"] != 1528:
        raise ValueError(f"Unexpected evidence totals: {counts}")
    atomic_json(
        output / "provenance.json",
        {
            "saved_source_files_checked": len(sources),
            "source_files_changed": 0,
            "original_inventory_files_verified": len(baseline["raw_sha256"]),
            "source_sha256": sources,
            "new_model_requests": 0,
            "new_market_rounds": 0,
            "paid_inference_calls": 0,
            "exported_counts": counts,
            "builder_sha256": file_hash(Path(__file__)),
            "outputs_sha256": {
                p.relative_to(output).as_posix(): file_hash(p)
                for p in output.rglob("*")
                if p.is_file()
            },
        },
    )
    shutil.copyfile(__file__, output / "report-builder.py")
    print(
        json.dumps(
            {
                "output": output.relative_to(root).as_posix(),
                **counts,
                "inputs_checked": len(sources),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
