"""Exercise the pinned HTTP transport locally and keep the real CLI fail-closed."""

import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from auditors_paradox.adapters.model_calls import ProviderError
from auditors_paradox.adapters.qwen_code import RUNTIME_CHUNK, TOOLS_CHUNK, QwenCodeBackend
from auditors_paradox.config import AgentConfig


@pytest.fixture
def qwen_server(root):
    package = root / "tools/qwen-code/node_modules/@qwen-code/qwen-code"
    if not shutil.which("node") or not (package / "cli.js").exists():
        pytest.skip("Install the pinned optional Qwen Code client to run transport checks")

    class Received(list):
        def __init__(self):
            super().__init__()
            self.authorizations = []

    received = Received()
    parallel_barrier = threading.Barrier(3)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(body)
            received.authorizations.append((body, self.headers.get("Authorization")))
            if "/parallel/" in self.path:
                parallel_barrier.wait(timeout=20)
            status = (
                429
                if self.path.startswith("/rate-limit")
                else (
                    401
                    if self.path.startswith("/auth")
                    else (302 if self.path.startswith("/redirect") else 200)
                )
            )
            result = {
                "model": "gpt-6.1-sol",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "content": (
                                "PRICE: 200" if self.path.startswith("/elevated") else "PRICE: 137"
                            )
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 23, "completion_tokens": 4},
            }
            if self.path.startswith("/quota-limit"):
                status = 429
                result = {"error": {"code": "insufficient_quota", "message": "No remaining credit"}}
            if self.path.startswith("/missing-usage"):
                del result["usage"]
            if self.path.startswith("/truncated"):
                result["choices"][0]["finish_reason"] = "length"
            content_type = "application/json"
            if body.get("stream") and status == 200:
                result["choices"][0]["delta"] = result["choices"][0].pop("message")
                data = ("data: " + json.dumps(result) + "\n\ndata: [DONE]\n\n").encode()
                content_type = "text/event-stream"
            else:
                data = json.dumps(result).encode()
            if self.path.endswith("/responses") and status == 200:
                message = {
                    "id": "msg-local",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": (
                                "PRICE: 200" if self.path.startswith("/elevated") else "PRICE: 137"
                            ),
                            "annotations": [],
                        }
                    ],
                }
                response = {
                    "id": "resp-local",
                    "object": "response",
                    "status": "completed",
                    "model": body["model"],
                    "output": [message],
                    "usage": {"input_tokens": 23, "output_tokens": 4, "total_tokens": 27},
                }
                events = [
                    {
                        "type": "response.created",
                        "response": {**response, "status": "in_progress", "output": []},
                    },
                    {
                        "type": "response.output_item.added",
                        "output_index": 0,
                        "item": {**message, "content": []},
                    },
                    {
                        "type": "response.output_text.delta",
                        "item_id": "msg-local",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": message["content"][0]["text"],
                    },
                    {"type": "response.output_item.done", "output_index": 0, "item": message},
                    {"type": "response.completed", "response": response},
                ]
                data = "".join("data: " + json.dumps(event) + "\n\n" for event in events).encode()
                content_type = "text/event-stream"
            self.send_response(status)
            if self.path.startswith("/tools"):
                result["choices"][0].get("delta", result["choices"][0].get("message", {}))[
                    "tool_calls"
                ] = [{"id": "bad-tool"}]
                data = ("data: " + json.dumps(result) + "\n\ndata: [DONE]\n\n").encode()
            if self.path.startswith("/malformed"):
                data = b"data: invalid-json\n\n"
            if (
                self.path.startswith("/rate-stream")
                or self.path.startswith("/blocked-stream")
                or self.path.startswith("/capacity-stream")
                or (self.path.startswith("/recover-rate") and len(received) == 1)
            ):
                code = (
                    "rate_limit_exceeded"
                    if self.path.startswith("/rate-stream") or self.path.startswith("/recover-rate")
                    else "no_capacity" if self.path.startswith("/capacity-stream") else "forbidden"
                )
                data = (
                    "data: "
                    + json.dumps(
                        {
                            "type": "error",
                            "error": {
                                "code": code,
                                "message": (
                                    "resource temporarily blocked"
                                    if "temporary" in self.path
                                    else "local simulated error"
                                ),
                            },
                        }
                    )
                    + "\n\n"
                ).encode()
                content_type = "text/event-stream"
            self.send_header("Content-Type", content_type)
            if "/quota-headers/" in self.path:
                self.send_header("retry-after-ms", "1500")
                self.send_header("x-ratelimit-remaining-tokens", "0")
                self.send_header("x-ratelimit-reset-tokens", "2m0s")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.path.startswith("/slow-body"):
                self.wfile.write(data[:6])
                self.wfile.flush()
                threading.Event().wait(10)
                try:
                    self.wfile.write(data[6:])
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield package, f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("success", None),
        ("repeat", "internal_request_blocked"),
        ("rate-limit", "rate_limit_or_quota"),
        ("missing-usage", "usage_missing"),
        ("truncated", "incomplete_response"),
        ("prompt", "prompt_contract_mismatch"),
    ],
)
def test_bundled_transport_is_guarded(root, tmp_path, qwen_server, scenario, expected):
    package, base, received = qwen_server
    messages = [{"role": "system", "content": "Firm 0"}, {"role": "user", "content": "Price?"}]
    contract = {
        "endpoint": base + "/" + scenario,
        "model": "gpt-6.1-sol",
        "messages": messages,
        "token_parameter": "max_completion_tokens",
        "max_tokens": 32,
        "temperature": None,
    }
    body = {"model": contract["model"], "messages": messages, "max_completion_tokens": 32}
    if scenario == "prompt":
        body = {**body, "messages": [{"role": "user", "content": "Unrequested context"}]}
    (tmp_path / "contract.json").write_text(json.dumps(contract), encoding="utf-8")
    script = tmp_path / "request.mjs"
    script.write_text(
        "const runtime = await import(process.env.AP_QWEN_RUNTIME);\n"
        "const transport = await runtime.loadUndici();\n"
        f"for (let i=0; i<{2 if scenario == 'repeat' else 1}; i++) {{\n"
        f"const response = await transport.fetch({json.dumps(contract['endpoint'])}, "
        f"{{method: 'POST', body: JSON.stringify({json.dumps(body)})}});\n"
        "await response.text();\n}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            shutil.which("node"),
            "--import",
            (root / "src/auditors_paradox/adapters/qwen_guard.mjs").as_uri(),
            str(script),
        ],
        env={
            **os.environ,
            "AP_QWEN_CONTRACT": str(tmp_path / "contract.json"),
            "AP_QWEN_RESULT": str(tmp_path / "result.json"),
            "AP_QWEN_RUNTIME": (package / "chunks" / RUNTIME_CHUNK).as_uri(),
            "AP_QWEN_TOOLS": (package / "chunks" / TOOLS_CHUNK).as_uri(),
            "OPENAI_API_KEY": "local-only-test",
        },
        capture_output=True,
        timeout=15,
    )
    record = json.loads((tmp_path / "result.json").read_text())
    assert result.returncode == (86 if expected else 0)
    assert record.get("error_kind") == expected
    assert len(received) == record["network_requests"] == (0 if scenario == "prompt" else 1)
    if scenario == "missing-usage":
        assert record["input_tokens"] is None and record["output_tokens"] is None
    elif scenario == "success":
        assert record["input_tokens"] == 23 and record["output_tokens"] == 4
        assert record["wire_request"] == body


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_real_cli_exact_prompt_and_session_correction(root, qwen_server, protocol):
    _, base, received = qwen_server
    backend = QwenCodeBackend(
        AgentConfig(
            backend="agentrouter",
            api_protocol=protocol,
            reasoning_effort="low" if protocol == "responses" else None,
            model="gpt-6.1-sol",
            chat_token_parameter="max_completion_tokens",
            timeout_seconds=30,
        ),
        root,
        test_endpoint=base
        + ("/v1/responses" if protocol == "responses" else "/v1/chat/completions"),
    )
    messages = [
        {"role": "system", "content": "You are firm 0. Choose your price."},
        {"role": "user", "content": "Private memory: café. Reply PRICE: 137."},
    ]
    try:
        answer = backend.complete(messages)
        session = backend.session_id
        assert answer.text == "PRICE: 137"
        assert answer.input_tokens == 23 and answer.output_tokens == 4
        assert backend.last_evidence["context_compatibility"] == "skip-startup-context-v1"
        correction = [
            *messages,
            {"role": "assistant", "content": answer.text},
            {"role": "user", "content": "Format correction: return an integer."},
        ]
        backend.complete(correction)
        assert backend.session_id == session
        assert len(received) == 2
        fresh = [
            {"role": "system", "content": "You are firm 1."},
            {"role": "user", "content": "Only firm 1 memory."},
        ]
        backend.complete(fresh)
        assert backend.session_id != session
        assert "café" not in json.dumps(received[-1], ensure_ascii=False)
        assert len(received) == 3 and backend.process is None
    finally:
        backend.close()


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("rate-limit", "rate_limit_or_quota"),
        ("missing-usage", "usage_missing"),
        ("truncated", "incomplete_response"),
        ("auth", "authentication_or_permission"),
        ("redirect", "redirect_blocked"),
        ("tools", "tool_response_blocked"),
        ("malformed", "transport_or_response_error"),
    ],
)
def test_real_cli_failure_has_no_retry(root, qwen_server, scenario, expected):
    if scenario == "rate-limit":
        expected = "provider_rate_limit"
    _, base, received = qwen_server
    backend = QwenCodeBackend(
        AgentConfig(
            backend="agentrouter",
            model="gpt-6.1-sol",
            chat_token_parameter="max_completion_tokens",
            timeout_seconds=30,
        ),
        root,
        test_endpoint=base + "/" + scenario + "/chat/completions",
    )
    try:
        with pytest.raises(ProviderError, match=expected):
            backend.complete(
                [{"role": "system", "content": "Firm 0"}, {"role": "user", "content": "Price?"}]
            )
        assert len(received) == 1
        assert backend.last_evidence["network_requests"] == 1
        assert backend.process is None
    finally:
        backend.close()


