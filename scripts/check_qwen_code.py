"""Exercise the genuine pinned client against a local server without paid requests."""

import json
import os
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from auditors_paradox.adapters.qwen_code import QwenCodeBackend
from auditors_paradox.config import AgentConfig
from auditors_paradox.evidence import atomic_json

REASONS = {
    "unexpected_remote_context": "The client tried to attach server-side conversation or stored-prompt context outside this firm's recorded messages.",
    "unexpected_network_connection": "The client attempted a connection outside the request guard.",
    "unexpected_tools": "The client added tools to a pricing request that must have none.",
    "prompt_contract_mismatch": (
        "Qwen Code changed the experiment's messages. The local report records the additions; "
        "this is a client prompt-isolation failure, not an API-token error."
    ),
}


def main():
    """Keep the qualification evidence even when the client's request is rejected."""
    requests = []
    responses = "--responses" in sys.argv

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            result = {
                "id": "local-check",
                "object": "chat.completion",
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "PRICE: 137"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 4, "total_tokens": 104},
            }
            if self.path.endswith("/responses"):
                message = {
                    "id": "msg-local",
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "PRICE: 137", "annotations": []}],
                }
                response = {
                    "id": "resp-local",
                    "object": "response",
                    "status": "completed",
                    "model": body["model"],
                    "output": [message],
                    "usage": {"input_tokens": 100, "output_tokens": 4, "total_tokens": 104},
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
                        "delta": "PRICE: 137",
                    },
                    {"type": "response.output_item.done", "output_index": 0, "item": message},
                    {"type": "response.completed", "response": response},
                ]
                data = "".join("data: " + json.dumps(event) + "\n\n" for event in events).encode()
                content_type = "text/event-stream"
            elif body.get("stream"):
                result["object"] = "chat.completion.chunk"
                result["choices"][0]["delta"] = result["choices"][0].pop("message")
                data = ("data: " + json.dumps(result) + "\n\ndata: [DONE]\n\n").encode()
                content_type = "text/event-stream"
            else:
                data = json.dumps(result).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    root = Path(__file__).resolve().parents[1]
    atomic_json(
        root / "tools/qwen-code/qualification.json",
        {"status": "checking", "reason": "Local acceptance checks in progress"},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cfg = AgentConfig(
        backend="agentrouter",
        api_protocol="responses" if responses else "chat",
        model="gpt-6.1-sol",
        base_url="https://agentrouter.org/v1",
        chat_token_parameter="max_completion_tokens",
        max_output_tokens=768,
        timeout_seconds=30,
    )
    backend = QwenCodeBackend(
        cfg,
        root,
        test_endpoint=f"http://127.0.0.1:{server.server_port}/v1/"
        + ("responses" if responses else "chat/completions"),
    )
    try:
        backend.complete(
            [
                {"role": "system", "content": "You are firm 0. Choose your price."},
                {
                    "role": "user",
                    "content": "Your last price was 137. Reply PRICE: followed by an integer.",
                },
            ]
        )
        status = "basic_check_passed"
        error = None
    except Exception as exc:
        status, error = "blocked", str(exc)
    finally:
        backend.close()
        server.shutdown()
    report = {
        "status": status,
        "error": error,
        "identity": backend.identity,
        "server_requests": len(requests),
        "paid_requests": 0,
        "qualification_status": "blocked",
        "explanation": REASONS.get(
            backend.last_evidence.get("error_kind"),
            "A basic reply alone does not qualify this client for paid research runs.",
        ),
        "evidence": backend.last_evidence,
        "session_directory": backend.directory.relative_to(root).as_posix(),
    }
    target = root / "runs/diagnostics/qwen-client" / backend.session_id
    target.mkdir(parents=True)
    if status == "basic_check_passed":
        junit = target / "acceptance.xml"
        test_env = {
            **os.environ,
            "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }
        check = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                "-m",
                "pytest",
                "tests/integration/test_qwen_transport.py",
                "tests/unit/test_qwen_code.py",
                "-q",
                "--tb=short",
                "--junitxml=" + str(junit),
                "--basetemp=" + str(root / ".cache" / ("qualification-" + backend.session_id)),
            ],
            cwd=root,
            env=test_env,
            capture_output=True,
            text=True,
            encoding="utf8",
            timeout=240,
        )
        (target / "acceptance.txt").write_text(check.stdout + check.stderr, encoding="utf8")
        suites = ET.parse(junit).getroot() if junit.exists() else None
        skipped = (
            sum(int(s.get("skipped", 0)) for s in suites.iter("testsuite"))
            if suites is not None
            else 1
        )
        if check.returncode == 0 and skipped == 0:
            report["qualification_status"] = "qualified"
            report["explanation"] = (
                "Local request, isolation, failure, market-delivery and cancellation checks passed. Live access is still unverified."
            )
            atomic_json(
                root / "tools/qwen-code/qualification.json",
                {
                    "status": "qualified",
                    "identity": backend.identity,
                    "report": (target / "report.json").relative_to(root).as_posix(),
                    "scope": "Local simulated qualification only; runtime compatibility hook recorded",
                },
            )
        else:
            status = "blocked"
            error = "acceptance_suite_failed"
            report.update(status=status, error="acceptance_suite_failed")
    atomic_json(target / "report.json", report)
    if report["qualification_status"] != "qualified":
        atomic_json(
            root / "tools/qwen-code/qualification.json",
            {
                "status": "blocked",
                "identity": backend.identity,
                "report": (target / "report.json").relative_to(root).as_posix(),
                "reason": "Local client acceptance did not pass; paid dispatch remains blocked",
            },
        )
    print(
        json.dumps(
            {
                "status": status,
                "error": error,
                "server_requests": len(requests),
                "explanation": report["explanation"],
                "qualification_status": report["qualification_status"],
                "report": (target / "report.json").relative_to(root).as_posix(),
            }
        )
    )
    return 0 if status == "basic_check_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
