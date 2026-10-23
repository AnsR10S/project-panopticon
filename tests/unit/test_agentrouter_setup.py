"""Check the provider setup and GPT chat request shape without using real credentials."""

import importlib.util
import shutil
import sys
from types import SimpleNamespace as NS

import pytest

from auditors_paradox.adapters.model_calls import ProviderBackend
from auditors_paradox.config import AgentConfig, load_config


@pytest.mark.parametrize("parameter", ["max_tokens", "max_completion_tokens"])
def test_agentrouter_token_limit_parameter_is_explicit(monkeypatch, parameter):
    """Send only the configured completion limit and omit an unspecified temperature."""
    requests = []

    def complete(**kwargs):
        requests.append(kwargs)
        return NS(
            choices=[NS(message=NS(content="PRICE: 137"))],
            usage=NS(prompt_tokens=20, completion_tokens=4),
            model="gpt-6.1-sol",
        )

    monkeypatch.setenv("AGENTROUTER_API_KEY", "fake-unit-test-token")
    monkeypatch.setitem(
        sys.modules,
        "openai",
        NS(OpenAI=lambda **kwargs: NS(chat=NS(completions=NS(create=complete)))),
    )
    cfg = AgentConfig(
        backend="agentrouter",
        model="gpt-6.1-sol",
        base_url="https://co.agentrouter.org/v1",
        chat_token_parameter=parameter,
        max_output_tokens=768,
    )
    answer = ProviderBackend(cfg).complete([{"role": "user", "content": "price?"}])
    assert answer.text == "PRICE: 137"
    assert requests[0][parameter] == 768
    assert "temperature" not in requests[0]
    assert ({"max_tokens", "max_completion_tokens"} - {parameter}).isdisjoint(requests[0])


def test_setup_writes_distinct_secret_free_configs_and_preserves_existing(
    root, tmp_path, monkeypatch
):
    """Preparation needs prices, never credentials, and cannot overwrite prior settings."""
    spec = importlib.util.spec_from_file_location(
        "setup_router", root / "scripts/setup_agentrouter.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    base = tmp_path / "configs/diagnostics"
    base.mkdir(parents=True)
    shutil.copyfile(
        root / "configs/diagnostics/local-repair-natural.json", base / "local-repair-natural.json"
    )
    monkeypatch.setattr(module, "preflight", lambda *args, **kwargs: None)
    monkeypatch.delenv("AGENTROUTER_API_KEY", raising=False)
    paths = module.prepare(
        tmp_path,
        "gpt-6.1-sol",
        3,
        15,
        1,
        20,
        "https://co.agentrouter.org/v1",
        "max_completion_tokens",
    )
    smoke, full = [load_config(p) for p in paths]
    assert smoke.rounds == 3 and smoke.batch_arms == ["silent"]
    assert smoke.budget.max_calls == 18 and smoke.study_purpose == "diagnostic"
    assert full.rounds == 100 and len(full.batch_arms) == 4 and full.analysis_last_rounds == 20
    assert full.memory_window == 20 and full.budget.max_cost_usd == 20
    assert full.agent.temperature is None and full.agent.prompt_profile == "price_first"
    assert full.agent.chat_token_parameter == "max_completion_tokens"
    before = [p.read_bytes() for p in paths]
    assert all("api_key" not in p.read_text().lower() for p in paths)
    with pytest.raises(ValueError, match="already exist"):
        module.prepare(
            tmp_path, "gpt-6.1-sol", 3, 15, 1, 20, "https://co.agentrouter.org/v1", "max_tokens"
        )
    assert before == [p.read_bytes() for p in paths]


def test_invalid_rates_do_not_create_partial_setup(root, tmp_path, monkeypatch):
    """Bad account rates fail validation before either config is written."""
    spec = importlib.util.spec_from_file_location(
        "setup_router_invalid", root / "scripts/setup_agentrouter.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    base = tmp_path / "configs/diagnostics"
    base.mkdir(parents=True)
    shutil.copyfile(
        root / "configs/diagnostics/local-repair-natural.json", base / "local-repair-natural.json"
    )
    monkeypatch.setattr(module, "preflight", lambda *args, **kwargs: None)
    with pytest.raises(ValueError):
        module.prepare(
            tmp_path, "gpt-6.1-sol", -1, 15, 1, 20, "https://co.agentrouter.org/v1", "max_tokens"
        )
    assert not (tmp_path / "configs/local").exists()


def test_setup_cli_defaults_to_main_account_host(root, monkeypatch):
    """Default the real command-line parser to the documented main host."""
    spec = importlib.util.spec_from_file_location(
        "setup_router_main", root / "scripts/setup_agentrouter.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    monkeypatch.setattr(module, "prepare", lambda *args, **kwargs: calls.append(args) or [])
    monkeypatch.setattr(
        sys, "argv", ["setup_agentrouter.py", "--input-rate", "3", "--output-rate", "15"]
    )
    module.main()
    assert calls[0][6] == "https://agentrouter.org/v1"
