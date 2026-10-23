"""Exercise the catalogue diagnostic without real credentials or inference."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from auditors_paradox.config import AgentConfig


@pytest.mark.parametrize("status", [None, 401, 403, 404, "client_rejected"])
def test_catalogue_check_reports_safe_status_only(config, monkeypatch, capsys, status):
    """List models once and never print a provider body that could contain a secret."""
    path = Path(__file__).resolve().parents[2] / "scripts/check_agentrouter.py"
    spec = importlib.util.spec_from_file_location("check_router", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config.agent = AgentConfig(
        backend="agentrouter", model="gpt-6.1-sol", base_url="https://co.agentrouter.org/v1"
    )
    calls = []

    def catalogue():
        calls.append("GET models")
        if status:
            error = RuntimeError("fake-secret-provider-payload")
            error.status_code = 401 if status == "client_rejected" else status
            if status == "client_rejected":
                error.body = {"error": {"message": "unauthorized client detected"}}
            raise error
        return NS(data=[NS(id="gpt-6.1-sol")])

    monkeypatch.setattr(
        module, "ProviderBackend", lambda cfg: NS(client=NS(models=NS(list=catalogue)))
    )
    assert module.check(config) == (1 if status else 0)
    output = capsys.readouterr().out
    assert "fake-secret-provider-payload" not in output
    assert calls == ["GET models"]
    if status:
        assert f'"http_status": {401 if status == "client_rejected" else status}' in output
        if status == "client_rejected":
            assert '"error_kind": "client_not_authorized"' in output
            assert "Request provider authorization" in output
    else:
        assert '"model_listed": true' in output


def test_check_entry_point_loads_config_path(tmp_path, monkeypatch, capsys):
    """Run argument parsing and real config loading before the fake catalogue call."""
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "check_router_main", root / "scripts/check_agentrouter.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cfg = (root / "configs/diagnostics/local-repair-natural.json").read_text(encoding="utf-8")
    data = json.loads(cfg)
    data["agent"].update(
        backend="agentrouter",
        model="gpt-6.1-sol",
        base_url="https://co.agentrouter.org/v1",
        api_protocol="chat",
    )
    data["budget"].update(max_cost_usd=1, input_usd_per_million=3, output_usd_per_million=15)
    config_path = tmp_path / "router config.json"
    config_path.write_text(json.dumps(data), encoding="utf-8")
    calls = []

    def backend(agent):
        calls.append(agent.model)
        return NS(client=NS(models=NS(list=lambda: NS(data=[NS(id="gpt-6.1-sol")]))))

    monkeypatch.setattr(module, "ProviderBackend", backend)
    monkeypatch.setattr(sys, "argv", [str(root / "scripts/check_agentrouter.py"), str(config_path)])
    assert module.main() == 0
    assert calls == ["gpt-6.1-sol"]
    assert '"model_listed": true' in capsys.readouterr().out
