"""This is the main loop, we run the rounds, apply the treatment rules and save the evidence."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed, wait
from dataclasses import asdict
from pathlib import Path
from typing import Any

from auditors_paradox.action_parsing import parse_action, parser_identity
from auditors_paradox.adapters.collusionlab import (
    Market,
    build_agent,
    system_prompt,
    verify_upstream,
)
from auditors_paradox.adapters.model_calls import (
    Backend,
    Budget,
    ProviderBackend,
    RecordedClient,
    RunStopped,
    ScriptedBackend,
)
from auditors_paradox.auditing import BehavioralAuditor
from auditors_paradox.config import StudyConfig, project_root, safe_identifier
from auditors_paradox.contracts import Delivery, fingerprint
from auditors_paradox.evidence import (
    EvidenceWriter,
    atomic_json,
    file_hash,
    source_revision,
    source_snapshot,
)
from auditors_paradox.feedback.composer import feedback_paths, load_feedback
from auditors_paradox.feedback.router import route
from auditors_paradox.feedback.schedules import schedule_hash
from auditors_paradox.interventions import ManualController
from auditors_paradox.observations import memory_record, private_note
from auditors_paradox.pricing import PRICE_ONLY_PROFILES, pricing_templates
from auditors_paradox.sanctions import assess_sanctions
from auditors_paradox.strategies import algorithm_selection

BackendFactory = Callable[[StudyConfig, Market, int], Backend]


class RunFailed(RuntimeError):
    """Raised when a run fails, it keeps the saved manifest path for the caller."""

    def __init__(self, manifest: Path, cause: BaseException) -> None:
        """Attach the saved manifest path and a clean description of the cause."""
        super().__init__(f"Run failed ({type(cause).__name__}); evidence: {manifest}")
        self.manifest = manifest


class ActionParseError(RuntimeError):
    """Reject an undecidable round instead of inventing a market price."""


def default_backend(cfg: StudyConfig, market: Market, agent_id: int) -> Backend:
    """Build a separate model client, or scripted reply queue, for each firm."""
    if cfg.agent.client == "qwen_code":
        from auditors_paradox.adapters.qwen_code import QwenCodeBackend

        return QwenCodeBackend(
            cfg.agent, project_root(), credential_env=cfg.agent.credential_env(agent_id)
        )
    if cfg.agent.backend == "scripted":
        if cfg.agent.scripted_replies is not None:
            replies = list(cfg.agent.scripted_replies[agent_id])
        else:
            price = (
                market.benchmarks.nash_price
                if cfg.agent.scripted_policy == "competitive"
                else market.benchmarks.joint_price
            )
            replies = [f"PRICE: {price}"] * cfg.rounds
        return ScriptedBackend(replies)
    return ProviderBackend(cfg.agent, price_bounds=(cfg.market.price_min, cfg.market.price_max))


def preflight(cfg: StudyConfig, root: Path, *, allow_paid: bool = False) -> tuple[Market, str]:
    """Before anything runs, check permission, budget, protocol, source pin and economics."""
    if cfg.agent.backend not in {"scripted", "ollama"} and not allow_paid:
        raise ValueError("Live execution requires an explicit --allow-paid invocation")
    if cfg.budget.max_calls < cfg.rounds * 3:
        raise ValueError("The call budget cannot cover even one decision per agent per round")
    if (root / cfg.budget.stop_file).exists():
        raise ValueError("Operator stop file is present; remove it before starting a new run")
    protocol = (root / cfg.protocol).resolve()
    if not protocol.is_relative_to((root / "docs/research").resolve()) or not protocol.is_file():
        raise ValueError("Protocol must name an existing file inside docs/research")
    if cfg.protocol_sha256 and file_hash(protocol) != cfg.protocol_sha256:
        raise ValueError("Frozen protocol hash does not match the current protocol bytes")
    revision = verify_upstream(root)
    for name in (cfg.agent.system_prompt_path, cfg.agent.action_prompt_path):
        if name:
            path = (root / name).resolve()
            if not path.is_relative_to((root / "prompts/pricing").resolve()) or not path.is_file():
                raise ValueError(
                    "Custom pricing template must exist inside project prompts/pricing"
                )
    if cfg.agent.system_prompt_path or cfg.agent.action_prompt_path or cfg.agent.prompt_variables:
        system, action = pricing_templates(
            cfg, lambda path: (root / path).read_text(encoding="utf8")
        )
        system_prompt(cfg, system, 0)
        action.format(
            round_number=1,
            n_rounds=cfg.rounds,
            window_size=cfg.memory_window,
            memory_context="(preview)",
            recent_messages="(none)",
            action_space_description="integer price",
            strategic_guidance="",
        )
    return Market.build(cfg), revision


def preview_initial_prompts(cfg: StudyConfig, root: Path) -> list[list[dict[str, str]]]:
    """Assemble first-decision prompts through the real agent with no model or market step."""
    verify_upstream(root)
    market = Market.build(cfg)
    observation = market.reset(cfg.seed)
    system, action = pricing_templates(cfg, lambda path: (root / path).read_text(encoding="utf8"))
    snapshots: list[list[dict[str, str]]] = []

    class PreviewClient:
        def generate(self, messages: list[dict[str, str]]) -> str:
            snapshots.append([dict(message) for message in messages])
            return (json.dumps({"price": market.benchmarks.nash_price})
                        if cfg.agent.action_parser == "json_price_v1"
                        else f"PRICE: {market.benchmarks.nash_price}")

    for firm in range(3):
        agent = build_agent(cfg, market, PreviewClient(), firm, system, action)
        agent.memory.update(memory_record(0, firm, observation, cfg=cfg))
        agent.decide_action(observation, [])
    return snapshots


def run_experiment(
    cfg: StudyConfig,
    root: Path,
    *,
    run_id: str | None = None,
    replicate_id: str = "r000",
    assignment_block: str = "standalone",
    allow_paid: bool = False,
    backend_factory: BackendFactory | None = None,
    output_root: Path | None = None,
    resume_from: Path | None = None,
) -> Path:
    """Run one fresh market and save the evidence whether it completes or fails.

    Reused run folders are rejected and failed attempts are kept, so a retry needs a new ID.
    """
    root = root.resolve()
    checkpoint = None
    saved_events: list[dict[str, Any]] = []
    if resume_from is not None:
        from auditors_paradox.continuation import read_checkpoint

        checkpoint, saved_events = read_checkpoint(resume_from, cfg, root)
    market, revision = preflight(cfg, root, allow_paid=allow_paid)
    identity = safe_identifier(run_id or f"run-{uuid.uuid4().hex[:12]}")
    base = (output_root or root / "runs" / cfg.run_kind).resolve()
    directory = base / cfg.study_id / identity
    if not directory.resolve().is_relative_to(root):
        raise ValueError("Run output must stay inside this project")
    protocol_hash = file_hash(root / cfg.protocol)
    resolved = cfg.model_dump(mode="json")
    system, action = pricing_templates(cfg, lambda path: (root / path).read_text(encoding="utf8"))
    texts = load_feedback(root, cfg)
    prompt_hashes = {
        p.relative_to(root).as_posix(): file_hash(p)
        for p in sorted((root / "prompts").rglob("*.txt"))
    }
    comparable = cfg.model_dump(mode="json", exclude={"arm", "seed", "run_kind", "study_id"})
    observation_only_prompt = cfg.agent.prompt_profile == "price_only" and bool(cfg.agent.system_prompt_path)
    writer = EvidenceWriter(
        directory,
        {
            "run_id": identity,
            "study_id": cfg.study_id,
            "run_kind": cfg.run_kind,
            "arm": cfg.arm,
            "replicate_id": replicate_id,
            "assignment_block": assignment_block,
            "config": resolved,
            "config_sha256": fingerprint(resolved),
            "protocol_sha256": protocol_hash,
            "comparison_key": fingerprint(
                {
                    "config": comparable,
                    "prompts": prompt_hashes,
                    "protocol": protocol_hash,
                    "upstream": revision,
                }
            ),
            "prompt_hashes": prompt_hashes,
            "upstream_commit": revision,
            "pricing_templates": {"system": system, "action": action},
            "feedback_texts": texts,
            **(
                {
                    "feedback_template_paths": feedback_paths(cfg),
                    "researcher_intervened": False,
                    "study_schema_version": 2,
                    "sanction_disclosure": cfg.v2.sanction_disclosure,
                    "assembled_system_prompt_sha256": {
                        str(i): hashlib.sha256(
                            system_prompt(cfg, system, i).encode("utf8")
                        ).hexdigest()
                        for i in range(3)
                    },
                }
                if cfg.v2
                else {}
            ),
            "schedule_sha256": schedule_hash(cfg.feedback.schedule_rounds),
            "benchmarks": asdict(market.benchmarks),
            "data_class": (
                "scripted_engineering_fixture" if cfg.agent.backend == "scripted" else "live_model"
            ),
            "replicate_seed": cfg.seed,
            "enforcement": (
                "positive_profit_fraction"
                if cfg.arm in {"public_fine", "private_fine", "fine_only"}
                else "disabled"
            ),
            "notice_retention": "one_decision",
            "memory_policy": (
                "authorized_outcomes_v2"
                if cfg.v2
                else (
                    ("price_only_observations_v1" if observation_only_prompt else "price_only_canonical_v1")
                    if cfg.agent.prompt_profile == "price_only"
                    else "private_returned_text_v1"
                )
            ),
            "parse_failure_policy": cfg.agent.parse_failure_policy,
            "source_revision": source_revision(root),
            "algorithms": algorithm_selection(cfg),
        },
    )
    budget = Budget(cfg.budget, cfg.agent, root=root)
    controller: ManualController | None = None
    if checkpoint:
        budget.started -= checkpoint.get("active_elapsed_seconds", 0)

    def finish(status: str, error: dict[str, str] | None = None) -> Path:
        """Keep cumulative active time across continuations without counting downtime as model work."""
        writer.manifest["active_elapsed_seconds"] = time.monotonic() - budget.started
        if controller is not None:
            controller.close(status)
        manifest = writer.finish(status, error)
        if cfg.v2 and cfg.v2.index_enabled:
            try:
                from auditors_paradox.research_index import ResearchIndex

                with ResearchIndex(root, root / cfg.v2.index_path) as index:
                    index.ingest_run(manifest)
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as index_error:
                atomic_json(
                    directory / "control/index-warning.json",
                    {"status": "degraded", "error_type": type(index_error).__name__},
                )
        return manifest

    clients: list[RecordedClient] = []
    pool: ThreadPoolExecutor | None = None
    try:
        atomic_json(directory / "resolved-config.json", resolved)
        writer.manifest["source_tree_sha256"] = source_snapshot(root, directory / "source.zip")
        from auditors_paradox.prompt_trials import trial_metadata
        writer.manifest.update(trial_metadata(cfg.model_dump(mode="json"), assignment_block))
        if cfg.v2:
            controller = ManualController(cfg, writer, texts)
        if checkpoint is not None:
            assert resume_from is not None
            shutil.copyfile(resume_from, directory / "parent-manifest.json")
            if writer.manifest["source_tree_sha256"] != checkpoint["source_tree_sha256"]:
                raise ValueError(
                    "Study source changed since failure; continuation is blocked before model requests"
                )
            writer.manifest["continuation"] = {
                "root_manifest": checkpoint.get("continuation", {}).get(
                    "root_manifest",
                    checkpoint.get("recovery", {}).get(
                        "root_manifest", resume_from.relative_to(root).as_posix()
                    ),
                ),
                "root_run_id": checkpoint.get("continuation", {}).get(
                    "root_run_id",
                    checkpoint.get("recovery", {}).get("root_run_id", checkpoint["run_id"]),
                ),
                "parent_manifest": resume_from.relative_to(root).as_posix(),
                "parent_manifest_sha256": file_hash(resume_from),
                "parent_event_count": len(saved_events),
                "parent_completed_rounds": checkpoint["completed_rounds"],
                "parent_ended_at": checkpoint["ended_at"],
                "new_started_at": writer.manifest["started_at"],
            }
            for saved in saved_events:
                if writer.append(saved["type"], saved["payload"]) != saved["record_hash"]:
                    raise ValueError("Continuation prefix differs from its sealed source")
            budget.calls = checkpoint["model_calls"]
            budget.reserved_usd = checkpoint["reserved_cost_usd"]
            budget.observed_usd = checkpoint["observed_cost_estimate_usd"]
            budget.usage_incomplete = checkpoint.get("usage_incomplete", False)
            if cfg.v2:
                writer.manifest["researcher_intervened"] = checkpoint.get(
                    "researcher_intervened", False
                )
        factory = backend_factory or default_backend
        for i in range(3):
            clients.append(
                RecordedClient(factory(cfg, market, i), cfg.agent.model, i, writer, budget)
            )
        agents = [build_agent(cfg, market, clients[i], i, system, action) for i in range(3)]
        if cfg.agent.decision_workers == 3:
            pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="pricing-firm")
        observation = market.reset(cfg.seed)
        for i, agent in enumerate(agents):
            agent.memory.update(memory_record(0, i, observation, cfg=cfg))
        auditor = BehavioralAuditor(cfg.auditor, market.benchmarks, v2=bool(cfg.v2))
        inbox: tuple[Delivery, ...] = ()
        saved_decisions = {
            (e["payload"]["round"], e["payload"]["agent_id"]): e["payload"]
            for e in saved_events
            if e["type"] == "decision"
        }
        saved_calls = {
            e["payload"]["call_id"]: e["payload"]
            for e in saved_events
            if e["type"] == "model_call_completed"
        }
        restored_rounds = checkpoint["completed_rounds"] if checkpoint else 0
        for round_number in range(1, cfg.rounds + 1):
            if round_number <= restored_rounds:
                prices = [saved_decisions[round_number, i]["price"] for i in range(3)]
                rewards, observation, _ = market.step(prices)
                event = auditor.evaluate(round_number, prices, rewards)
                if cfg.v2:
                    assessed = assess_sanctions(cfg, event, rewards)
                    observation = {
                        **observation,
                        "gross_profits": rewards,
                        "profits": [a.net_profit for a in assessed],
                        "penalties": [a.deduction for a in assessed],
                    }
                inbox = tuple(
                    d for d in route(cfg, event, texts) if d.status == "scheduled_for_next_round"
                )
                if cfg.v2:
                    inbox = tuple(
                        Delivery(**{k: v for k, v in e["payload"].items() if k != "text"})
                        for e in saved_events
                        if e["type"] == "delivery"
                        and e["payload"]["source_round"] == round_number
                        and e["payload"]["status"] == "scheduled_for_next_round"
                    )
                    if (
                        checkpoint
                        and checkpoint.get("pending_boundary", {}).get("source_round")
                        == round_number
                    ):
                        assert controller is not None
                        recovered = tuple(
                            Delivery(**{k: v for k, v in d.items() if k != "text"})
                            for d in checkpoint["pending_boundary"]["deliveries"]
                        )
                        writer.append(
                            "boundary_recovery",
                            {
                                "source_round": round_number,
                                "status": "accepted_inbox_restored_operator_resume_required",
                            },
                            durable=True,
                        )
                        recovered = controller.boundary(
                            round_number, recovered, budget.check_stop, initially_paused=True
                        )
                        for delivery in recovered:
                            writer.append("delivery", delivery.to_dict())
                        inbox = tuple(
                            d for d in recovered if d.status == "scheduled_for_next_round"
                        )
                for i, agent in enumerate(agents):
                    decision = saved_decisions[round_number, i]
                    returned = saved_calls[decision["call_ids"][-1]]["response"]
                    if cfg.agent.prompt_profile in PRICE_ONLY_PROFILES:
                        returned = f"PRICE: {prices[i]}"
                    agent.memory.update(
                        memory_record(round_number, i, observation, returned, cfg=cfg)
                    )
                continue
            if controller is not None:
                controller.collecting(round_number)
            # Freeze what every agent sees before anyone picks a price this round.
            current = {
                i: [d for d in inbox if d.recipient == i and d.effective_round == round_number]
                for i in range(3)
            }
            frozen = [agent.memory.to_prompt_context() for agent in agents]
            for i, client in enumerate(clients):
                client.begin_round(round_number, [d.delivery_id for d in current[i]])
                if checkpoint and round_number == restored_rounds + 1:
                    client.round_attempt_ids = [
                        e["payload"]["call_id"]
                        for e in saved_events
                        if e["type"] == "model_call_started"
                        and e["payload"]["round"] == round_number
                        and e["payload"]["agent_id"] == i
                    ]
                    prior_observations = [
                        e["payload"]
                        for e in saved_events
                        if e["type"] == "observation"
                        and e["payload"]["round"] == round_number
                        and e["payload"]["agent_id"] == i
                    ]
                    if prior_observations:
                        if prior_observations[0]["memory_context"] != frozen[
                            i
                        ] or prior_observations[0]["notice_texts"] != [d.text for d in current[i]]:
                            raise ValueError(
                                "Restored observation differs from the frozen original"
                            )
                        continue
                writer.append(
                    "observation",
                    {
                        "round": round_number,
                        "agent_id": i,
                        "memory_context": frozen[i],
                        "delivery_ids": client.delivery_ids,
                        "notice_texts": [d.text for d in current[i]],
                    },
                )
            actions: list[int] = []
            decision_started = time.perf_counter()

            def decide(i: int) -> int:
                """Choose one firm's price using only its frozen history and current inbox."""
                budget.check_stop()
                agent = agents[i]
                if (round_number, i) in saved_decisions:
                    saved = saved_decisions[round_number, i]
                    clients[i].round_call_ids = saved["call_ids"]
                    agent.last_pricing_reasoning = saved_calls[saved["call_ids"][-1]]["response"]
                    return int(saved["price"])
                price = int(
                    agent.decide_action({"round": round_number - 1}, [d.text for d in current[i]])
                )
                fallback = bool(
                    agent.fallback_events and agent.fallback_events[-1]["round"] == round_number
                )
                if fallback and cfg.agent.parse_failure_policy == "abort":
                    writer.append(
                        "decision_rejected",
                        {
                            "round": round_number,
                            "agent_id": i,
                            "call_ids": clients[i].round_call_ids,
                            "reason": "parsing_attempts_exhausted_no_action_executed",
                            "parsing": {
                                "strategy": parser_identity(
                                    cfg.agent.action_parser, cfg.agent.model
                                ),
                                "method": parse_action(
                                    agent.last_pricing_reasoning or "",
                                    cfg.market.price_min,
                                    cfg.market.price_max,
                                    parser_identity(cfg.agent.action_parser, cfg.agent.model),
                                ).method,
                            },
                        },
                    )
                    raise ActionParseError(
                        "Parsing attempts exhausted; the market round was not executed"
                    )
                decision: dict[str, Any] = {
                    "round": round_number,
                    "agent_id": i,
                    "price": price,
                    "call_ids": clients[i].round_call_ids,
                    "fallback": fallback,
                }
                if cfg.agent.action_parser != "legacy":
                    strategy = parser_identity(cfg.agent.action_parser, cfg.agent.model)
                    result = parse_action(
                        agent.last_pricing_reasoning or "",
                        cfg.market.price_min,
                        cfg.market.price_max,
                        strategy,
                    )
                    decision["parsing"] = {"strategy": strategy, **asdict(result)}
                writer.append("decision", decision)
                for delivery in current[i]:
                    writer.append(
                        "delivery_consumed",
                        {
                            "delivery_id": delivery.delivery_id,
                            "round": round_number,
                            "agent_id": i,
                            "call_id": clients[i].round_call_ids[0],
                        },
                    )
                return price

            if pool is None:
                actions = [decide(i) for i in range(3)]
            else:
                futures: dict[Future[int], int] = {}
                chosen: dict[int, int] = {}
                try:
                    for i in range(3):
                        futures[pool.submit(decide, i)] = i
                    for future in as_completed(futures):
                        chosen[futures[future]] = future.result()
                except BaseException:
                    budget.cancel()
                    for future in futures:
                        future.cancel()
                    wait(futures)
                    raise
                actions = [chosen[i] for i in range(3)]
            decisions_finished = time.perf_counter()
            rewards, next_observation, done = market.step(actions)
            event = auditor.evaluate(round_number, actions, rewards)
            assessed = assess_sanctions(cfg, event, rewards)
            penalties = [a.deduction for a in assessed]
            net_rewards = [reward - fine for reward, fine in zip(rewards, penalties, strict=True)]
            next_observation = {
                **next_observation,
                "profits": net_rewards,
                "penalties": penalties,
                "gross_profits": rewards,
            }
            assessment_finished = time.perf_counter()
            writer.append(
                "round",
                {
                    "round": round_number,
                    "prices": actions,
                    "quantities": next_observation["quantities"],
                    "gross_rewards": rewards,
                    "net_rewards": net_rewards,
                    "penalties": penalties,
                },
            )
            writer.append("audit", event.to_dict())
            if cfg.v2:
                for assessment in assessed:
                    writer.append("penalty_assessment", assessment.to_dict())
            deliveries = route(cfg, event, texts)
            if not cfg.v2:
                for delivery in deliveries:
                    writer.append("delivery", delivery.to_dict())
                inbox = tuple(d for d in deliveries if d.status == "scheduled_for_next_round")
            for i, agent in enumerate(agents):
                returned = agent.last_pricing_reasoning
                if cfg.v2 and cfg.agent.prompt_profile == "economics_private_note":
                    full_note = private_note(returned, len(returned))
                    if full_note is not None and len(full_note) > cfg.v2.note_max_chars:
                        writer.append(
                            "private_note_truncated",
                            {
                                "round": round_number,
                                "agent_id": i,
                                "original_characters": len(full_note),
                                "retained_characters": cfg.v2.note_max_chars,
                                "call_id": clients[i].round_call_ids[-1],
                            },
                        )
                if cfg.agent.prompt_profile in PRICE_ONLY_PROFILES:
                    canonical_price = f"PRICE: {actions[i]}"
                    format_valid = returned.strip() == canonical_price
                    if cfg.agent.action_parser == "json_price_v1":
                        format_valid = (
                            parse_action(
                                returned,
                                cfg.market.price_min,
                                cfg.market.price_max,
                                "json_price_v1",
                            ).price
                            == actions[i]
                        )
                    if not format_valid:
                        writer.append(
                            "price_only_format_deviation",
                            {
                                "round": round_number,
                                "agent_id": i,
                                "call_id": clients[i].round_call_ids[-1],
                            },
                        )
                    returned = None if observation_only_prompt and not cfg.v2 else canonical_price
                agent.memory.update(
                    memory_record(round_number, i, next_observation, returned, cfg=cfg)
                )
            writer.append(
                "round_committed",
                {
                    "round": round_number,
                    "decision_count": len(actions),
                    "delivery_count": len(deliveries),
                },
            )
            if cfg.v2:
                writer.append(
                    "phase_timing",
                    {
                        "round": round_number,
                        "decision_wall_seconds": decisions_finished - decision_started,
                        "market_and_assessment_seconds": assessment_finished - decisions_finished,
                        "memory_and_raw_commit_seconds": time.perf_counter() - assessment_finished,
                    },
                )
            if controller is not None:
                for delivery in deliveries:
                    writer.append("automatic_delivery", delivery.to_dict())
                boundary_started = time.perf_counter()
                deliveries = controller.boundary(round_number, deliveries, budget.check_stop)
                writer.append(
                    "phase_timing",
                    {
                        "round": round_number,
                        "boundary_seconds": time.perf_counter() - boundary_started,
                    },
                )
                for delivery in deliveries:
                    writer.append("delivery", delivery.to_dict())
                inbox = tuple(d for d in deliveries if d.status == "scheduled_for_next_round")
                if cfg.v2 and cfg.v2.index_enabled:
                    index_started = time.perf_counter()
                    try:
                        from auditors_paradox.research_index import ResearchIndex

                        with ResearchIndex(root, root / cfg.v2.index_path) as index:
                            index.ingest_run(directory / "manifest.json")
                        writer.append(
                            "phase_timing",
                            {
                                "round": round_number,
                                "index_seconds": time.perf_counter() - index_started,
                            },
                        )
                    except (OSError, ValueError, RuntimeError, sqlite3.Error) as index_error:
                        atomic_json(
                            directory / "control/index-warning.json",
                            {"status": "degraded", "error_type": type(index_error).__name__},
                        )
            if done != (round_number == cfg.rounds):
                raise RuntimeError("Market completion disagrees with the configured horizon")
        writer.manifest.update(
            {
                "model_calls": budget.calls,
                "reserved_cost_usd": budget.reserved_usd,
                "observed_cost_estimate_usd": budget.observed_usd,
                "usage_incomplete": budget.usage_incomplete,
                "fallback_count": sum(len(agent.fallback_events) for agent in agents),
                "actual_run_rounds": cfg.rounds,
            }
        )
        # Check the recorded events before we mark the run as complete.
        from auditors_paradox.evaluation.validate import check_events

        records = [
            json.loads(line)
            for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        errors = check_events(records, writer.manifest)
        if errors:
            writer.manifest["validation_errors"] = errors
            raise RuntimeError("Run fidelity validation failed: " + "; ".join(errors))
        return finish("complete")
    except (KeyboardInterrupt, SystemExit) as exc:
        finish("interrupted", {"type": type(exc).__name__, "message": "Execution interrupted"})
        raise
    except Exception as exc:
        writer.manifest.update(
            {
                "model_calls": budget.calls,
                "reserved_cost_usd": budget.reserved_usd,
                "observed_cost_estimate_usd": budget.observed_usd,
                "usage_incomplete": budget.usage_incomplete,
            }
        )
        manifest = finish(
            "stopped" if isinstance(exc, RunStopped) else "failed",
            {"type": type(exc).__name__, "message": "Execution stopped; inspect structured events"},
        )
        raise RunFailed(manifest, exc) from exc
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
        for client in clients:
            client.close()
