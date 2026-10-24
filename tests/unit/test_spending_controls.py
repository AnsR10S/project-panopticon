"""Switch optional money caps without changing costs, pacing or research settings."""

import copy
import importlib
import json
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from auditors_paradox.adapters.model_calls import Budget, RunStopped, ScriptedBackend
from auditors_paradox.batch import run_sweep
from auditors_paradox.config import AgentConfig, BudgetConfig, StudyConfig
from auditors_paradox.contracts import Completion
from auditors_paradox.evaluation.inference import AnalysisSettings


@pytest.fixture
def spending_modules(root, monkeypatch):
    monkeypatch.syspath_prepend(str(root / "scripts"))
    return importlib.import_module("research_control"), importlib.import_module(
        "validate_qwen_live"
    )


def test_disabled_money_caps_keep_usage_and_request_limit():
    budget = Budget(
        BudgetConfig(
            enforce_spending_caps=False,
            max_calls=1,
            max_cost_usd=0.000001,
            input_usd_per_million=3,
            output_usd_per_million=15,
        ),
        AgentConfig(backend="openai", model="fixture", max_output_tokens=4096),
    )
    reserved = budget.reserve([{"role": "user", "content": "large reservation"}])
    budget.account(Completion("PRICE: 137", 20000, 4096, "fixture"))
    assert reserved > budget.config.max_cost_usd
    assert budget.observed_usd == pytest.approx(0.12144)
    assert budget.reserved_usd == reserved
    with pytest.raises(RuntimeError, match="model-call budget"):
        budget.reserve([])
    budget.cancel()
    with pytest.raises(RunStopped):
        budget.check_stop()


@pytest.mark.parametrize("kind", ["stop", "elapsed"])
def test_disabled_money_caps_keep_stop_and_timeout(tmp_path, kind):
    budget = Budget(
        BudgetConfig(enforce_spending_caps=False, max_elapsed_seconds=1),
        AgentConfig(),
        root=tmp_path,
    )
    if kind == "stop":
        (tmp_path / ".stop").touch()
    else:
        budget.started -= 2
    with pytest.raises(RunStopped):
        budget.check_stop()


def test_live_config_without_optional_dollar_cap_still_requires_rates():
    data = {
        "agent": {"backend": "openai", "model": "fixture"},
        "budget": {
            "enforce_spending_caps": False,
            "input_usd_per_million": 3,
            "output_usd_per_million": 15,
        },
    }
    cfg = StudyConfig.model_validate(data)
    assert cfg.budget.max_cost_usd is None
    data["budget"]["enforce_spending_caps"] = True
    with pytest.raises(ValidationError):
        StudyConfig.model_validate(data)
    data["budget"]["enforce_spending_caps"] = False
    data["budget"].pop("input_usd_per_million")
    with pytest.raises(ValidationError):
        StudyConfig.model_validate(data)


def test_notebook_defaults_and_upgrade_keep_the_switch_visible(root, spending_modules):
    from build_research_notebook import build, upgrade

    control, _ = spending_modules
    document = build()
    source = next(c["source"] for c in document["cells"] if c["id"] == "configuration")
    ns = {"StudyConfig": StudyConfig, "control": control, "AnalysisSettings": AnalysisSettings}
    exec(source, ns)
    assert ns["ENFORCE_SPENDING_CAPS"] is False
    assert ns["STUDY"]["budget"]["enforce_spending_caps"] is False
    assert ns["CONTROL"]["lifetime_cost_limit_usd"] == 1000
    edited = copy.deepcopy(document)
    cell = next(c for c in edited["cells"] if c["id"] == "configuration")
    cell["source"] = source.replace("ENFORCE_SPENDING_CAPS = False", "ENFORCE_SPENDING_CAPS = True")
    new = upgrade(edited)
    updated = next(c["source"] for c in new["cells"] if c["id"] == "configuration")
    assert "ENFORCE_SPENDING_CAPS = True" in updated
    assert updated.count("ENFORCE_SPENDING_CAPS =") == 1


