"""Run All validates the selected route without bypassing failures or starting paid work in preview."""

import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest

from auditors_paradox.config import StudyConfig
from auditors_paradox.evaluation.inference import AnalysisSettings


@pytest.fixture
def setup_readiness(root, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(root / "scripts"))
    controller = importlib.import_module("research_control")
    validation = importlib.import_module("validate_qwen_live")
    cfg = StudyConfig.model_validate(
        {
            "agent": {
                "backend": "agentrouter",
                "client": "qwen_code",
                "model": "gpt-5.6-sol",
                "base_url": "https://agentrouter.org/v1",
                "api_protocol": "responses",
            },
            "budget": {
                "max_cost_usd": 10,
                "input_usd_per_million": 8,
                "output_usd_per_million": 40,
            },
        }
    )
    plan = tmp_path / "plan/plan.json"
    plan.parent.mkdir()
    controls = controller.Controls(requests_per_minute=3)
    monkeypatch.setattr(
        controller, "check_frozen", lambda *a: ({}, cfg, controls, AnalysisSettings())
    )
    monkeypatch.setattr(controller, "preview", lambda *a: {"paid_launch_blockers": []})
    return controller, validation, cfg, plan, tmp_path


def evidence(root, cfg, status="complete"):
    directory = root / "runs/diagnostics/qwen-live"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "validation-case.json").write_text(
        json.dumps(
            {
                "status": status,
                "config": cfg.model_dump(mode="json"),
            }
        )
    )


def test_preview_does_not_start_missing_validation(setup_readiness, monkeypatch):
    controller, _, _, plan, root = setup_readiness
    monkeypatch.setattr(
        controller.subprocess, "Popen", lambda *a, **k: pytest.fail("No paid request in preview")
    )
    result = asyncio.run(controller.ensure_agentrouter_ready(root, plan))
    assert result["status"] == "validation_required" and result["paid_requests"] == 0


def test_matching_validation_is_reused_without_new_work(setup_readiness, monkeypatch):
    controller, _, cfg, plan, root = setup_readiness
    evidence(root, cfg)
    monkeypatch.setattr(
        controller.subprocess, "Popen", lambda *a, **k: pytest.fail("Already validated")
    )
    result = asyncio.run(
        controller.ensure_agentrouter_ready(
            root, plan, execute=True, allow_paid=True, on_line=lambda s: None
        )
    )
    assert result["status"] == "ready" and result["reused"]


@pytest.mark.parametrize(
    "status", ["failed", "probe_complete", "pricing_probe_complete", "running"]
)
def test_partial_checks_do_not_qualify_a_full_pilot(setup_readiness, status):
    _, validation, cfg, _, root = setup_readiness
    evidence(root, cfg, status)
    assert validation.matching_live_validation(root, cfg.agent) == []
    if status.endswith("probe_complete"):
        assert validation.matching_live_validation(root, cfg.agent, allow_probe=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("model", "gpt-6.1-sol"),
        ("base_url", "https://co.agentrouter.org/v1"),
        ("api_protocol", "chat"),
        ("prompt_profile", "price_first"),
        ("max_output_tokens", 512),
        ("temperature", 0.6),
        ("reasoning_effort", "low"),
    ],
)
def test_settings_must_match(setup_readiness, field, value):
    _, validation, cfg, _, root = setup_readiness
    evidence(root, cfg)
    changed = cfg.agent.model_copy(update={field: value})
    assert validation.matching_live_validation(root, changed) == []


def test_paid_permission_and_enabled_readiness_are_required(setup_readiness, monkeypatch):
    controller, _, _, plan, root = setup_readiness
    monkeypatch.setattr(
        controller.subprocess, "Popen", lambda *a, **k: pytest.fail("Still blocked")
    )
    with pytest.raises(ValueError, match="ALLOW_PAID"):
        asyncio.run(controller.ensure_agentrouter_ready(root, plan, execute=True))
    with pytest.raises(ValueError, match="VALIDATE_AGENTROUTER"):
        asyncio.run(
            controller.ensure_agentrouter_ready(
                root, plan, execute=True, allow_paid=True, validate_missing=False
            )
        )


@pytest.mark.parametrize("success", [True, False])
def test_validation_success_and_failure_are_separate_from_launch(
    setup_readiness, monkeypatch, success
):
    controller, _, cfg, plan, root = setup_readiness
    commands, lines = [], []

    def start(command, **kwargs):
        commands.append(command)
        kwargs["stdout"].write("Separate diagnostic execution\n")
        kwargs["stdout"].flush()
        if success:
            evidence(root, cfg)
        return SimpleNamespace(
            pid=987654, returncode=0 if success else 1, poll=lambda: 0 if success else 1
        )

    monkeypatch.setattr(controller.subprocess, "Popen", start)
    coroutine = controller.ensure_agentrouter_ready(
        root, plan, execute=True, allow_paid=True, new_request_limit=31, on_line=lines.append
    )
    if success:
        result = asyncio.run(coroutine)
        assert result["status"] == "ready" and not result["reused"]
    else:
        with pytest.raises(RuntimeError, match="No experiment was launched"):
            asyncio.run(coroutine)
    assert len(commands) == 1
    assert "--sweep-config" not in commands[0]
    assert commands[0][commands[0].index("--new-request-limit") + 1] == "31"
    assert "Separate diagnostic execution" in lines
    assert controller.worker_process(987654) is None


def test_non_agentrouter_connection_is_skipped(setup_readiness, monkeypatch):
    controller, _, _, plan, root = setup_readiness
    cfg = StudyConfig()
    monkeypatch.setattr(
        controller, "check_frozen", lambda *a: ({}, cfg, controller.Controls(), AnalysisSettings())
    )
    assert asyncio.run(controller.ensure_agentrouter_ready(root, plan))["status"] == "not_required"


def test_exit_zero_without_completed_evidence_still_blocks(setup_readiness, monkeypatch):
    controller, _, _, plan, root = setup_readiness
    monkeypatch.setattr(
        controller.subprocess,
        "Popen",
        lambda *a, **k: SimpleNamespace(
            pid=987655,
            returncode=0,
            poll=lambda: 0,
        ),
    )
    with pytest.raises(RuntimeError, match="without matching completed evidence"):
        asyncio.run(
            controller.ensure_agentrouter_ready(
                root, plan, execute=True, allow_paid=True, on_line=lambda s: None
            )
        )
    status = json.loads((plan.parent / "agentrouter-readiness.json").read_text())
    assert status["status"] == "failed" and status["error"] == "matching_validation_missing"
