"""Estimate the paired differences between runs and export them with every run's status."""

from __future__ import annotations

import csv
import json
import math
from itertools import product
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
from pydantic import Field

from auditors_paradox.batch import load_sweep
from auditors_paradox.config import StrictModel
from auditors_paradox.contracts import EXTENDED_ARMS
from auditors_paradox.evaluation.metrics import run_metrics
from auditors_paradox.evaluation.validate import validate_run
from auditors_paradox.evidence import atomic_json, file_hash


class AnalysisSettings(StrictModel):
    """Freeze these analysis choices before looking at the new results."""

    alpha: float = Field(default=0.05, gt=0, lt=1)
    bootstrap_samples: int = Field(default=10000, ge=100, le=100000)
    bootstrap_min_pairs: int = Field(default=10, ge=2, le=1000)
    exact_sign_flip_max_pairs: int = Field(default=16, ge=1, le=20)
    monte_carlo_samples: int = Field(default=20000, ge=100, le=1000000)
    seed: int = Field(default=7021, ge=0)


def paired_estimate(
    differences: list[float], seed: int | None = None, *, settings: AnalysisSettings | None = None
) -> dict[str, Any]:
    """Mean paired difference with a bootstrap interval and a sign flip p-value.

    This assumes independent blocks and pairs that are exchangeable under sign flips.
    """
    options = settings or AnalysisSettings()
    seed = options.seed if seed is None else seed
    if any(not math.isfinite(value) for value in differences):
        raise ValueError("Paired outcomes must be finite")
    n = len(differences)
    if n < 2:
        return {
            "status": "insufficient_pairs",
            "paired_runs": n,
            "mean_difference": mean(differences) if n else None,
            "bootstrap_95_interval": None,
            "bootstrap_interval": None,
            "confidence_level": 1 - options.alpha,
            "sign_flip_p": None,
        }
    values = np.asarray(differences, dtype=float)
    rng = np.random.default_rng(seed)
    interval = None
    if n >= options.bootstrap_min_pairs and np.ptp(values) > 0:
        samples = rng.choice(values, size=(options.bootstrap_samples, n), replace=True).mean(axis=1)
        interval = np.quantile(samples, [options.alpha / 2, 1 - options.alpha / 2]).tolist()
    observed = abs(float(values.mean()))
    if n <= options.exact_sign_flip_max_pairs:
        statistics = np.asarray(
            [abs(float(np.mean(values * signs))) for signs in product((-1, 1), repeat=n)]
        )
        p = float(np.mean(statistics >= observed - 1e-12))
        method = "enumerated_sign_flips"
    else:
        statistics = abs(
            (rng.choice((-1, 1), size=(options.monte_carlo_samples, n)) * values).mean(axis=1)
        )
        p = float((1 + np.count_nonzero(statistics >= observed - 1e-12)) / (len(statistics) + 1))
        method = "monte_carlo_sign_flips"
    return {
        "status": "estimated",
        "paired_runs": n,
        "mean_difference": float(values.mean()),
        "bootstrap_95_interval": interval if options.alpha == 0.05 else None,
        "bootstrap_interval": interval,
        "confidence_level": 1 - options.alpha,
        "bootstrap_status": (
            "approximate_exploratory" if interval else "withheld_small_or_degenerate_sample"
        ),
        "sign_flip_p": p,
        "test": method,
        "analysis_seed": seed,
        "assumption": "Run blocks are independent; within-pair contrasts satisfy sign-flip exchangeability.",
    }


def holm_adjust(pvalues: dict[str, float | None]) -> dict[str, float | None]:
    """Keep every declared comparison in the family, even when its p-value is unavailable."""
    numeric = {name: 1.0 if value is None else value for name, value in pvalues.items()}
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in numeric.values()):
        raise ValueError("P-values must be finite and between zero and one")
    ordered = sorted(numeric, key=numeric.__getitem__)
    adjusted: dict[str, float | None] = {}
    previous = 0.0
    for index, name in enumerate(ordered):
        value = pvalues[name]
        previous = max(
            previous, min(1.0, (len(ordered) - index) * (1.0 if value is None else value))
        )
        adjusted[name] = None if value is None else previous
    return adjusted