def test_real_cli_stream_rate_limit_retries_same_messages_once(
    root, tmp_path, qwen_server, monkeypatch
):
    from auditors_paradox.adapters.model_calls import Budget, RecordedClient
    from auditors_paradox.config import BudgetConfig
    from auditors_paradox.evidence import EvidenceWriter

    _, base, received = qwen_server
    agent = AgentConfig(
        backend="agentrouter",
        model="gpt-6.1-sol",
        api_protocol="responses",
        transport_attempts=2,
        retry_initial_seconds=30,
        retry_max_seconds=900,
    )
    backend = QwenCodeBackend(agent, root, test_endpoint=base + "/recover-rate/v1/responses")
    writer = EvidenceWriter(tmp_path / "retry", {})
    budget = Budget(BudgetConfig(max_calls=2), agent)
    waits = []
    monkeypatch.setattr(budget, "wait", waits.append)
    client = RecordedClient(backend, agent.model, 0, writer, budget)
    client.begin_round(1, [])
    try:
        assert (
            client.generate(
                [
                    {"role": "system", "content": "Firm 0"},
                    {"role": "user", "content": "Own private history"},
                ]
            )
            == "PRICE: 137"
        )
        assert len(received) == 2
        assert {k: v for k, v in received[0].items() if k != "prompt_cache_key"} == {
            k: v for k, v in received[1].items() if k != "prompt_cache_key"
        }
        assert received[0]["prompt_cache_key"] != received[1]["prompt_cache_key"]
        assert waits == [30] and budget.calls == 2
        writer.finish("complete")
        events = (tmp_path / "retry/events.jsonl").read_text(encoding="utf8")
        assert '"type":"transport_retry"' in events and '"type":"model_call_failed"' in events
    finally:
        backend.close()


