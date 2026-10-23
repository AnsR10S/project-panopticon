"""Prepare, launch and inspect a frozen notebook plan without changing the market runner."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pydantic import Field

from auditors_paradox.config import StrictModel, StudyConfig, safe_identifier
from auditors_paradox.contracts import fingerprint
from auditors_paradox.evaluation.inference import AnalysisSettings, analyze_sweep
from auditors_paradox.evidence import atomic_json, file_hash, source_snapshot
from auditors_paradox.runner import preflight
from auditors_paradox.runner import preview_initial_prompts as preview_initial_prompts
from auditors_paradox.strategies import algorithm_selection

_OWNED_WORKERS: dict[int, subprocess.Popen[Any]] = {}


def worker_python(root: Path) -> str:
    """Use the project's runtime even when the notebook kernel came from elsewhere."""
    candidate = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(candidate) if candidate.is_file() else sys.executable


def worker_process(pid: int) -> subprocess.Popen[Any] | None:
    """Return only a worker created by this notebook process."""
    return _OWNED_WORKERS.get(pid)


def forget_worker(pid: int) -> None:
    """Release the handle after a worker has finished."""
    _OWNED_WORKERS.pop(pid, None)


class Controls(StrictModel):
    """These limits cover the whole notebook batch, not just one arm."""

    replicates: int = Field(default=1, ge=1, le=1000)
    assignment_seed: int = Field(default=31415, ge=0)
    requests_per_minute: int = Field(default=1, ge=1)
    tokens_per_minute: int = Field(default=20000, ge=1)
    new_request_limit: int = Field(default=3600, ge=1, le=4000)
    new_cost_limit_usd: float = Field(default=1000, gt=0)
    lifetime_cost_limit_usd: float = Field(default=1000, gt=0, le=1000)
    workflow_seconds: float = Field(default=86400, gt=0)
    label: str = "exploratory-four-arm"
    resume_sweep: str | None = None


