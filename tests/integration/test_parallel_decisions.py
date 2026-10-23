"""Exercise concurrent firms, cancellation and the shared allowance without paid inference."""

import importlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from auditors_paradox.adapters.model_calls import Budget, ProviderError
from auditors_paradox.config import AgentConfig, BudgetConfig, StudyConfig
from auditors_paradox.contracts import Completion
from auditors_paradox.evaluation.validate import validate_run
from auditors_paradox.runner import RunFailed, run_experiment


def parallel_config(config, **agent):
    data = config.model_dump()
    data["agent"].update(decision_workers=3, **agent)
    data["budget"]["stop_file"] = ".cache/test-parallel-only.stop"
    return StudyConfig.model_validate(data)


def test_round_barrier_and_private_notes_with_three_overlapping_firms(root, config, tmp_path):
    barrier = threading.Barrier(3)
    observed = {i: [] for i in range(3)}

    class Firm:
        def __init__(self, firm):
            self.firm = firm

        def complete(self, messages):
            observed[self.firm].append(json.loads(json.dumps(messages)))
            barrier.wait(timeout=5)
            return Completion(f"PRICE: 90\nPRIVATE_FIRM_{self.firm}", 20, 4, "scripted")

    path = run_experiment(
        parallel_config(config),
        root,
        run_id="parallel",
        output_root=tmp_path,
        backend_factory=lambda cfg, market, firm: Firm(firm),
    )
    assert validate_run(path)["valid"]
    events = [json.loads(line) for line in path.with_name("events.jsonl").read_text().splitlines()]
    for turn in range(1, config.rounds + 1):
        starts = [
            e["sequence"]
            for e in events
            if e["type"] == "model_call_started" and e["payload"]["round"] == turn
        ]
        finishes = [
            e["sequence"]
            for e in events
            if e["type"] == "model_call_completed" and e["payload"]["round"] == turn
        ]
        assert len(starts) == 3 and max(starts) < min(finishes)
    for firm, prompts in observed.items():
        assert len(prompts) == config.rounds
        assert f"PRIVATE_FIRM_{firm}" in json.dumps(prompts[-1])
        assert all(
            f"PRIVATE_FIRM_{other}" not in json.dumps(prompts)
            for other in range(3)
            if other != firm
        )


def test_one_firms_parse_retry_does_not_regenerate_its_rivals(root, config, tmp_path):
    first_round = threading.Barrier(3)
    clients = []

    class Firm:
        def __init__(self, firm):
            self.firm, self.calls = firm, 0
            clients.append(self)

        def complete(self, messages):
            self.calls += 1
            if self.calls == 1:
                first_round.wait(timeout=5)
            text = "bad JSON" if self.firm == 1 and self.calls == 1 else "PRICE: 90"
            return Completion(text, 20, 4, "scripted")

    path = run_experiment(
        parallel_config(config),
        root,
        run_id="retry",
        output_root=tmp_path,
        backend_factory=lambda cfg, market, firm: Firm(firm),
    )
    assert validate_run(path)["valid"]
    assert {c.firm: c.calls for c in clients} == {0: 6, 1: 7, 2: 6}


def test_failed_firm_cancels_siblings_before_evidence_closes(root, config, tmp_path):
    started = threading.Barrier(3)
    clients = []

    class Firm:
        def __init__(self, firm):
            self.firm, self.closed = firm, False
            clients.append(self)

        def set_stop_check(self, check):
            self.check = check

        def complete(self, messages):
            started.wait(timeout=5)
            if self.firm == 0:
                raise ProviderError("test_fatal_error")
            while True:
                self.check()
                time.sleep(0.005)

        def close(self):
            self.closed = True

    with pytest.raises(RunFailed) as error:
        run_experiment(
            parallel_config(config),
            root,
            run_id="cancel",
            output_root=tmp_path,
            backend_factory=lambda cfg, market, firm: Firm(firm),
        )
    manifest = json.loads(error.value.manifest.read_text())
    events = [
        json.loads(line)
        for line in error.value.manifest.with_name("events.jsonl").read_text().splitlines()
    ]
    assert manifest["completed_rounds"] == 0 and all(c.closed for c in clients)
    assert len([e for e in events if e["type"] == "model_call_failed"]) == 3
    assert not any(e["type"] in {"round", "round_committed"} for e in events)


def test_shared_gate_allows_overlap_without_losing_accounting(root, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(root / "scripts"))
    module = importlib.import_module("validate_qwen_live")
    barrier, gate = threading.Barrier(3), threading.RLock()
    agent = AgentConfig(
        backend="agentrouter",
        client="qwen_code",
        decision_workers=3,
        credential_mode="per_firm",
        model="gpt-6.1-sol",
        max_output_tokens=32,
    )
    budget = Budget(
        BudgetConfig(
            max_calls=3, max_cost_usd=1, input_usd_per_million=3, output_usd_per_million=15
        ),
        agent,
    )
    pacer = module.RequestPacer({}, 3, 20000)
    records, snapshots = [], []

    class FakeClient:
        def __init__(self, agent, root, credential_env):
            self.directory = root / ".cache/fake-client"
            self.last_evidence = {}
            self.credential_env = credential_env

        def set_stop_check(self, check):
            self.check = check

        def complete(self, messages):
            barrier.wait(timeout=5)
            self.last_evidence = {"status": "complete", "network_requests": 1}
            return Completion("137", 20, 4, "gpt-6.1-sol")

        def close(self):
            pass

    clients = [
        module.ManagedBackend(
            agent,
            root,
            budget,
            pacer,
            records,
            lambda: snapshots.append(json.dumps(records)),
            "local",
            gate,
            firm=i,
            backend_factory=FakeClient,
        )
        for i in range(3)
    ]
    original = budget.agent
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(
            pool.map(
                lambda client: client.complete([{"role": "user", "content": "private"}]), clients
            )
        )
    assert len(replies) == 3 and budget.calls == 3
    assert budget.agent is original
    assert len(records) == 3 and all(record["status"] == "complete" for record in records)
    assert {r["credential_env"] for r in records} == {
        f"AGENTROUTER_API_KEY_FIRM_{i}" for i in range(3)
    }
    assert budget.reserved_usd == pytest.approx(budget.observed_usd)
    assert budget.observed_usd == pytest.approx(3 * (20 * 3 + 4 * 15) / 1e6)
    assert len(pacer.entries) == 3 and len(snapshots) == 6
    with pytest.raises(RuntimeError, match="model-call budget"):
        budget.reserve([])


def test_concurrent_reservations_cannot_oversubscribe_one_cost_cap():
    agent = AgentConfig(backend="agentrouter", max_output_tokens=32)
    budget = Budget(
        BudgetConfig(
            max_calls=30,
            max_cost_usd=0.001,
            request_overhead_tokens=0,
            input_usd_per_million=3,
            output_usd_per_million=15,
        ),
        agent,
    )

    def attempt(_):
        try:
            return budget.reserve([])
        except RuntimeError:
            return None

    with ThreadPoolExecutor(max_workers=3) as pool:
        amounts = list(pool.map(attempt, range(30)))
    accepted = [value for value in amounts if value is not None]
    assert budget.calls == len(accepted) == 2
    assert budget.reserved_usd == pytest.approx(sum(accepted)) and budget.reserved_usd <= 0.001
