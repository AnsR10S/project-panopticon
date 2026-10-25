"""Run the approved bounded live validation through Qwen Code with a shared allowance."""

import argparse
import atexit
import json
import math
import random
import re
import shutil
import time
import uuid
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import RLock
from typing import Any

from agentrouter_credentials import load_tokens

from auditors_paradox.adapters.model_calls import Budget, ProviderError
from auditors_paradox.adapters.qwen_code import QwenCodeBackend
from auditors_paradox.config import AgentConfig, BudgetConfig, StudyConfig, load_config
from auditors_paradox.contracts import Completion, canonical
from auditors_paradox.evidence import atomic_json
from auditors_paradox.runner import run_experiment


class RequestPacer:
    """Keep the shared rolling-minute allowance across firms, retries and restarts."""

    def __init__(
        self,
        previous: dict[str, Any],
        requests_per_minute: int,
        tokens_per_minute: int,
        *,
        recovery_successes: int = 3,
        recovery_floor_seconds: float = 60,
    ) -> None:
        if requests_per_minute < 1 or tokens_per_minute < 1:
            raise ValueError("Request and token pacing limits must be positive")
        self.requests_per_minute = requests_per_minute
        self.tokens_per_minute = tokens_per_minute
        self.entries = list(previous.get("entries", []))
        self.cooldown_until = previous.get("cooldown_until", 0)
        self.rate_limit_streak = previous.get("rate_limit_streak", 0)
        self.last_rate_limit_at = previous.get("last_rate_limit_at", 0)
        self.adaptive_interval_seconds = previous.get(
            "adaptive_interval_seconds", 60 if self.rate_limit_streak else 0
        )
        self.successes_since_limit = previous.get("successes_since_limit", 0)
        self.transitions = list(previous.get("transitions", []))
        self.counted_successes = {tuple(value) for value in previous.get("counted_successes", [])}
        self.recovery_successes = recovery_successes
        self.recovery_floor_seconds = recovery_floor_seconds
        if recovery_successes < 1 or recovery_floor_seconds <= 0:
            raise ValueError("Pacing recovery controls must be positive")
        self.provider_limits = dict(previous.get("provider_limits", {}))
        self.provider_windows = dict(previous.get("provider_windows", {}))

    @staticmethod
    def seconds(value: Any, now: float) -> float:
        """Read relative reset durations, Unix times and HTTP dates without guessing a quota."""
        try:
            number = float(value)
            return (
                max(0.0, number - now if number > 1e9 else number) if math.isfinite(number) else 0.0
            )
        except (ValueError, TypeError):
            text = str(value or "")
        pieces = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", text)
        if pieces and "".join(n + unit for n, unit in pieces) == text:
            return sum(
                float(n) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit] for n, unit in pieces
            )
        try:
            return max(0.0, parsedate_to_datetime(text).timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            return 0.0

    def effective_limit(self, kind: str) -> int:
        """Treat operator caps and provider-reported limits as ceilings, never promises."""
        configured = self.requests_per_minute if kind == "requests" else self.tokens_per_minute
        return min(configured, self.provider_limits.get(kind, configured))

    def delay(self, tokens: int, now: float) -> float:
        """Reserve the full output ceiling and a conservative input estimate for pacing."""
        if tokens > self.effective_limit("tokens"):
            raise ValueError(
                "One request exceeds tokens-per-minute; raise that limit or shorten the request"
            )
        active = sorted((e for e in self.entries if e["at"] > now - 60), key=lambda e: e["at"])
        released = max(now, self.cooldown_until)
        if self.entries and self.adaptive_interval_seconds:
            released = max(
                released, max(e["at"] for e in self.entries) + self.adaptive_interval_seconds
            )
        for kind, amount in (("requests", 1), ("tokens", tokens), ("project-tokens", tokens)):
            window = self.provider_windows.get(kind, {})
            if window.get("reset_at", 0) > now and window.get("remaining", amount) < amount:
                released = max(released, window["reset_at"])
        while len(active) >= self.effective_limit("requests") or sum(
            e["tokens"] for e in active
        ) + tokens > self.effective_limit("tokens"):
            released = max(released, active.pop(0)["at"] + 60)
        return max(0, released - now)

    def record(self, tokens: int, now: float) -> None:
        """Save the reservation before the client can transmit anything."""
        if self.delay(tokens, now) > 0:
            raise RuntimeError("Request pacing allowance is not ready")
        retention = max(60, self.adaptive_interval_seconds)
        self.entries = [e for e in self.entries if e["at"] > now - retention]
        self.entries.append({"at": now, "tokens": tokens})
        for kind, amount in (("requests", 1), ("tokens", tokens), ("project-tokens", tokens)):
            window = self.provider_windows.get(kind, {})
            if window.get("reset_at", 0) > now:
                window["remaining"] = max(0, window["remaining"] - amount)

    def observe(self, evidence: dict[str, Any], now: float) -> None:
        """Honor provider delays and slow repeated rate failures instead of starting over."""
        headers = evidence.get("rate_limit_headers", {})
        received = evidence.get("headers_received_at_ms", now * 1000) / 1000
        for kind in ("requests", "tokens", "project-tokens"):
            try:
                limit = int(headers.get(f"x-ratelimit-limit-{kind}", 0))
                if limit > 0 and kind != "project-tokens":
                    self.provider_limits[kind] = min(limit, self.provider_limits.get(kind, limit))
                remaining = int(headers[f"x-ratelimit-remaining-{kind}"])
                reset = self.seconds(headers.get(f"x-ratelimit-reset-{kind}"), received)
                old = self.provider_windows.get(kind, {})
                if reset > 0 and received >= old.get("received_at", 0):
                    if old.get("reset_at", 0) > now:
                        remaining = min(remaining, old["remaining"])
                    self.provider_windows[kind] = {
                        "remaining": max(0, remaining),
                        "reset_at": received + reset,
                        "received_at": received,
                    }
            except (KeyError, ValueError, TypeError):
                pass
        limited = evidence.get("error_kind") in {"provider_rate_limit", "rate_limit_or_quota"}
        delay = evidence.get("retry_after_seconds", 0) or 0
        delay = max(
            delay, max(0, received + self.seconds(headers.get("retry-after"), received) - now)
        )
        if limited:
            before = self.adaptive_interval_seconds
            self.rate_limit_streak += 1
            self.last_rate_limit_at = now
            self.successes_since_limit = 0
            self.counted_successes.clear()
            self.adaptive_interval_seconds = min(300, max(60, self.adaptive_interval_seconds * 2))
            delay = max(
                delay,
                min(900, 60 * 2 ** min(self.rate_limit_streak - 1, 4)) + random.uniform(0, 10),
            )
            self.transitions.append(
                {
                    "at": now,
                    "kind": "rate_limit",
                    "from_seconds": before,
                    "to_seconds": self.adaptive_interval_seconds,
                }
            )
        elif evidence.get("status") == "complete" and evidence.get(
            "dispatched_at_ms", 0
        ) / 1000 > max(self.last_rate_limit_at, self.cooldown_until):
            success_identity = (evidence["dispatched_at_ms"], str(evidence.get("request_id", "")))
            if success_identity in self.counted_successes:
                return
            self.counted_successes.add(success_identity)
            self.successes_since_limit += 1
            if self.successes_since_limit >= self.recovery_successes:
                before = self.adaptive_interval_seconds
                self.rate_limit_streak = 0
                self.successes_since_limit = 0
                interval = self.adaptive_interval_seconds / 2
                self.adaptive_interval_seconds = (
                    interval if interval >= self.recovery_floor_seconds else 0
                )
                if before != self.adaptive_interval_seconds:
                    self.transitions.append(
                        {
                            "at": now,
                            "kind": "eligible_success_recovery",
                            "from_seconds": before,
                            "to_seconds": self.adaptive_interval_seconds,
                        }
                    )
        if delay > 0:
            self.cooldown_until = max(self.cooldown_until, now + delay)

    def state(self) -> dict[str, Any]:
        return {
            "policy_version": "adaptive-recovery-v2",
            "transitions": self.transitions,
            "counted_successes": sorted(self.counted_successes),
            "entries": self.entries,
            "cooldown_until": self.cooldown_until,
            "rate_limit_streak": self.rate_limit_streak,
            "last_rate_limit_at": self.last_rate_limit_at,
            "adaptive_interval_seconds": self.adaptive_interval_seconds,
            "successes_since_limit": self.successes_since_limit,
            "provider_limits": self.provider_limits,
            "provider_windows": self.provider_windows,
        }


def pricing_replay(records, root, profile, model):
    """Replay an initial real market observation, never a hypothetical diagnostic case."""
    candidates = [
        r
        for r in records
        if len(r.get("messages", [])) == 2
        and r["messages"][0]
        .get("content", "")
        .startswith("You manage Firm 0 in a simulated market")
        and r["messages"][1].get("content", "").startswith("Decision round 1.")
        and "\nChoose your own price:" in r["messages"][1]["content"]
    ]
    if not candidates:
        raise ValueError("No recorded initial real-market observation is available to replay")
    saved = next(
        (
            r
            for r in candidates
            if r.get("evidence", {}).get("wire_request", {}).get("model") == model
        ),
        candidates[0],
    )
    messages = [dict(m) for m in saved["messages"]]
    prefix, _, original_tail = messages[-1]["content"].partition("\nChoose your own price:")
    bounds = original_tail.splitlines()[0].strip()
    name = "price-only" if profile == "price_only" else "price-first"
    template = (root / f"prompts/pricing/{name}.txt").read_text(encoding="utf8")
    _, _, tail = template.partition("Choose your own price:")
    tail = tail.replace("{action_space_description}", bounds.removesuffix("."))
    messages[-1]["content"] = prefix + "\nChoose your own price:" + tail
    if profile == "bounded_price_first":
        messages[-1]["content"] += "\n" + (root / "prompts/pricing/bounded-guidance.txt").read_text(
            encoding="utf8"
        )
    return saved["validation"], messages


def saved_connection(records, directory, agent, *, exact_messages=None, client_identity=None):
    """Reuse a successful matching connection or pricing reply without another request."""
    from auditors_paradox.evaluation.validate import parsed_price

    for record in reversed(records):
        if record.get("status") != "complete" or record.get("reported_model") != agent.model:
            continue
        if exact_messages is not None and record.get("messages") != exact_messages:
            continue
        if (
            client_identity is not None
            and record.get("evidence", {}).get("client_identity") != client_identity
        ):
            continue
        reply = record.get("response", "").strip()
        if reply != "OK" and parsed_price(reply, 100, 250) is None:
            continue
        source = directory / (record["validation"] + ".json")
        if not source.is_file():
            continue
        prior = json.loads(source.read_text(encoding="utf8")).get("config", {}).get("agent", {})
        if all(
            prior.get(key) == getattr(agent, key) for key in ("model", "base_url", "api_protocol")
        ):
            return record
    return None


def settle_usage(budget: Budget, completion: Completion, reserved: float) -> float:
    """Keep the reported charge and release only the known unused part of this reservation."""
    with budget.lock:
        before = budget.observed_usd
        try:
            budget.account(completion)
        finally:
            actual = budget.observed_usd - before
            budget.reserved_usd += actual - reserved
    return actual


class ManagedBackend:
    """Share the allowance and pacing gate while each firm owns its client and credential."""

    def __init__(
        self,
        agent: AgentConfig,
        root: Path,
        budget: Budget,
        pacer: RequestPacer,
        records: list[dict[str, Any]],
        save: Callable[[], None],
        identity: str,
        lock: RLock,
        *,
        firm: int = 0,
        alternative_probe: bool = False,
        enforce_probe_cost_cap: bool = True,
        backend_factory: Callable[..., QwenCodeBackend] = QwenCodeBackend,
    ) -> None:
        self.agent, self.root, self.budget, self.pacer = agent, root, budget, pacer
        self.records, self.save, self.identity, self.lock = records, save, identity, lock
        self.firm, self.alternative_probe = firm, alternative_probe
        self.enforce_probe_cost_cap = enforce_probe_cost_cap
        self.credential_env = agent.credential_env(firm)
        self.backend = backend_factory(agent, root, credential_env=self.credential_env)
        self.run_stop_check: Callable[[], None] = lambda: None
        self.backend.set_stop_check(self.check_stop)

    @property
    def last_evidence(self) -> dict[str, Any]:
        """Expose the firm's safe request evidence to the outer run recorder."""
        return self.backend.last_evidence

    def check_stop(self) -> None:
        """Honor both the workflow stop and the current market's sibling cancellation."""
        self.budget.check_stop()
        self.run_stop_check()

    def set_stop_check(self, check: Callable[[], None]) -> None:
        """Attach the current market's cancellation check."""
        self.run_stop_check = check

    def close(self) -> None:
        """Close only this firm's owned client resources."""
        self.backend.close()

    def complete(self, messages: list[dict[str, str]]) -> Completion:
        """Reserve and record under one lock, then release it before client inference."""
        from auditors_paradox.contracts import Completion

        self.backend.last_evidence = {
            "network_requests": 0,
            "status": "not_dispatched",
            "credential_env": self.credential_env,
        }
        paced_tokens = (
            len(canonical(messages).encode("utf8"))
            + self.budget.config.request_overhead_tokens
            + self.agent.max_output_tokens
        )
        last_wait_message = 0.0
        queued_at = time.monotonic()
        provider_cooldown_seconds = 0.0
        while True:
            self.check_stop()
            with self.lock:
                now = time.monotonic()
                previous = (
                    self.budget.firm_dispatches.get(self.firm, 0)
                    if self.agent.decision_workers == 3
                    else self.budget.last_dispatch
                )
                delay = max(
                    self.pacer.delay(paced_tokens, time.time()),
                    self.agent.min_request_interval_seconds - (now - previous),
                )
                if delay <= 0:
                    estimate = self.budget.estimate(messages, self.agent)
                    if (
                        self.alternative_probe
                        and self.enforce_probe_cost_cap
                        and sum(
                            r.get("reserved_usd", 0)
                            for r in self.records
                            if r.get("alternative_probe")
                        )
                        + estimate
                        > 1
                    ):
                        raise RuntimeError(
                            "Combined alternative-model probe allowance of $1 exhausted"
                        )
                    reserved = self.budget.reserve(messages, self.agent)
                    record = {
                        "validation": self.identity,
                        "status": "reserved",
                        "messages": messages,
                        "firm": self.firm,
                        "credential_env": self.credential_env,
                        "alternative_probe": self.alternative_probe,
                        "reserved_usd": reserved,
                        "dispatched_at": time.time(),
                        "paced_token_reservation": paced_tokens,
                    }
                    self.pacer.record(paced_tokens, record["dispatched_at"])
                    self.budget.last_dispatch = time.monotonic()
                    self.budget.firm_dispatches[self.firm] = self.budget.last_dispatch
                    self.records.append(record)
                    self.save()
                    break
            if now - last_wait_message >= 30:
                print(
                    f"Firm {self.firm}: shared request pacing, waiting {delay:.1f} seconds",
                    flush=True,
                )
                last_wait_message = now
            wait_started = time.monotonic()
            in_provider_cooldown = time.time() < self.pacer.cooldown_until
            self.budget.wait(min(delay, 0.25))
            if in_provider_cooldown:
                provider_cooldown_seconds += time.monotonic() - wait_started
        accounted = False
        started = time.monotonic()
        try:
            self.check_stop()
            result = self.backend.complete(messages)
            result.metadata["queue_and_pacing_seconds"] = started - queued_at
            result.metadata["provider_cooldown_seconds"] = provider_cooldown_seconds
            result.metadata["client_elapsed_seconds"] = time.monotonic() - started
            with self.lock:
                self.pacer.observe(self.backend.last_evidence, time.time())
                accounted = True
                record["estimated_cost_usd"] = settle_usage(self.budget, result, reserved)
                record.update(
                    status="complete",
                    response=result.text,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    reported_model=result.reported_model,
                    network_requests=self.backend.last_evidence.get("network_requests"),
                    evidence=self.backend.last_evidence,
                    session_directory=self.backend.directory.relative_to(self.root).as_posix(),
                )
            return result
        except BaseException as exc:
            with self.lock:
                evidence = self.backend.last_evidence
                record.update(status="failed", error=type(exc).__name__, evidence=evidence)
                self.pacer.observe(evidence, time.time())
                if not accounted and all(
                    type(evidence.get(k)) is int for k in ("input_tokens", "output_tokens")
                ):
                    record["estimated_cost_usd"] = settle_usage(
                        self.budget,
                        Completion(
                            "",
                            evidence["input_tokens"],
                            evidence["output_tokens"],
                            evidence.get("reported_model", self.agent.model),
                        ),
                        reserved,
                    )
                elif not accounted and evidence.get("network_requests") == 0:
                    with self.budget.lock:
                        self.budget.reserved_usd -= reserved
                    record["released_unsent_reservation_usd"] = reserved
            raise
        finally:
            with self.lock:
                record["elapsed_seconds"] = time.monotonic() - started
                self.save()


VALIDATION_FIELDS = (
    "model",
    "base_url",
    "api_protocol",
    "prompt_profile",
    "max_output_tokens",
    "temperature",
    "reasoning_effort",
)


def matching_live_validation(
    root: Path, agent: AgentConfig, *, allow_probe: bool = False
) -> list[str]:
    """Read matching completed checks without loading credentials or sending requests."""
    statuses = {"complete"}
    if allow_probe:
        statuses.update({"probe_complete", "pricing_probe_complete"})
    matches = []
    for path in sorted((root / "runs/diagnostics/qwen-live").glob("validation-*.json")):
        prior = json.loads(path.read_text(encoding="utf8"))
        recorded = prior.get("config", {}).get("agent", {})
        if prior.get("status") in statuses and all(
            recorded.get(key) == getattr(agent, key) for key in VALIDATION_FIELDS
        ):
            matches.append(path.relative_to(root).as_posix())
    return matches


def workflow_cost_cap(
    cfg: StudyConfig, held: float, new_limit: float | None, lifetime_limit: float = 1000
) -> float:
    """Keep the authorized total; optional workflow caps apply only when enabled."""
    if not math.isfinite(lifetime_limit) or not 0 < lifetime_limit <= 1000:
        raise ValueError("lifetime-cost-limit must be positive and at most the authorized $1,000")
    if new_limit is not None and (not math.isfinite(new_limit) or new_limit <= 0):
        raise ValueError("new-cost-limit must be finite and positive")
    return (
        min(lifetime_limit, held + new_limit)
        if cfg.budget.enforce_spending_caps and new_limit is not None
        else lifetime_limit
    )


def workflow_call_cap(previous_attempts: int, additional_limit: int | None, extended: bool) -> int:
    """A fresh validation gets additional calls; prior usage still counts toward 4,000."""
    allowance = additional_limit if additional_limit is not None else (4000 if extended else 64)
    if previous_attempts < 0 or allowance < 1:
        raise ValueError("Request counts must be nonnegative with a positive new allowance")
    return min(4000, previous_attempts + allowance)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--model", choices=["gpt-6.1-sol", "gpt-5.6-sol", "gpt-6-astra"])
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--reuse-probe", action="store_true")
    parser.add_argument("--pricing-probe", action="store_true")
    parser.add_argument("--protocol", choices=["chat", "responses"])
    parser.add_argument(
        "--prompt-profile",
        choices=[
            "price_first",
            "bounded_price_first",
            "price_only",
            "explicit_economics",
            "qualitative_v2",
            "explicit_economics_v2",
            "economics_price_only",
            "economics_private_note",
        ],
    )
    parser.add_argument("--output-tokens", type=int)
    parser.add_argument("--requests-per-minute", type=int, default=1)
    parser.add_argument("--tokens-per-minute", type=int, default=20000)
    parser.add_argument("--new-request-limit", type=int)
    parser.add_argument("--new-cost-limit", type=float)
    parser.add_argument("--lifetime-cost-limit", type=float, default=1000)
    parser.add_argument(
        "--base-url", choices=["https://agentrouter.org/v1", "https://co.agentrouter.org/v1"]
    )
    parser.add_argument("--diagnostics", action="store_true")
    parser.add_argument("--v2-battery", action="store_true")
    parser.add_argument("--diagnostic-limit", type=int, default=56)
    parser.add_argument("--pilot-rounds", type=int, default=0)
    parser.add_argument("--replicates", type=int, default=1)
    parser.add_argument("--sweep-config", action="store_true")
    parser.add_argument("--resume-sweep", type=Path)
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--sweep-id")
    parser.add_argument("--assignment-seed", type=int, default=31415)
    parser.add_argument("--analysis-settings", type=Path)
    parser.add_argument("--workflow-seconds", type=float)
    args = parser.parse_args()
    if args.v2_battery:
        if not args.allow_paid or args.new_request_limit is None:
            raise ValueError("V2 battery needs explicit paid/request allowances")
        if not 1 <= args.diagnostic_limit <= 56:
            raise ValueError("diagnostic-limit must be 1 to 56")
        args.diagnostics = True
    if args.sweep_config and (
        not args.allow_paid
        or args.new_request_limit is None
        or any(
            (
                args.probe_only,
                args.pricing_probe,
                args.diagnostics,
                args.pilot_rounds,
                args.model,
                args.protocol,
                args.prompt_profile,
                args.output_tokens,
                args.base_url,
            )
        )
    ):
        raise ValueError(
            "Exact-config sweeps require explicit paid/request limits and no overrides"
        )
    replicate_limit = 1000 if args.sweep_config else 5
    if not 0 <= args.pilot_rounds <= 100 or not 1 <= args.replicates <= replicate_limit:
        raise ValueError("Use 1–100 pilot rounds and 1–5 replicates")
    root = Path(__file__).resolve().parents[1]
    cfg = load_config(args.config)
    workflow_cost_cap(cfg, 0, args.new_cost_limit, args.lifetime_cost_limit)
    if (
        cfg.budget.enforce_spending_caps
        and (args.v2_battery or args.sweep_config)
        and args.new_cost_limit is None
    ):
        raise ValueError("Enabled spending caps require an explicit new-cost-limit")
    if args.protocol:
        data = cfg.model_dump(mode="json")
        data["agent"]["api_protocol"] = args.protocol
        if args.protocol == "chat":
            data["agent"]["reasoning_effort"] = None
        cfg = StudyConfig.model_validate(data)
    if args.pricing_probe:
        data = cfg.model_dump(mode="json")
        data["agent"].update(max_output_tokens=8192, timeout_seconds=300)
        cfg = StudyConfig.model_validate(data)
    if args.prompt_profile or args.output_tokens:
        data = cfg.model_dump(mode="json")
        if args.prompt_profile:
            data["agent"]["prompt_profile"] = args.prompt_profile
        if args.output_tokens:
            data["agent"]["max_output_tokens"] = args.output_tokens
        cfg = StudyConfig.model_validate(data)
    if args.base_url:
        data = cfg.model_dump(mode="json")
        data["agent"]["base_url"] = args.base_url
        cfg = StudyConfig.model_validate(data)
    if args.model:
        data = cfg.model_dump(mode="json")
        data["agent"]["model"] = args.model
        rates = {"gpt-6.1-sol": (3, 15), "gpt-5.6-sol": (8, 40), "gpt-6-astra": (16, 80)}
        data["budget"].update(
            input_usd_per_million=rates[args.model][0], output_usd_per_million=rates[args.model][1]
        )
        cfg = StudyConfig.model_validate(data)
    if cfg.agent.client != "qwen_code" or cfg.agent.model not in {
        "gpt-6.1-sol",
        "gpt-5.6-sol",
        "gpt-6-astra",
    }:
        raise ValueError("Validation requires Qwen Code and an owner-authorized model")
    QwenCodeBackend(cfg.agent, root).close()
    load_tokens(root, cfg.agent.credential_mode)
    directory = root / "runs/diagnostics/qwen-live"
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / "active.lock"
    try:
        lock = lock_path.open("x", encoding="utf8")
    except FileExistsError:
        raise RuntimeError(
            "Another paid workflow may be active. Inspect it before removing active.lock."
        ) from None

    def release():
        lock.close()
        lock_path.unlink(missing_ok=True)

    atexit.register(release)
    ledger = directory / "allowance.json"
    previous = json.loads(ledger.read_text(encoding="utf8")) if ledger.exists() else {}
    pacer = RequestPacer(
        previous.get("pacing", {}),
        args.requests_per_minute,
        args.tokens_per_minute,
        recovery_successes=cfg.v2.pacing_successes if cfg.v2 else 3,
        recovery_floor_seconds=cfg.v2.pacing_recovery_floor_seconds if cfg.v2 else 60,
    )
    if args.pilot_rounds or args.diagnostics or args.sweep_config:
        if not matching_live_validation(root, cfg.agent, allow_probe=bool(args.diagnostics)):
            raise ValueError(
                f"Complete matching live validation for {cfg.agent.model} / {cfg.agent.prompt_profile} "
                "before the exploratory pilot. Run the notebook's AgentRouter readiness cell first."
            )
    call_cap = workflow_call_cap(
        previous.get("reserved_attempts", 0),
        args.new_request_limit,
        bool(args.pilot_rounds or args.diagnostics or args.sweep_config),
    )
    dollar_cap = workflow_cost_cap(
        cfg, previous.get("reserved_usd", 0), args.new_cost_limit, args.lifetime_cost_limit
    )
    caps = BudgetConfig(
        max_calls=call_cap,
        enforce_spending_caps=True,
        max_cost_usd=dollar_cap,
        request_overhead_tokens=cfg.budget.request_overhead_tokens,
        input_usd_per_million=cfg.budget.input_usd_per_million,
        output_usd_per_million=cfg.budget.output_usd_per_million,
        max_elapsed_seconds=43200 if args.pilot_rounds else 3600,
        stop_file=cfg.budget.stop_file,
    )
    if args.workflow_seconds is not None:
        caps = BudgetConfig.model_validate(
            {**caps.model_dump(), "max_elapsed_seconds": args.workflow_seconds}
        )
    budget = Budget(caps, cfg.agent, root=root)
    budget.calls = previous.get("reserved_attempts", 0)
    budget.reserved_usd = previous.get("reserved_usd", 0)
    budget.observed_usd = previous.get("observed_usd", 0)
    records = previous.get("requests", [])
    identity = (
        args.sweep_id
        or (
            "diagnostics-"
            if args.diagnostics
            else ("pilot-" if args.pilot_rounds else "validation-")
        )
        + uuid.uuid4().hex[:12]
    )
    from auditors_paradox.config import safe_identifier

    safe_identifier(identity)
    if (directory / identity).exists() or (directory / (identity + ".json")).exists():
        raise FileExistsError("Workflow identity already exists; preserve it and choose a new ID")
    report = {
        "id": identity,
        "status": "running",
        "stages": [],
        "scope": "Operational validation, not research inference",
        "config": cfg.model_dump(mode="json"),
        "optional_spending_caps_enforced": cfg.budget.enforce_spending_caps,
        "cumulative_cost_limit_usd": dollar_cap,
        "request_pacing": {
            "requests_per_minute": pacer.requests_per_minute,
            "tokens_per_minute": pacer.tokens_per_minute,
            "provider_limits": pacer.provider_limits,
        },
    }
    source = directory / identity
    source.mkdir()
    for filename in (
        "validate_qwen_live.py",
        "agentrouter_credentials.py",
        "research_diagnostics.py",
    ):
        shutil.copyfile(root / "scripts" / filename, source / filename)
    target = directory / (identity + ".json")

    workflow_lock = RLock()

    def save():
        with workflow_lock:
            atomic_json(
                directory / "pacing.json",
                {"validation": identity, "model": cfg.agent.model, "pacing": pacer.state()},
            )
            atomic_json(
                ledger,
                {
                    "reserved_attempts": budget.calls,
                    "reserved_usd": budget.reserved_usd,
                    "observed_usd": budget.observed_usd,
                    "requests": records,
                    "pacing": pacer.state(),
                },
            )
            atomic_json(target, report)

    def Managed(agent, firm=0):
        return ManagedBackend(
            agent,
            root,
            budget,
            pacer,
            records,
            save,
            identity,
            workflow_lock,
            firm=firm,
            alternative_probe=args.probe_only and agent.model != "gpt-6.1-sol",
            enforce_probe_cost_cap=cfg.budget.enforce_spending_caps,
        )

    def call_diagnostic(client, messages):
        for attempt in range(3):
            try:
                return client.complete(messages)
            except ProviderError as error:
                if not error.retryable or attempt == 2:
                    raise
                budget.wait(max(30, error.retry_after))
        raise AssertionError("Diagnostic attempt loop did not finish")

    save()
    try:
        if args.sweep_config:
            from auditors_paradox.batch import run_sweep
            from auditors_paradox.evaluation.inference import AnalysisSettings, analyze_sweep

            settings = (
                AnalysisSettings.model_validate_json(
                    args.analysis_settings.read_text(encoding="utf8")
                )
                if args.analysis_settings
                else AnalysisSettings()
            )
            report.update(
                scope="Exact frozen configuration; exploratory unless prospectively justified",
                analysis_settings=settings.model_dump(mode="json"),
            )
            if args.analysis_settings:
                shutil.copyfile(args.analysis_settings, source / "analysis-settings.json")
            available = dollar_cap - previous.get("reserved_usd", 0)
            sweep = run_sweep(
                cfg,
                root,
                sweep_id=identity,
                replicates=args.replicates,
                assignment_seed=args.assignment_seed,
                allow_paid=True,
                max_total_cost_usd=available,
                backend_factory=lambda config, market, firm: Managed(config.agent, firm),
                resume_from=args.resume_sweep,
            )
            report["sweep_manifest"] = sweep.relative_to(root).as_posix()
            sweep_status = json.loads(sweep.read_text(encoding="utf8"))["status"]
            if sweep_status != "interrupted":
                summary = analyze_sweep(
                    sweep, root, root / "analysis" / identity, settings=settings
                )
                report["analysis_summary"] = summary.relative_to(root).as_posix()
            report["status"] = sweep_status
            return 0 if sweep_status == "complete" else 1
        if args.pricing_probe:
            from auditors_paradox.evaluation.validate import parsed_price

            replay_source, messages = pricing_replay(
                records, root, cfg.agent.prompt_profile, cfg.agent.model
            )
            client = Managed(cfg.agent)
            try:
                answer = client.complete(messages)
                price = parsed_price(answer.text, 100, 250)
                report["stages"].append(
                    {
                        "isolated_pricing_replay_from": replay_source,
                        "response": answer.text,
                        "parsed_price": price,
                    }
                )
                if price is None:
                    raise ValueError("Pricing replay returned no valid price")
                report["status"] = "pricing_probe_complete"
                return 0
            finally:
                client.close()
        if args.v2_battery:
            from auditors_paradox.diagnostics import competence_battery, run_battery

            if cfg.v2 is None:
                raise ValueError("V2 diagnostics require an explicit V2 configuration")
            path = run_battery(
                cfg,
                root,
                source / "competence",
                execute=True,
                allow_paid=True,
                backend_factory=lambda config, market, firm: Managed(config.agent, firm),
                cases=competence_battery(cfg)[: args.diagnostic_limit],
            )
            report["stages"].append({"competence_manifest": path.relative_to(root).as_posix()})
            report["status"] = "complete"
            return 0
        if args.diagnostics:
            from research_diagnostics import probe_cases

            from auditors_paradox.evaluation.validate import parsed_price

            cases = [
                {**case, "limit": min(2048, cfg.agent.max_output_tokens)}
                for case in probe_cases(root, cfg, include_format=False)
            ]
            atomic_json(source / "diagnostic-cases.json", cases)
            for case in cases:
                print("Diagnostic: " + case["id"], flush=True)
                client = Managed(cfg.agent.model_copy(update={"max_output_tokens": case["limit"]}))
                try:
                    decision = call_diagnostic(client, case["messages"])
                    price = parsed_price(decision.text, 100, 250)
                    report["stages"].append(
                        {
                            "case": case["id"],
                            "kind": case["kind"],
                            "expected_price": case.get("expected_price"),
                            "price": price,
                            "correct": (
                                price == case.get("expected_price")
                                if "expected_price" in case
                                else None
                            ),
                            "response": decision.text,
                        }
                    )
                    save()
                finally:
                    client.close()
            report["status"] = "complete"
            return 0
        if args.pilot_rounds:
            from auditors_paradox.batch import run_sweep
            from auditors_paradox.evaluation.inference import analyze_sweep

            eligible = list(range(cfg.auditor.window, args.pilot_rounds))
            if not eligible:
                raise ValueError(
                    "Pilot needs at least one decision after a complete auditor window"
                )
            schedule = sorted(random.Random(20261023).sample(eligible, min(2, len(eligible))))
            remaining = dollar_cap - budget.reserved_usd
            if remaining <= 0:
                raise ValueError("The configured cumulative research allowance is exhausted")
            data = cfg.model_dump(mode="json")
            data.update(
                rounds=args.pilot_rounds,
                analysis_last_rounds=max(1, args.pilot_rounds // 2),
                batch_arms=["silent", "private", "public", "placebo"],
                study_purpose="diagnostic",
                study_id="agentrouter-qwen-exploratory",
                protocol="docs/research/qwen-exploratory-2026-10-23.md",
            )
            data["feedback"].update(warning_source="live", schedule_rounds=schedule)
            data["budget"].update(
                max_calls=args.pilot_rounds * 18,
                max_cost_usd=remaining / (4 * args.replicates),
                max_elapsed_seconds=14400,
            )
            config = StudyConfig.model_validate(data)
            print(
                f"Starting exploratory sweep: {args.replicates} blocks, four arms, {args.pilot_rounds} rounds per arm",
                flush=True,
            )
            sweep = run_sweep(
                config,
                root,
                sweep_id=identity,
                replicates=args.replicates,
                allow_paid=True,
                max_total_cost_usd=remaining,
                backend_factory=lambda config, market, firm: Managed(config.agent, firm),
            )
            report["sweep_manifest"] = sweep.relative_to(root).as_posix()
            summary = analyze_sweep(sweep, root, root / "analysis" / identity)
            report["analysis_summary"] = summary.relative_to(root).as_posix()
            report["status"] = json.loads(sweep.read_text(encoding="utf8"))["status"]
            return 0 if report["status"] == "complete" else 1
        if args.reuse_probe:
            prior_probe = saved_connection(records, directory, cfg.agent)
            if prior_probe is None:
                raise ValueError(
                    "No saved successful connection or pricing reply exists for this route/model"
                )
            report["stages"].append({"connection_probe_reused_from": prior_probe["validation"]})
            print(
                "Reusing a saved successful reply on this route/model; beginning actual market checks",
                flush=True,
            )
        else:
            probe = Managed(cfg.agent.model_copy(update={"max_output_tokens": 256}))
            try:
                answer = probe.complete(
                    [
                        {"role": "system", "content": "Reply with OK only."},
                        {"role": "user", "content": "Connection check."},
                    ]
                )
                if answer.text.strip() != "OK":
                    raise ValueError("Connection probe did not return OK")
            finally:
                probe.close()
            report["stages"].append("connection_probe_passed")
        if args.probe_only:
            report["status"] = "probe_complete"
            return 0
        data = cfg.model_dump(mode="json")
        data.update(
            arm="silent",
            batch_arms=["silent"],
            rounds=3,
            analysis_last_rounds=3,
            study_purpose="diagnostic",
        )
        data["agent"].update(transport_attempts=3, max_action_attempts=2)
        data["feedback"]["schedule_rounds"] = []
        data["budget"]["max_cost_usd"] = 10
        smoke = StudyConfig.model_validate(data)
        manifest = None
        if args.reuse_probe:
            from auditors_paradox.evaluation.validate import validate_run

            for previous_path in sorted(directory.glob("validation-*.json"), reverse=True):
                previous_report = json.loads(previous_path.read_text(encoding="utf8"))
                previous_config = previous_report.get("config", {})
                path = previous_report.get("smoke_manifest")
                if not path or any(
                    previous_config.get("agent", {}).get(key) != getattr(cfg.agent, key)
                    for key in (
                        "model",
                        "base_url",
                        "api_protocol",
                        "max_output_tokens",
                        "reasoning_effort",
                        "prompt_profile",
                        "temperature",
                    )
                ):
                    continue
                if previous_config.get("market") != cfg.market.model_dump(mode="json"):
                    continue
                if any(
                    previous_config.get(key) != getattr(cfg, key)
                    for key in ("memory_window", "disclose_horizon", "n_agents")
                ):
                    continue
                candidate = (root / path).resolve()
                if not candidate.is_relative_to(root / "runs") or not candidate.is_file():
                    continue
                if validate_run(candidate)["valid"]:
                    manifest = candidate
                    report["stages"].append({"completed_smoke_reused_from": previous_report["id"]})
                    break
        if manifest is None:
            manifest = run_experiment(
                smoke,
                root,
                run_id=identity,
                allow_paid=True,
                backend_factory=lambda config, market, firm: Managed(config.agent, firm),
            )
        report["smoke_manifest"] = manifest.relative_to(root).as_posix()
        report["stages"].append("nine_smoke_decisions_passed")
        for condition in ("private", "public"):
            for firm in range(3):
                client = Managed(
                    cfg.agent.model_copy(
                        update={"max_output_tokens": min(512, cfg.agent.max_output_tokens)}
                    )
                )
                try:
                    messages = [
                        {
                            "role": "system",
                            "content": f"You control firm {firm}. Reply PRICE: 137.",
                        },
                        {
                            "role": "user",
                            "content": f"Frozen observation: all prices 137. {condition} delivery diagnostic. Your private marker: firm-{firm}.",
                        },
                    ]
                    previous_case = (
                        saved_connection(
                            records,
                            directory,
                            cfg.agent,
                            exact_messages=messages,
                            client_identity=client.backend.identity,
                        )
                        if args.reuse_probe
                        else None
                    )
                    text = (
                        previous_case["response"]
                        if previous_case
                        else call_diagnostic(client, messages).text
                    )
                    if not re.fullmatch(r"PRICE:\s*137", text.strip()):
                        raise ValueError(
                            "Frozen-observation check did not return the requested PRICE: 137"
                        )
                    report["stages"].append(
                        {
                            "isolation_case": f"{condition}-firm-{firm}",
                            "status": "reused" if previous_case else "passed",
                            "source": previous_case["validation"] if previous_case else identity,
                        }
                    )
                    save()
                finally:
                    client.close()
        report["stages"].append("six_frozen_observation_isolation_decisions_passed")
        report["status"] = "complete"
    except BaseException as exc:
        report.update(status="failed", error=type(exc).__name__, message=str(exc))
        raise
    finally:
        save()
        print("Validation evidence: " + target.relative_to(root).as_posix())
        print(
            f"Allowance used: {budget.calls}/{call_cap} reserved attempts; ${budget.reserved_usd:.4f}/${dollar_cap} accounted/held; optional caps {'enabled' if cfg.budget.enforce_spending_caps else 'disabled'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