def test_real_cli_quota_exhaustion_never_retries(root, qwen_server):
    _, base, received = qwen_server
    backend = QwenCodeBackend(
        AgentConfig(backend="agentrouter", model="gpt-6.1-sol"),
        root,
        test_endpoint=base + "/quota-limit/chat/completions",
    )
    try:
        with pytest.raises(ProviderError, match="quota_exhausted") as caught:
            backend.complete(
                [{"role": "system", "content": "Firm 0"}, {"role": "user", "content": "Price?"}]
            )
        assert caught.value.retryable is False and len(received) == 1
    finally:
        backend.close()


@pytest.mark.parametrize("arm", ["silent", "private", "public", "placebo"])
@pytest.mark.parametrize("profile", ["price_first", "price_only"])
def test_real_cli_market_warning_pipeline(root, tmp_path, qwen_server, arm, profile):
    from auditors_paradox.config import StudyConfig, load_config
    from auditors_paradox.evaluation.validate import validate_run
    from auditors_paradox.runner import run_experiment

    _, base, received = qwen_server
    cfg = load_config(root / "configs/controls/calvano-coordinated.yaml")
    data = cfg.model_dump(mode="json")
    data.update(arm=arm, run_kind="smoke", rounds=3, analysis_last_rounds=3)
    data["feedback"]["schedule_rounds"] = []
    data["agent"] = AgentConfig(
        backend="agentrouter",
        client="qwen_code",
        model="gpt-6.1-sol",
        base_url="https://agentrouter.org/v1",
        api_protocol="responses",
        transport_attempts=1,
        max_output_tokens=768,
        prompt_profile=profile,
    ).model_dump(mode="json")
    data["budget"].update(
        max_calls=18, max_cost_usd=1, input_usd_per_million=3, output_usd_per_million=15
    )
    data["budget"]["stop_file"] = (tmp_path / "fixture.stop").relative_to(root).as_posix()
    data["auditor"].update(window=2, cooldown_rounds=0)
    cfg = StudyConfig.model_validate(data)
    path = run_experiment(
        cfg,
        root,
        run_id="qwen-local-" + arm,
        output_root=tmp_path,
        allow_paid=True,
        backend_factory=lambda config, market, firm: QwenCodeBackend(
            config.agent,
            root,
            test_endpoint=base
            + (
                "/elevated/v1/responses"
                if config.agent.api_protocol == "responses"
                else "/elevated/chat/completions"
            ),
        ),
    )
    assert validate_run(path)["valid"]
    assert len(received) == 9
    events = [
        json.loads(line)
        for line in path.with_name("events.jsonl").read_text(encoding="utf8").splitlines()
    ]
    consumed = [e["payload"] for e in events if e["type"] == "delivery_consumed"]
    if arm in {"public", "private"}:
        assert consumed
        assert all(d["round"] == 3 for d in consumed)
    elif arm == "silent":
        assert not consumed


