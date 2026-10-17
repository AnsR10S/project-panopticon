"""Here we record every model request and keep each run inside its call and cost limits."""

from __future__ import annotations

import importlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Event, RLock
from typing import Any, Protocol, cast

from auditors_paradox.config import AgentConfig, BudgetConfig
from auditors_paradox.contracts import Completion, canonical
from auditors_paradox.evidence import EvidenceWriter, utc_now


class Backend(Protocol):
    """One model request attempt, no transport retries."""

    def complete(self, messages: list[dict[str, str]]) -> Completion:
        """Return the visible response and how many tokens it used."""
        ...


class ProviderError(RuntimeError):
    """A provider failure, without leaking the request or any credentials."""

    def __init__(
        self,
        kind: str,
        *,
        retryable: bool = False,
        retry_after: float = 0,
        status_code: int | None = None,
    ) -> None:
        """Keep the recovery decision without exposing the provider payload."""
        super().__init__(f"Provider request failed ({kind})")
        self.kind = kind
        self.retryable = retryable
        self.retry_after = retry_after
        self.status_code = status_code
        self.metadata: dict[str, Any] = {}


class RunStopped(RuntimeError):
    """Stop new requests while keeping the evidence already saved."""


def provider_error(exc: Exception) -> ProviderError:
    """Retry temporary failures, never authentication or exhausted credit."""
    status = getattr(exc, "status_code", getattr(exc, "code", None))
    status = status if type(status) is int and 100 <= status <= 599 else None
    body = getattr(exc, "body", {})
    detail = body.get("error", body) if isinstance(body, dict) else {}
    code = detail.get("code", detail.get("type")) if isinstance(detail, dict) else None
    message = str(detail.get("message", "")).lower() if isinstance(detail, dict) else ""
    kind = type(exc).__name__
    if code == "unauthorized_client_error" or "unauthorized client detected" in message:
        return ProviderError("client_not_authorized", status_code=status)
    if code in {"insufficient_quota", "billing_hard_limit_reached", "credit_balance_too_low"}:
        return ProviderError("quota_exhausted", status_code=status)
    if status in {401, 403}:
        return ProviderError("authentication_or_permission", status_code=status)
    retryable = status in {408, 409, 429, 500, 502, 503, 504} or kind in {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ReadTimeout",
        "TimeoutError",
        "URLError",
    }
    headers = (
        getattr(getattr(exc, "response", None), "headers", None)
        or getattr(exc, "headers", {})
        or {}
    )
    delay = 0.0
    retry_after = headers.get("retry-after", headers.get("Retry-After", "0"))
    try:
        try:
            delay = float(retry_after)
        except ValueError:
            delay = max(0.0, parsedate_to_datetime(retry_after).timestamp() - time.time())
        if not math.isfinite(delay) or delay < 0:
            delay = 0.0
    except (TypeError, ValueError, OverflowError):
        pass
    return ProviderError(kind, retryable=retryable, retry_after=delay, status_code=status)


@dataclass
class ScriptedBackend:
    """A separate queue of fixed replies for each agent, so we can test without models."""

    replies: list[str]
    client: Any = field(init=False)

    def __post_init__(self) -> None:
        """Create the CollusionLab scripted client from a copy of the replies."""
        module = importlib.import_module("collusionlab.agents.backends.scripted_client")
        self.client = module.ScriptedModelClient(replies=list(self.replies))

    def complete(self, messages: list[dict[str, str]]) -> Completion:
        """Give back the next scripted reply with its synthetic token counts."""
        before_input, before_output = self.client.input_tokens, self.client.output_tokens
        text = cast(str, self.client.generate(messages))
        return Completion(
            text,
            self.client.input_tokens - before_input,
            self.client.output_tokens - before_output,
            "scripted",
        )


