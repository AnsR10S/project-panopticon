"""Replay V2 economics, interventions and exactly what each firm could observe."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from auditors_paradox.action_parsing import correction_reason, parse_action, parser_identity
from auditors_paradox.adapters.collusionlab import Benchmarks, system_prompt
from auditors_paradox.auditing import BehavioralAuditor
from auditors_paradox.config import StudyConfig
from auditors_paradox.contracts import fingerprint
from auditors_paradox.economics import quantities, rewards
from auditors_paradox.feedback.router import route
from auditors_paradox.interventions import InterventionDraft, resolve_draft
from auditors_paradox.observations import ResearchMemory, memory_record
from auditors_paradox.sanctions import assess_sanctions


def equivalent(actual: Any, expected: Any) -> bool:
    """Allow declared floating arithmetic error while requiring exact categorical evidence."""
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            equivalent(actual[k], expected[k]) for k in actual
        )
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            equivalent(a, b) for a, b in zip(actual, expected, strict=True)
        )
    if type(actual) is float or type(expected) is float:
        return (
            type(actual) in {int, float}
            and type(expected) in {int, float}
            and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-10)
        )
    return type(actual) is type(expected) and bool(actual == expected)


def correction_text(response: str, cfg: StudyConfig) -> str:
    """Reproduce the pinned parser's correction without admitting extra context."""
    cleaned = response.strip().strip(".,;:!?\"' \t\n")
    tagged = re.findall(r"(?im)^\s*price\s*:\s*(-?\d+)\s*$", cleaned)
    value = int(tagged[-1]) if tagged else int(cleaned) if re.fullmatch(r"-?\d+", cleaned) else None
    strategy = parser_identity(cfg.agent.action_parser, cfg.agent.model)
    if strategy != "legacy":
        reason = correction_reason(
            parse_action(response, cfg.market.price_min, cfg.market.price_max, strategy),
            cfg.market.price_min,
            cfg.market.price_max,
        )
    elif not cleaned:
        reason = "empty action"
    elif value is not None:
        reason = f"price {value} out of grid [{cfg.market.price_min}, {cfg.market.price_max}]"
    else:
        reason = f"could not parse action; expected final line 'PRICE: <int>' with valid range {cfg.market.price_min}..{cfg.market.price_max}"
    return f"Your previous response could not be parsed: {reason}. Please respond with only integer price between {cfg.market.price_min} and {cfg.market.price_max} inclusive."


