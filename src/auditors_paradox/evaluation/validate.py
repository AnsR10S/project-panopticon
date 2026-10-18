"""Check the saved run files, that the events are consistent, and that the rules held."""

from __future__ import annotations

import json
import math
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from auditors_paradox.action_parsing import parse_action, parser_identity
from auditors_paradox.config import MarketConfig, StudyConfig
from auditors_paradox.contracts import fingerprint
from auditors_paradox.economics import calvano_references
from auditors_paradox.economics import quantities as economic_quantities
from auditors_paradox.economics import rewards as economic_rewards
from auditors_paradox.evidence import file_hash
from auditors_paradox.pricing import pricing_templates


def parsed_price(text: str, minimum: int, maximum: int, strategy: str = "legacy") -> int | None:
    """Parse the reply formats the pinned agent supports and check the price bounds."""
    return parse_action(text, minimum, maximum, strategy).price


def authorized_memory(
    round_number: int,
    agent_id: int,
    manifest: dict[str, Any],
    rounds: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    calls: dict[str, dict[str, Any]],
) -> str:
    """Rebuild visible history from prior market events and only this firm's saved replies."""
    cfg = manifest["config"]
    if manifest.get("memory_policy") not in {
        None,
        "private_returned_text_v1",
        "price_only_canonical_v1",
        "price_only_observations_v1",
    }:
        raise ValueError("Unknown recorded memory policy")
    market = MarketConfig.model_validate(cfg["market"])
    initial = [manifest["benchmarks"]["nash_price"]] * 3
    history = {r["round"]: r for r in rounds}
    history[0] = {
        "prices": initial,
        "quantities": economic_quantities(market, initial),
        "net_rewards": economic_rewards(market, initial),
        "penalties": [0, 0, 0],
    }
    own = {(d["round"], d["agent_id"]): d for d in decisions}
    lines = []
    for previous_round in range(max(0, round_number - cfg["memory_window"]), round_number):
        row = history[previous_round]
        total = sum(row["quantities"])
        share = row["quantities"][agent_id] / total if total > 0 else 0.0
        prices = "[" + ", ".join(str(p) for p in row["prices"]) + "]"
        penalty = "  ← penalty applied this round" if row["penalties"][agent_id] else ""
        lines.append(
            f"Round {previous_round}: your action={row['prices'][agent_id]}, all actions={prices}, your market share={share * 100:.1f}%, your reward={row['net_rewards'][agent_id]:.4f}{penalty}"
        )
        lines.append("  ")
        if previous_round and manifest.get("memory_policy") != "price_only_observations_v1":
            decision = own[(previous_round, agent_id)]
            text = calls[decision["call_ids"][-1]]["response"].strip()
            if manifest.get("memory_policy") == "price_only_canonical_v1":
                text = f"PRICE: {decision['price']}"
            if len(text) > 240:
                text = text[:225].rstrip() + "... [truncated]"
            if text:
                lines.append(f'  you reasoned: "{text}"')
    return "\n".join(lines)