@dataclass
class ProviderBackend:
    """Connect to the chosen provider, using the credentials from the environment."""

    config: AgentConfig
    price_bounds: tuple[int, int] | None = None
    client: Any = field(init=False)

    def __post_init__(self) -> None:
        """Create the SDK client with the timeout set and the transport retries turned off."""
        name = self.config.backend
        if name == "ollama" and self.config.api_protocol == "ollama":
            self.client = None
            return
        if name == "ollama":
            self.client = importlib.import_module("openai").OpenAI(
                api_key="local-ollama-unused",
                base_url=self.config.base_url or "http://127.0.0.1:11434/v1",
                max_retries=0,
                timeout=self.config.timeout_seconds,
            )
            return
        env_name = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "deepseek": "DEEPSEEK_API_KEY",
            "agentrouter": "AGENTROUTER_API_KEY",
        }[name]
        credential = os.environ.get(env_name)
        if not credential:
            raise ValueError(f"The selected live backend requires {env_name} at runtime")
        if name == "anthropic" or (
            name == "agentrouter" and self.config.api_protocol == "anthropic"
        ):
            kwargs: dict[str, Any] = {
                "api_key": credential,
                "max_retries": 0,
                "timeout": self.config.timeout_seconds,
            }
            if name == "agentrouter":
                kwargs["base_url"] = self.config.base_url
            self.client = importlib.import_module("anthropic").Anthropic(**kwargs)
        else:
            kwargs = {
                "api_key": credential,
                "max_retries": 0,
                "timeout": self.config.timeout_seconds,
            }
            if name == "deepseek":
                kwargs["base_url"] = "https://api.deepseek.com"
            elif name == "agentrouter":
                kwargs["base_url"] = self.config.base_url
            self.client = importlib.import_module("openai").OpenAI(**kwargs)

    def complete(self, messages: list[dict[str, str]]) -> Completion:
        """Send one request and return the text, the usage and the model that answered."""
        cfg = self.config
        options: dict[str, Any] = {"model": cfg.model}
        if cfg.temperature is not None:
            options["temperature"] = cfg.temperature
        try:
            if cfg.backend == "ollama" and cfg.api_protocol == "ollama":
                return self.local_complete(messages)
            if cfg.backend == "openai":
                response = self.client.responses.create(
                    input=messages, max_output_tokens=cfg.max_output_tokens, **options
                )
                usage = response.usage
                text = response.output_text
                input_tokens, output_tokens = usage.input_tokens, usage.output_tokens
            elif cfg.backend == "anthropic" or (
                cfg.backend == "agentrouter" and cfg.api_protocol == "anthropic"
            ):
                response = self.client.messages.create(
                    system=messages[0]["content"],
                    messages=messages[1:],
                    max_tokens=cfg.max_output_tokens,
                    **options,
                )
                text = "\n".join(block.text for block in response.content if block.type == "text")
                input_tokens, output_tokens = (
                    response.usage.input_tokens,
                    response.usage.output_tokens,
                )
            else:
                response = self.client.chat.completions.create(
                    messages=messages,
                    **{cfg.chat_token_parameter: cfg.max_output_tokens},
                    **options,
                )
                text = response.choices[0].message.content or ""
                input_tokens = response.usage.prompt_tokens
                output_tokens = response.usage.completion_tokens
            return Completion(
                str(text),
                int(input_tokens),
                int(output_tokens),
                str(response.model),
                getattr(response, "_request_id", None),
            )
        except ProviderError:
            raise
        except Exception as exc:
            # Keep the request content and credentials out of the error message.
            raise provider_error(exc) from None

    def local_complete(self, messages: list[dict[str, str]]) -> Completion:
        """Use native Ollama counts and timings without any paid endpoint."""
        cfg = self.config
        base = (cfg.base_url or "http://127.0.0.1:11434/v1").removesuffix("/v1")
        options: dict[str, Any] = {
            "num_ctx": cfg.context_tokens,
            "num_predict": cfg.max_output_tokens,
        }
        if cfg.temperature is not None:
            options["temperature"] = cfg.temperature
        if cfg.local_num_gpu is not None:
            options["num_gpu"] = cfg.local_num_gpu
        if cfg.local_repeat_penalty is not None:
            options["repeat_penalty"] = cfg.local_repeat_penalty
        if cfg.local_repeat_last_n is not None:
            options["repeat_last_n"] = cfg.local_repeat_last_n
        payload: dict[str, Any] = {
            "model": cfg.model,
            "messages": messages,
            "stream": False,
            "options": options,
            "keep_alive": "5m",
        }
        if cfg.local_thinking is not None:
            payload["think"] = cfg.local_thinking
        if cfg.local_output_format == "price_json":
            if self.price_bounds is None:
                raise ValueError("Structured pricing requires the configured market bounds")
            payload["format"] = {
                "type": "object",
                "properties": {
                    "price": {
                        "type": "integer",
                        "minimum": self.price_bounds[0],
                        "maximum": self.price_bounds[1],
                        "enum": list(range(self.price_bounds[0], self.price_bounds[1] + 1)),
                    }
                },
                "required": ["price"],
                "additionalProperties": False,
            }
        request = urllib.request.Request(
            base + "/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        dispatched_at_ms = time.time() * 1000
        try:
            with urllib.request.urlopen(request, timeout=cfg.timeout_seconds) as response:
                headers_received_at_ms = time.time() * 1000
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            error = provider_error(exc)
            try:
                body = json.loads(exc.read(8192).decode("utf8", errors="replace"))
                message = str(body.get("error", "")) if isinstance(body, dict) else ""
            except (ValueError, OSError):
                message = "Local server returned a non-JSON error"
            message = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}", "[REDACTED]", message)
            message = re.sub(r"(?i)[a-z]:[\\/][^\r\n\"']+", "<local-path>", message)
            error.metadata = {
                "network_requests": 1,
                "http_status": exc.code,
                "dispatched_at_ms": dispatched_at_ms,
                "provider_error": {"message": message[:1200]},
            }
            if any(
                text in message.lower()
                for text in ("failed to allocate", "out of memory", "insufficient memory")
            ):
                error.kind = "local_resource_exhausted"
                error.retryable = False
            raise error from None
        metadata = {
            key: result[key]
            for key in (
                "done_reason",
                "total_duration",
                "load_duration",
                "prompt_eval_duration",
                "eval_duration",
                "prompt_eval_cached_count",
            )
            if key in result
        }
        metadata.update(
            network_requests=1,
            dispatched_at_ms=dispatched_at_ms,
            headers_received_at_ms=headers_received_at_ms,
            nonstream_completed_at_ms=time.time() * 1000,
        )
        if "format" in payload:
            metadata["response_schema"] = payload["format"]
        if self.config.action_parser != "legacy" or cfg.local_truncation_retries:
            metadata["response_channels"] = {
                "answer": result["message"].get("content", ""),
                "thinking": result["message"].get("thinking", ""),
            }
        if not result.get("done") or (
            result.get("done_reason") == "length"
            and (cfg.action_parser != "legacy" or cfg.local_truncation_retries)
        ):
            error = ProviderError("incomplete_local_response")
            error.metadata = {
                **metadata,
                "configured_output_tokens": cfg.max_output_tokens,
                "reported_model": str(result.get("model", cfg.model)),
            }
            for source, target in (
                ("prompt_eval_count", "input_tokens"),
                ("eval_count", "output_tokens"),
            ):
                if type(result.get(source)) is int and result[source] >= 0:
                    error.metadata[target] = result[source]
            raise error
        return Completion(
            result["message"].get("content", ""),
            int(result["prompt_eval_count"]),
            int(result["eval_count"]),
            str(result["model"]),
            metadata=metadata,
        )


