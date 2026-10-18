"""Command line for running the experiments locally and reviewing the evidence offline."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from auditors_paradox import __version__
from auditors_paradox.adapters.collusionlab import verify_upstream
from auditors_paradox.batch import run_sweep
from auditors_paradox.config import load_config, project_root
from auditors_paradox.evaluation.inference import analyze_sweep
from auditors_paradox.evaluation.metrics import market_trajectory
from auditors_paradox.evaluation.validate import semantic_fingerprint, validate_run
from auditors_paradox.evidence import file_hash
from auditors_paradox.matrix import run_matrix
from auditors_paradox.runner import RunFailed, preflight, run_experiment
from auditors_paradox.usage import profile_usage


def verify_sources(root: Path) -> dict[str, Any]:
    """Check the local original files, or just report it when the private archive is missing."""
    inputs = (
        root / "docs/source-materials/source-manifest.json",
        root / "docs/reviews/2026-10-16/evidence/source-inventory.json",
    )
    present = [path.is_file() for path in inputs]
    if not all(present):
        unavailable = not any(present)
        return {
            "status": "not_available" if unavailable else "incomplete_archive",
            "valid": None if unavailable else False,
            "checked_files": 0,
            "files_removed": None,
            "errors": [] if unavailable else ["Private source archive is incomplete"],
        }
    manifest = json.loads(
        (root / "docs/source-materials/source-manifest.json").read_text(encoding="utf-8")
    )
    errors = []
    baseline = json.loads(
        (root / "docs/reviews/2026-10-16/evidence/source-inventory.json").read_text(
            encoding="utf-8"
        )
    )
    expected = {row["path"]: row["sha256"] for row in baseline["files"]}
    current = {row["original_path"]: row["sha256"] for row in manifest["files"]}
    if current != expected or len(manifest["files"]) != len(expected):
        errors.append("Preservation mapping differs from the original inventory")
    for row in manifest["files"]:
        path = (root / row["current_path"]).resolve()
        if (
            not path.is_relative_to(root.resolve())
            or not path.is_file()
            or file_hash(path) != row["sha256"]
        ):
            errors.append(row["current_path"])
    return {
        "status": "verified" if not errors else "invalid",
        "valid": not errors,
        "checked_files": len(manifest["files"]),
        "files_removed": manifest["files_removed"],
        "errors": errors,
    }


def parser() -> argparse.ArgumentParser:
    """All the commands and arguments for running and reviewing the experiments."""
    result = argparse.ArgumentParser(prog="auditors-paradox", description=__doc__)
    result.add_argument("--version", action="version", version=__version__)
    result.add_argument("--project-root", type=Path)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check upstream pin and preserved source inventory")
    commands.add_parser("verify-sources", help="Check all migrated input hashes")
    plan = commands.add_parser("plan", help="Show workload and budget without calling a model")
    plan.add_argument("config", type=Path)
    plan.add_argument("--replicates", type=int, default=3)
    plan.add_argument("--matrix", action="store_true")
    usage = commands.add_parser(
        "usage", help="Measure recorded requests and forecast a priced workload"
    )
    usage.add_argument("source", type=Path)
    usage.add_argument("--output", type=Path)
    usage.add_argument("--planned-runs", type=int, default=1)
    usage.add_argument("--planned-rounds", type=int, default=100)
    usage.add_argument("--input-rate", type=float)
    usage.add_argument("--output-rate", type=float)
    usage.add_argument("--input-factor", type=float, default=1)
    usage.add_argument("--output-factor", type=float, default=1)
    usage.add_argument("--safety-factor", type=float, default=1.25)
    usage.add_argument("--target-encoding", choices=["o200k_base", "cl100k_base"])
    prepare = commands.add_parser(
        "prepare-api", help="Write a local AgentRouter config with explicit account rates"
    )
    prepare.add_argument("config", type=Path)
    prepare.add_argument("--model", required=True)
    prepare.add_argument("--input-rate", type=float, required=True)
    prepare.add_argument("--output-rate", type=float, required=True)
    prepare.add_argument("--max-cost", type=float, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    matrix = commands.add_parser(
        "matrix", help="Compare both auditor designs and both horizon disclosures"
    )
    matrix.add_argument("config", type=Path)
    matrix.add_argument("--matrix-id")
    matrix.add_argument("--replicates", type=int, default=2)
    matrix.add_argument("--allow-paid", action="store_true")
    matrix.add_argument("--max-total-cost-usd", type=float)
    check = commands.add_parser(
        "validate-config", help="Check config, economics, prompts, and source pin"
    )
    check.add_argument("config", type=Path)
    check.add_argument(
        "--allow-paid",
        action="store_true",
        help="Validate a live config without dispatching any request",
    )
    run = commands.add_parser("run", help="Execute one independent trajectory")
    run.add_argument("config", type=Path)
    run.add_argument("--run-id")
    run.add_argument("--output-root", type=Path)
    run.add_argument("--resume-from", type=Path)
    run.add_argument(
        "--allow-paid",
        action="store_true",
        help="Explicitly authorize this configured budgeted live run",
    )
    sweep = commands.add_parser("sweep", help="Run randomized blocks of all four feedback arms")
    sweep.add_argument("config", type=Path)
    sweep.add_argument("--sweep-id")
    sweep.add_argument("--replicates", type=int, default=3)
    sweep.add_argument("--output-root", type=Path)
    sweep.add_argument("--assignment-seed", type=int, default=31415)
    sweep.add_argument("--allow-paid", action="store_true")
    sweep.add_argument("--max-total-cost-usd", type=float)
    verify = commands.add_parser("verify", help="Validate checksums and full treatment fidelity")
    verify.add_argument("run", type=Path)
    inspect = commands.add_parser(
        "inspect", help="Inspect saved outcomes; does not estimate counterfactuals"
    )
    inspect.add_argument("run", type=Path)
    compare = commands.add_parser("compare-scripted", help="Compare stable trajectory fingerprints")
    compare.add_argument("first", type=Path)
    compare.add_argument("second", type=Path)
    analyze = commands.add_parser("analyze", help="Validate and analyze a terminal sweep")
    analyze.add_argument("sweep", type=Path)
    analyze.add_argument("--output", type=Path, required=True)
    freeze = commands.add_parser(
        "freeze-config", help="Write a new confirmatory config tied to current protocol bytes"
    )
    freeze.add_argument("config", type=Path)
    freeze.add_argument("--output", type=Path, required=True)
    index = commands.add_parser(
        "index", help="Rebuild, query or back up the local research index; never calls models"
    )
    index.add_argument("action", choices=["ingest", "check", "query", "export", "backup"])
    index.add_argument("--database", type=Path, default=Path("runs/research.sqlite3"))
    index.add_argument("--source", type=Path)
    index.add_argument("--sql", default="SELECT arm,status,relative_path FROM runs ORDER BY run_id")
    index.add_argument("--output", type=Path)
    diagnostic = commands.add_parser(
        "diagnostic", help="Freeze competence cases or run a bounded scripted/local battery"
    )
    diagnostic.add_argument("config", type=Path)
    diagnostic.add_argument("--output", type=Path, required=True)
    diagnostic.add_argument("--execute-local", action="store_true")
    diagnostic.add_argument("--limit", type=int, default=56)
    diagnostic.add_argument(
        "--kind", choices=["competence", "warning-response"], default="competence"
    )
    calibration = commands.add_parser(
        "calibrate-sanctions", help="Calculate deterministic sanction economics without requests"
    )
    calibration.add_argument("config", type=Path)
    profile = commands.add_parser(
        "timings", help="Separate saved request waiting, wire and reasoning usage"
    )
    profile.add_argument("manifest", type=Path)
    power = commands.add_parser(
        "power-plan",
        help="Simulate declared four-arm scenarios from independent development blocks",
    )
    power.add_argument("scenario", type=Path)
    power.add_argument("--output", type=Path, required=True)
    recover = commands.add_parser(
        "recover-boundary",
        help="Snapshot a provably abandoned committed manual boundary; never dispatches inference",
    )
    recover.add_argument("source", type=Path)
    recover.add_argument("--output", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    """Run the chosen command and return the exit code."""
    args = parser().parse_args(argv)
    try:
        root = (args.project_root or project_root()).resolve()
        value: Any
        if args.command == "recover-boundary":
            from auditors_paradox.recovery import recover_boundary

            print(recover_boundary(root, args.source, args.output))
        elif args.command == "index":
            from auditors_paradox.research_index import ResearchIndex

            with ResearchIndex(root, root / args.database) as index:
                if args.action == "ingest":
                    if args.source is None:
                        raise ValueError("--source is required")
                    source = (root / args.source).resolve()
                    payload = json.loads(source.read_text(encoding="utf8"))
                    action = (
                        index.ingest_sweep
                        if payload.get("artifact_kind") == "sweep"
                        else (
                            index.ingest_analysis
                            if source.name == "summary.json"
                            else index.ingest_run
                        )
                    )
                    value = action(source)
                elif args.action == "check":
                    value = index.check()
                elif args.action == "query":
                    value = index.query(args.sql)
                else:
                    if args.output is None:
                        raise ValueError("--output is required")
                    if args.action == "backup":
                        index.backup(root / args.output)
                        value = {"backup": str(args.output)}
                    else:
                        value = {"exported_rows": index.export(args.sql, root / args.output)}
            print(json.dumps(value, indent=2))
        elif args.command == "diagnostic":
            from auditors_paradox.diagnostics import (
                competence_battery,
                run_battery,
                warning_response_battery,
            )

            cfg = load_config(args.config)
            if not 1 <= args.limit <= 56:
                raise ValueError("limit must be 1 to 56")
            if args.execute_local and cfg.agent.backend not in {"scripted", "ollama"}:
                raise ValueError(
                    "CLI diagnostic execution is local only; paid diagnostics require an authorized managed workflow"
                )
            print(
                run_battery(
                    cfg,
                    root,
                    root / args.output,
                    execute=args.execute_local,
                    cases=(
                        competence_battery(cfg)
                        if args.kind == "competence"
                        else warning_response_battery(cfg)
                    )[: args.limit],
                )
            )
        elif args.command == "calibrate-sanctions":
            from auditors_paradox.diagnostics import sanction_calibration

            print(json.dumps(sanction_calibration(load_config(args.config)), indent=2))
        elif args.command == "timings":
            from auditors_paradox.diagnostics import profile_requests

            print(json.dumps(profile_requests(root / args.manifest), indent=2))
        elif args.command == "power-plan":
            from auditors_paradox.evaluation.planning import power_scenario
            from auditors_paradox.evidence import atomic_json

            output = (root / args.output).resolve()
            if not output.is_relative_to(root) or output.exists():
                raise ValueError("Choose a new project-local planning output")
            value = power_scenario(**json.loads(args.scenario.read_text(encoding="utf8")))
            atomic_json(output, value)
            print(output)
        elif args.command in {"doctor", "verify-sources"}:
            sources = verify_sources(root)
            value = {"version": __version__, "sources": sources}
            if args.command == "doctor":
                value["upstream_commit"] = verify_upstream(root)
                value["live_model_validation"] = "not performed by this command"
            print(json.dumps(value, indent=2))
            if sources["valid"] or (
                args.command == "doctor" and sources["status"] == "not_available"
            ):
                return 0
            return 2 if sources["status"] == "not_available" else 1
        if args.command == "plan":
            cfg = load_config(args.config)
            if not 1 <= args.replicates <= 1000:
                raise ValueError("replicates must be between 1 and 1000")
            runs = len(cfg.batch_arms) * args.replicates * (4 if args.matrix else 1)
            paid = cfg.agent.backend not in {"scripted", "ollama"}
            print(
                json.dumps(
                    {
                        "runs": runs,
                        "arms": cfg.batch_arms,
                        "auditor_horizon_cells": 4 if args.matrix else 1,
                        "replicates": args.replicates,
                        "base_model_requests": runs * cfg.rounds * 3,
                        "request_ceiling": runs
                        * min(
                            cfg.budget.max_calls,
                            cfg.rounds
                            * 3
                            * cfg.agent.max_action_attempts
                            * cfg.agent.transport_attempts,
                        ),
                        "configured_cost_ceiling_usd": (
                            runs * float(cfg.budget.max_cost_usd or 0)
                            if paid and cfg.budget.enforce_spending_caps
                            else None
                        ),
                        "optional_spending_caps_enforced": cfg.budget.enforce_spending_caps,
                        "interpretation": "Planning only; pilot repeats are not a confirmatory power calculation",
                    },
                    indent=2,
                )
            )
        elif args.command == "usage":
            print(
                json.dumps(
                    profile_usage(
                        args.source,
                        root,
                        output=args.output,
                        planned_runs=args.planned_runs,
                        planned_rounds=args.planned_rounds,
                        input_rate=args.input_rate,
                        output_rate=args.output_rate,
                        input_factor=args.input_factor,
                        output_factor=args.output_factor,
                        safety_factor=args.safety_factor,
                        target_encoding=args.target_encoding,
                    ),
                    indent=2,
                )
            )
        elif args.command == "prepare-api":
            cfg = load_config(args.config)
            data = cfg.model_dump(mode="json")
            data.update(study_id="agentrouter-local-pilot", run_kind="pilot")
            data["agent"].update(
                backend="agentrouter",
                model=args.model,
                api_protocol="chat",
                base_url="https://agentrouter.org/v1",
                parse_failure_policy="abort",
            )
            data["budget"].update(
                max_cost_usd=args.max_cost,
                input_usd_per_million=args.input_rate,
                output_usd_per_million=args.output_rate,
            )
            configured = type(cfg).model_validate(data)
            target = args.output.resolve()
            if not target.is_relative_to((root / "configs/local").resolve()):
                raise ValueError("Write prepared provider configs inside configs/local")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(configured.model_dump(mode="json"), indent=2) + "\n")
            print("Prepared local provider config; no request was made")
        elif args.command == "validate-config":
            cfg = load_config(args.config)
            market, revision = preflight(cfg, root, allow_paid=args.allow_paid)
            print(
                json.dumps(
                    {
                        "valid": True,
                        "backend": cfg.agent.backend,
                        "upstream_commit": revision,
                        "benchmarks": asdict(market.benchmarks),
                    },
                    indent=2,
                )
            )
        elif args.command == "matrix":
            path = run_matrix(
                load_config(args.config),
                root,
                matrix_id=args.matrix_id,
                replicates=args.replicates,
                allow_paid=args.allow_paid,
                max_total_cost_usd=args.max_total_cost_usd,
            )
            print(path)
            return 0 if json.loads(path.read_text(encoding="utf-8"))["status"] == "complete" else 2
        elif args.command == "run":
            print(
                run_experiment(
                    load_config(args.config),
                    root,
                    run_id=args.run_id,
                    allow_paid=args.allow_paid,
                    output_root=args.output_root,
                    resume_from=(root / args.resume_from) if args.resume_from else None,
                )
            )
        elif args.command == "sweep":
            path = run_sweep(
                load_config(args.config),
                root,
                sweep_id=args.sweep_id,
                replicates=args.replicates,
                assignment_seed=args.assignment_seed,
                allow_paid=args.allow_paid,
                max_total_cost_usd=args.max_total_cost_usd,
                output_root=args.output_root,
            )
            print(path)
            if json.loads(path.read_text(encoding="utf-8"))["status"] != "complete":
                return 2
        elif args.command == "verify":
            value = validate_run(args.run)
            print(json.dumps(value, indent=2))
            return 0 if value["valid"] else 1
        elif args.command == "inspect":
            if not validate_run(args.run)["valid"]:
                raise ValueError("Inspect requires a complete verified run")
            print(json.dumps(market_trajectory(args.run), indent=2))
        elif args.command == "compare-scripted":
            if not all(validate_run(path)["valid"] for path in (args.first, args.second)):
                raise ValueError("Both trajectories must be complete verified runs")
            for path in (args.first, args.second):
                directory = path if path.is_dir() else path.parent
                if (
                    json.loads((directory / "manifest.json").read_text(encoding="utf-8"))[
                        "data_class"
                    ]
                    != "scripted_engineering_fixture"
                ):
                    raise ValueError("compare-scripted requires scripted engineering fixtures")
            first, second = semantic_fingerprint(args.first), semantic_fingerprint(args.second)
            print(
                json.dumps(
                    {"equivalent": first == second, "first": first, "second": second}, indent=2
                )
            )
            return 0 if first == second else 1
        elif args.command == "analyze":
            print(analyze_sweep(args.sweep, root, args.output))
        elif args.command == "freeze-config":
            cfg = load_config(args.config)
            data = cfg.model_dump(mode="json")
            data.update(run_kind="confirmatory", protocol_sha256=file_hash(root / cfg.protocol))
            frozen = type(cfg).model_validate(data)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(frozen.model_dump(mode="json"), indent=2) + "\n")
            print(args.output)
        return 0
    except KeyboardInterrupt:
        print("Interrupted; completed rounds and request evidence are retained", file=sys.stderr)
        return 130
    except (ValueError, OSError, RunFailed, sqlite3.Error) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