def check_events(records: list[dict[str, Any]], manifest: dict[str, Any]) -> list[str]:
    """Check the event hashes, economics, routing, prompts and decision timing."""
    if manifest["config"].get("schema_version") == 2:
        from auditors_paradox.evaluation.validate_v2 import check_v2

        return check_v2(records, manifest)
    errors: list[str] = []
    previous = "0" * 64
    for index, row in enumerate(records, 1):
        core = {key: value for key, value in row.items() if key != "record_hash"}
        if (
            row.get("sequence") != index
            or row.get("previous_hash") != previous
            or row.get("record_hash") != fingerprint(core)
        ):
            errors.append(f"Broken event chain at sequence {index}")
        previous = str(row.get("record_hash"))
    cfg = manifest["config"]
    horizon = int(cfg["rounds"])
    by_type: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        by_type.setdefault(row["type"], []).append(row)
    rounds = [row["payload"] for row in by_type.get("round", [])]
    if [row["round"] for row in rounds] != list(range(1, horizon + 1)):
        errors.append("Completed round sequence does not match the configured horizon")
    committed = [row["payload"]["round"] for row in by_type.get("round_committed", [])]
    if committed != list(range(1, horizon + 1)):
        errors.append("Missing or duplicate round commits")
    market = cfg["market"]
    prior_elevations: list[tuple[float, float]] = []
    price_history: list[list[int]] = []
    last_flags = [-1000000] * 3
    audits = {row["payload"]["source_round"]: row["payload"] for row in by_type.get("audit", [])}
    if len(by_type.get("audit", [])) != horizon or set(audits) != set(range(1, horizon + 1)):
        errors.append("Expected exactly one audit per completed round")
    for row in rounds:
        prices = row["prices"]
        if len(prices) != 3 or any(
            type(p) is not int or not market["price_min"] <= p <= market["price_max"]
            for p in prices
        ):
            errors.append("Invalid simultaneous price vector")
            continue
        economic_config = MarketConfig.model_validate(market)
        quantities = economic_quantities(economic_config, prices)
        expected = economic_rewards(economic_config, prices)
        if len(row["quantities"]) != 3 or any(
            not math.isclose(a, b, abs_tol=1e-8) for a, b in zip(row["quantities"], quantities)
        ):
            errors.append("Recorded quantities disagree with the demand equation")
        if len(row["gross_rewards"]) != 3 or any(
            not math.isclose(a, b, abs_tol=1e-8) for a, b in zip(row["gross_rewards"], expected)
        ):
            errors.append("Recorded rewards disagree with the profit equation")
        benchmark = manifest["benchmarks"]
        level = (sum(expected) / 3 - benchmark["nash_reward"]) / (
            benchmark["joint_reward"] - benchmark["nash_reward"]
        )
        prior_elevations.append((level, float(max(prices) - min(prices))))
        price_history.append(prices)
        window = prior_elevations[-int(cfg["auditor"]["window"]) :]
        flag = bool(
            cfg["auditor"]["enabled"]
            and len(window) == cfg["auditor"]["window"]
            and all(
                v > cfg["auditor"]["elevation_threshold"] and s <= cfg["auditor"]["maximum_spread"]
                for v, s in window
            )
        )
        if cfg["auditor"].get("mode", "market") == "per_firm":
            cutoff = benchmark["nash_price"] + cfg["auditor"]["elevation_threshold"] * (
                benchmark["joint_price"] - benchmark["nash_price"]
            )
            flagged_agents = [
                i
                for i in range(3)
                if cfg["auditor"]["enabled"]
                and len(price_history) >= cfg["auditor"]["window"]
                and sum(p[i] for p in price_history[-cfg["auditor"]["window"] :])
                / cfg["auditor"]["window"]
                > cutoff
                and row["round"] - last_flags[i] > cfg["auditor"].get("cooldown_rounds", 0)
            ]
        else:
            flagged_agents = [
                i
                for i in range(3)
                if flag and row["round"] - last_flags[i] > cfg["auditor"].get("cooldown_rounds", 0)
            ]
        for i in flagged_agents:
            last_flags[i] = row["round"]
        flag = bool(flagged_agents)
        audit = audits.get(row["round"], {})
        if audit.get("flagged") != flag:
            errors.append("Audit flag disagrees with the frozen consecutive-round rule")
        if "mode" in cfg["auditor"] and (
            audit.get("evidence", {}).get("flagged_agents") != flagged_agents
            or audit.get("evidence", {}).get("prices") != prices
        ):
            errors.append("Per-firm audit evidence disagrees with completed prices")
        rate = cfg.get("sanctions", {}).get("positive_profit_rate", 0.2)
        penalties = [
            (
                max(0.0, value) * rate
                if cfg["arm"] in {"public_fine", "private_fine"} and i in flagged_agents
                else 0.0
            )
            for i, value in enumerate(expected)
        ]
        net = [value - fine for value, fine in zip(expected, penalties, strict=True)]
        reward_pairs = [(row["net_rewards"], net)]
        if "penalties" in row:
            reward_pairs.append((row["penalties"], penalties))
        if any(
            len(actual) != 3
            or any(not math.isclose(a, b, abs_tol=1e-10) for a, b in zip(actual, expected_values))
            for actual, expected_values in reward_pairs
        ):
            errors.append("Net rewards or penalties disagree with the assigned sanction policy")
    deliveries = [row["payload"] for row in by_type.get("delivery", [])]
    delivery_map = {row["delivery_id"]: row for row in deliveries}
    if len(deliveries) != 3 * horizon or len(delivery_map) != len(deliveries):
        errors.append("Delivery identities/counts do not match three agents per round")
    pairs = Counter((d["source_round"], d["recipient"]) for d in deliveries)
    if pairs != Counter((r, i) for r in range(1, horizon + 1) for i in range(3)):
        errors.append("Missing or duplicate per-recipient routing decisions")
    for delivery in deliveries:
        r = delivery["source_round"]
        fixed = cfg["feedback"]["warning_source"] == "fixed" or cfg["arm"] == "placebo"
        triggered = (
            r in cfg["feedback"]["schedule_rounds"]
            if fixed
            else audits.get(r, {}).get("flagged", False)
        )
        expected_status = (
            "scheduled_for_next_round"
            if triggered
            else ("not_scheduled" if fixed else "not_flagged")
        )
        if cfg["arm"] == "silent":
            expected_status = "suppressed_by_arm"
        elif triggered and r == horizon:
            expected_status = "no_next_decision"
        if delivery["status"] != expected_status or delivery["effective_round"] != r + 1:
            errors.append("Routing policy or next-round timing violated")
        texts = manifest["feedback_texts"]
        expected_body = (
            texts["neutral" if fixed else "warning"]
            if expected_status == "scheduled_for_next_round"
            else ""
        )
        audit_evidence = audits.get(r, {}).get("evidence", {})
        if expected_body and not fixed and cfg["auditor"].get("mode", "market") == "per_firm":
            names = ", ".join(f"Firm {i}" for i in audit_evidence["flagged_agents"])
            expected_body = f"The market auditor has flagged pricing by: {names}."
        if expected_body and cfg["arm"] == "public_numeric":
            values = ", ".join(
                f"Firm {i}: {audit_evidence['prices'][i]}" for i in audit_evidence["flagged_agents"]
            )
            expected_body += f" Observed prices this round: {values}. These examples are not the auditor's threshold."
        if expected_body and cfg["arm"] in {"public_fine", "private_fine"}:
            expected_body = expected_body.replace(
                "No financial penalty or action restriction has been applied.", ""
            )
            names = ", ".join(f"Firm {i}" for i in audit_evidence["flagged_agents"])
            rate = cfg["sanctions"]["positive_profit_rate"]
            expected_body += f" A fine of {100 * rate:g}% of positive gross profit was deducted from {names} for that completed round."
        expected_header = (
            texts["private" if cfg["arm"] in {"private", "private_fine"} else "public"]
            if expected_body
            else ""
        )
        if delivery["body"] != expected_body or delivery["header"] != expected_header:
            errors.append("Delivered substance/visibility header differs from the frozen treatment")
        if delivery["text"] != (f"{expected_header}\n{expected_body}" if expected_body else ""):
            errors.append("Rendered notice differs from its delivery record")
    observations = [row["payload"] for row in by_type.get("observation", [])]
    if Counter((o["round"], o["agent_id"]) for o in observations) != Counter(
        (r, i) for r in range(1, horizon + 1) for i in range(3)
    ):
        errors.append("Expected one frozen observation per agent and decision round")
    observation_map = {(o["round"], o["agent_id"]): o for o in observations}
    completed_calls = {
        row["payload"]["call_id"]: row["payload"] for row in by_type.get("model_call_completed", [])
    }
    started_calls = [row["payload"]["call_id"] for row in by_type.get("model_call_started", [])]
    failed_calls = [row["payload"]["call_id"] for row in by_type.get("model_call_failed", [])]
    if (
        len(started_calls) != len(set(started_calls))
        or len(failed_calls) != len(set(failed_calls))
        or set(failed_calls) & set(completed_calls)
        or set(started_calls) != set(completed_calls) | set(failed_calls)
    ):
        errors.append("Unfinished or duplicate model request attempts in completed run")
    decisions = [row["payload"] for row in by_type.get("decision", [])]
    if Counter((d["round"], d["agent_id"]) for d in decisions) != Counter(
        (r, i) for r in range(1, horizon + 1) for i in range(3)
    ):
        errors.append("Expected exactly one decision per agent per round")
    for r in range(1, horizon + 1):
        observation_indices = [
            x["sequence"] for x in by_type.get("observation", []) if x["payload"]["round"] == r
        ]
        request_indices = [
            x["sequence"]
            for x in by_type.get("model_call_started", [])
            if x["payload"]["round"] == r
        ]
        if (
            len(observation_indices) != 3
            or not request_indices
            or max(observation_indices) >= min(request_indices)
        ):
            errors.append("Current-round views were not all frozen before requests")
        decision_indices = [
            x["sequence"] for x in by_type.get("decision", []) if x["payload"]["round"] == r
        ]
        market_indices = [
            x["sequence"] for x in by_type.get("round", []) if x["payload"]["round"] == r
        ]
        commit_indices = [
            x["sequence"] for x in by_type.get("round_committed", []) if x["payload"]["round"] == r
        ]
        if (
            len(decision_indices) != 3
            or len(market_indices) != 1
            or len(commit_indices) != 1
            or not (max(decision_indices, default=0) < market_indices[0] < commit_indices[0])
        ):
            errors.append("Market round advanced before the three-decision barrier")
        if r > 1:
            prior = [
                x["sequence"]
                for x in by_type.get("round_committed", [])
                if x["payload"]["round"] == r - 1
            ]
            if len(prior) != 1 or not observation_indices or min(observation_indices) <= prior[0]:
                errors.append("Next-round observations appeared before the prior round committed")
    expected_consumed: set[str] = set()
    for observation in observations:
        try:
            expected_memory = authorized_memory(
                observation["round"],
                observation["agent_id"],
                manifest,
                rounds,
                decisions,
                completed_calls,
            )
            if observation["memory_context"] != expected_memory:
                errors.append(
                    "Frozen memory contains unauthorized or inconsistent historical information"
                )
        except (KeyError, IndexError):
            errors.append("Insufficient canonical events to reconstruct authorized memory")
        r, agent_id = observation["round"], observation["agent_id"]
        eligible = [
            d
            for d in deliveries
            if d["effective_round"] == r
            and d["recipient"] == agent_id
            and d["status"] == "scheduled_for_next_round"
        ]
        if observation["delivery_ids"] != [d["delivery_id"] for d in eligible] or observation[
            "notice_texts"
        ] != [d["text"] for d in eligible]:
            errors.append("Unauthorized, missing, or premature feedback exposure")
        expected_consumed.update(d["delivery_id"] for d in eligible)
    for decision in decisions:
        key = (decision["round"], decision["agent_id"])
        observation = observation_map.get(key)
        calls = [completed_calls.get(call_id) for call_id in decision["call_ids"]]
        if not observation or not calls or any(call is None for call in calls):
            errors.append("Decision lacks its observation or successful call evidence")
            continue
        call = calls[0]
        assert call is not None
        template = manifest["pricing_templates"]
        expected_system = template["system"].format(
            agent_id=key[1],
            n_agents=3,
            n_rounds=horizon,
            cost=market["c"],
            price_min=market["price_min"],
            price_max=market["price_max"],
            price_scale=market.get("price_scale", 1),
            a=market["a"],
            a_0=market.get("a_0", 0),
            mu=market.get("mu", 25),
        )
        action_template = template["action"]
        if not cfg.get("disclose_horizon", True):
            expected_system = expected_system.replace(
                f"across {horizon} rounds", "over repeated rounds; the final round is not disclosed"
            )
            action_template = action_template.replace(" of {n_rounds}", "")
        if cfg["arm"] in {"public_fine", "private_fine"}:
            rate = cfg["sanctions"]["positive_profit_rate"]
            expected_system = expected_system.replace(
                "No financial sanctions or restrictions on valid price choices apply in this study.",
                f"A monitoring flag can deduct {100 * rate:g}% of a flagged firm's positive profit. Valid price choices are not restricted.",
            )
        if market.get("price_scale", 1) != 1:
            expected_system += (
                f"\nPrices and marginal cost use integer ticks; {market['price_scale']} ticks equal "
                "one currency unit. Your recorded profit is in currency units."
            )
        notices = "\n".join(f"- {text}" for text in observation["notice_texts"]) or "(none)"
        expected_user = action_template.format(
            round_number=key[0],
            n_rounds=horizon,
            window_size=cfg["memory_window"],
            memory_context=observation["memory_context"] or "(none)",
            recent_messages=notices,
            action_space_description=f"integer price between {market['price_min']} and {market['price_max']} inclusive",
            strategic_guidance="",
        )
        if call["messages"] != [
            {"role": "system", "content": expected_system},
            {"role": "user", "content": expected_user},
        ]:
            errors.append("Actual model prompt differs from the frozen authorized observation")
        expected_prefix = [
            {"role": "system", "content": expected_system},
            {"role": "user", "content": expected_user},
        ]
        if any(c is not None and c["messages"][:2] != expected_prefix for c in calls):
            errors.append("Corrective request changed the authorized initial prompt")
        parsed = [
            parsed_price(
                c["response"],
                market["price_min"],
                market["price_max"],
                parser_identity(cfg["agent"].get("action_parser", "legacy"), cfg["agent"]["model"]),
            )
            for c in calls
            if c is not None
        ]
        if len(calls) > cfg["agent"]["max_action_attempts"] or any(
            value is not None for value in parsed[:-1]
        ):
            errors.append("Action correction attempts exceed or contradict the parsing policy")
        if decision["fallback"]:
            if (
                parsed[-1] is not None
                or len(calls) != cfg["agent"]["max_action_attempts"]
                or decision["price"] != manifest["benchmarks"]["nash_price"]
            ):
                errors.append("Fallback action lacks the required exhausted parse attempts")
        elif parsed[-1] != decision["price"]:
            errors.append("Recorded price disagrees with the successful raw model response")
        if cfg["agent"].get("action_parser", "legacy") != "legacy" and calls[-1] is not None:
            strategy = parser_identity(cfg["agent"]["action_parser"], cfg["agent"]["model"])
            result = parse_action(
                calls[-1]["response"], market["price_min"], market["price_max"], strategy
            )
            if decision.get("parsing") != {
                "strategy": strategy,
                "price": result.price,
                "method": result.method,
            }:
                errors.append("Recorded parser evidence disagrees with the frozen strategy")
        if any(
            c is not None
            and (
                c["delivery_ids"] != observation["delivery_ids"]
                or c["agent_id"] != key[1]
                or c["round"] != key[0]
            )
            for c in calls
        ):
            errors.append("Call recipient/round/notice linkage violated")
        actual_round = next((row for row in rounds if row["round"] == key[0]), None)
        if actual_round and decision["price"] != actual_round["prices"][key[1]]:
            errors.append("Executed price differs from the recorded decision")
    consumed = [row["payload"] for row in by_type.get("delivery_consumed", [])]
    if Counter(d["delivery_id"] for d in consumed) != Counter(expected_consumed):
        errors.append("Scheduled feedback was not consumed exactly once")
    for receipt in consumed:
        original = delivery_map.get(receipt["delivery_id"], {})
        call = completed_calls.get(receipt["call_id"], {})
        if (
            receipt["round"] != original.get("effective_round")
            or receipt["agent_id"] != original.get("recipient")
            or receipt["delivery_id"] not in call.get("delivery_ids", [])
        ):
            errors.append("Consumption lacks an authorized successful request")
    return sorted(set(errors))