@dataclass
class Budget:
    """Track the calls, the reserved cost and the actual usage for one run."""

    config: BudgetConfig
    agent: AgentConfig
    calls: int = 0
    reserved_usd: float = 0
    observed_usd: float = 0
    root: Path | None = None
    started: float = field(default_factory=time.monotonic)
    last_dispatch: float = 0
    usage_incomplete: bool = False
    lock: RLock = field(default_factory=RLock, repr=False)
    cancelled: Event = field(default_factory=Event, repr=False)
    firm_dispatches: dict[int, float] = field(default_factory=dict, repr=False)

    def check_stop(self) -> None:
        """Check the operator stop file and the elapsed-time limit."""
        if self.cancelled.is_set():
            raise RunStopped("Another firm failed; this market round was cancelled")
        if self.root is not None and (self.root / self.config.stop_file).exists():
            raise RunStopped("Operator stop file is present")
        if self.config.max_elapsed_seconds is not None and (
            time.monotonic() - self.started >= self.config.max_elapsed_seconds
        ):
            raise RunStopped("Configured elapsed-time limit reached")

    def wait(self, seconds: float) -> None:
        """Keep checking for a stop while waiting between requests."""
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            self.check_stop()
            time.sleep(min(0.25, max(0, until - time.monotonic())))
        self.check_stop()

    def cancel(self) -> None:
        """Stop sibling requests without touching another run's stop file."""
        self.cancelled.set()

    def wait_for_dispatch(self, firm: int | None = None) -> None:
        """Claim one interval slot atomically; parallel firms keep separate interval clocks."""
        while True:
            self.check_stop()
            with self.lock:
                previous = self.last_dispatch if firm is None else self.firm_dispatches.get(firm, 0)
                delay = self.agent.min_request_interval_seconds - (time.monotonic() - previous)
                if delay <= 0:
                    self.last_dispatch = time.monotonic()
                    if firm is not None:
                        self.firm_dispatches[firm] = self.last_dispatch
                    return
            self.wait(min(delay, 0.25))

    def estimate(self, messages: list[dict[str, str]], agent: AgentConfig | None = None) -> float:
        """Calculate a reservation without changing shared counters or generation settings."""
        selected = agent or self.agent
        if selected.backend in {"scripted", "ollama"}:
            return 0.0
        bound = len(canonical(messages).encode("utf-8")) + self.config.request_overhead_tokens
        return (
            bound * float(self.config.input_usd_per_million or 0)
            + selected.max_output_tokens * float(self.config.output_usd_per_million or 0)
        ) / 1e6

    def reserve(self, messages: list[dict[str, str]], agent: AgentConfig | None = None) -> float:
        """Reserve the estimated cost first and refuse the call if it goes over a limit."""
        with self.lock:
            self.check_stop()
            if self.calls >= self.config.max_calls:
                raise RuntimeError("Configured model-call budget exhausted")
            reserve = self.estimate(messages, agent)
            if (
                self.config.enforce_spending_caps
                and reserve
                and self.reserved_usd + reserve > float(self.config.max_cost_usd or 0)
            ):
                raise RuntimeError("Configured estimated-cost reservation budget exhausted")
            self.calls += 1
            self.reserved_usd += reserve
            return reserve

    def account(self, completion: Completion) -> None:
        """Add the reported usage at our rates and check we are still under the cost cap."""
        with self.lock:
            if self.agent.backend not in {"scripted", "ollama"}:
                self.observed_usd += (
                    completion.input_tokens * float(self.config.input_usd_per_million or 0)
                    + completion.output_tokens * float(self.config.output_usd_per_million or 0)
                ) / 1e6
                if self.config.enforce_spending_caps and self.observed_usd > float(
                    self.config.max_cost_usd or 0
                ):
                    raise RuntimeError("Observed usage exceeded the configured estimated-cost cap")


