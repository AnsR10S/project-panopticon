"""Here we test the provider adapters with fake SDK clients."""

import json
import sys
from types import SimpleNamespace as NS

import pytest

from auditors_paradox.adapters.model_calls import (
    Budget,
    ProviderBackend,
    ProviderError,
    RecordedClient,
    provider_error,
)
from auditors_paradox.config import AgentConfig, BudgetConfig
from auditors_paradox.contracts import Completion
from auditors_paradox.evidence import EvidenceWriter


@pytest.mark.parametrize(
    "name,key",
    [
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("deepseek", "DEEPSEEK_API_KEY"),
    ],
)
def test_BR_009_explicit_provider_attempts_and_usage(monkeypatch, name, key):
    """Each provider gets one attempt per call and the usage is recorded right."""
    requests = []
    constructors = []

    def create(**kwargs):
        requests.append(kwargs)
        return NS(
            usage=NS(input_tokens=11, output_tokens=7, prompt_tokens=11, completion_tokens=7),
            model="reported-model",
            output_text="PRICE: 60",
            _request_id="fake-request",
            content=[NS(type="text", text="PRICE: 60"), NS(type="other")],
            choices=[NS(message=NS(content="PRICE: 60"))],
        )

    def factory(**kwargs):
        constructors.append(kwargs)
        return NS(
            responses=NS(create=create),
            messages=NS(create=create),
            chat=NS(completions=NS(create=create)),
        )

    monkeypatch.setitem(sys.modules, "openai", NS(OpenAI=factory))
    monkeypatch.setitem(sys.modules, "anthropic", NS(Anthropic=factory))
    monkeypatch.setenv(key, "fake-test-credential")
    config = AgentConfig(backend=name, model="selected-model", temperature=0.2)
    backend = ProviderBackend(config)
    answer = backend.complete(
        [{"role": "system", "content": "system"}, {"role": "user", "content": "price?"}]
    )
    assert answer.text == "PRICE: 60" and answer.input_tokens == 11 and answer.output_tokens == 7
    assert constructors[0]["max_retries"] == 0
    assert requests[0]["model"] == "selected-model" and requests[0]["temperature"] == 0.2
    if name == "deepseek":
        assert constructors[0]["base_url"] == "https://api.deepseek.com"


def test_BR_009_absent_credentials_and_sanitized_provider_failure(monkeypatch):
    """Missing keys fail clearly and provider errors never leak the request."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="runtime"):
        ProviderBackend(AgentConfig(backend="openai", model="chosen"))
    monkeypatch.setenv("OPENAI_API_KEY", "fake-value-never-log")

    def broken(**kwargs):
        raise RuntimeError("fake-value-never-log")

    monkeypatch.setitem(
        sys.modules, "openai", NS(OpenAI=lambda **kwargs: NS(responses=NS(create=broken)))
    )
    with pytest.raises(ProviderError) as caught:
        ProviderBackend(AgentConfig(backend="openai", model="chosen")).complete([])
    assert "fake-value-never-log" not in str(caught.value)


def test_BR_009_call_and_cost_reservations(tmp_path):
    """Calls and costs are reserved before sending and stop at the limits."""
    agent = AgentConfig(backend="openai", model="chosen", max_output_tokens=16)
    budget = Budget(
        BudgetConfig(
            max_calls=2, max_cost_usd=0.01, input_usd_per_million=1, output_usd_per_million=1
        ),
        agent,
    )
    budget.reserve([{"role": "user", "content": "hello"}])
    budget.account(Completion("PRICE: 60", 10, 4, "chosen"))
    assert budget.observed_usd == 14 / 1e6
    with pytest.raises(RuntimeError, match="Observed"):
        budget.account(Completion("x", 1_000_000, 1_000_000, "chosen"))
    tiny = Budget(
        BudgetConfig(
            max_calls=1, max_cost_usd=0.0000001, input_usd_per_million=1, output_usd_per_million=1
        ),
        agent,
    )
    with pytest.raises(RuntimeError, match="reservation"):
        tiny.reserve([{"role": "user", "content": "hello"}])
    scripted = Budget(BudgetConfig(max_calls=1), AgentConfig())
    scripted.reserve([])
    with pytest.raises(RuntimeError, match="call budget"):
        scripted.reserve([])


def test_BR_008_completed_request_survives_post_call_budget_failure(tmp_path):
    """A finished request stays saved even if the budget check after it fails."""

    class Fake:
        def complete(self, messages):
            return Completion("PRICE: 60", 1_000_000, 1, "mock-model")

    agent = AgentConfig(backend="openai", model="mock-model")
    budget = Budget(
        BudgetConfig(
            max_calls=4, max_cost_usd=0.01, input_usd_per_million=1, output_usd_per_million=1
        ),
        agent,
    )
    writer = EvidenceWriter(tmp_path / "run", {})
    client = RecordedClient(Fake(), "mock-model", 0, writer, budget)
    client.begin_round(1, [])
    with pytest.raises(RuntimeError, match="Observed"):
        client.generate([{"role": "user", "content": "price"}])
    assert client.input_tokens == 1_000_000 and client.cost_estimate() > 0.01
    writer.finish("failed")


@pytest.mark.parametrize("status", [401, 403])
def test_provider_http_status_survives_safe_evidence(tmp_path, status):
    """Keep the numeric HTTP status while excluding raw credential-bearing errors."""
    raw = RuntimeError("fake-secret-never-persist")
    raw.status_code = status
    safe = provider_error(raw)

    class FailedBackend:
        def complete(self, messages):
            raise safe

    writer = EvidenceWriter(tmp_path / "run", {})
    client = RecordedClient(
        FailedBackend(), "mock", 0, writer, Budget(BudgetConfig(), AgentConfig())
    )
    with pytest.raises(ProviderError):
        client.generate([])
    writer.finish("failed")
    evidence = (tmp_path / "run/events.jsonl").read_text()
    failure = json.loads(evidence.splitlines()[-1])["payload"]
    assert failure["http_status"] == status
    assert failure["error_kind"] == "authentication_or_permission"
    assert "fake-secret-never-persist" not in evidence


def test_client_rejection_is_not_mislabeled_as_invalid_credentials():
    """Keep the gateway's client restriction distinct without persisting its raw body."""
    raw = RuntimeError("do-not-log-this-token")
    raw.status_code = 401
    raw.body = {"error": {"message": "unauthorized client detected, contact support"}}
    safe = provider_error(raw)
    assert safe.kind == "client_not_authorized"
    assert safe.status_code == 401 and not safe.retryable
    assert "do-not-log-this-token" not in str(safe)