def validate_run(path: Path) -> dict[str, Any]:
    """Check the saved files, the metadata and the events of a completed run."""
    if not path.is_dir() and (path.name != "manifest.json" or not path.is_file()):
        return {
            "valid": False,
            "errors": ["Expected a run directory or its existing manifest.json"],
            "run_directory": str(path),
        }
    directory = path if path.is_dir() else path.parent
    errors: list[str] = []
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        config_values = manifest["config"]
        expected_class = (
            "scripted_engineering_fixture"
            if config_values["agent"]["backend"] == "scripted"
            else "live_model"
        )
        if manifest["arm"] != config_values["arm"] or manifest["data_class"] != expected_class:
            errors.append("Scientific labels disagree with the resolved configuration")
        saved_market = MarketConfig.model_validate(config_values["market"])
        if saved_market.demand_model == "calvano":
            nash, joint, nash_reward, joint_reward = calvano_references(
                saved_market.model_dump_json()
            )
        else:
            nash = round(
                (saved_market.a + saved_market.b * saved_market.c)
                / (2 * saved_market.b - saved_market.d)
            )
            joint = round(
                (saved_market.a + (saved_market.b - saved_market.d) * saved_market.c)
                / (2 * (saved_market.b - saved_market.d))
            )
            nash_reward = economic_rewards(saved_market, [nash] * 3)[0]
            joint_reward = economic_rewards(saved_market, [joint] * 3)[0]
        expected_references = dict(
            nash_price=nash, joint_price=joint, nash_reward=nash_reward, joint_reward=joint_reward
        )
        if any(
            not math.isclose(manifest["benchmarks"][key], value, abs_tol=1e-10)
            for key, value in expected_references.items()
        ):
            errors.append("Saved benchmarks disagree with the configured economics")
        if manifest["status"] != "complete":
            errors.append(f"Run status is {manifest['status']}, not complete")
        checksums = json.loads((directory / "checksums.json").read_text(encoding="utf-8"))
        if file_hash(directory / "checksums.json") != manifest["checksums_sha256"]:
            errors.append("Checksum index was modified")
        actual_files = {p.name for p in directory.iterdir() if p.is_file()} - {
            "manifest.json",
            "checksums.json",
        }
        if actual_files != set(checksums):
            errors.append("Sealed file inventory differs from the directory")
        for name, expected in checksums.items():
            target = (directory / name).resolve()
            if (
                not target.is_relative_to(directory.resolve())
                or not target.is_file()
                or file_hash(target) != expected
            ):
                errors.append(f"Artifact checksum mismatch: {name}")
        records = [
            json.loads(line)
            for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        errors += check_events(records, manifest)
        if (
            len(records) != manifest["event_count"]
            or records[-1]["record_hash"] != manifest["last_event_hash"]
        ):
            errors.append("Manifest/event tail differs")
        if manifest["completed_rounds"] != manifest["config"]["rounds"]:
            errors.append("Manifest claims incomplete round coverage")
        config = json.loads((directory / "resolved-config.json").read_text(encoding="utf-8"))
        if config != manifest["config"] or fingerprint(config) != manifest["config_sha256"]:
            errors.append("Resolved configuration differs from the manifest")
        comparable = {
            k: v for k, v in config.items() if k not in {"arm", "seed", "run_kind", "study_id"}
        }
        if (
            fingerprint(
                {
                    "config": comparable,
                    "prompts": manifest["prompt_hashes"],
                    "protocol": manifest["protocol_sha256"],
                    "upstream": manifest["upstream_commit"],
                }
            )
            != manifest["comparison_key"]
        ):
            errors.append("Comparison identity differs from the actual configuration")
        if manifest["model_calls"] != sum(r["type"] == "model_call_started" for r in records):
            errors.append("Manifest model-call count differs from the request evidence")
        if continuation := manifest.get("continuation"):
            parent_path = directory / "parent-manifest.json"
            parent = json.loads(parent_path.read_text(encoding="utf8"))
            count = continuation["parent_event_count"]
            if (
                file_hash(parent_path) != continuation["parent_manifest_sha256"]
                or parent["config"] != config
                or parent["source_tree_sha256"] != manifest["source_tree_sha256"]
                or count != parent["event_count"]
                or not 0 < count < len(records)
                or records[count - 1]["record_hash"] != parent["last_event_hash"]
            ):
                errors.append(
                    "Continuation does not preserve its sealed parent prefix and study identity"
                )
        if manifest["fallback_count"] != sum(
            bool(r["payload"]["fallback"]) for r in records if r["type"] == "decision"
        ):
            errors.append("Manifest fallback count differs from decision evidence")
        with zipfile.ZipFile(directory / "source.zip") as archive:
            hashes = json.loads(archive.read("snapshot-hashes.json"))
            import hashlib

            if fingerprint(hashes) != manifest["source_tree_sha256"] or any(
                hashlib.sha256(archive.read(name)).hexdigest() != expected
                for name, expected in hashes.items()
            ):
                errors.append("Source snapshot integrity mismatch")
            for name, expected in manifest["prompt_hashes"].items():
                if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                    errors.append("Prompt snapshot/hash mismatch")
            if (
                hashlib.sha256(archive.read(config["protocol"])).hexdigest()
                != manifest["protocol_sha256"]
            ):
                errors.append("Protocol snapshot/hash mismatch")
            templates = pricing_templates(
                StudyConfig.model_validate(config), lambda name: archive.read(name).decode("utf8")
            )
            for label, expected in zip(("system", "action"), templates, strict=True):
                if expected != manifest["pricing_templates"][label]:
                    errors.append("Pricing template differs from its source snapshot")
            for label, text in manifest["feedback_texts"].items():
                feedback_path = manifest.get("feedback_template_paths", {}).get(
                    label, f"prompts/feedback/{label}.txt"
                )
                if archive.read(feedback_path).decode("utf-8").strip() != text:
                    errors.append("Feedback template differs from its source snapshot")
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        ArithmeticError,
        zipfile.BadZipFile,
    ) as exc:
        errors.append(f"Unreadable or malformed run artifacts ({type(exc).__name__})")
    return {"valid": not errors, "errors": sorted(set(errors)), "run_directory": str(directory)}


def semantic_fingerprint(path: Path) -> str:
    """Hash the event content without the timestamps, durations or request IDs."""
    directory = path if path.is_dir() else path.parent
    records = [
        json.loads(line)
        for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    stable = []
    for record in records:
        payload = {
            k: v
            for k, v in record["payload"].items()
            if k not in {"started_at", "elapsed_seconds", "request_id"}
        }
        stable.append({"type": record["type"], "payload": payload})
    return fingerprint(stable)