def check_v2(
    records: list[dict[str, Any]],
    manifest: dict[str, Any],
    *,
    completed_rounds: int | None = None,
    allow_unfrozen_boundary: bool = False,
) -> list[str]:
    """Validate a complete V2 stream with no hidden manual edits or extra decisions."""
    from auditors_paradox.evaluation.validate import parsed_price

    errors: list[str] = []
    cfg = StudyConfig.model_validate(manifest["config"])
    parsing_strategy = parser_identity(cfg.agent.action_parser, cfg.agent.model)
    assert cfg.v2 is not None
    if (
        "sanction_disclosure" in manifest
        and manifest["sanction_disclosure"] != cfg.v2.sanction_disclosure
    ):
        errors.append("Sanction disclosure differs from the frozen configuration")
    if "assembled_system_prompt_sha256" in manifest:
        import hashlib

        expected_hashes = {
            str(i): hashlib.sha256(
                system_prompt(cfg, manifest["pricing_templates"]["system"], i).encode("utf8")
            ).hexdigest()
            for i in range(3)
        }
        if manifest["assembled_system_prompt_sha256"] != expected_hashes:
            errors.append("Assembled system prompt hashes differ from the frozen disclosure")
    horizon = cfg.rounds if completed_rounds is None else completed_rounds
    previous = "0" * 64
    grouped: dict[str, list[dict[str, Any]]] = {}
    for sequence, record in enumerate(records, 1):
        if (
            record["sequence"] != sequence
            or record["previous_hash"] != previous
            or fingerprint({k: v for k, v in record.items() if k != "record_hash"})
            != record["record_hash"]
        ):
            errors.append("Broken V2 event chain")
        previous = record["record_hash"]
        grouped.setdefault(record["type"], []).append(record)

    def values(kind: str) -> list[dict[str, Any]]:
        return [r["payload"] for r in grouped.get(kind, [])]

    accepted_interventions = [
        c["intervention"] for c in values("manual_control") if c.get("status") == "applied"
    ]
    if any(item not in accepted_interventions for item in values("manual_intervention")):
        errors.append("Manual projection has no authoritative acceptance")
    if (
        not manifest.get("recovery")
        and not manifest.get("continuation")
        and values("manual_intervention") != accepted_interventions
    ):
        errors.append("Missing manual acceptance projection outside a recovered attempt")
    outcomes = values("round")
    if [r["round"] for r in outcomes] != list(range(1, horizon + 1)):
        errors.append("Completed round sequence does not match the configured horizon")
        return errors
    if [r["round"] for r in values("round_committed")] != list(range(1, horizon + 1)):
        errors.append("Missing or duplicate V2 commits")
    expected_keys = Counter((r, i) for r in range(1, horizon + 1) for i in range(3))
    for kind in ("observation", "decision", "penalty_assessment"):
        if Counter((r["round"], r["agent_id"]) for r in values(kind)) != expected_keys:
            errors.append(f"Missing or duplicate V2 {kind}")
    calls = {c["call_id"]: c for c in values("model_call_completed")}
    starts = values("model_call_started")
    failed = values("model_call_failed")
    ids = [c["call_id"] for c in starts]
    failed_ids = [c["call_id"] for c in failed]
    start_map = {c["call_id"]: c for c in starts}
    for call_id, completed_call in calls.items():
        original = start_map.get(call_id, {})
        if any(
            completed_call.get(key) != original.get(key)
            for key in (
                "round",
                "agent_id",
                "messages",
                "delivery_ids",
                "requested_model",
                "transport_attempt",
            )
        ):
            errors.append("V2 successful response differs from its recorded request")
    if (
        len(ids) != len(set(ids))
        or len(calls) != len(values("model_call_completed"))
        or len(failed_ids) != len(set(failed_ids))
        or set(calls) & set(failed_ids)
        or set(ids) != set(calls) | set(failed_ids)
    ):
        errors.append("Unfinished or duplicate V2 requests")
    decisions = {(d["round"], d["agent_id"]): d for d in values("decision")}
    observations = {(d["round"], d["agent_id"]): d for d in values("observation")}
    audits = {d["source_round"]: d for d in values("audit")}
    penalties = {(d["round"], d["agent_id"]): d for d in values("penalty_assessment")}
    if len(audits) != horizon or len(values("audit")) != horizon:
        errors.append("Missing or duplicate V2 audit")
    benchmarks = Benchmarks(**manifest["benchmarks"])
    auditor = BehavioralAuditor(cfg.auditor, benchmarks, v2=True)
    memories = [ResearchMemory(cfg) for _ in range(3)]
    initial = [benchmarks.nash_price] * 3
    first = {
        "prices": initial,
        "quantities": quantities(cfg.market, initial),
        "profits": rewards(cfg.market, initial),
    }
    for i in range(3):
        memories[i].update(memory_record(0, i, first, cfg=cfg))
    inbox: list[dict[str, Any]] = []
    consumed: set[str] = set()

    def sequences(kind: str, r: int, field: str = "round") -> list[int]:
        return [e["sequence"] for e in grouped.get(kind, []) if e["payload"].get(field) == r]

    for outcome in outcomes:
        r = outcome["round"]
        prices = outcome["prices"]
        if len(prices) != 3 or any(
            type(p) is not int or not cfg.market.price_min <= p <= cfg.market.price_max
            for p in prices
        ):
            errors.append("Invalid V2 simultaneous prices")
            continue
        gross = rewards(cfg.market, prices)
        qs = quantities(cfg.market, prices)
        audit = auditor.evaluate(r, prices, gross)
        assessed = assess_sanctions(cfg, audit, gross)
        net = [a.net_profit for a in assessed]
        deductions = [a.deduction for a in assessed]
        if any(
            abs(a - b) > 1e-9
            for name, expected in (
                ("gross_rewards", gross),
                ("net_rewards", net),
                ("quantities", qs),
                ("penalties", deductions),
            )
            for a, b in zip(outcome[name], expected, strict=True)
        ):
            errors.append("V2 accounting disagrees with replayed economics")
        if not equivalent(audits.get(r), audit.to_dict()) or any(
            not equivalent(penalties.get((r, i)), a.to_dict()) for i, a in enumerate(assessed)
        ):
            errors.append("V2 qualification, severity or sanction policy differs from replay")
        obs_seq = sequences("observation", r)
        req_seq = sequences("model_call_started", r)
        decision_seq = sequences("decision", r)
        market_seq = sequences("round", r)
        commits = sequences("round_committed", r)
        audit_seq = sequences("audit", r, "source_round")
        penalty_seq = sequences("penalty_assessment", r)
        boundary_seq = sequences("boundary_frozen", r, "source_round")
        automatic_seq = sequences("automatic_delivery", r, "source_round")
        delivery_seq = sequences("delivery", r, "source_round")
        pending = allow_unfrozen_boundary and r == horizon
        if (
            len(audit_seq) != 1
            or len(penalty_seq) != 3
            or len(automatic_seq) != 3
            or len(commits) != 1
            or not market_seq[0]
            < audit_seq[0]
            < min(penalty_seq)
            <= max(penalty_seq)
            < commits[0]
            < min(automatic_seq)
            or (
                not pending
                and (
                    len(boundary_seq) != 1
                    or max(automatic_seq) >= boundary_seq[0]
                    or (delivery_seq and min(delivery_seq) <= boundary_seq[0])
                )
            )
            or (pending and (boundary_seq or delivery_seq))
        ):
            errors.append("V2 assessment, commitment or boundary ordering violated")
        if (
            len(obs_seq) != 3
            or not req_seq
            or max(obs_seq) >= min(req_seq)
            or len(decision_seq) != 3
            or len(commits) != 1
            or not max(decision_seq) < market_seq[0] < commits[0]
        ):
            errors.append("V2 simultaneous observation/decision barrier violated")
        if r > 1 and (
            not obs_seq
            or min(obs_seq)
            <= max(sequences("boundary_frozen", r - 1, "source_round"), default=10**12)
        ):
            errors.append("Next prompts precede the frozen boundary")
        for i in range(3):
            obs = observations.get((r, i), {})
            decision = decisions.get((r, i), {})
            eligible = [
                d
                for d in inbox
                if d["recipient"] == i and d["status"] == "scheduled_for_next_round"
            ]
            if (
                obs.get("memory_context") != memories[i].to_prompt_context()
                or obs.get("delivery_ids") != [d["delivery_id"] for d in eligible]
                or obs.get("notice_texts") != [d["text"] for d in eligible]
            ):
                errors.append("V2 unauthorized history or notice exposure")
            consumed.update(d["delivery_id"] for d in eligible)
            template = manifest["pricing_templates"]
            action = (
                template["action"].replace(" of {n_rounds}", "")
                if not cfg.disclose_horizon
                else template["action"]
            )
            expected = [
                {"role": "system", "content": system_prompt(cfg, template["system"], i)},
                {
                    "role": "user",
                    "content": action.format(
                        round_number=r,
                        n_rounds=cfg.rounds,
                        window_size=cfg.memory_window,
                        memory_context=memories[i].to_prompt_context() or "(none)",
                        recent_messages="\n".join(f"- {d['text']}" for d in eligible) or "(none)",
                        action_space_description=f"integer price between {cfg.market.price_min} and {cfg.market.price_max} inclusive",
                        strategic_guidance="",
                    ),
                },
            ]
            if any(
                c["messages"][:2] != expected or c["delivery_ids"] != obs.get("delivery_ids")
                for c in starts
                if c["round"] == r and c["agent_id"] == i
            ):
                errors.append("V2 actual request differs from authorized prompt")
            conversation = list(expected)
            for started in [c for c in starts if c["round"] == r and c["agent_id"] == i]:
                if started["messages"] != conversation:
                    errors.append("V2 parsing correction changed the permitted conversation")
                successful_call = calls.get(started["call_id"])
                if (
                    successful_call is not None
                    and parsed_price(
                        successful_call["response"],
                        cfg.market.price_min,
                        cfg.market.price_max,
                        parsing_strategy,
                    )
                    is None
                ):
                    conversation += [
                        {"role": "assistant", "content": successful_call["response"]},
                        {
                            "role": "user",
                            "content": correction_text(successful_call["response"], cfg),
                        },
                    ]
            accepted = [calls.get(cid) for cid in decision.get("call_ids", [])]
            if (
                not accepted
                or len(accepted) > cfg.agent.max_action_attempts
                or any(c is None or c["round"] != r or c["agent_id"] != i for c in accepted)
            ):
                errors.append("V2 decision lacks matching successful calls")
                continue
            parsed = [
                parsed_price(
                    c["response"], cfg.market.price_min, cfg.market.price_max, parsing_strategy
                )
                for c in accepted
                if c
            ]
            if (
                any(x is not None for x in parsed[:-1])
                or parsed[-1] != decision.get("price")
                or decision.get("price") != prices[i]
                or decision.get("fallback")
            ):
                errors.append("V2 executed price differs from accepted response")
            last_call = accepted[-1]
            assert last_call is not None
            returned = last_call["response"]
            if cfg.agent.action_parser != "legacy":
                result = parse_action(
                    returned, cfg.market.price_min, cfg.market.price_max, parsing_strategy
                )
                if decision.get("parsing") != {
                    "strategy": parsing_strategy,
                    "price": result.price,
                    "method": result.method,
                }:
                    errors.append("V2 parser evidence differs from the frozen strategy")
            memories[i].update(
                memory_record(
                    r,
                    i,
                    {
                        "prices": prices,
                        "quantities": qs,
                        "profits": net,
                        "gross_profits": gross,
                        "penalties": deductions,
                    },
                    returned,
                    cfg=cfg,
                )
            )
        expected_deliveries = route(cfg, audit, manifest["feedback_texts"])
        automatic = [d for d in values("automatic_delivery") if d["source_round"] == r]
        if automatic != [d.to_dict() for d in expected_deliveries]:
            errors.append("V2 automatic notice policy changed")
        for intervention in [x for x in accepted_interventions if x["source_round"] == r]:
            digest = fingerprint({k: v for k, v in intervention.items() if k != "preview_hash"})
            if (
                digest != intervention["preview_hash"]
                or intervention["effective_round"] != r + 1
                or r >= cfg.rounds
                or not cfg.v2.manual_enabled
            ):
                errors.append("Invalid manual intervention identity or timing")
            expected_deliveries, changed = resolve_draft(
                expected_deliveries,
                InterventionDraft.model_validate(intervention["draft"]),
                intervention["intervention_id"],
                manifest["feedback_texts"],
            )
            if (
                intervention["deliveries"] != [d.to_dict() for d in expected_deliveries]
                or intervention["changed_automatic_ids"] != changed
                or intervention["protocol_sha256"] != manifest["protocol_sha256"]
                or intervention["source_tree_sha256"] != manifest["source_tree_sha256"]
            ):
                errors.append("Manual preview differs from applied deliveries or provenance")
            confirmations = [
                c
                for c in values("manual_control")
                if c.get("status") == "applied" and c.get("intervention") == intervention
            ]
            if len(confirmations) != 1:
                errors.append("Manual intervention lacks one durable confirmation")
            elif (
                confirmations[0].get("kind") != "confirm"
                or confirmations[0].get("preview_hash") != intervention["preview_hash"]
                or confirmations[0].get("revision") != intervention["revision"]
            ):
                errors.append("Durable acceptance does not confirm the exact preview revision")
        inbox = [d.to_dict() for d in expected_deliveries]
        actual = [d for d in values("delivery") if d["source_round"] == r]
        boundaries = [b for b in values("boundary_frozen") if b["source_round"] == r]
        if not pending and (
            actual != inbox
            or len(boundaries) != 1
            or boundaries[0]["deliveries_sha256"] != fingerprint(inbox)
        ):
            errors.append("Final delivery differs from the frozen V2 boundary")
    receipts = values("delivery_consumed")
    if Counter(call for d in values("decision") for call in d["call_ids"]) != Counter(calls.keys()):
        errors.append("V2 successful calls are not used exactly once by accepted decisions")
    if cfg.agent.client == "qwen_code" and any(
        c.get("usage_metadata", {}).get("network_requests") != 1 for c in calls.values()
    ):
        errors.append("V2 guarded request did not record exactly one transmission")
    if Counter(x["delivery_id"] for x in receipts) != Counter(consumed):
        errors.append("V2 notices not consumed exactly once")
    delivery_map = {d["delivery_id"]: d for d in values("delivery")}
    for receipt in receipts:
        delivery = delivery_map.get(receipt["delivery_id"], {})
        call = calls.get(receipt["call_id"], {})
        if (
            receipt["round"] != delivery.get("effective_round")
            or receipt["agent_id"] != delivery.get("recipient")
            or receipt["delivery_id"] not in call.get("delivery_ids", [])
        ):
            errors.append("V2 consumption has no authorized successful request")
    if bool(accepted_interventions) != bool(manifest.get("researcher_intervened")):
        errors.append("Researcher intervention label contradicts evidence")
    return sorted(set(errors))
