"""Test bounded model probes without any paid provider requests."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from auditors_paradox.adapters.model_calls import ProviderError
from auditors_paradox.config import AgentConfig
from auditors_paradox.contracts import Completion


@pytest.mark.parametrize("mode", ["mixed", "unauthorized", "empty", "stopped"])
def test_three_probes_keep_bounds_and_independent_evidence(config, tmp_path, monkeypatch, mode):
    """Keep failures separate, stop global failures, and preserve every completed reply."""
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "router_probes", root / "scripts/try_agentrouter_models.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config.agent = AgentConfig(
        backend="agentrouter", model="gpt-6-astra", base_url="https://co.agentrouter.org/v1"
    )
    before = config.model_dump_json()
    calls = []

    class FakeBackend:
        def __init__(self, agent):
            self.agent = agent

        def complete(self, messages):
            calls.append(self.agent.model)
            assert self.agent.max_output_tokens == 256 and self.agent.transport_attempts == 1
            assert messages == [
                {"role": "user", "content": "Reply with exactly OK. No explanation."}
            ]
            if mode in {"mixed", "unauthorized"} and len(calls) == 1:
                raise ProviderError(
                    "authentication_or_permission",
                    status_code=401 if mode == "unauthorized" else 403,
                )
            return Completion("" if mode == "empty" else "OK", 10, 5, self.agent.model)

    monkeypatch.setattr(module, "ProviderBackend", FakeBackend)
    if mode == "stopped":
        (tmp_path / ".stop").touch()
    directory, rows = module.run_probes(config, tmp_path)
    assert config.model_dump_json() == before
    assert [row["model"] for row in rows] == list(module.RATES)
    assert len(calls) == {"mixed": 3, "unauthorized": 1, "empty": 3, "stopped": 0}[mode]
    result = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    assert result["reserved_usd"] <= 0.10
    assert result["request_attempts"] == len(calls)
    assert (directory / "results.csv").exists()
    if mode == "mixed":
        assert [row["status"] for row in rows] == ["failed", "reply_received", "reply_received"]
        for row in rows[1:]:
            assert row["reported_model"] == row["model"]
            manifest = json.loads(
                (directory / row["model"] / "manifest.json").read_text(encoding="utf-8")
            )
            assert manifest["result"]["reported_model"] == row["model"]
        assert result["observed_estimate_usd"] == pytest.approx(2 * (10 * 8 + 5 * 40) / 1e6)
    elif mode in {"unauthorized", "stopped"}:
        assert all(row["status"] == "not_attempted" for row in rows[1:])
    else:
        assert all(row["status"] == "empty_reply" for row in rows)


def test_probe_cli_without_token_never_sends_request(monkeypatch, capsys):
    """Fail before loading a config or contacting a provider when no key is available."""
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "router_probes_main", root / "scripts/try_agentrouter_models.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv("AGENTROUTER_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["try_agentrouter_models.py", "missing-config.json"])
    assert module.main() == 2
    assert "Missing runtime" in capsys.readouterr().out


def test_probe_cli_loads_real_config_and_saves_results(tmp_path, monkeypatch, capsys):
    """Exercise the real entry point with a fake token and provider, never a live call."""
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "router_probes_entry", root / "scripts/try_agentrouter_models.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = json.loads(
        (root / "configs/diagnostics/local-repair-natural.json").read_text(encoding="utf-8")
    )
    data["agent"].update(
        backend="agentrouter",
        api_protocol="chat",
        model="gpt-6.1-sol",
        base_url="https://co.agentrouter.org/v1",
    )
    data["budget"].update(max_cost_usd=1, input_usd_per_million=3, output_usd_per_million=15)
    config_path = tmp_path / "config with spaces.json"
    config_path.write_text(json.dumps(data), encoding="utf-8")

    class FakeBackend:
        def __init__(self, agent):
            self.model = agent.model

        def complete(self, messages):
            return Completion("OK", 10, 2, self.model)

    monkeypatch.setattr(module, "ProviderBackend", FakeBackend)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "scripts/try_agentrouter_models.py"))
    monkeypatch.setenv("AGENTROUTER_API_KEY", "fake-token-never-print")
    monkeypatch.setattr(sys, "argv", ["try_agentrouter_models.py", str(config_path)])
    assert module.main() == 0
    output = capsys.readouterr().out
    assert output.count('"status": "reply_received"') == 3
    assert "fake-token-never-print" not in output
    assert (
        len(list((tmp_path / "runs/diagnostics/agentrouter-model-probes").glob("*/results.csv")))
        == 1
    )
