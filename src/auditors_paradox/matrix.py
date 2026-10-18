"""Run and analyze each auditor by horizon condition separately."""

from __future__ import annotations

import json
import math
import random
import uuid
from pathlib import Path

from auditors_paradox.batch import run_sweep
from auditors_paradox.config import StudyConfig, safe_identifier
from auditors_paradox.evaluation.inference import analyze_sweep
from auditors_paradox.evidence import atomic_json


def run_matrix(
    cfg: StudyConfig,
    root: Path,
    *,
    matrix_id: str | None = None,
    replicates: int = 2,
    allow_paid: bool = False,
    max_total_cost_usd: float | None = None,
    output_root: Path | None = None,
) -> Path:
    """Run all four auditor by horizon conditions with the selected arms.

    Rule settings stay fixed, every assignment is saved, and a paid budget must cover all four.
    """
    if not 1 <= replicates <= 1000:
        raise ValueError("replicates must be between 1 and 1000")
    paid = cfg.agent.backend not in {"scripted", "ollama"}
    per_cell = float(cfg.budget.max_cost_usd or 0) * len(cfg.batch_arms) * replicates
    if paid and not allow_paid:
        raise ValueError("Paid matrices require --allow-paid")
    if (
        paid
        and cfg.budget.enforce_spending_caps
        and (
            max_total_cost_usd is None
            or not math.isfinite(max_total_cost_usd)
            or max_total_cost_usd <= 0
            or 4 * per_cell > max_total_cost_usd
        )
    ):
        raise ValueError("Paid matrices require an explicit budget covering all four cells")
    identity = safe_identifier(matrix_id or f"matrix-{uuid.uuid4().hex[:8]}")
    base = (output_root or root / "runs" / cfg.run_kind).resolve()
    directory = base / cfg.study_id / "matrices" / identity
    if not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("Matrix outputs must stay inside the project")
    cells = []
    for mode in ("market", "per_firm"):
        for disclosed in (False, True):
            label = f"{mode}-{'disclosed' if disclosed else 'hidden'}"
            safe_identifier(f"{identity}-{label}-public_numeric-r{replicates - 1:04d}")
            cells.append(
                {
                    "cell": label,
                    "auditor_mode": mode,
                    "disclose_horizon": disclosed,
                    "status": "assigned",
                }
            )
    random.Random(cfg.seed).shuffle(cells)
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "manifest.json"
    manifest = {
        "artifact_kind": "matrix",
        "matrix_id": identity,
        "status": "running",
        "cell_order_seed": cfg.seed,
        "planned_runs": 4 * len(cfg.batch_arms) * replicates,
        "max_total_cost_usd": max_total_cost_usd,
        "cells": cells,
    }
    atomic_json(path, manifest)
    try:
        for cell_index, cell in enumerate(cells):
            data = cfg.model_dump(mode="json")
            data.update(
                disclose_horizon=cell["disclose_horizon"],
                auditor={**data["auditor"], "mode": cell["auditor_mode"]},
            )
            current = StudyConfig.model_validate(data)
            sweep = run_sweep(
                current,
                root,
                sweep_id=f"{identity}-{cell['cell']}",
                replicates=replicates,
                allow_paid=allow_paid,
                max_total_cost_usd=per_cell if paid else None,
                output_root=base,
            )
            cell.update(
                status=json.loads(sweep.read_text(encoding="utf-8"))["status"],
                sweep_manifest=str(sweep.relative_to(root)),
            )
            analysis_base = base / "analysis" if output_root else root / "analysis"
            report = analyze_sweep(sweep, root, analysis_base / identity / str(cell["cell"]))
            cell["analysis"] = str(report.relative_to(root))
            atomic_json(path, manifest)
            if json.loads(sweep.read_text(encoding="utf-8")).get("stop_reason"):
                manifest["stop_reason"] = "provider_or_operator_stop"
                for remaining in cells[cell_index + 1 :]:
                    remaining["status"] = "not_run"
                break
        manifest["status"] = (
            "complete" if all(c["status"] == "complete" for c in cells) else "partial"
        )
    except (KeyboardInterrupt, SystemExit):
        manifest["status"] = "interrupted"
        raise
    except Exception:
        manifest["status"] = "failed"
        raise
    finally:
        atomic_json(path, manifest)
    return path