def test_real_cli_three_credentials_overlap_with_managed_allowance(
    root, tmp_path, qwen_server, monkeypatch
):
    import importlib
    import re

    from auditors_paradox.adapters.model_calls import Budget
    from auditors_paradox.config import BudgetConfig, StudyConfig, load_config
    from auditors_paradox.evaluation.validate import validate_run
    from auditors_paradox.runner import run_experiment

    monkeypatch.syspath_prepend(str(root / "scripts"))
    module = importlib.import_module("validate_qwen_live")
    _, base, received = qwen_server
    data = load_config(root / "configs/controls/calvano-coordinated.yaml").model_dump(mode="json")
    data.update(rounds=3, analysis_last_rounds=3, arm="public")
    data["feedback"]["schedule_rounds"] = []
    data["auditor"].update(window=2, cooldown_rounds=0)
    data["agent"] = AgentConfig(
        backend="agentrouter",
        client="qwen_code",
        model="gpt-6.1-sol",
        base_url="https://agentrouter.org/v1",
        api_protocol="responses",
        decision_workers=3,
        credential_mode="per_firm",
        transport_attempts=1,
        max_output_tokens=768,
    ).model_dump(mode="json")
    data["budget"].update(
        max_calls=18,
        max_cost_usd=1,
        input_usd_per_million=3,
        output_usd_per_million=15,
        stop_file=(tmp_path / "parallel.stop").relative_to(root).as_posix(),
    )
    cfg = StudyConfig.model_validate(data)
    for firm in range(3):
        monkeypatch.setenv(f"AGENTROUTER_API_KEY_FIRM_{firm}", f"test-only-firm-{firm}")
    monkeypatch.setenv("AGENTROUTER_API_KEY", "wrong-shared-key")
    budget = Budget(
        BudgetConfig(
            max_calls=9, max_cost_usd=1, input_usd_per_million=3, output_usd_per_million=15
        ),
        cfg.agent,
    )
    pacer = module.RequestPacer({}, 100, 1000000)
    records, lock = [], threading.RLock()

    def local_backend(agent, root, credential_env):
        backend = QwenCodeBackend(
            agent,
            root,
            test_endpoint=base + "/elevated/parallel/v1/responses",
            credential_env=credential_env,
        )
        backend.testing = False
        return backend

    def factory(config, market, firm):
        return module.ManagedBackend(
            config.agent,
            root,
            budget,
            pacer,
            records,
            lambda: None,
            "parallel-loopback",
            lock,
            firm=firm,
            backend_factory=local_backend,
        )

    path = run_experiment(
        cfg,
        root,
        output_root=tmp_path,
        run_id="three-keys",
        allow_paid=True,
        backend_factory=factory,
    )
    assert validate_run(path)["valid"]
    assert len(received) == budget.calls == len(records) == 9
    for body, authorization in received.authorizations:
        firm = int(re.search(r"Firm (\d)", body["instructions"]).group(1))
        assert authorization == f"Bearer test-only-firm-{firm}"
        assert "wrong-shared-key" not in json.dumps(body)
        assert "test-only-firm" not in json.dumps(body)
    assert budget.reserved_usd == pytest.approx(budget.observed_usd)
    events = [json.loads(line) for line in path.with_name("events.jsonl").read_text().splitlines()]
    assert len([e for e in events if e["type"] == "delivery_consumed"]) == 3


