"""Run the pinned Qwen client with our own memory, request checks and stop controls."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from auditors_paradox.adapters.model_calls import ProviderError
from auditors_paradox.config import AgentConfig
from auditors_paradox.contracts import Completion
from auditors_paradox.evidence import atomic_json, file_hash

VERSION = "0.26.0"
RUNTIME_CHUNK = "chunk-ZZZAYAJW.js"
TOOLS_CHUNK = "chunk-2UNFJQ2D.js"


def installation(root: Path) -> dict[str, str]:
    """Check the installed version and record the exact package and launcher hashes."""
    folder = root / "tools/qwen-code"
    package = folder / "node_modules/@qwen-code/qwen-code"
    try:
        lock = json.loads((folder / "package-lock.json").read_text(encoding="utf-8"))
        info = json.loads((package / "package.json").read_text(encoding="utf-8"))
        pinned = lock["packages"]["node_modules/@qwen-code/qwen-code"]
        if info["version"] != VERSION or pinned["version"] != VERSION:
            raise ValueError("Qwen Code version does not match the study pin")
        return {
            "client": "qwen_code",
            "version": VERSION,
            "npm_integrity": pinned["integrity"],
            "lock_sha256": file_hash(folder / "package-lock.json"),
            "cli_sha256": file_hash(package / "cli.js"),
            "guard_sha256": file_hash(Path(__file__).with_name("qwen_guard.mjs")),
            "adapter_sha256": file_hash(Path(__file__)),
            "runtime_sha256": file_hash(package / "chunks" / RUNTIME_CHUNK),
            "tools_sha256": file_hash(package / "chunks" / TOOLS_CHUNK),
            "core_sha256": file_hash(package / "chunks/chunk-YGSOH3UK.js"),
            "undici_sha256": file_hash(package / "chunks/undici-PAA55QBV.js"),
            "responses_sha256": file_hash(
                package / "chunks/openaiResponsesContentGenerator-IZKOP7FE.js"
            ),
        }
    except (OSError, KeyError) as exc:
        raise ValueError("Install the pinned client with scripts/setup_qwen_code.ps1") from exc


class QwenCodeBackend:
    """Keep a private client session only for the parsing attempts of one decision."""

    def __init__(
        self,
        config: AgentConfig,
        root: Path,
        *,
        test_endpoint: str | None = None,
        check_stop: Callable[[], None] | None = None,
        credential_env: str = "AGENTROUTER_API_KEY",
    ) -> None:
        self.config, self.root = config, root.resolve()
        if credential_env not in {
            "AGENTROUTER_API_KEY",
            *(f"AGENTROUTER_API_KEY_FIRM_{i}" for i in range(3)),
        }:
            raise ValueError("Choose a shared or firm-specific AgentRouter credential variable")
        self.credential_env = credential_env
        self.identity = installation(self.root)
        self.node = shutil.which("node")
        if not self.node:
            raise ValueError("Qwen Code requires Node.js 22 or later")
        self.check_stop = check_stop or (lambda: None)
        self.endpoint = (config.base_url or "") + (
            "/responses" if config.api_protocol == "responses" else "/chat/completions"
        )
        self.testing = test_endpoint is not None
        if test_endpoint:
            url = urlsplit(test_endpoint)
            if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port:
                raise ValueError("The simulated server must use an explicit loopback port")
            self.endpoint = test_endpoint
        else:
            qualification = self.root / "tools/qwen-code/qualification.json"
            if not qualification.is_file():
                raise ValueError("Qwen Code has not passed the local request-contract checks")
            result = json.loads(qualification.read_text(encoding="utf-8"))
            if result.get("status") != "qualified" or result.get("identity") != self.identity:
                raise ValueError("Qwen Code is not qualified for paid requests; see its review")
        self.directory: Path | None = None
        self.session_id = ""
        self.previous: list[dict[str, str]] = []
        self.process: subprocess.Popen[str] | None = None
        self.last_evidence: dict[str, Any] = {}

    def set_stop_check(self, check: Callable[[], None]) -> None:
        """Use the run's stop file and time limit while the client is working."""
        self.check_stop = check

    def close(self) -> None:
        """Stop only the child we own and keep its credential-free diagnostic files."""
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.communicate(timeout=5)
        self.process = None

    def complete(self, messages: list[dict[str, str]]) -> Completion:
        """Let one recorded attempt reach the gateway, with no hidden client retries."""
        self.last_evidence = {
            "network_requests": 0,
            "status": "not_dispatched",
            "credential_env": self.credential_env,
        }
        cfg = self.config
        if len(messages) < 2 or messages[0]["role"] != "system" or messages[-1]["role"] != "user":
            raise ProviderError("unsupported_cli_conversation")
        first = len(messages) == 2
        if first:
            self.close()
            self.session_id = str(uuid.uuid4())
            self.directory = self.root / ".cache/qwen-sessions" / self.session_id
            self.directory.mkdir(parents=True)
            self.previous = []
        elif self.directory is None or messages[:-1] != self.previous:
            raise ProviderError("cli_session_mismatch")
        assert self.directory is not None
        folder = self.directory
        attempt = sum(m["role"] == "user" for m in messages)
        result_path = folder / f"attempt-{attempt}-result.json"
        contract_path = folder / f"attempt-{attempt}-contract.json"
        atomic_json(
            contract_path,
            {
                "endpoint": self.endpoint,
                "model": cfg.model,
                "messages": messages,
                "max_tokens": cfg.max_output_tokens,
                "token_parameter": cfg.chat_token_parameter,
                "temperature": cfg.temperature,
                "protocol": cfg.api_protocol,
                "reasoning_effort": cfg.reasoning_effort,
            },
        )
        home = folder / "client-home"
        home.mkdir(exist_ok=True)
        atomic_json(
            home / "settings.json",
            {
                "security": {
                    "auth": {
                        "selectedType": (
                            "openai-responses" if cfg.api_protocol == "responses" else "openai"
                        )
                    }
                },
                "general": {"enableAutoUpdate": False, "chatRecording": True},
                "model": {
                    "name": cfg.model,
                    "skipStartupContext": True,
                    "skipNextSpeakerCheck": True,
                    "skipLoopDetection": True,
                    "generationConfig": {
                        **(
                            {"reasoning": {"effort": cfg.reasoning_effort}}
                            if cfg.reasoning_effort
                            else {}
                        ),
                        "maxRetries": 0,
                        "timeout": int(cfg.timeout_seconds * 1000),
                        "samplingParams": {
                            (
                                "max_output_tokens"
                                if cfg.api_protocol == "responses"
                                else cfg.chat_token_parameter
                            ): cfg.max_output_tokens,
                            **(
                                {"temperature": cfg.temperature}
                                if cfg.temperature is not None
                                else {}
                            ),
                        },
                    },
                },
                "telemetry": {"enabled": False},
            },
        )
        atomic_json(folder / "empty-settings.json", {})
        credential = (
            "local-simulated-token" if self.testing else os.environ.get(self.credential_env)
        )
        if not credential:
            raise ValueError(f"AgentRouter requires {self.credential_env} at runtime")
        env = {
            k: v
            for k, v in os.environ.items()
            if k.upper()
            in {
                "SYSTEMROOT",
                "WINDIR",
                "PATH",
                "PATHEXT",
                "TEMP",
                "TMP",
                "COMSPEC",
            }
        }
        env.update(
            {
                "QWEN_HOME": str(home),
                "QWEN_RUNTIME_DIR": str(home),
                "QWEN_CODE_SYSTEM_SETTINGS_PATH": str(folder / "empty-settings.json"),
                "QWEN_CODE_SYSTEM_DEFAULTS_PATH": str(folder / "empty-settings.json"),
                "OPENAI_API_KEY": credential,
                "OPENAI_BASE_URL": self.endpoint.removesuffix("/chat/completions").removesuffix(
                    "/responses"
                ),
                "OPENAI_MODEL": cfg.model,
                "QWEN_CODE_UNATTENDED_RETRY": "0",
                "QWEN_CODE_NO_RELAUNCH": "1",
                "QWEN_CODE_MODELS_DEV": "off",
                "QWEN_USAGE_STATISTICS_ENABLED": "false",
                "QWEN_CODE_MAX_OUTPUT_TOKENS": str(cfg.max_output_tokens),
                "AP_QWEN_CONTRACT": str(contract_path),
                "AP_QWEN_RESULT": str(result_path),
                "AP_QWEN_CORE": (
                    self.root
                    / "tools/qwen-code/node_modules/@qwen-code/qwen-code/chunks/chunk-YGSOH3UK.js"
                )
                .resolve()
                .as_uri(),
                "AP_QWEN_RUNTIME": (
                    self.root
                    / "tools/qwen-code/node_modules/@qwen-code/qwen-code/chunks"
                    / RUNTIME_CHUNK
                )
                .resolve()
                .as_uri(),
                "AP_QWEN_TOOLS": (
                    self.root
                    / "tools/qwen-code/node_modules/@qwen-code/qwen-code/chunks"
                    / TOOLS_CHUNK
                )
                .resolve()
                .as_uri(),
                "NO_COLOR": "1",
                "CI": "1",
                "GIT_CEILING_DIRECTORIES": str(folder.parent),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": str(folder / "empty-git-config"),
            }
        )
        command = [
            str(self.node),
            "--expose-gc",
            f"--max-old-space-size={cfg.client_heap_mb}",
            "--import",
            Path(__file__).with_name("qwen_guard.mjs").resolve().as_uri(),
            str(self.root / "tools/qwen-code/node_modules/@qwen-code/qwen-code/cli.js"),
            "--safe-mode",
            "--auth-type",
            "openai-responses" if cfg.api_protocol == "responses" else "openai",
            "--model",
            cfg.model,
            "--system-prompt",
            messages[0]["content"],
            "--max-tool-calls",
            "0",
            "--max-session-turns",
            str(attempt),
            "--max-wall-time",
            str(max(1, int(cfg.timeout_seconds))),
            "--output-format",
            "stream-json",
            "--input-format",
            "stream-json",
            "--chat-recording",
            "true",
            "--telemetry=false",
        ]
        command += ["--session-id", self.session_id] if first else ["--resume", self.session_id]
        self.check_stop()
        client_started_at_ms = time.time() * 1000
        self.process = subprocess.Popen(
            command,
            cwd=folder,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        started = time.monotonic()
        try:
            pending: str | None = (
                json.dumps(
                    {
                        "type": "user",
                        "message": {"role": "user", "content": messages[-1]["content"]},
                    }
                )
                + "\n"
            )
            while True:
                self.check_stop()
                if time.monotonic() - started > cfg.timeout_seconds:
                    raise ProviderError("cli_timeout")
                try:
                    stdout, stderr = self.process.communicate(pending, timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    pending = None
            result = (
                json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
            )
            self.last_evidence = {
                **result,
                "client_started_at_ms": client_started_at_ms,
                "client_heap_mb": cfg.client_heap_mb,
                "client_finished_at_ms": time.time() * 1000,
                "credential_env": self.credential_env,
                "client_identity": self.identity,
                "client_exit_code": self.process.returncode,
            }
            # Keep local startup diagnostics useful, without retaining the runtime token.
            (folder / f"attempt-{attempt}-stderr.txt").write_text(
                stderr.replace(credential, "[REDACTED]"), encoding="utf-8"
            )
            if result.get("status") != "complete" or self.process.returncode != 0:
                kind = result.get("error_kind", "cli_execution_failed")
                if "heap out of memory" in stderr.lower():
                    kind = "cli_memory_exhausted"
                detail = result.get("provider_error", {})
                if (
                    detail.get("code")
                    in {
                        "insufficient_quota",
                        "billing_hard_limit_reached",
                        "credit_balance_too_low",
                    }
                    or result.get("http_status") == 402
                ):
                    kind = "quota_exhausted"
                elif kind == "rate_limit_or_quota":
                    kind = "provider_rate_limit"
                self.last_evidence["error_kind"] = kind
                hint = re.search(
                    r"(?:retry|try again)\s+(?:after|in)\s+(\d+(?:\.\d+)?)\s*(?:seconds?|s)\b",
                    str(detail.get("message", "")),
                    re.I,
                )
                result["error_kind"] = kind
                result["retry_after_seconds"] = max(
                    result.get("retry_after_seconds", 0), float(hint.group(1)) if hint else 0
                )
                if result.get("provider_error", {}).get("code") == "no_capacity":
                    kind = "provider_capacity_unavailable"
                temporary_resource = (
                    result.get("error_kind") == "provider_resource_blocked"
                    and "temporarily blocked"
                    in result.get("provider_error", {}).get("message", "").lower()
                )
                error = ProviderError(
                    kind,
                    status_code=result.get("http_status"),
                    retryable=first
                    and (
                        temporary_resource
                        or kind in {"provider_rate_limit", "provider_capacity_unavailable"}
                        or (
                            kind == "provider_http_error"
                            and result.get("http_status") in {408, 409, 500, 502, 503, 504}
                        )
                    ),
                    retry_after=max(
                        60 if temporary_resource or kind == "provider_capacity_unavailable" else 30,
                        result.get("retry_after_seconds", 0),
                        float(hint.group(1)) if hint else 0,
                    ),
                )
                error.metadata = self.last_evidence
                raise error
            answer = str(result["response"])
            self.previous = [*messages, {"role": "assistant", "content": answer}]
            return Completion(
                answer,
                result["input_tokens"],
                result["output_tokens"],
                result["reported_model"],
                result.get("request_id"),
                self.last_evidence,
            )
        except BaseException as exc:
            if result_path.exists():
                self.last_evidence = {
                    **json.loads(result_path.read_text(encoding="utf-8")),
                    "client_identity": self.identity,
                }
            if isinstance(exc, ProviderError):
                self.last_evidence["error_kind"] = exc.kind
                self.last_evidence["retry_after_seconds"] = exc.retry_after
                exc.metadata = self.last_evidence
            raise
        finally:
            self.close()