def test_preview_ignores_optional_caps_but_keeps_total(root, spending_modules, monkeypatch):
    from build_research_notebook import build

    control, _ = spending_modules
    ns = {"StudyConfig": StudyConfig, "control": control, "AnalysisSettings": AnalysisSettings}
    exec(next(c["source"] for c in build()["cells"] if c["id"] == "configuration"), ns)
    study, controls, analysis = ns["STUDY"], ns["CONTROL"], ns["ANALYSIS"]
    study["agent"].update(
        backend="agentrouter",
        client="qwen_code",
        model="gpt-6.1-sol",
        base_url="https://agentrouter.org/v1",
        api_protocol="responses",
    )
    study["budget"]["max_cost_usd"] = 0.000001
    controls["new_cost_limit_usd"] = 0.000001
    ledger = root / "runs/diagnostics/qwen-live/allowance.json"
    original_exists = Path.exists
    monkeypatch.setattr(Path, "exists", lambda p: p == ledger or original_exists(p))
    monkeypatch.setattr(
        control,
        "read_json",
        lambda p: {"reserved_usd": 60, "observed_usd": 55, "reserved_attempts": 162},
    )
    before = copy.deepcopy(study)
    result = control.preview(root, study, controls, analysis)
    assert study == before and not result["paid_launch_blockers"]
    assert result["per_run_reservation_cap_usd"] is None
    assert result["batch_reservation_cap_usd"] is None
    assert result["remaining_lifetime_reservation_allowance_usd"] == 940
    study["budget"]["enforce_spending_caps"] = True
    assert control.preview(root, study, controls, analysis)["paid_launch_blockers"]
    study["budget"]["enforce_spending_caps"] = False
    monkeypatch.setattr(
        control, "read_json", lambda p: {"reserved_usd": 1000, "reserved_attempts": 162}
    )
    assert (
        "cumulative" in control.preview(root, study, controls, analysis)["paid_launch_blockers"][0]
    )


def test_worker_total_replaces_old_fifty_dollar_limit(spending_modules):
    control, live = spending_modules
    cfg = StudyConfig.model_validate({"budget": {"enforce_spending_caps": False}})
    assert live.workflow_cost_cap(cfg, 60, 0.01) == 1000
    assert live.workflow_cost_cap(cfg, 990, 0.01) == 1000
    cfg.budget.enforce_spending_caps = True
    assert live.workflow_cost_cap(cfg, 60, 0.01) == 60.01
    assert live.workflow_cost_cap(cfg, 999, 100) == 1000
    with pytest.raises(ValueError):
        live.workflow_cost_cap(cfg, 0, 1, 1001)
    with pytest.raises(ValidationError):
        control.Controls(lifetime_cost_limit_usd=1001)
    with pytest.raises(ValueError):
        live.workflow_cost_cap(cfg, 0, -1)


def test_real_sweep_with_disabled_money_caps_is_still_explicit(root, tmp_path, config):
    data = config.model_dump(mode="json")
    data.update(rounds=2, analysis_last_rounds=1, batch_arms=["silent"])
    data["feedback"]["schedule_rounds"] = []
    data["agent"].update(backend="openai", model="local-fixture")
    data["budget"].update(
        enforce_spending_caps=False,
        max_cost_usd=None,
        input_usd_per_million=3,
        output_usd_per_million=15,
    )
    cfg = StudyConfig.model_validate(data)
    with pytest.raises(ValueError, match="allow-paid"):
        run_sweep(cfg, root, output_root=tmp_path)
    path = run_sweep(
        cfg,
        root,
        output_root=tmp_path,
        replicates=1,
        allow_paid=True,
        backend_factory=lambda c, m, i: ScriptedBackend([f"PRICE: {cfg.market.price_min}"] * 2),
    )
    assert json.loads(path.read_text())["status"] == "complete"


def test_optional_probe_cap_can_be_disabled_without_disabling_total(root, spending_modules):
    _, live = spending_modules
    agent = AgentConfig(backend="agentrouter", model="gpt-5.6-sol", max_output_tokens=16)
    budget = Budget(
        BudgetConfig(
            max_calls=2, max_cost_usd=1000, input_usd_per_million=3, output_usd_per_million=15
        ),
        agent,
    )
    budget.reserved_usd = 60
    records = [{"alternative_probe": True, "reserved_usd": 2}]

    class FakeClient:
        def __init__(self, agent, root, credential_env):
            self.directory = root / "runs/diagnostics/fake-spending"
            self.last_evidence = {}

        def set_stop_check(self, check):
            pass

        def complete(self, messages):
            self.last_evidence = {"network_requests": 1, "status": "complete"}
            return Completion("PRICE: 137", 20, 4, "fixture")

        def close(self):
            pass

    client = live.ManagedBackend(
        agent,
        root,
        budget,
        live.RequestPacer({}, 100, 100000),
        records,
        lambda: None,
        "local-fixture",
        threading.RLock(),
        alternative_probe=True,
        enforce_probe_cost_cap=False,
        backend_factory=FakeClient,
    )
    assert client.complete([{"role": "user", "content": "price"}]).text == "PRICE: 137"
    assert budget.observed_usd > 0 and records[-1]["status"] == "complete"
    budget.reserved_usd = 1000
    with pytest.raises(RuntimeError, match="reservation budget"):
        client.complete([])