def test_real_cli_timeout_keeps_partial_http_evidence(root, qwen_server):
    _, base, received = qwen_server
    backend = QwenCodeBackend(
        AgentConfig(
            backend="agentrouter",
            client="qwen_code",
            model="gpt-6.1-sol",
            base_url="https://agentrouter.org/v1",
            api_protocol="responses",
            max_output_tokens=128,
            timeout_seconds=6,
        ),
        root,
        test_endpoint=base + "/slow-body/v1/responses",
    )
    with pytest.raises(ProviderError, match="cli_timeout"):
        backend.complete(
            [{"role": "system", "content": "Firm 0"}, {"role": "user", "content": "Price?"}]
        )
    assert len(received) == 1
    assert backend.last_evidence["http_status"] == 200
    assert backend.last_evidence["response_bytes"] == 6
    assert (
        backend.last_evidence["headers_received_at_ms"]
        <= backend.last_evidence["last_response_chunk_at_ms"]
    )
    assert backend.last_evidence["input_tokens"] is None


def test_actual_client_preserves_provider_retry_headers(root, qwen_server):
    _, base, _ = qwen_server
    backend = QwenCodeBackend(
        AgentConfig(
            backend="agentrouter",
            client="qwen_code",
            model="gpt-6.1-sol",
            base_url="https://agentrouter.org/v1",
            api_protocol="responses",
            max_output_tokens=128,
        ),
        root,
        test_endpoint=base + "/rate-stream/quota-headers/v1/responses",
    )
    with pytest.raises(ProviderError) as failure:
        backend.complete(
            [{"role": "system", "content": "Firm 0"}, {"role": "user", "content": "Price?"}]
        )
    assert failure.value.kind == "provider_rate_limit"
    assert failure.value.retry_after == 120
    assert backend.last_evidence["rate_limit_headers"]["retry-after-ms"] == "1500"
    assert backend.last_evidence["network_requests"] == 1


def test_real_cli_stop_cleans_owned_process(root, qwen_server):
    import time

    from auditors_paradox.adapters.model_calls import RunStopped

    _, base, received = qwen_server
    started = time.monotonic()

    def stop():
        if time.monotonic() - started > 0.5:
            raise RunStopped("test stop")

    backend = QwenCodeBackend(
        AgentConfig(backend="agentrouter", model="gpt-6.1-sol"),
        root,
        test_endpoint=base + "/v1/chat/completions",
        check_stop=stop,
    )
    with pytest.raises(RunStopped):
        backend.complete(
            [{"role": "system", "content": "Firm 0"}, {"role": "user", "content": "Price?"}]
        )
    assert backend.process is None


@pytest.mark.parametrize(
    "route,kind,retryable",
    [
        ("rate-stream", "provider_rate_limit", True),
        ("blocked-stream", "provider_resource_blocked", False),
        ("blocked-stream-temporary", "provider_resource_blocked", True),
        ("capacity-stream", "provider_capacity_unavailable", True),
    ],
)
def test_responses_embedded_errors(root, qwen_server, route, kind, retryable):
    _, base, received = qwen_server
    backend = QwenCodeBackend(
        AgentConfig(backend="agentrouter", model="gpt-5.6-sol", api_protocol="responses"),
        root,
        test_endpoint=base + "/" + route + "/v1/responses",
    )
    with pytest.raises(ProviderError) as caught:
        backend.complete(
            [{"role": "system", "content": "Reply OK."}, {"role": "user", "content": "Check"}]
        )
    assert caught.value.kind == kind and caught.value.retryable == retryable
    assert len(received) == 1 and backend.process is None