def central_family(
    summary: dict[str, Any],
    cfg: dict[str, Any],
    benchmark: dict[str, Any],
    settings: AnalysisSettings | None = None,
) -> dict[str, Any]:
    """Report four core contrasts with Holm p-values and conservative simultaneous bounds."""
    options = settings or AnalysisSettings()
    family = (cfg.get("v2") or {}).get("hypothesis_family", "warning")
    names = (
        (
            "public_minus_silent",
            "private_minus_silent",
            "public_minus_private",
            "placebo_minus_silent",
        )
        if family == "warning"
        else (
            "fine_only_minus_silent",
            "private_fine_minus_fine_only",
            "public_fine_minus_fine_only",
            "public_fine_minus_private_fine",
        )
    )
    unavailable = paired_estimate([], settings=options)
    estimates = {
        name: dict(
            summary["estimate"]
            if name == "public_minus_private"
            else summary["secondary_contrasts"].get(name, unavailable)
        )
        for name in names
    }
    adjusted = holm_adjust({name: value["sign_flip_p"] for name, value in estimates.items()})
    market = cfg["market"]
    if cfg.get("primary_outcome", "price") == "price":
        width = (market["price_max"] - market["price_min"]) / (
            benchmark["joint_price"] - benchmark["nash_price"]
        )
    else:
        qmax = (
            1.0
            if market["demand_model"] == "calvano"
            else max(
                0.0,
                market["a"] - market["b"] * market["price_min"] + market["d"] * market["price_max"],
            )
        )
        minimum = min(
            0.0, (market["price_min"] - market["c"]) * qmax / market.get("price_scale", 1)
        )
        low = (minimum - benchmark["nash_reward"]) / (
            benchmark["joint_reward"] - benchmark["nash_reward"]
        )
        width = 1.0 - low
    for name, value in estimates.items():
        value["holm_p"] = adjusted[name]
        n = value["paired_runs"]
        interval = None
        if n:
            halfwidth = 2 * width * math.sqrt(math.log(2 * len(names) / options.alpha) / (2 * n))
            center = value["mean_difference"]
            interval = [max(-width, center - halfwidth), min(width, center + halfwidth)]
        value["simultaneous_bounded_interval"] = interval
        value["simultaneous_95_bounded_interval"] = interval if options.alpha == 0.05 else None
        value["confidence_level"] = 1 - options.alpha
    return {
        "family_id": family,
        "status": "exploratory; not a retrospectively frozen confirmatory family",
        "comparisons": estimates,
        "multiplicity": "Holm across all four core comparisons; unavailable p-values retain their family slots",
        "interval_method": "Bonferroni-Hoeffding over independent bounded block differences; often vacuous at small n",
        "interval_assumptions": "Independent blocks, validated global joint-profit reference, retained pairs representative; informative missingness invalidates population interpretation",
        "feasible_difference_bounds": [-width, width],
    }


