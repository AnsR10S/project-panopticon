"""Export full request evidence beside round outcomes, including unsuccessful attempts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from statistics import mean
from typing import Any

from auditors_paradox.evidence import atomic_json
from auditors_paradox.research_index import safe_data


def packed(value: Any) -> str:
    """Keep structured records in CSV cells while excluding credentials."""
    return json.dumps(safe_data(value), ensure_ascii=False, separators=(",", ":"))


def write_table(path: Path, rows: list[dict[str, Any]], empty_fields: list[str]) -> None:
    """Always write headers, even when no requests or outcomes exist."""
    fields = list(dict.fromkeys(key for row in rows for key in row)) or empty_fields
    with path.open("w", encoding="utf8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def export_request_evidence(
    rows: list[dict[str, Any]], root: Path, output: Path
) -> list[dict[str, Any]]:
    """One round row holds all its requests; a second CSV gives one row per attempt."""
    table: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    warnings = []
    for row in rows:
        if not row.get("manifest_path"):
            continue
        path = (root / row["manifest_path"]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Export manifest escaped the project")
        try:
            manifest = json.loads(path.read_text(encoding="utf8"))
            events = [
                json.loads(line)
                for line in path.with_name("events.jsonl").read_text(encoding="utf8").splitlines()
            ]
            benchmark = manifest["benchmarks"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            warnings.append(
                {"manifest_path": row["manifest_path"], "error_type": type(error).__name__}
            )
            continue
        shared = {k: row[k] for k in ("run_id", "arm", "replicate_id")}
        shared.update(
            run_status=row["status"],
            included_in_inference=row["included"],
            exclusion=row.get("exclusion", ""),
            manifest_path=row["manifest_path"],
            source_tree_sha256=manifest["source_tree_sha256"],
        )
        shared.update({k: row.get(k) for k in ("prompt_trial", "prompt_number", "prompt_version", "cycle") if k in row})
        committed = {e["payload"]["round"] for e in events if e["type"] == "round_committed"}
        decisions = {
            c: e["payload"]
            for e in events
            if e["type"] == "decision"
            for c in e["payload"]["call_ids"]
        }
        terminals = {
            e["payload"]["call_id"]: e
            for e in events
            if e["type"] in {"model_call_completed", "model_call_failed"}
        }
        by_round: dict[int, list[dict[str, Any]]] = {}
        for event in events:
            value = event["payload"]
            number = value.get("round", value.get("source_round"))
            if type(number) is int and number > 0:
                by_round.setdefault(number, []).append(event)
        round_requests: dict[int, list[dict[str, Any]]] = {}
        for event in events:
            if event["type"] != "model_call_started":
                continue
            start = event["payload"]
            terminal = terminals.get(start["call_id"])
            end = terminal["payload"] if terminal else {}
            failed = terminal is not None and terminal["type"] == "model_call_failed"
            metadata = end.get("client_evidence" if failed else "usage_metadata", {})
            usage = metadata if failed else end
            messages = start.get("messages", [])
            decision = decisions.get(start["call_id"], {})
            record = {
                **shared,
                "round": start["round"],
                "firm_id": start["agent_id"],
                "call_id": start["call_id"],
                "transport_attempt": start.get("transport_attempt"),
                "status": "failed" if failed else "completed" if terminal else "unfinished",
                "committed_round": start["round"] in committed,
                "accepted_decision": bool(decision)
                and decision["call_ids"][-1] == start["call_id"],
                "requested_model": start.get("requested_model"),
                "reported_model": usage.get("reported_model"),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "usage_known": all(
                    type(usage.get(k)) is int for k in ("input_tokens", "output_tokens")
                ),
                "error_kind": end.get("error_kind"),
                "done_reason": metadata.get("done_reason"),
                "started_at": start.get("started_at"),
                "elapsed_seconds": end.get("elapsed_seconds"),
                "output_token_limit": start.get(
                    "output_token_limit", manifest["config"]["agent"]["max_output_tokens"]
                ),
                "system_prompt": "\n".join(m["content"] for m in messages if m["role"] == "system"),
                "user_prompt": next(
                    (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
                ),
                "messages_json": packed(messages),
                "response": end.get(
                    "response", metadata.get("response_channels", {}).get("answer")
                ),
                "thinking": metadata.get("response_channels", {}).get("thinking"),
                "usage_metadata_json": packed(metadata),
                "decision_json": packed(decision),
                "request_event_json": packed(event),
                "response_event_json": packed(terminal),
            }
            record = safe_data(record)
            requests.append(record)
            round_requests.setdefault(start["round"], []).append(record)
        for number, grouped in sorted(by_round.items()):
            outcomes = [e["payload"] for e in grouped if e["type"] == "round"]
            outcome = outcomes[-1] if outcomes else None
            audit = [e["payload"] for e in grouped if e["type"] == "audit"]
            entry = {
                **shared,
                "round": number,
                "committed": number in committed,
                "market_outcome_available": outcome is not None,
                "mean_price": mean(outcome["prices"]) if outcome else None,
                "price_spread": (
                    max(outcome["prices"]) - min(outcome["prices"]) if outcome else None
                ),
                "gross_profit_elevation": (
                    (mean(outcome["gross_rewards"]) - benchmark["nash_reward"])
                    / (benchmark["joint_reward"] - benchmark["nash_reward"])
                    if outcome
                    else None
                ),
                "triggered": audit[-1]["flagged"] if audit else None,
                "consumed_notices": sum(e["type"] == "delivery_consumed" for e in grouped),
                **{f"price_firm_{i}": outcome["prices"][i] if outcome else None for i in range(3)},
                "request_count": len(round_requests.get(number, [])),
                "requests_json": packed(round_requests.get(number, [])),
                "observations_json": packed(
                    [e["payload"] for e in grouped if e["type"] == "observation"]
                ),
                "decisions_json": packed(
                    [e["payload"] for e in grouped if e["type"] == "decision"]
                ),
                "market_outcome_json": packed(outcome),
                "audits_json": packed(audit),
                "deliveries_json": packed(
                    [
                        e["payload"]
                        for e in grouped
                        if e["type"] in {"delivery", "delivery_consumed"}
                    ]
                ),
                "events_json": packed(grouped),
                "config_json": packed(manifest["config"]),
            }
            table.append(entry)
    write_table(
        output / "rounds.csv", table, ["run_id", "arm", "round", "committed", "requests_json"]
    )
    write_table(
        output / "requests.csv",
        requests,
        ["run_id", "round", "firm_id", "call_id", "messages_json"],
    )
    if warnings:
        atomic_json(output / "export-warnings.json", warnings)
    return table