def last_failed_sweep(root: Path, study: dict[str, Any]) -> str:
    """Select the latest terminal failure for this study without starting or replacing anything."""
    cfg = StudyConfig.model_validate(study)
    candidates = sorted(
        (root / "runs" / cfg.run_kind / cfg.study_id / "sweeps").glob("*/manifest.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        candidate = read_json(path)
        agent = candidate.get("base_config", {}).get("agent", {})
        if candidate.get("status") in {"failed", "partial"} and all(
            agent.get(k) == getattr(cfg.agent, k) for k in ("backend", "client", "model")
        ):
            return path.relative_to(root).as_posix()
    raise ValueError("No failed or partial sweep exists for this study")


def inside(root: Path, relative: str) -> Path:
    """Keep control files and evidence inside this project."""
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Path must stay inside the project")
    return path


def read_json(path: Path) -> dict[str, Any]:
    """Read a small control or manifest file."""
    return json.loads(path.read_text(encoding="utf8"))  # type: ignore[no-any-return]


def design_digest(study: dict[str, Any], controls: dict[str, Any], analysis: dict[str, Any]) -> str:
    """Any changed setting needs a new frozen plan before launch."""
    return fingerprint({"study": study, "controls": controls, "analysis": analysis})


def preview(
    root: Path, study: dict[str, Any], controls: dict[str, Any], analysis: dict[str, Any]
) -> dict[str, Any]:
    """Check the real configuration and show workload, economics and current allowance offline."""
    cfg = StudyConfig.model_validate(study)
    ctl = Controls.model_validate(controls)
    opts = AnalysisSettings.model_validate(analysis)
    parent = None
    if ctl.resume_sweep:
        from auditors_paradox.continuation import check_sweep_checkpoint

        parent = check_sweep_checkpoint(inside(root, ctl.resume_sweep), cfg, root)
        if (
            parent["replicates"] != ctl.replicates
            or parent["assignment_seed"] != ctl.assignment_seed
        ):
            raise ValueError("Continuation must retain the original blocks and assignment seed")
    safe_identifier(ctl.label)
    market, upstream = preflight(cfg, root, allow_paid=True)
    runs = len(cfg.batch_arms) * ctl.replicates
    minimum = runs * cfg.rounds * cfg.n_agents
    maximum = (
        minimum
        * cfg.agent.max_action_attempts
        * (cfg.agent.transport_attempts + cfg.agent.local_truncation_retries)
    )
    paid = cfg.agent.backend not in {"scripted", "ollama"}
    ledger_path = root / "runs/diagnostics/qwen-live/allowance.json"
    ledger = read_json(ledger_path) if ledger_path.exists() else {}
    held = float(ledger.get("reserved_usd", 0))
    spending_caps = cfg.budget.enforce_spending_caps
    remaining_dollars = max(0, ctl.lifetime_cost_limit_usd - held)
    cap = runs * float(cfg.budget.max_cost_usd or 0)
    if parent is not None:
        from auditors_paradox.continuation import remaining_work

        minimum, cap = remaining_work(parent, root)
        maximum = (
            minimum
            * cfg.agent.max_action_attempts
            * (cfg.agent.transport_attempts + cfg.agent.local_truncation_retries)
        )
    warnings = []
    if ctl.replicates < 2:
        warnings.append(
            "One block gives descriptive contrasts only; no paired p-value or bootstrap CI."
        )
    if cfg.feedback.warning_source == "live":
        warnings.append(
            "Live warning exposure is behavior dependent and can be zero; this tests policies."
        )
    else:
        warnings.append(
            "Fixed notices are neutral hypothetical diagnostics, not factual misconduct warnings."
        )
    if not cfg.feedback.schedule_rounds and "placebo" in cfg.batch_arms:
        warnings.append("Placebo has no scheduled notices and is an empty-message control.")
    if cfg.rounds in cfg.feedback.schedule_rounds:
        warnings.append("A final-round notice has no next decision and cannot affect behavior.")
    if opts.bootstrap_min_pairs < 10:
        warnings.append(
            "Bootstrap threshold below 10 is a changed exploratory analysis, not more evidence."
        )
    if cfg.agent.decision_workers == 3:
        warnings.append(
            "Three concurrent firm workers share the workflow RPM/TPM and cost caps; extra keys do not guarantee extra upstream quota."
        )
        if ctl.requests_per_minute < 3:
            warnings.append(
                "Shared requests_per_minute is below 3; pacing prevents a full simultaneous three-request burst."
            )
    best_p = 2.0 ** (1 - ctl.replicates) if ctl.replicates >= 2 else None
    if best_p is None or best_p > opts.alpha / 4:
        warnings.append(
            "This block count cannot produce any Holm rejection across four core comparisons with the current two-sided exact sign-flip test, even in the best case. It remains a development sample."
        )
    if ctl.new_request_limit < maximum:
        warnings.append(
            "The workflow request cap is below the worst-case retry workload; it can stop early."
        )
    paid_blockers = []
    available_comparisons = {
        "public_minus_silent": ("public", "silent"),
        "private_minus_silent": ("private", "silent"),
        "public_minus_private": ("public", "private"),
        "placebo_minus_silent": ("placebo", "silent"),
        "numeric_minus_public": ("public_numeric", "public"),
        "public_fine_minus_public": ("public_fine", "public"),
        "private_fine_minus_private": ("private_fine", "private"),
        "fine_only_minus_silent": ("fine_only", "silent"),
        "private_fine_minus_fine_only": ("private_fine", "fine_only"),
        "public_fine_minus_fine_only": ("public_fine", "fine_only"),
        "public_fine_minus_private_fine": ("public_fine", "private_fine"),
    }
    selected = [
        name for name, arms in available_comparisons.items() if set(arms) <= set(cfg.batch_arms)
    ]
    question = "Effects of the selected audit policies on tail economic elevation for this model and market"
    if not selected:
        question = "Describe selected-condition execution and behavior; no supported between-arm contrast is assigned"
    elif cfg.feedback.warning_source == "fixed":
        question = "Effects of scheduled neutral-notification framing, not behavior-triggered misconduct warnings"
    if any(arm in cfg.batch_arms for arm in ("public_fine", "private_fine", "fine_only")):
        question = "Effects of the assigned financial regime and event-message publicity on gross-profit elevation; fines also change initial instructions"
        warnings.append(
            "Fine arms introduce punishment incentives; they do not isolate observe-only information."
        )
        if cfg.v2:
            warnings.append(
                f"Sanction disclosure={cfg.v2.sanction_disclosure}; changing disclosure is an information treatment requiring a new study ID/protocol. Historical missing settings mean transparent."
            )
    if "public_numeric" in cfg.batch_arms:
        warnings.append(
            "Numeric warnings add observed-price focal information beyond publicity framing."
        )
    if not cfg.auditor.enabled and cfg.feedback.warning_source == "live":
        warnings.append(
            "Detector disabled: no live warning mechanism can be exercised; scheduled Placebo is separate."
        )
    if paid:
        if cfg.agent.backend != "agentrouter" or cfg.agent.client != "qwen_code":
            paid_blockers.append(
                "This controller permits paid execution only through the shared Qwen allowance."
            )
        if held >= ctl.lifetime_cost_limit_usd:
            paid_blockers.append("The cumulative research spending allowance is exhausted.")
        if spending_caps and cap > min(ctl.new_cost_limit_usd, remaining_dollars) + 1e-10:
            paid_blockers.append(
                "Sum of per-run cost caps exceeds the new allowance or configured lifetime ceiling."
            )
        if minimum > min(ctl.new_request_limit, 4000 - ledger.get("reserved_attempts", 0)):
            paid_blockers.append(
                "The request allowance cannot cover one request per planned decision."
            )
        output_floor = (
            cfg.rounds
            * 3
            * (
                cfg.agent.max_output_tokens * float(cfg.budget.output_usd_per_million or 0)
                + cfg.budget.request_overhead_tokens * float(cfg.budget.input_usd_per_million or 0)
            )
            / 1e6
        )
        if spending_caps and output_floor > float(cfg.budget.max_cost_usd or 0):
            paid_blockers.append(
                "Even the output/overhead reservation floor exceeds the per-run cost cap."
            )
        warnings.append(
            "Per-run budgets accumulate full reservations; memory and retries may exhaust them early."
            if spending_caps
            else "Optional per-run/workflow spending caps are disabled; cost accounting and the cumulative research allowance remain active."
        )
        warnings.append("Rates are your inputs, not a gateway quote; actual billing can differ.")
    return {
        "configuration_valid": True,
        "algorithms": algorithm_selection(cfg),
        "paid": paid,
        "research_question": question,
        "assigned_supported_contrasts": selected,
        "primary_score": f"Mean final {cfg.analysis_last_rounds}-round {cfg.primary_outcome} elevation",
        "warning_mechanism": cfg.feedback.warning_source,
        "auditor_mode": cfg.auditor.mode,
        "horizon": "disclosed" if cfg.disclose_horizon else "hidden",
        "arms": cfg.batch_arms,
        "independent_blocks": ctl.replicates,
        "sign_flip_resolution": {
            "best_case_raw_p_with_no_zero_differences": best_p,
            "first_holm_threshold_four_tests": opts.alpha / 4,
            "adequate_power_established": False,
        },
        "resume_sweep": ctl.resume_sweep,
        "gateway_pacing": ledger.get("pacing", {}) if paid else {},
        "planned_trajectories": runs,
        "planned_market_rounds": cfg.rounds * runs,
        "minimum_requests_and_accepted_decisions": minimum,
        "worst_case_requests_before_caps": maximum,
        "batch_per_run_call_cap_sum": cfg.budget.max_calls * runs,
        "minimum_dispatch_minutes_at_selected_pacing": (
            (minimum - 1) // ctl.requests_per_minute if paid else None
        ),
        "spending_caps_enforced": spending_caps,
        "lifetime_cost_ceiling_enforced": True,
        "per_run_reservation_cap_usd": cfg.budget.max_cost_usd if paid and spending_caps else None,
        "batch_reservation_cap_usd": cap if paid and spending_caps else None,
        "configured_per_run_cost_limit_usd": cfg.budget.max_cost_usd,
        "configured_new_cost_limit_usd": ctl.new_cost_limit_usd,
        "configured_lifetime_cost_limit_usd": ctl.lifetime_cost_limit_usd,
        "known_prior_estimated_cost_usd": ledger.get("observed_usd", 0),
        "prior_known_plus_unknown_cost_holds_usd": held,
        "remaining_lifetime_reservation_allowance_usd": remaining_dollars,
        "paid_launch_blockers": paid_blockers,
        "warnings": warnings,
        "benchmarks": asdict(market.benchmarks),
        "upstream_commit": upstream,
        "analysis": opts.model_dump(mode="json"),
        "draft_digest": design_digest(study, controls, analysis),
    }


def freeze(
    root: Path, study: dict[str, Any], controls: dict[str, Any], analysis: dict[str, Any]
) -> Path:
    """Save a fresh plan and source snapshot; never overwrite an earlier plan or run."""
    report = preview(root, study, controls, analysis)
    cfg = StudyConfig.model_validate(study)
    ctl = Controls.model_validate(controls)
    identity = safe_identifier(f"nb-{ctl.label}-{uuid.uuid4().hex[:10]}")
    directory = root / "runs/notebook-control" / identity
    directory.mkdir(parents=True, exist_ok=False)
    config = cfg.model_dump(mode="json")
    atomic_json(directory / "study.json", config)
    atomic_json(directory / "controls.json", ctl.model_dump(mode="json"))
    atomic_json(directory / "analysis.json", AnalysisSettings.model_validate(analysis).model_dump())
    archived_source = source_snapshot(root, directory / "source.zip")
    for name in (
        "research_control.py",
        "research_progress.py",
        "validate_qwen_live.py",
        "agentrouter_credentials.py",
    ):
        shutil.copyfile(root / "scripts" / name, directory / name)
    notebook = root / "notebooks/00_research_control.ipynb"
    if notebook.exists():
        shutil.copyfile(notebook, directory / notebook.name)
    hashes = {p.name: file_hash(p) for p in directory.iterdir() if p.is_file()}
    manifest = {
        "schema_version": 1,
        "artifact_kind": "notebook_plan",
        "id": identity,
        "draft_digest": report["draft_digest"],
        "preview": report,
        "source_tree_sha256": archived_source,
        "hashes": hashes,
        "scripts": {
            name: file_hash(root / "scripts" / name)
            for name in (
                "research_control.py",
                "research_progress.py",
                "validate_qwen_live.py",
                "agentrouter_credentials.py",
            )
        },
    }
    atomic_json(directory / "plan.json", manifest)
    return directory / "plan.json"


def check_frozen(
    root: Path, plan_path: Path
) -> tuple[dict[str, Any], StudyConfig, Controls, AnalysisSettings]:
    """Reject edited plans, changed source or a changed protocol before execution."""
    plan_path = inside(root, plan_path.relative_to(root).as_posix())
    plan = read_json(plan_path)
    directory = plan_path.parent
    for name, expected in plan["hashes"].items():
        if file_hash(inside(root, (directory / name).relative_to(root).as_posix())) != expected:
            raise ValueError("Frozen file changed; make a new plan")
    for name, expected in plan["scripts"].items():
        if file_hash(root / "scripts" / name) != expected:
            raise ValueError("Controller source changed; make a new plan")
    temporary = root / ".cache" / f"notebook-source-{uuid.uuid4().hex}.zip"
    temporary.parent.mkdir(exist_ok=True)
    try:
        current = source_snapshot(root, temporary)
    finally:
        temporary.unlink(missing_ok=True)
    if current != plan["source_tree_sha256"]:
        raise ValueError("Study source or protocol changed; make a new plan")
    return (
        plan,
        StudyConfig.model_validate(read_json(directory / "study.json")),
        Controls.model_validate(read_json(directory / "controls.json")),
        AnalysisSettings.model_validate(read_json(directory / "analysis.json")),
    )


async def ensure_agentrouter_ready(
    root: Path,
    plan_path: Path,
    *,
    execute: bool = False,
    allow_paid: bool = False,
    validate_missing: bool = True,
    new_request_limit: int = 31,
    reuse_probe: bool = True,
    refresh_seconds: float = 1,
    on_line: Any = print,
) -> dict[str, Any]:
    """Complete missing model checks before launch, keeping diagnostics outside the experiment."""
    _, cfg, ctl, settings = check_frozen(root, plan_path)
    if cfg.agent.backend != "agentrouter" or cfg.agent.client != "qwen_code":
        return {"status": "not_required"}
    from validate_qwen_live import matching_live_validation, saved_connection

    matches = matching_live_validation(root, cfg.agent)
    if matches:
        on_line(f"AgentRouter ready: matching completed validation for {cfg.agent.model}.")
        return {"status": "ready", "reused": True, "validation": matches[-1]}
    if not execute:
        return {"status": "validation_required", "model": cfg.agent.model, "paid_requests": 0}
    if not allow_paid:
        raise ValueError("AgentRouter validation requires ALLOW_PAID=True before paid requests")
    feasibility = preview(
        root, cfg.model_dump(mode="json"), ctl.model_dump(), settings.model_dump()
    )
    if feasibility["paid_launch_blockers"]:
        raise ValueError("; ".join(feasibility["paid_launch_blockers"]))
    if not validate_missing:
        raise ValueError("Enable VALIDATE_AGENTROUTER to complete the missing model checks")
    if not 1 <= new_request_limit <= 4000 or refresh_seconds <= 0:
        raise ValueError("Choose a positive refresh interval and 1–4000 validation requests")
    command = [
        worker_python(root),
        "-X",
        "utf8",
        str(root / "scripts/validate_qwen_live.py"),
        str(plan_path.parent / "study.json"),
        "--allow-paid",
        "--new-request-limit",
        str(new_request_limit),
        "--new-cost-limit",
        str(ctl.new_cost_limit_usd),
        "--lifetime-cost-limit",
        str(ctl.lifetime_cost_limit_usd),
        "--requests-per-minute",
        str(ctl.requests_per_minute),
        "--tokens-per-minute",
        str(ctl.tokens_per_minute),
    ]
    ledger_path = root / "runs/diagnostics/qwen-live/allowance.json"
    if reuse_probe and ledger_path.exists():
        prior = read_json(ledger_path)
        if saved_connection(prior.get("requests", []), ledger_path.parent, cfg.agent):
            command.append("--reuse-probe")
    log_path = plan_path.parent / f"agentrouter-validation-{uuid.uuid4().hex[:8]}.log"
    on_line(
        f"Validating {cfg.agent.model} / {cfg.agent.prompt_profile}; up to {new_request_limit} new paid requests."
    )
    on_line("The experiment starts only after the separate smoke and isolation checks pass.")
    environment = {
        **os.environ,
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "PYTHONUNBUFFERED": "1",
    }
    with log_path.open("x", encoding="utf8") as output:
        process = subprocess.Popen(
            command,
            cwd=root,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    _OWNED_WORKERS[process.pid] = process
    status_path = plan_path.parent / "agentrouter-readiness.json"
    atomic_json(
        status_path,
        {"status": "running", "pid": process.pid, "log": log_path.relative_to(root).as_posix()},
    )
    try:
        with log_path.open(encoding="utf8", errors="replace") as stream:
            while True:
                for line in stream:
                    on_line(line.rstrip())
                if process.poll() is not None:
                    for line in stream:
                        on_line(line.rstrip())
                    break
                await asyncio.sleep(refresh_seconds)
        if process.returncode:
            atomic_json(
                status_path,
                {
                    "status": "failed",
                    "exit_code": process.returncode,
                    "log": log_path.relative_to(root).as_posix(),
                },
            )
            raise RuntimeError(
                f"AgentRouter validation failed. No experiment was launched. Inspect {log_path.relative_to(root).as_posix()}"
            )
        matches = matching_live_validation(root, cfg.agent)
        if not matches:
            atomic_json(
                status_path,
                {
                    "status": "failed",
                    "error": "matching_validation_missing",
                    "log": log_path.relative_to(root).as_posix(),
                },
            )
            raise RuntimeError(
                "The validator exited without matching completed evidence; experiment remains blocked"
            )
        result = {
            "status": "ready",
            "reused": False,
            "validation": matches[-1],
            "log": log_path.relative_to(root).as_posix(),
        }
        atomic_json(status_path, result)
        return result
    except (KeyboardInterrupt, asyncio.CancelledError):
        request_stop(root, plan_path)
        atomic_json(
            status_path,
            {
                "status": "stop_requested",
                "pid": process.pid,
                "log": log_path.relative_to(root).as_posix(),
            },
        )
        raise
    finally:
        if process.poll() is not None:
            forget_worker(process.pid)


def launch(
    root: Path,
    plan_path: Path,
    study: dict[str, Any],
    controls: dict[str, Any],
    analysis: dict[str, Any],
    *,
    allow_paid: bool = False,
    execute: bool = False,
) -> dict[str, Any]:
    """Start a background worker only after the notebook operator enables execution."""
    plan, cfg, _, _ = check_frozen(root, plan_path)
    if design_digest(study, controls, analysis) != plan["draft_digest"]:
        raise ValueError("Notebook controls changed after freezing; freeze and review a new plan")
    if not execute:
        return {
            "status": "preview_only",
            "message": "Set EXECUTE = True after reviewing the frozen plan.",
        }
    paid = cfg.agent.backend not in {"scripted", "ollama"}
    if paid and not allow_paid:
        raise ValueError("Paid execution is off; the notebook operator must set ALLOW_PAID = True")
    report = preview(root, study, controls, analysis)
    if paid and report["paid_launch_blockers"]:
        raise ValueError("; ".join(report["paid_launch_blockers"]))
    directory = plan_path.parent
    if (directory / "worker.json").exists():
        raise FileExistsError("This plan was already started; preserve it and freeze a new plan")
    command = [
        worker_python(root),
        "-X",
        "utf8",
        str(root / "scripts/research_control.py"),
        "worker",
        str(plan_path),
    ]
    if paid:
        command.append("--allow-paid")
    environment = {
        **os.environ,
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "PYTHONUNBUFFERED": "1",
    }
    with (directory / "worker.log").open("x", encoding="utf8") as log:
        process = subprocess.Popen(
            command,
            cwd=root,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    _OWNED_WORKERS[process.pid] = process
    return {
        "status": "starting",
        "pid": process.pid,
        "plan": plan_path.relative_to(root).as_posix(),
        "log": (directory / "worker.log").relative_to(root).as_posix(),
    }


def run_worker(root: Path, plan_path: Path, *, allow_paid: bool = False) -> int:
    """Run the existing sweep and analyzer with exactly the saved settings."""
    plan, cfg, ctl, settings = check_frozen(root, plan_path)
    directory = plan_path.parent
    status_path = directory / "worker.json"
    with status_path.open("x", encoding="utf8") as stream:
        stream.write(json.dumps({"status": "running", "pid": os.getpid()}))
    status: dict[str, Any] = {"status": "running", "pid": os.getpid()}
    paid = cfg.agent.backend not in {"scripted", "ollama"}
    try:
        if paid:
            if not allow_paid:
                raise ValueError("Paid execution requires --allow-paid")
            checked = preview(
                root, cfg.model_dump(mode="json"), ctl.model_dump(), settings.model_dump()
            )
            if checked["paid_launch_blockers"]:
                raise ValueError("; ".join(checked["paid_launch_blockers"]))
            command = [
                sys.executable,
                "-X",
                "utf8",
                str(root / "scripts/validate_qwen_live.py"),
                str(directory / "study.json"),
                "--sweep-config",
                "--allow-paid",
                "--sweep-id",
                plan["id"],
                "--replicates",
                str(ctl.replicates),
                "--assignment-seed",
                str(ctl.assignment_seed),
                "--analysis-settings",
                str(directory / "analysis.json"),
                "--new-request-limit",
                str(ctl.new_request_limit),
                "--new-cost-limit",
                str(ctl.new_cost_limit_usd),
                "--lifetime-cost-limit",
                str(ctl.lifetime_cost_limit_usd),
                "--requests-per-minute",
                str(ctl.requests_per_minute),
                "--tokens-per-minute",
                str(ctl.tokens_per_minute),
                "--workflow-seconds",
                str(ctl.workflow_seconds),
            ]
            if ctl.resume_sweep:
                command += ["--resume-sweep", str(inside(root, ctl.resume_sweep))]
            completed = subprocess.run(command, cwd=root, check=False)
            detail = root / "runs/diagnostics/qwen-live" / (plan["id"] + ".json")
            status.update(read_json(detail) if detail.exists() else {"status": "failed"})
            status["exit_code"] = completed.returncode
        else:
            from auditors_paradox.batch import run_sweep

            sweep = run_sweep(
                cfg,
                root,
                sweep_id=plan["id"],
                replicates=ctl.replicates,
                assignment_seed=ctl.assignment_seed,
                resume_from=inside(root, ctl.resume_sweep) if ctl.resume_sweep else None,
            )
            status.update(
                sweep_manifest=sweep.relative_to(root).as_posix(), status=read_json(sweep)["status"]
            )
            if status["status"] != "interrupted":
                summary = analyze_sweep(
                    sweep, root, root / "analysis" / plan["id"], settings=settings
                )
                status["analysis_summary"] = summary.relative_to(root).as_posix()
    except BaseException as exc:
        status.update(status="failed", error=type(exc).__name__, message=str(exc))
        raise
    finally:
        atomic_json(status_path, status)
    return 0 if status["status"] == "complete" else 1


def inspect(root: Path, plan_path: Path, *, log_lines: int = 12) -> dict[str, Any]:
    """Read progress and saved rounds without transmitting any model request."""
    from research_progress import ProgressReader

    return ProgressReader(root, plan_path).read(log_lines=log_lines)


def request_stop(root: Path, plan_path: Path) -> str:
    """Request a cooperative stop; committed rounds remain saved and are not resumed."""
    cfg = StudyConfig.model_validate(read_json(plan_path.parent / "study.json"))
    target = inside(root, cfg.budget.stop_file)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch(exist_ok=True)
    from auditors_paradox.evidence import utc_now

    atomic_json(
        plan_path.parent / "stop-request.json", {"requested_at": utc_now(), "source": "operator"}
    )
    return target.relative_to(root).as_posix()


def read_table(path: Path) -> list[dict[str, str]]:
    """Read exported run or round CSV without requiring pandas."""
    csv.field_size_limit(64 * 1024 * 1024)
    with path.open(encoding="utf8", newline="") as stream:
        return list(csv.DictReader(stream))


def auditor_diagnostics(root: Path, study: dict[str, Any]) -> Path:
    """Inspect configured rules on synthetic cases without asking a model to choose prices."""
    from auditors_paradox.auditing import BehavioralAuditor
    from auditors_paradox.economics import rewards
    from auditors_paradox.feedback.composer import load_feedback
    from auditors_paradox.feedback.router import route

    cfg = StudyConfig.model_validate(study)
    from auditors_paradox.adapters.collusionlab import Market, verify_upstream

    verify_upstream(root)
    market = Market.build(cfg)
    low, high = market.benchmarks.nash_price, market.benchmarks.joint_price
    length = max(12, 3 * cfg.auditor.window + cfg.auditor.cooldown_rounds)
    cfg = StudyConfig.model_validate(
        {**cfg.model_dump(mode="json"), "rounds": max(length, cfg.rounds)}
    )
    window = cfg.auditor.window
    scenarios = {
        "competitive_reference": [[low] * 3] * length,
        "sustained_joint_reference": [[high] * 3] * length,
        "one_round_spike": [[low] * 3] * window + [[high] * 3] + [[low] * 3] * window,
        "unilateral_elevation": [[high, low, low]] * length,
        "unaligned_elevated_prices": [[high, high, min(cfg.market.price_max, high + 10)]] * length,
    }
    output = root / "runs/diagnostics" / ("notebook-auditor-" + uuid.uuid4().hex[:10])
    output.mkdir(parents=True)
    result: dict[str, Any] = {
        "data_class": "scripted_detector_diagnostic",
        "model_requests": 0,
        "config": cfg.model_dump(mode="json"),
        "scenarios": {},
    }
    for name, sequence in scenarios.items():
        auditor = BehavioralAuditor(cfg.auditor, market.benchmarks, v2=bool(cfg.v2))
        events = [
            auditor.evaluate(t, prices, rewards(cfg.market, prices))
            for t, prices in enumerate(sequence, 1)
        ]
        result["scenarios"][name] = {
            "prices": sequence,
            "audit_events": [event.to_dict() for event in events],
            "flagged_rounds": [e.source_round for e in events if e.flagged],
            "public_scheduled_effective_rounds": [
                delivery.effective_round
                for event in events
                for delivery in route(
                    cfg.model_copy(
                        update={
                            "arm": (
                                "public_fine"
                                if cfg.v2 and cfg.v2.hypothesis_family.startswith("sanctions_")
                                else "public"
                            )
                        }
                    ),
                    event,
                    load_feedback(root, cfg),
                )
                if delivery.recipient == 0 and delivery.status == "scheduled_for_next_round"
            ],
        }
    atomic_json(output / "report.json", result)
    return output / "report.json"


def baseline_auditor_assessment(root: Path, study: dict[str, Any], manifests: list[str]) -> Path:
    """Replay the declared detector on compatible Silent evidence without selecting thresholds."""
    import numpy as np

    from auditors_paradox.adapters.collusionlab import Benchmarks
    from auditors_paradox.auditing import BehavioralAuditor
    from auditors_paradox.evaluation.validate import validate_run

    cfg = StudyConfig.model_validate(study)
    if not manifests or len(manifests) != len(set(manifests)):
        raise ValueError(
            "Supply distinct development Silent manifests; no runs are selected automatically"
        )
    result: dict[str, Any] = {
        "kind": "exploratory_silent_detector_assessment",
        "model_requests": 0,
        "thresholds_changed": False,
        "interpretation": "Alert frequency is not a false-positive rate; Silent can contain coordination.",
        "runs": [],
    }
    identities = set()
    for relative in manifests:
        path = inside(root, relative)
        if not path.is_relative_to(root.resolve() / "runs") or not validate_run(path)["valid"]:
            raise ValueError("Every baseline must be valid saved evidence inside runs")
        saved = read_json(path)
        original = StudyConfig.model_validate(saved["config"])
        if bool(original.v2) != bool(cfg.v2) or saved.get("researcher_intervened"):
            raise ValueError(
                "Baseline memory version differs or the trajectory was researcher-intervened"
            )
        if (
            cfg.v2
            and original.v2
            and any(
                getattr(cfg.v2, name) != getattr(original.v2, name)
                for name in ("history_format", "note_max_chars")
            )
        ):
            raise ValueError("Baseline used a different V2 memory profile")
        if original.arm != "silent" or original.run_kind == "confirmatory":
            raise ValueError("Only separate development Silent runs are eligible")
        for name in ("market", "memory_window", "disclose_horizon", "rounds"):
            if getattr(original, name) != getattr(cfg, name):
                raise ValueError(f"Baseline mismatch: {name}")
        for name in (
            "backend",
            "client",
            "model",
            "base_url",
            "api_protocol",
            "prompt_profile",
            "action_parser",
            "system_prompt_path",
            "action_prompt_path",
            "prompt_variables",
            "temperature",
            "max_output_tokens",
            "reasoning_effort",
            "context_tokens",
            "local_thinking",
        ):
            if getattr(original.agent, name) != getattr(cfg.agent, name):
                raise ValueError(f"Baseline agent mismatch: {name}")
        if cfg.agent.prompt_profile == "price_only" and saved.get("memory_policy") != (
            "authorized_outcomes_v2" if cfg.v2 else "price_only_canonical_v1"
        ):
            raise ValueError("Baseline used a different price-only memory policy")
        identities.add((saved["source_tree_sha256"], saved["data_class"]))
        events = [
            json.loads(line)
            for line in path.with_name("events.jsonl").read_text(encoding="utf8").splitlines()
        ]
        auditor = BehavioralAuditor(cfg.auditor, Benchmarks(**saved["benchmarks"]), v2=bool(cfg.v2))
        audits = [
            auditor.evaluate(
                e["payload"]["round"], e["payload"]["prices"], e["payload"]["gross_rewards"]
            )
            for e in events
            if e["type"] == "round"
        ]
        windows = [
            audits[i - cfg.auditor.window + 1 : i + 1]
            for i in range(cfg.auditor.window - 1, len(audits))
        ]
        values = {
            "round_profit_elevation": [a.evidence["reward_elevation"] for a in audits],
            "round_price_spread": [a.evidence["price_spread"] for a in audits],
            "window_min_profit_elevation": [
                min(a.evidence["reward_elevation"] for a in w) for w in windows
            ],
            "window_max_price_spread": [
                max(a.evidence["price_spread"] for a in w) for w in windows
            ],
        }
        flags = [a.source_round for a in audits if a.flagged]
        result["runs"].append(
            {
                "manifest": relative,
                "manifest_sha256": file_hash(path),
                "source_tree_sha256": saved["source_tree_sha256"],
                "data_class": saved["data_class"],
                "trigger_rounds": flags,
                "usable_trigger_rounds": [t for t in flags if t < cfg.rounds],
                "post_first_notice_rounds": cfg.rounds - flags[0] if flags else 0,
                "market_pattern_windows": sum(
                    all(
                        a.evidence["reward_elevation"] > cfg.auditor.elevation_threshold
                        and a.evidence["price_spread"] <= cfg.auditor.maximum_spread
                        for a in w
                    )
                    for w in windows
                ),
                "descriptive_quantiles_0_25_50_75_95_100": {
                    k: np.quantile(v, [0, 0.25, 0.5, 0.75, 0.95, 1]).tolist()
                    for k, v in values.items()
                },
            }
        )
    if len(identities) > 1:
        raise ValueError("Assess different source versions or synthetic/live baselines separately")
    result["auditor"] = cfg.auditor.model_dump()
    result["selection_warning"] = (
        "These are operator-selected development runs; no misconduct ground truth or automatic threshold optimization is supplied. Freeze future choices before fresh confirmation."
    )
    output = (
        root / "runs/diagnostics" / ("silent-detector-" + uuid.uuid4().hex[:10]) / "report.json"
    )
    output.parent.mkdir(parents=True)
    atomic_json(output, result)
    return output


def main() -> int:
    """Only the explicit worker command executes a study."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["worker", "inspect"])
    parser.add_argument("plan", type=Path)
    parser.add_argument("--allow-paid", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.action == "worker":
        return run_worker(root, args.plan.resolve(), allow_paid=args.allow_paid)
    print(json.dumps(inspect(root, args.plan.resolve()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