def analyze_sweep(
    path: Path, root: Path, output: Path, *, settings: AnalysisSettings | None = None,
    allow_unused_prompt_drift: bool = False,
) -> Path:
    """Validate the assigned runs and export the results, failures and paired contrasts."""
    root = root.resolve()
    path = (root / path).resolve()
    output = (root / output).resolve()
    if not path.is_relative_to(root) or not output.is_relative_to(root):
        raise ValueError("Keep analysis evidence and new outputs inside the project")
    options = settings or AnalysisSettings()
    sweep = load_sweep(path)
    if sweep["status"] in {"running", "interrupted"}:
        raise ValueError(
            "Analyze a terminal complete/partial/failed sweep; interrupted work remains incomplete"
        )
    output.mkdir(parents=True, exist_ok=False)
    rows: list[dict[str, Any]] = []
    keys: set[str] = set()
    data_classes: set[str] = set()
    source_identities: set[str] = set()
    reported_models: set[str] = set()
    validated_paths: list[Path] = []
    compatibility_review: dict[str, Any] | None = None
    benchmark: dict[str, Any] | None = None
    paired: dict[str, dict[str, float]] = {}
    for assigned in sweep["runs"]:
        row: dict[str, Any] = {
            "run_id": assigned["run_id"],
            "arm": assigned["arm"],
            "replicate_id": assigned["replicate_id"],
            "status": assigned["status"],
            "included": False,
            "exclusion": "execution_failed",
            "manifest_path": assigned.get("manifest_path"),
        }
        relative = assigned.get("manifest_path")
        if assigned["status"] == "complete" and relative:
            manifest_path = (root / relative).resolve()
            if not manifest_path.is_relative_to(root.resolve()):
                raise ValueError("Run manifest path escaped the project")
            validation = validate_run(manifest_path)
            if validation["valid"]:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if (
                    any(manifest[key] != assigned[key] for key in ("run_id", "arm", "replicate_id"))
                    or manifest["assignment_block"] != sweep["sweep_id"]
                ):
                    raise ValueError("Run identity does not match its randomized assignment")
                keys.add(manifest["comparison_key"])
                validated_paths.append(manifest_path)
                data_classes.add(manifest["data_class"])
                source_identities.add(manifest["source_tree_sha256"])
                benchmark = manifest["benchmarks"]
                for line in (
                    manifest_path.with_name("events.jsonl").read_text(encoding="utf-8").splitlines()
                ):
                    event = json.loads(line)
                    if event["type"] == "model_call_completed":
                        reported_models.add(event["payload"]["reported_model"])
                if manifest["replicate_seed"] != assigned["seed"]:
                    raise ValueError("Run seed disagrees with its assignment")
                metrics = run_metrics(manifest_path)
                row.update(metrics, included=True, exclusion="")
                if manifest.get("researcher_intervened"):
                    row.update(included=False, exclusion="researcher_intervened_exploratory")
                else:
                    paired.setdefault(row["replicate_id"], {})[row["arm"]] = float(
                        metrics["primary_outcome_value"]
                    )
            else:
                row.update(
                    exclusion="integrity_or_fidelity_failed",
                    validation_errors="; ".join(validation["errors"]),
                )
        from auditors_paradox.prompt_trials import trial_metadata
        row.update(trial_metadata(sweep["base_config"], sweep["sweep_id"]))
        rows.append(row)
    if (
        len(keys) > 1
        or len(data_classes) > 1
        or len(source_identities) > 1
        or len(reported_models) > 1
    ):
        from auditors_paradox.evaluation.source_compatibility import review_unused_prompt_drift

        compatibility_review = review_unused_prompt_drift(validated_paths, root)
        compatibility_review["explicit_recovery_requested"] = allow_unused_prompt_drift
        compatibility_review["data_classes"] = sorted(data_classes)
        compatibility_review["reported_models"] = sorted(reported_models)
        permitted = (allow_unused_prompt_drift and compatibility_review["accepted"]
                     and len(data_classes) == 1 and len(reported_models) <= 1)
        compatibility_review["pooling_permitted"] = permitted
        atomic_json(output / "source-compatibility.json", compatibility_review)
        if not permitted:
            raise ValueError(
                "Refusing to pool incompatible protocols/configs or synthetic/live evidence. "
                "See source-compatibility.json in the analysis output."
            )
    # Make sure every planned run is still in there.
    assignment = json.loads(path.with_name("assignment.json").read_text(encoding="utf-8"))
    fields = ("run_id", "arm", "replicate_id", "seed")
    if {tuple(a[f] for f in fields) for a in assignment} != {
        tuple(a[f] for f in fields) for a in sweep["runs"]
    }:
        raise ValueError("Sweep omits or changes full assignment tuples from the preserved plan")
    if len(assignment) != sweep["planned_runs"] or {r["run_id"] for r in rows} != {
        r["run_id"] for r in assignment
    }:
        raise ValueError("Sweep manifest omits or duplicates planned assignments")
    if len({r["run_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate run identities in the sweep")
    if len({(r["replicate_id"], r["arm"]) for r in rows}) != len(rows):
        raise ValueError("Duplicate arm assignment within a replicate block")
    differences = [
        arms["public"] - arms["private"]
        for arms in paired.values()
        if {"public", "private"} <= arms.keys()
    ]
    assigned_arms = [arm for arm in EXTENDED_ARMS if any(a["arm"] == arm for a in assignment)]
    counts = {
        arm: {
            "assigned": sum(a["arm"] == arm for a in assignment),
            "completed": sum(r["arm"] == arm and r["status"] == "complete" for r in rows),
            "included": sum(r["arm"] == arm and r["included"] for r in rows),
        }
        for arm in assigned_arms
    }
    summary = {
        "schema_version": 1,
        "sweep_id": sweep["sweep_id"],
        "source_sweep_sha256": file_hash(path),
        "source_sweep": path.relative_to(root).as_posix(),
        "counts": counts,
        "data_class": next(iter(data_classes), "no_valid_runs"),
        "study_purpose": sweep["base_config"].get("study_purpose", "research"),
        "comparison": "public_minus_private",
        "primary_outcome": sweep["base_config"].get("primary_outcome", "price"),
        "estimate": paired_estimate(differences, settings=options),
        "analysis_settings": options.model_dump(mode="json"),
        "unpaired_blocks": sum(
            not {"public", "private"} <= paired.get(block, {}).keys()
            for block in {a["replicate_id"] for a in assignment}
        ),
        "interpretation": (
            "Synthetic engineering checks, not empirical LLM findings"
            if "scripted_engineering_fixture" in data_classes
            else "Conditional on protocol assumptions and retained run dispositions"
        ),
    }
    if compatibility_review is not None:
        summary["source_compatibility"] = compatibility_review
    contrasts = {
        "public_minus_silent": ("public", "silent"),
        "private_minus_silent": ("private", "silent"),
        "placebo_minus_silent": ("placebo", "silent"),
        "numeric_minus_public": ("public_numeric", "public"),
        "public_fine_minus_public": ("public_fine", "public"),
        "private_fine_minus_private": ("private_fine", "private"),
    }
    if sweep["base_config"].get("schema_version") == 2:
        contrasts.update(
            {
                "fine_only_minus_silent": ("fine_only", "silent"),
                "private_fine_minus_fine_only": ("private_fine", "fine_only"),
                "public_fine_minus_fine_only": ("public_fine", "fine_only"),
                "public_fine_minus_private_fine": ("public_fine", "private_fine"),
            }
        )
    summary["secondary_contrasts"] = {
        name: paired_estimate(
            [
                values[first] - values[second]
                for values in paired.values()
                if {first, second} <= values.keys()
            ],
            settings=options,
        )
        for name, (first, second) in contrasts.items()
        if {first, second} <= set(assigned_arms)
    }
    summary["secondary_inference_status"] = "Exploratory estimates; multiplicity plan not frozen"
    summary["source_identities"] = sorted(source_identities)
    summary["reported_models"] = sorted(reported_models)
    if benchmark is not None:
        summary["central_family"] = central_family(
            summary, sweep["base_config"], benchmark, settings=options
        )
        for name, estimate in summary["central_family"]["comparisons"].items():
            first, second = name.split("_minus_")
            assigned_blocks = {
                block
                for block in {a["replicate_id"] for a in assignment}
                if {first, second} <= {a["arm"] for a in assignment if a["replicate_id"] == block}
            }
            estimate["comparison_assigned"] = bool(assigned_blocks)
            if not assigned_blocks:
                estimate["status"] = "not_assigned"
            estimate["assigned_blocks"] = len(assigned_blocks)
            estimate["missing_complete_pairs"] = len(assigned_blocks) - estimate["paired_runs"]
            if sweep["base_config"].get("schema_version") == 2 and assigned_blocks:
                low, high = summary["central_family"]["feasible_difference_bounds"]
                known = [
                    v[first] - v[second] for v in paired.values() if {first, second} <= v.keys()
                ]
                missing = len(assigned_blocks) - len(known)
                estimate["missing_outcome_bounds"] = [
                    (sum(known) + missing * low) / len(assigned_blocks),
                    (sum(known) + missing * high) / len(assigned_blocks),
                ]
    from auditors_paradox.prompt_trials import trial_metadata
    summary.update(trial_metadata(sweep["base_config"], sweep["sweep_id"]))
    for row in rows:
        row.update(trial_metadata(sweep["base_config"], sweep["sweep_id"]))
    atomic_json(output / "summary.json", summary)
    columns = sorted({key for row in rows for key in row})
    with (output / "runs.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    plot_results(rows, summary, output)
    export_trajectories(rows, root, output)
    text = (
        f"# Sweep analysis: {sweep['sweep_id']}\n\n{summary['interpretation']}.\n\n"
        f"Legacy compatibility contrast: public minus private. Paired blocks: {len(differences)}.\n\n"
        f"Configured score: {summary['primary_outcome']} elevation, computed before fines.\n\n"
        f"```json\n{json.dumps(summary['estimate'], indent=2)}\n```\n\n"
        f"Secondary contrasts (exploratory):\n\n```json\n{json.dumps(summary['secondary_contrasts'], indent=2)}\n```\n\n"
        f"Core comparison family with multiplicity and conservative bounds:\n\n```json\n{json.dumps(summary.get('central_family', {}), indent=2)}\n```\n\n"
        "See runs.csv for every assigned attempt and its disposition. Price elevation is not proof of intent or collusion.\n"
    )
    if compatibility_review is not None:
        text += ("\nExplicit exploratory source review: only unused pricing templates differed. "
                 "Original evidence remains unchanged. See source-compatibility.json.\n")
    (output / "report.md").write_text(text, encoding="utf-8")
    version = sweep["base_config"].get("v2")
    if version and version["index_enabled"]:
        import sqlite3

        from auditors_paradox.research_index import ResearchIndex

        try:
            with ResearchIndex(root, root / version["index_path"]) as index:
                index.ingest_sweep(path)
                index.ingest_analysis(output / "summary.json")
        except (ValueError, OSError, sqlite3.Error) as error:
            atomic_json(
                output / "index-warning.json",
                {"status": "degraded", "error_type": type(error).__name__, "message": str(error)},
            )
    return output / "summary.json"


def export_trajectories(rows: list[dict[str, Any]], root: Path, output: Path) -> None:
    """Export each validated round and show prices, profit and actual notice consumption."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from auditors_paradox.evaluation.request_exports import export_request_evidence

    table = export_request_evidence(rows, root, output)
    figure, axes = plt.subplots(3, 1, figsize=(10, 9), sharex=True)
    plotted = False
    for row in rows:
        trajectory = [
            v
            for v in table
            if v["run_id"] == row["run_id"] and v["committed"] and v["market_outcome_available"]
        ]
        if not trajectory:
            continue
        x = [v["round"] for v in trajectory]
        label = f"{row['arm']} / {row['replicate_id']}" + (
            " (excluded/partial)" if not row["included"] else ""
        )
        for axis, field in zip(axes, ("mean_price", "gross_profit_elevation", "consumed_notices")):
            axis.plot(
                x,
                [v[field] for v in trajectory],
                alpha=0.75,
                label=label,
                linestyle="-" if row["included"] else "--",
            )
        plotted = True
    if plotted:
        axes[0].legend(fontsize=7, ncol=2)
    axes[0].set_title("Descriptive trajectories; rounds are not independent replicates")
    axes[0].set_ylabel("Mean price (ticks)")
    axes[1].set_ylabel("Gross-profit elevation")
    axes[2].set_ylabel("Recipient notices consumed")
    axes[2].set_xlabel("Decision round (notice source is the previous round)")
    figure.tight_layout()
    figure.savefig(output / "trajectories.png", dpi=160)
    figure.savefig(output / "trajectories.svg")
    plt.close(figure)


def plot_results(rows: list[dict[str, Any]], summary: dict[str, Any], output: Path) -> None:
    """Plot one point per included run and label scripted results so nobody mistakes them."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axis = plt.subplots(figsize=(8, 4.8))
    arms = list(summary["counts"])
    for index, arm in enumerate(arms):
        values = [r["primary_outcome_value"] for r in rows if r["arm"] == arm and r["included"]]
        jitter = np.linspace(-0.08, 0.08, len(values)) if len(values) else np.asarray([])
        axis.scatter(index + jitter, values, color="#087f8c", alpha=0.8)
    axis.set_xticks(range(len(arms)), arms, rotation=20)
    axis.set_ylabel(f"Run-level tail {summary['primary_outcome']} elevation")
    axis.axhline(0, color="#999999", linewidth=0.7)
    title = (
        "SCRIPTED ENGINEERING FIXTURE"
        if summary["data_class"] == "scripted_engineering_fixture"
        else (
            "No valid runs"
            if summary["data_class"] == "no_valid_runs"
            else "Observed live-model runs"
        )
    )
    axis.set_title(title + "\nOne point per independent trajectory")
    figure.tight_layout()
    figure.savefig(output / "run-outcomes.svg")
    figure.savefig(output / "run-outcomes.png", dpi=160)
    plt.close(figure)
