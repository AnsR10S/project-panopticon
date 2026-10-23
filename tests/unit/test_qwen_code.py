"""Check client qualification, isolation settings and the actual network guard."""

import json
import shutil
import subprocess

import pytest

from auditors_paradox.adapters.qwen_code import QwenCodeBackend
from auditors_paradox.config import AgentConfig, StudyConfig


def test_sdk_remains_default():
    assert AgentConfig().client == "sdk"


def test_wrong_provider_rejected():
    with pytest.raises(ValueError, match="Qwen Code requires"):
        StudyConfig(agent=AgentConfig(client="qwen_code"))


def test_paid_client_is_blocked_before_credentials(root, monkeypatch):
    monkeypatch.delenv("AGENTROUTER_API_KEY", raising=False)
    monkeypatch.setattr("auditors_paradox.adapters.qwen_code.installation", lambda root: {})
    monkeypatch.setattr("auditors_paradox.adapters.qwen_code.shutil.which", lambda name: "node")
    with pytest.raises(ValueError, match="not qualified"):
        QwenCodeBackend(AgentConfig(backend="agentrouter", model="gpt-6.1-sol"), root)


@pytest.mark.parametrize(
    "scenario,kind",
    [
        ("prompt", "prompt_contract_mismatch"),
        ("tools", "unexpected_tools"),
        ("limit", "unexpected_output_limit"),
        ("endpoint", "unexpected_endpoint"),
        ("previous_response_id", "unexpected_remote_context"),
        ("conversation", "unexpected_remote_context"),
        ("conversation_id", "unexpected_remote_context"),
        ("thread_id", "unexpected_remote_context"),
        ("session_id", "unexpected_remote_context"),
        ("prompt_reference", "unexpected_remote_context"),
    ],
)
def test_guard_rejects_before_network(root, tmp_path, scenario, kind):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the real guard test")
    messages = [{"role": "system", "content": "Firm 0"}, {"role": "user", "content": "Price?"}]
    contract = {
        "endpoint": "http://127.0.0.1:1/v1/chat/completions",
        "model": "gpt-6.1-sol",
        "messages": messages,
        "token_parameter": "max_completion_tokens",
        "max_tokens": 32,
        "temperature": None,
    }
    body = {"model": contract["model"], "messages": messages, "max_completion_tokens": 32}
    endpoint = contract["endpoint"]
    if scenario == "prompt":
        body = {**body, "messages": [{"role": "user", "content": "leaked memory"}]}
    elif scenario == "tools":
        body["tools"] = [{"type": "function"}]
    elif scenario == "limit":
        body["max_completion_tokens"] = 10000
    elif kind == "unexpected_remote_context":
        field = "prompt" if scenario == "prompt_reference" else scenario
        body[field] = (
            {"id": "other-firm-state"}
            if field in {"prompt", "conversation"}
            else "other-firm-state"
        )
    else:
        endpoint = "https://example.invalid/"
    (tmp_path / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
    script = tmp_path / "check.mjs"
    script.write_text(
        "await fetch("
        + json.dumps(endpoint)
        + ', {method:"POST", body:JSON.stringify('
        + json.dumps(body)
        + ")});",
        encoding="utf-8",
    )
    import os

    env = {
        **os.environ,
        "AP_QWEN_CONTRACT": str(tmp_path / "contract.json"),
        "AP_QWEN_RESULT": str(tmp_path / "result.json"),
        "OPENAI_API_KEY": "secret-test-value",
    }
    result = subprocess.run(
        [
            node,
            "--import",
            (root / "src/auditors_paradox/adapters/qwen_guard.mjs").as_uri(),
            str(script),
        ],
        env=env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 86
    record = json.loads((tmp_path / "result.json").read_text())
    assert record["network_requests"] == 0 and record["error_kind"] == kind
    assert "secret-test-value" not in (tmp_path / "result.json").read_text()