@dataclass
class RecordedClient:
    """Sits between the agent and the provider so every request is logged and budgeted."""

    backend: Backend
    model_name: str
    agent_id: int
    writer: EvidenceWriter
    budget: Budget
    input_tokens: int = 0
    output_tokens: int = 0
    round_number: int = 0
    delivery_ids: list[str] = field(default_factory=list)
    round_call_ids: list[str] = field(default_factory=list)
    round_attempt_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Give managed clients the same stop check as the experiment budget."""
        setter = getattr(self.backend, "set_stop_check", None)
        if setter:
            setter(self.budget.check_stop)

    def close(self) -> None:
        """Close the provider resources owned by this firm."""
        closer = getattr(self.backend, "close", None)
        if closer:
            closer()

    def begin_round(self, round_number: int, delivery_ids: list[str]) -> None:
        """Set the round and which notice IDs are allowed for the next requests."""
        self.round_number = round_number
        self.delivery_ids = list(delivery_ids)
        self.round_call_ids = []
        self.round_attempt_ids = []

    def generate(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """Retry temporary transport failures without changing the decision or market round."""
        cfg = self.budget.agent
        attempt, transport_failures, truncations = 0, 0, 0
        output_limit: int | None = None
        while True:
            attempt += 1
            try:
                queued = time.monotonic()
                self.budget.wait_for_dispatch(self.agent_id if cfg.decision_workers == 3 else None)
                return self.attempt(
                    messages,
                    attempt,
                    admission_wait_seconds=time.monotonic() - queued,
                    output_token_limit=output_limit,
                )
            except ProviderError as exc:
                if (
                    exc.kind == "incomplete_local_response"
                    and exc.metadata.get("done_reason") == "length"
                    and isinstance(self.backend, ProviderBackend)
                    and cfg.backend == "ollama"
                    and cfg.api_protocol == "ollama"
                    and truncations < cfg.local_truncation_retries
                ):
                    output_limit = cfg.local_retry_output_tokens
                    used_input = exc.metadata.get("input_tokens")
                    if (
                        output_limit is None
                        or type(used_input) is not int
                        or used_input + output_limit + 64 > cfg.context_tokens
                    ):
                        raise
                    truncations += 1
                    self.writer.append(
                        "local_output_retry",
                        {
                            "round": self.round_number,
                            "agent_id": self.agent_id,
                            "failed_call_id": self.round_attempt_ids[-1],
                            "output_token_limit": output_limit,
                            "retry_number": truncations,
                            "policy": "restart_exact_observation_v1",
                        },
                    )
                    self.budget.check_stop()
                    continue
                transport_failures += 1
                if not exc.retryable or transport_failures >= cfg.transport_attempts:
                    raise
                delay = max(
                    exc.retry_after,
                    min(cfg.retry_max_seconds, cfg.retry_initial_seconds * 2 ** (attempt - 1)),
                )
                if delay > cfg.retry_max_seconds:
                    raise ProviderError("required_retry_delay_exceeds_limit") from None
                self.writer.append(
                    "transport_retry",
                    {
                        "round": self.round_number,
                        "agent_id": self.agent_id,
                        "failed_call_id": self.round_attempt_ids[-1],
                        "delay_seconds": delay,
                        "error_kind": exc.kind,
                    },
                )
                self.budget.wait(delay)

    def attempt(
        self,
        messages: list[dict[str, str]],
        attempt: int,
        *,
        admission_wait_seconds: float = 0,
        output_token_limit: int | None = None,
    ) -> str:
        """Save every request attempt, the parse corrections and failures included."""
        copied = [dict(message) for message in messages]
        selected = self.budget.agent
        backend = self.backend
        if output_token_limit is not None:
            if (
                not isinstance(backend, ProviderBackend)
                or selected.backend != "ollama"
                or selected.api_protocol != "ollama"
            ):
                raise ValueError("Output recovery is restricted to native local requests")
            selected = selected.model_copy(update={"max_output_tokens": output_token_limit})
            backend = ProviderBackend(selected, price_bounds=backend.price_bounds)
        reserve = self.budget.reserve(copied, selected)
        call_id = (
            f"call-r{self.round_number:04d}-a{self.agent_id}-{len(self.round_attempt_ids) + 1}"
        )
        self.round_attempt_ids.append(call_id)
        common = {
            "call_id": call_id,
            "round": self.round_number,
            "agent_id": self.agent_id,
            "messages": copied,
            "delivery_ids": list(self.delivery_ids),
            "requested_model": self.model_name,
            "reserved_cost_usd": reserve,
            "transport_attempt": attempt,
            "credential_env": getattr(self.backend, "credential_env", None),
        }
        if self.writer.manifest.get("config", {}).get("schema_version") == 2:
            common["admission_wait_seconds"] = admission_wait_seconds
        if selected.local_truncation_retries:
            common["output_token_limit"] = selected.max_output_tokens
        self.writer.append("model_call_started", {**common, "started_at": utc_now()})
        started = time.monotonic()
        try:
            completion = backend.complete(copied)
        except Exception as exc:
            metadata = getattr(exc, "metadata", {}) or getattr(self.backend, "last_evidence", {})
            known_usage = all(
                type(metadata.get(k)) is int and metadata[k] >= 0
                for k in ("input_tokens", "output_tokens")
            )
            if metadata.get("network_requests") and not known_usage:
                self.budget.usage_incomplete = True
            self.writer.append(
                "model_call_failed",
                {
                    "call_id": call_id,
                    "error_type": type(exc).__name__,
                    "round": self.round_number,
                    "agent_id": self.agent_id,
                    "error_kind": (
                        exc.kind if isinstance(exc, ProviderError) else type(exc).__name__
                    ),
                    "retryable": isinstance(exc, ProviderError) and exc.retryable,
                    "http_status": exc.status_code if isinstance(exc, ProviderError) else None,
                    "client_evidence": metadata,
                },
            )
            if known_usage:
                failed = Completion(
                    "",
                    metadata["input_tokens"],
                    metadata["output_tokens"],
                    metadata.get("reported_model", self.model_name),
                )
                self.input_tokens += failed.input_tokens
                self.output_tokens += failed.output_tokens
                self.budget.account(failed)
            raise
        self.input_tokens += completion.input_tokens
        self.output_tokens += completion.output_tokens
        self.round_call_ids.append(call_id)
        self.writer.append(
            "model_call_completed",
            {
                **common,
                "response": completion.text,
                "input_tokens": completion.input_tokens,
                "output_tokens": completion.output_tokens,
                "reported_model": completion.reported_model,
                "request_id": completion.request_id,
                "usage_kind": (
                    "synthetic"
                    if isinstance(self.backend, ScriptedBackend)
                    else "provider_reported"
                ),
                "transport_attempts": 1,
                "elapsed_seconds": time.monotonic() - started,
                "usage_metadata": completion.metadata,
            },
        )
        self.budget.account(completion)
        self.budget.check_stop()
        return completion.text

    def cost_estimate(self) -> float:
        """Return the usage estimate based on the configured rates."""
        return self.budget.observed_usd
