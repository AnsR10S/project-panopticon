"""Verify a real widget click can stop a run while the notebook launch cell is awaiting it."""

import json
import os
import queue
import re
import sys
import time
import uuid

import pytest


def test_stop_button_works_while_real_kernel_is_busy(root):
    jupyter = pytest.importorskip("jupyter_client")
    pytest.importorskip("ipywidgets")
    identity = uuid.uuid4().hex[:10]
    stop_relative = f".cache/widget-stop-{identity}"
    manager = jupyter.KernelManager(kernel_name="python3")
    manager.kernel_spec.argv = [
        sys.executable,
        "-m",
        "ipykernel_launcher",
        "-f",
        "{connection_file}",
    ]
    environment = {
        **os.environ,
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "IPYTHONDIR": str(root / ".cache/ipython-widget-test"),
    }
    manager.start_kernel(cwd=str(root), env=environment)
    client = manager.client()
    client.start_channels()
    clicked = False
    output = ""
    try:
        client.wait_for_ready(timeout=25)
        code = f"""
import sys, json
from pathlib import Path
ROOT = Path({str(root)!r})
sys.path.insert(0, str(ROOT / "scripts"))
import research_control as control
import research_progress as progress
from build_research_notebook import build
from auditors_paradox.config import StudyConfig
from auditors_paradox.evaluation.inference import AnalysisSettings
exec(next(c["source"] for c in build()["cells"] if c["id"] == "configuration"))
STUDY.update(rounds=6, analysis_last_rounds=3)
STUDY["feedback"]["schedule_rounds"] = [2]
STUDY["agent"]["min_request_interval_seconds"] = 0.3
STUDY["budget"]["stop_file"] = {stop_relative!r}
CONTROL["label"] = "widget-stop-test"
PLAN_PATH = control.freeze(ROOT, STUDY, CONTROL, ANALYSIS)
LAUNCH = control.launch(ROOT, PLAN_PATH, STUDY, CONTROL, ANALYSIS, execute=True)
STATE = await progress.watch(ROOT, PLAN_PATH, LAUNCH, refresh_seconds=0.05)
print("STOP_RESULT=" + json.dumps({{"status":STATE["status"], "percent":STATE["percent_complete"]}}))
"""
        request = client.execute(code)
        client.execute("print('LATER_STAGE_AFTER_LAUNCH')")
        button = None
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            try:
                message = client.get_iopub_msg(timeout=1)
            except queue.Empty:
                continue
            kind, content = message["header"]["msg_type"], message["content"]
            if (
                kind == "comm_open"
                and content.get("data", {}).get("state", {}).get("_model_name") == "ButtonModel"
                and content.get("data", {}).get("state", {}).get("description") == "Stop experiment"
            ):
                button = content["comm_id"]
            if (
                kind in {"comm_msg", "display_data", "update_display_data"}
                and not clicked
                and button
            ):
                value = content.get("data", {}).get("text/html", "")
                if kind == "comm_msg":
                    value = content.get("data", {}).get("state", {}).get("value", "")
                if re.search(r"Rounds [1-9][0-9]*/", value):
                    client.shell_channel.send(
                        client.session.msg(
                            "comm_msg",
                            content={
                                "comm_id": button,
                                "data": {"method": "custom", "content": {"event": "click"}},
                            },
                        )
                    )
                    clicked = True
            if kind == "stream":
                output += content["text"]
            if kind == "error":
                pytest.fail("\n".join(content.get("traceback", [])))
            if (
                kind == "status"
                and content["execution_state"] == "idle"
                and message["parent_header"].get("msg_id") == request
            ):
                break
        else:
            pytest.fail("The real kernel did not finish after the live Stop click")
        match = re.search(r"STOP_RESULT=(\{[^\n]+\})", output)
        assert clicked and match, output
        result = json.loads(match.group(1))
        assert (
            result["status"] == "stopped"
        ), "Widget click was not handled while the launch cell was busy"
        assert 0 < result["percent"] < 100
        evidence = root / "runs/diagnostics" / ("notebook-stop-ui-" + identity + ".json")
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "real_kernel": True,
                    "real_widget_comm_click": True,
                    "clicked_while_cell_busy": True,
                    "result": result,
                    "paid_requests": 0,
                },
                indent=2,
            )
            + "\n",
            encoding="utf8",
        )
    finally:
        (root / stop_relative).touch(exist_ok=True)
        client.stop_channels()
        manager.shutdown_kernel(now=True)
