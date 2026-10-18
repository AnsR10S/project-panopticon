"""Run randomized blocks of the selected arms and keep the failed assignments too."""

from __future__ import annotations

import json
import math
import random
import uuid
from pathlib import Path
from typing import Any

from auditors_paradox.adapters.model_calls import ProviderError, RunStopped
from auditors_paradox.config import StudyConfig, safe_identifier
from auditors_paradox.evidence import atomic_json, utc_now
from auditors_paradox.runner import BackendFactory, RunFailed, run_experiment


def run_sweep(
    cfg: StudyConfig,
    root: Path,
    *,
    sweep_id: str | None = None,
    replicates: int = 3,
    assignment_seed: int = 31415,
    allow_paid: bool = False,
    max_total_cost_usd: float | None = None,
    backend_factory: BackendFactory | None = None,
    output_root: Path | None = None,
    resume_from: Path | None = None,
) -> Path:
    """Run the arms in a random order inside each replicate block, failures stay recorded.

    Seeds only label the blocks, they don't promise the same model replies across arms.
    """
    if not 1 <= replicates <= 1000:
        raise ValueError("replicates must be between 1 and 1000")
    if cfg.agent.backend not in {"scripted", "ollama"}:
        if not allow_paid:
            raise ValueError("Live sweeps require --allow-paid")
        if cfg.budget.enforce_spending_caps and (
            max_total_cost_usd is None
            or not math.isfinite(max_total_cost_usd)
            or max_total_cost_usd <= 0
        ):
            raise ValueError("Live sweeps require --allow-paid and an explicit total sweep budget")
        if (
            cfg.budget.enforce_spending_caps
            and resume_from is None
            and (
                float(cfg.budget.max_cost_usd or 0) * len(cfg.batch_arms) * replicates
                > float(max_total_cost_usd or 0)
            )
        ):
            raise ValueError("Per-run budgets exceed the explicit total sweep budget")
    identity = safe_identifier(sweep_id or f"sweep-{uuid.uuid4().hex[:10]}")
    parent = None
    if resume_from is not None:
        from auditors_paradox.continuation import check_sweep_checkpoint

        parent = check_sweep_checkpoint(resume_from, cfg, root)
        from auditors_paradox.continuation import remaining_work

        if (
            cfg.budget.enforce_spending_caps
            and cfg.agent.backend not in {"scripted", "ollama"}
            and remaining_work(parent, root)[1] > float(max_total_cost_usd or 0)
        ):
            raise ValueError("Remaining continuation reservations exceed the new sweep allowance")
        if parent["replicates"] != replicates or parent["assignment_seed"] != assignment_seed:
            raise ValueError(
                "Continuation must retain the original block count and assignment seed"
            )
    base = (output_root or root / "runs" / cfg.run_kind).resolve()
    directory = base / cfg.study_id / "sweeps" / identity
    if not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("Sweep output must stay inside this project")
    # Check the generated run IDs before we create the sweep folder.
    for arm in cfg.batch_arms:
        safe_identifier(f"{identity}-{arm}-r{replicates - 1:04d}")
    directory.mkdir(parents=True, exist_ok=False)
    rng = random.Random(assignment_seed)
    assignments: list[dict[str, Any]] = []
    for index in range(replicates):
        arms = list(cfg.batch_arms)
        rng.shuffle(arms)
        for arm in arms:
            assignments.append(
                {
                    "arm": arm,
                    "replicate_id": f"r{index:04d}",
                    "seed": cfg.seed + index,
                    "run_id": f"{identity}-{arm}-r{index:04d}",
                }
            )
    atomic_json(directory / "assignment.json", assignments)
    if parent is not None:
        assignments = [
            {k: row[k] for k in ("arm", "replicate_id", "seed", "run_id")} for row in parent["runs"]
        ]
        atomic_json(directory / "assignment.json", assignments)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "artifact_kind": "sweep",
        "sweep_id": identity,
        "study_id": cfg.study_id,
        "status": "running",
        "started_at": utc_now(),
        "assignment_seed": assignment_seed,
        "planned_runs": len(assignments),
        "replicates": replicates,
        "max_total_cost_usd": max_total_cost_usd,
        "base_config": cfg.model_dump(mode="json"),
        "runs": [],
    }
    path = directory / "manifest.json"
    if parent is not None:
        assert resume_from is not None
        manifest.update(
            sweep_id=parent["sweep_id"],
            continuation_attempt_id=identity,
            resumed_from=resume_from.relative_to(root).as_posix(),
        )
    atomic_json(path, manifest)
    try:
        for order, assignment in enumerate(assignments):
            prior = parent["runs"][order] if parent is not None else None
            if prior is not None and prior["status"] == "complete":
                manifest["runs"].append(dict(prior))
                atomic_json(path, manifest)
                continue
            data = cfg.model_dump(mode="json")
            data.update(arm=assignment["arm"], seed=assignment["seed"])
            current = StudyConfig.model_validate(data)
            outcome = {**assignment, "execution_order": order, "status": "failed"}
            halt = False
            try:
                result = run_experiment(
                    current,
                    root,
                    run_id=assignment["run_id"],
                    replicate_id=assignment["replicate_id"],
                    assignment_block=manifest["sweep_id"],
                    allow_paid=allow_paid,
                    backend_factory=backend_factory,
                    output_root=directory / "continued-runs" if parent is not None else base,
                    resume_from=(
                        root / prior["manifest_path"]
                        if prior and prior["status"] == "failed"
                        else None
                    ),
                )
                outcome.update(status="complete", manifest_path=str(result.relative_to(root)))
            except RunFailed as exc:
                halt = isinstance(exc.__cause__, (ProviderError, RunStopped))
                outcome.update(
                    manifest_path=str(exc.manifest.relative_to(root)), error_type="RunFailed"
                )
            except (ValueError, OSError) as exc:
                outcome.update(manifest_path=None, error_type=type(exc).__name__)
            manifest["runs"].append(outcome)
            atomic_json(path, manifest)
            if halt:
                manifest["stop_reason"] = "provider_or_operator_stop"
                for remaining_order, remaining in enumerate(assignments[order + 1 :], order + 1):
                    manifest["runs"].append(
                        {
                            **remaining,
                            "execution_order": remaining_order,
                            "status": "not_run",
                            "manifest_path": None,
                        }
                    )
                break
        completed = sum(row["status"] == "complete" for row in manifest["runs"])
        manifest.update(
            status=(
                "complete"
                if completed == len(assignments)
                else "partial" if completed else "failed"
            ),
            completed_runs=completed,
            failed_runs=len(assignments) - completed,
            ended_at=utc_now(),
        )
        atomic_json(path, manifest)
    except (KeyboardInterrupt, SystemExit):
        manifest.update(status="interrupted", ended_at=utc_now())
        atomic_json(path, manifest)
        raise
    return path


def load_sweep(path: Path) -> dict[str, Any]:
    """Read a sweep manifest and reject anything that is not a sweep."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if data.get("artifact_kind") != "sweep":
        raise ValueError("Analysis requires an explicit sweep manifest")
    return data
