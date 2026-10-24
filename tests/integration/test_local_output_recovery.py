"""Bound local output recovery without turning unfinished answers into decisions."""

import io
import json

import pytest

from auditors_paradox.adapters.model_calls import (
    Budget,
    ProviderBackend,
    ProviderError,
    RecordedClient,
    RunStopped,
)
from auditors_paradox.config import AgentConfig, BudgetConfig, StudyConfig, load_config
from auditors_paradox.evaluation.validate import validate_run
from auditors_paradox.evidence import EvidenceWriter
from auditors_paradox.runner import run_experiment


def local_agent(**updates):
    settings = dict(
        backend="ollama",
        api_protocol="ollama",
        model="deepseek-r1:7b-qwen-distill-q4_K_M",
        action_parser="auto",
        max_output_tokens=4096,
        context_tokens=8192,
        local_truncation_retries=updates.pop("retries", 1),
        local_retry_output_tokens=6144,
    )
    return AgentConfig(**{**settings, **updates})


def simulated(monkeypatch, reasons):
    sent = []
    queue = iter(reasons)

    def request(req, **kwargs):
        payload = json.loads(req.data)
        sent.append(payload)
        reason = next(queue)
        return io.BytesIO(
            json.dumps(
                {
                    "done": True,
                    "done_reason": reason,
                    "model": payload["model"],
                    "message": {
                        "content": (
                            "PRICE: 140" if reason == "stop" else "PRICE: 200\nActually I choose"
                        ),
                        "thinking": "PRIVATE_THINKING_DO_NOT_REPLAY",
                    },
                    "prompt_eval_count": 858,
                    "eval_count": 4096 if reason == "length" else 20,
                }
            ).encode()
        )

    monkeypatch.setattr("urllib.request.urlopen", request)
    return sent


@pytest.mark.parametrize("version", [1, 2])
def test_recovered_rounds_replay_and_record_every_attempt(root, tmp_path, monkeypatch, version):
    sent = simulated(monkeypatch, ["length"] + ["stop"] * 6)
    data = load_config(root / "configs/v2/warning.yaml").model_dump()
    data.update(rounds=2, analysis_last_rounds=1)
    data["feedback"]["schedule_rounds"] = []
    data["auditor"]["window"] = 2
    data["agent"] = local_agent().model_dump()
    if version == 1:
        data.update(schema_version=1, v2=None)
    else:
        data["v2"]["index_enabled"] = False
    path = run_experiment(
        StudyConfig.model_validate(data), root, output_root=tmp_path, run_id=f"recovered-v{version}"
    )
    checked = validate_run(path)
    assert checked["valid"], checked
    manifest = json.loads(path.read_text())
    assert manifest["completed_rounds"] == 2 and manifest["model_calls"] == 7
    assert sent[0]["messages"] == sent[1]["messages"]
    assert [r["options"]["num_predict"] for r in sent[:3]] == [4096, 6144, 4096]
    assert "PRIVATE_THINKING_DO_NOT_REPLAY" not in json.dumps([r["messages"] for r in sent])
    events = [json.loads(line) for line in path.with_name("events.jsonl").read_text().splitlines()]
    assert sum(e["type"] == "local_output_retry" for e in events) == 1
    assert all(e["payload"]["price"] == 140 for e in events if e["type"] == "decision")
    actual_usage = sum(
        e["payload"]["output_tokens"] for e in events if e["type"] == "model_call_completed"
    )
    actual_usage += sum(
        e["payload"]["client_evidence"]["output_tokens"]
        for e in events
        if e["type"] == "model_call_failed"
    )
    assert actual_usage == 4096 + 6 * 20


@pytest.mark.parametrize("retries,expected_calls", [(0, 1), (1, 2), (2, 3)])
def test_exhausted_recovery_still_aborts(tmp_path, monkeypatch, retries, expected_calls):
    sent = simulated(monkeypatch, ["length"] * 3)
    agent = local_agent(retries=retries)
    writer = EvidenceWriter(tmp_path / "attempts", {})
    budget = Budget(BudgetConfig(), agent)
    client = RecordedClient(ProviderBackend(agent), agent.model, 0, writer, budget)
    try:
        with pytest.raises(ProviderError) as exc:
            client.generate(
                [
                    {"role": "system", "content": "Own information"},
                    {"role": "user", "content": "Choose"},
                ]
            )
        assert exc.value.kind == "incomplete_local_response"
        assert budget.calls == expected_calls and len(sent) == expected_calls
        assert client.round_call_ids == []
    finally:
        writer.finish("failed")


def test_context_and_stop_are_checked_before_recovery(tmp_path, monkeypatch):
    sent = simulated(monkeypatch, ["length", "stop"])
    agent = local_agent(context_tokens=6500)
    writer = EvidenceWriter(tmp_path / "context", {})
    client = RecordedClient(
        ProviderBackend(agent), agent.model, 0, writer, Budget(BudgetConfig(), agent)
    )
    try:
        with pytest.raises(ProviderError):
            client.generate([])
        assert len(sent) == 1
    finally:
        writer.finish("failed")
    budget = Budget(BudgetConfig(), local_agent())
    budget.cancelled.set()
    with pytest.raises(RunStopped):
        budget.check_stop()


def test_recovery_is_explicit_and_local_only():
    assert AgentConfig().local_truncation_retries == 0
    with pytest.raises(ValueError, match="native local"):
        AgentConfig(local_truncation_retries=1, local_retry_output_tokens=512)
    with pytest.raises(ValueError, match="exceed"):
        local_agent(max_output_tokens=7000)


def test_repetition_controls_are_explicit_local_and_transmitted(monkeypatch):
    sent = simulated(monkeypatch, ["stop", "stop"])
    default = local_agent()
    ProviderBackend(default).complete([])
    assert "repeat_penalty" not in sent[0]["options"]
    assert "repeat_last_n" not in sent[0]["options"]
    selected = local_agent(temperature=0.6, local_repeat_penalty=1.2, local_repeat_last_n=512)
    ProviderBackend(selected).complete([])
    assert sent[1]["options"]["temperature"] == 0.6
    assert sent[1]["options"]["repeat_penalty"] == 1.2
    assert sent[1]["options"]["repeat_last_n"] == 512
    with pytest.raises(ValueError, match="native local"):
        AgentConfig(local_repeat_penalty=1.2)
