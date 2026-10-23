"""Execute the shipped notebook using scripted agents only and preserve its validation output."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import nbformat
from jupyter_client import KernelManager
from nbclient import NotebookClient


def main() -> int:
    """Verify real cell execution, background progress and result rendering without API usage."""
    root = Path(__file__).resolve().parents[1]
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["JUPYTER_RUNTIME_DIR"] = str(root / ".cache/jupyter-runtime")
    os.environ["IPYTHONDIR"] = str(root / ".cache/ipython")
    notebook = nbformat.read(root / "notebooks/00_research_control.ipynb", as_version=4)
    identity = uuid.uuid4().hex[:10]
    nbformat.validate(notebook)
    config = next(c for c in notebook.cells if c.id == "configuration")
    config.source += """
# This executed COPY is a free engineering check; the operator's notebook is untouched.
STUDY["agent"].update(backend="scripted", client="sdk", model="scripted", base_url=None,
                      api_protocol="chat", reasoning_effort=None, scripted_replies=None,
                      scripted_policy="competitive", min_request_interval_seconds=0)
STUDY["agent"].update(credential_mode="shared", decision_workers=3)
STUDY.update(rounds=50, analysis_last_rounds=25,
             batch_arms=["silent", "private", "public", "placebo"])
STUDY["feedback"].update(warning_source="live", schedule_rounds=[10, 30])
CONTROL["replicates"] = 1
CONTROL["resume_sweep"] = None
BASELINE_MANIFESTS = []
EXECUTE = True
ALLOW_PAID = False
DISPLAY["refresh_seconds"] = 0.1
assert STUDY["agent"]["backend"] == "scripted"
"""
    config.source += (
        f'\nSTUDY["budget"]["stop_file"] = ".cache/notebook-validation-stop-{identity}"\n'
    )
    calibration = next(c for c in notebook.cells if c.id == "detector-calibration")
    calibration.source = calibration.source.replace(
        "RUN_AUDITOR_DIAGNOSTIC = False", "RUN_AUDITOR_DIAGNOSTIC = True"
    )
    launch = next(c for c in notebook.cells if c.id == "launch")
    launch.source += "\nassert STATE['status'] == 'complete'\nassert STATE['percent_complete'] == 100\nassert STATE['totals']['completed_rounds'] == 200\n"
    output = root / "runs/diagnostics" / ("notebook-control-" + identity)
    output.mkdir(parents=True)
    manager = KernelManager(kernel_name="python3")
    manager.kernel_spec.argv = [
        sys.executable,
        "-m",
        "ipykernel_launcher",
        "-f",
        "{connection_file}",
    ]
    client = NotebookClient(
        notebook, km=manager, timeout=120, resources={"metadata": {"path": str(root)}}
    )
    try:
        client.execute()
    finally:
        nbformat.write(notebook, output / "executed.ipynb")
    record = {
        "status": "passed",
        "backend": "scripted",
        "paid_requests": 0,
        "market_rounds": 200,
        "accepted_decisions": 600,
        "code_cells": sum(c.cell_type == "code" for c in notebook.cells),
        "blocking_launch": True,
        "progress_percent": 100,
        "validation_overrides": "scripted backend, 50 rounds, four arms, one block, paid disabled; saved notebook unchanged",
        "notebook": (output / "executed.ipynb").relative_to(root).as_posix(),
    }
    (output / "report.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf8")
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
