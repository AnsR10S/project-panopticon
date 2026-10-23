"""Check allowance settlement and partial evidence without sending model requests."""

import csv
import importlib.util
import json
from pathlib import Path

import pytest

from auditors_paradox.adapters.model_calls import Budget
from auditors_paradox.config import AgentConfig, BudgetConfig
from auditors_paradox.contracts import Completion


def load_script(name, monkeypatch):
    """Import the script without running its paid entry point."""
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(name, scripts / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shared_pacing_waits_for_requests_and_tokens_after_restart(monkeypatch):
    module = load_script("validate_qwen_live", monkeypatch)
    pacer = module.RequestPacer({}, 2, 10000)
    pacer.record(6000, 100)
    assert pacer.delay(5000, 110) == 50
    restored = module.RequestPacer({"entries": pacer.entries}, 1, 10000)
    assert restored.delay(1000, 120) == 40
    assert restored.delay(1000, 160) == 0
    with pytest.raises(RuntimeError, match="not ready"):
        restored.record(1000, 120)
    with pytest.raises(ValueError, match="One request exceeds"):
        restored.delay(10001, 160)


def test_pacing_keeps_large_estimate_even_when_usage_would_be_small(monkeypatch):
    module = load_script("validate_qwen_live", monkeypatch)
    pacer = module.RequestPacer({}, 10, 10000)
    pacer.record(8000, 100)
    pacer.record(1500, 105)
    assert pacer.delay(1000, 110) == 50
    assert pacer.delay(9500, 160) == 5


def test_provider_cooldown_persists_and_backs_off(monkeypatch):
    module = load_script("validate_qwen_live", monkeypatch)
    monkeypatch.setattr(module.random, "uniform", lambda a, b: 0)
    pacer = module.RequestPacer({}, 1, 20000)
    pacer.observe({"error_kind": "provider_rate_limit", "retry_after_seconds": 5}, 100)
    assert pacer.delay(1000, 110) == 50
    restored = module.RequestPacer(pacer.state(), 1, 20000)
    restored.observe({"error_kind": "provider_rate_limit", "retry_after_seconds": 180}, 160)
    assert restored.delay(1000, 170) == 170
    assert restored.rate_limit_streak == 2
    restored.observe({"status": "complete", "dispatched_at_ms": 150000}, 345)
    assert restored.rate_limit_streak == 2 and restored.delay(1000, 345) == 0
    for n in range(3):
        restored.observe({"status": "complete", "dispatched_at_ms": (346 + n) * 1000}, 350)
    assert restored.rate_limit_streak == 0
    assert restored.adaptive_interval_seconds == 60
    for n in range(3):
        restored.observe({"status": "complete", "dispatched_at_ms": (351 + n) * 1000}, 355)
    assert restored.adaptive_interval_seconds == 0
    assert restored.state()["transitions"][-1]["kind"] == "eligible_success_recovery"


def test_rate_limit_staggers_all_firms_and_learns_provider_headers(monkeypatch):
    module = load_script("validate_qwen_live", monkeypatch)
    monkeypatch.setattr(module.random, "uniform", lambda a, b: 0)
    pacer = module.RequestPacer({}, 3, 20000)
    pacer.record(2000, 100)
    pacer.observe({"error_kind": "provider_rate_limit"}, 110)
    pacer.observe({"status": "complete", "dispatched_at_ms": 100000}, 112)
    assert pacer.rate_limit_streak == 1 and pacer.delay(2000, 112) == 58
    pacer.record(2000, 170)
    assert pacer.delay(2000, 171) == 59
    restored = module.RequestPacer(pacer.state(), 3, 20000)
    assert restored.delay(2000, 171) == 59
    restored.observe(
        {
            "headers_received_at_ms": 180000,
            "rate_limit_headers": {
                "x-ratelimit-limit-tokens": "10000",
                "x-ratelimit-limit-requests": "2",
                "x-ratelimit-remaining-tokens": "1000",
                "x-ratelimit-reset-tokens": "2m0s",
            },
        },
        180,
    )
    assert restored.effective_limit("tokens") == 10000
    assert restored.effective_limit("requests") == 2
    assert restored.delay(2000, 200) == 100
    with pytest.raises(ValueError, match="One request exceeds"):
        restored.delay(10001, 200)


def test_provider_reset_parsing_and_out_of_order_headers(monkeypatch):
    module = load_script("validate_qwen_live", monkeypatch)
    assert module.RequestPacer.seconds("1m500ms", 100) == 60.5
    assert module.RequestPacer.seconds("bad-value", 100) == 0
    assert module.RequestPacer.seconds("nan", 100) == 0
    pacer = module.RequestPacer({}, 3, 20000)
    pacer.observe(
        {
            "headers_received_at_ms": 200000,
            "rate_limit_headers": {
                "x-ratelimit-remaining-tokens": "1",
                "x-ratelimit-reset-tokens": "60",
            },
        },
        200,
    )
    pacer.observe(
        {
            "headers_received_at_ms": 190000,
            "rate_limit_headers": {
                "x-ratelimit-remaining-tokens": "9999",
                "x-ratelimit-reset-tokens": "1",
            },
        },
        201,
    )
    assert pacer.delay(2000, 201) == 59


def test_pricing_replay_never_substitutes_hypothetical_cases(monkeypatch):
    module = load_script("validate_qwen_live", monkeypatch)
    root = Path(__file__).resolve().parents[2]
    original = {
        "validation": "market-initial",
        "evidence": {"wire_request": {"model": "gpt-5.6-sol"}},
        "messages": [
            {"role": "system", "content": "You manage Firm 0 in a simulated market with 3 firms."},
            {
                "role": "user",
                "content": "Decision round 1.\nYour own memory.\nSystem notices: (none)\nChoose your own price: integer price between 100 and 250 inclusive.\nReturn PRICE: <integer> on the first line.\nYou may then add one note.",
            },
        ],
    }
    hypothetical = {
        "validation": "hypothetical",
        "messages": [
            {"role": "system", "content": "Hypothetical diagnostic"},
            {"role": "user", "content": "Not market evidence"},
        ],
    }
    before = json.dumps(original)
    source, messages = module.pricing_replay(
        [original, hypothetical], root, "price_only", "gpt-5.6-sol"
    )
    assert source == "market-initial" and json.dumps(original) == before
    assert messages[0] == original["messages"][0]
    assert (
        messages[1]["content"].split("Choose your own price:")[0]
        == original["messages"][1]["content"].split("Choose your own price:")[0]
    )
    assert (
        "exactly one line" in messages[1]["content"]
        and "You may then" not in messages[1]["content"]
    )
    with pytest.raises(ValueError, match="real-market"):
        module.pricing_replay([hypothetical], root, "price_only", "gpt-5.6-sol")


def test_saved_price_can_replace_probe_only_for_matching_route(monkeypatch, tmp_path):
    module = load_script("validate_qwen_live", monkeypatch)
    agent = AgentConfig(
        backend="agentrouter",
        model="gpt-6.1-sol",
        base_url="https://agentrouter.org/v1",
        api_protocol="responses",
    )
    record = {
        "validation": "price",
        "status": "complete",
        "reported_model": agent.model,
        "response": "PRICE: 142",
    }
    path = tmp_path / "price.json"
    data = {"config": {"agent": agent.model_dump(mode="json")}}
    path.write_text(json.dumps(data))
    assert module.saved_connection([record], tmp_path, agent) == record
    data["config"]["agent"]["base_url"] = "https://co.agentrouter.org/v1"
    path.write_text(json.dumps(data))
    assert module.saved_connection([record], tmp_path, agent) is None


def test_isolation_reuse_requires_exact_messages_and_client_identity(monkeypatch, tmp_path):
    module = load_script("validate_qwen_live", monkeypatch)
    agent = AgentConfig(
        backend="agentrouter",
        model="gpt-6.1-sol",
        base_url="https://agentrouter.org/v1",
        api_protocol="responses",
    )
    messages = [
        {"role": "system", "content": "Firm 1"},
        {"role": "user", "content": "Private observation"},
    ]
    client = {"guard": "same-version"}
    record = {
        "validation": "case",
        "status": "complete",
        "reported_model": agent.model,
        "response": "PRICE: 137",
        "messages": messages,
        "evidence": {"client_identity": client},
    }
    (tmp_path / "case.json").write_text(
        json.dumps({"config": {"agent": agent.model_dump(mode="json")}})
    )
    assert (
        module.saved_connection(
            [record], tmp_path, agent, exact_messages=messages, client_identity=client
        )
        == record
    )
    assert (
        module.saved_connection(
            [record],
            tmp_path,
            agent,
            exact_messages=[{"role": "user", "content": "Other firm"}],
            client_identity=client,
        )
        is None
    )
    assert (
        module.saved_connection(
            [record], tmp_path, agent, exact_messages=messages, client_identity={"guard": "changed"}
        )
        is None
    )


@pytest.mark.parametrize("output,reservation", [(20, 0.5), (0, 0.5), (20000, 0.5)])
def test_only_current_known_reservation_is_settled(monkeypatch, output, reservation):
    module = load_script("validate_qwen_live", monkeypatch)
    budget = Budget(
        BudgetConfig(max_cost_usd=50, input_usd_per_million=8, output_usd_per_million=40),
        AgentConfig(backend="agentrouter"),
    )
    budget.reserved_usd = 0.2 + reservation
    budget.observed_usd = 0.1
    actual = module.settle_usage(budget, Completion("", 0, output, "test"), reservation)
    assert actual == pytest.approx(output * 40 / 1e6)
    assert budget.observed_usd == pytest.approx(0.1 + actual)
    assert budget.reserved_usd == pytest.approx(0.2 + actual)


def test_over_cap_usage_is_still_settled_and_stops(monkeypatch):
    module = load_script("validate_qwen_live", monkeypatch)
    budget = Budget(
        BudgetConfig(max_cost_usd=0.1, input_usd_per_million=8, output_usd_per_million=40),
        AgentConfig(backend="agentrouter"),
    )
    budget.reserved_usd = 0.05
    with pytest.raises(RuntimeError, match="Observed usage exceeded"):
        module.settle_usage(budget, Completion("", 0, 10000, "test"), 0.05)
    assert budget.observed_usd == pytest.approx(0.4)
    assert budget.reserved_usd == pytest.approx(0.4)


def test_export_keeps_uncommitted_decisions_out_of_rounds(monkeypatch, tmp_path):
    module = load_script("report_qwen_connection", monkeypatch)
    ledger = tmp_path / "runs/diagnostics/qwen-live/allowance.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "reserved_attempts": 1,
                "reserved_usd": 0.5,
                "observed_usd": 0,
                "requests": [
                    {
                        "validation": "partial",
                        "status": "failed",
                        "evidence": {
                            "network_requests": 1,
                            "error_kind": "usage_missing",
                            "wire_request": {"model": "gpt-5.6-sol"},
                        },
                    }
                ],
            }
        )
    )
    market = tmp_path / "runs/pilot/agentrouter-qwen-smoke/partial"
    market.mkdir(parents=True)
    manifest = {
        "arm": "silent",
        "status": "failed",
        "config": {"rounds": 3, "agent": {"model": "gpt-5.6-sol"}},
        "benchmarks": {"nash_reward": 1, "joint_reward": 2},
    }
    (market / "manifest.json").write_text(json.dumps(manifest))
    events = [
        {"type": "decision", "payload": {"agent_id": 0, "price": 150, "round": 1}},
        {
            "type": "round",
            "payload": {"round": 1, "prices": [150, 150, 150], "gross_rewards": [1.5, 1.5, 1.5]},
        },
        {"type": "round_committed", "payload": {"round": 1}},
        {"type": "decision", "payload": {"agent_id": 0, "price": 160, "round": 2}},
    ]
    (market / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    output = tmp_path / "analysis/export"
    summary = module.build(tmp_path, output)
    assert summary["committed_rounds"] == 1
    assert summary["accepted_market_decisions"] == 2
    assert summary["committed_market_decisions"] == 1
    assert summary["unknown_usage_requests"] == 1
    assert (output / "source-allowance.json").read_bytes() == ledger.read_bytes()
    with (output / "requests.csv").open() as stream:
        assert next(csv.DictReader(stream))["output_tokens"] == ""
    with (output / "rounds.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1 and rows[0]["inferentially_included"] == "False"
    with pytest.raises(FileExistsError):
        module.build(tmp_path, output)
