"""Export the saved paid-client investigation without sending requests or changing evidence."""

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

RATES = {"gpt-6.1-sol": (3, 15), "gpt-5.6-sol": (8, 40), "gpt-6-astra": (16, 80)}


def failure_category(evidence):
    """Separate provider failures from absent usage and exhausted reasoning budgets."""
    error = evidence.get("provider_error", {})
    message = error.get("message", "").lower()
    if evidence.get("http_status") == 405 and "errors.aliyun.com" in message:
        return "gateway_html_block"
    if error.get("code") == "no_capacity":
        return "provider_capacity"
    if "does not support /v1/chat/completions" in message:
        return "protocol_mismatch"
    if evidence.get("error_kind") == "incomplete_response":
        usage = evidence.get("provider_usage", {})
        output = usage.get("output_tokens")
        reasoning = usage.get("output_tokens_details", {}).get("reasoning_tokens")
        if output is not None and output == reasoning:
            return "reasoning_budget_exhaustion"
    return evidence.get("error_kind", "unknown")


def write_csv(path, rows, fields):
    """Write unknown values as empty cells, without inventing zero usage."""
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build(root, output):
    """Read the shared ledger and its actual market runs into a fresh derived folder."""
    ledger_path = root / "runs/diagnostics/qwen-live/allowance.json"
    ledger_bytes = ledger_path.read_bytes()
    ledger = json.loads(ledger_bytes.decode("utf-8"))
    output.mkdir(parents=True, exist_ok=False)
    (output / "source-allowance.json").write_bytes(ledger_bytes)
    sources = {ledger_path.relative_to(root).as_posix(): ledger_path}
    requests = []
    for index, record in enumerate(ledger["requests"], 1):
        evidence = record.get("evidence", {})
        wire = evidence.get("wire_request", {})
        model = wire.get("model")
        usage = evidence.get("provider_usage", {})
        incoming = record.get("input_tokens", usage.get("input_tokens"))
        outgoing = record.get("output_tokens", usage.get("output_tokens"))
        known = incoming is not None and outgoing is not None
        rates = RATES.get(model)
        cost = (incoming * rates[0] + outgoing * rates[1]) / 1e6 if known and rates else None
        requests.append(
            {
                "attempt": index,
                "validation": record["validation"],
                "model": model,
                "status": record["status"],
                "network_requests": evidence.get(
                    "network_requests", record.get("network_requests")
                ),
                "http_status": evidence.get("http_status"),
                "failure_category": (
                    failure_category(evidence) if record["status"] != "complete" else ""
                ),
                "input_tokens": incoming,
                "output_tokens": outgoing,
                "reasoning_tokens": usage.get("output_tokens_details", {}).get("reasoning_tokens"),
                "usage_known": known,
                "usage_cost_estimate_usd": cost,
                "recorded_reservation_usd": record.get("reserved_usd"),
            }
        )
    rounds, decisions, markets, cases = [], [], [], []
    for identity in dict.fromkeys(r["validation"] for r in requests):
        report_path = ledger_path.parent / (identity + ".json")
        if report_path.exists():
            sources[report_path.relative_to(root).as_posix()] = report_path
            report = json.loads(report_path.read_text(encoding="utf-8"))
            for stage in report.get("stages", []):
                if isinstance(stage, dict) and "case" in stage:
                    cases.append({"validation": identity, **stage})
        manifest_path = root / "runs/pilot/agentrouter-qwen-smoke" / identity / "manifest.json"
        if not manifest_path.exists():
            continue
        events_path = manifest_path.with_name("events.jsonl")
        for path in (manifest_path, events_path):
            sources[path.relative_to(root).as_posix()] = path
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        committed = {e["payload"]["round"] for e in events if e["type"] == "round_committed"}
        run = {
            "run_id": identity,
            "arm": manifest["arm"],
            "model": manifest["config"]["agent"]["model"],
            "run_status": manifest["status"],
        }
        markets.append(
            {
                **run,
                "planned_rounds": manifest["config"]["rounds"],
                "committed_rounds": len(committed),
                "accepted_decisions": sum(e["type"] == "decision" for e in events),
                "trigger_events": sum(
                    e["type"] == "audit" and e["payload"]["flagged"] for e in events
                ),
                "consumed_notices": sum(
                    len(e["payload"].get("notice_texts", []))
                    for e in events
                    if e["type"] == "observation"
                ),
                "manifest": manifest_path.relative_to(root).as_posix(),
            }
        )
        for event in events:
            payload = event["payload"]
            if event["type"] == "decision":
                decisions.append(
                    {
                        **run,
                        "round": payload["round"],
                        "firm": payload["agent_id"],
                        "price": payload["price"],
                        "round_committed": payload["round"] in committed,
                    }
                )
            if event["type"] == "round" and payload["round"] in committed:
                mean_profit = sum(payload["gross_rewards"]) / len(payload["gross_rewards"])
                refs = manifest["benchmarks"]
                elevation = (mean_profit - refs["nash_reward"]) / (
                    refs["joint_reward"] - refs["nash_reward"]
                )
                rounds.append(
                    {
                        **run,
                        "round": payload["round"],
                        "firm_0_price": payload["prices"][0],
                        "firm_1_price": payload["prices"][1],
                        "firm_2_price": payload["prices"][2],
                        "mean_gross_profit": mean_profit,
                        "gross_profit_elevation": elevation,
                        "inferentially_included": False,
                    }
                )
    summary = {
        "scope": "Exploratory connection investigation; incomplete runs are not research replicates",
        "ledger_snapshot": "source-allowance.json",
        "reserved_attempts": ledger["reserved_attempts"],
        "actual_network_requests": sum(r["network_requests"] or 0 for r in requests),
        "network_count_unknown_records": sum(r["network_requests"] is None for r in requests),
        "successful_replies": sum(r["status"] == "complete" for r in requests),
        "known_usage_requests": sum(r["usage_known"] for r in requests),
        "unknown_usage_requests": sum(not r["usage_known"] for r in requests),
        "known_input_tokens": sum(r["input_tokens"] or 0 for r in requests),
        "known_output_tokens": sum(r["output_tokens"] or 0 for r in requests),
        "known_reasoning_tokens": sum(r["reasoning_tokens"] or 0 for r in requests),
        "observed_cost_estimate_usd": ledger["observed_usd"],
        "recomputed_known_cost_usd": round(
            sum(r["usage_cost_estimate_usd"] or 0 for r in requests), 6
        ),
        "held_allowance_usd": ledger["reserved_usd"],
        "owner_total_ceiling_usd": 50,
        "failure_categories": dict(
            Counter(r["failure_category"] for r in requests if r["failure_category"])
        ),
        "by_model": {
            model: {
                "requests": sum(
                    r["network_requests"] or 0 for r in requests if r["model"] == model
                ),
                "successful_replies": sum(
                    r["status"] == "complete" for r in requests if r["model"] == model
                ),
                "known_cost_usd": round(
                    sum(r["usage_cost_estimate_usd"] or 0 for r in requests if r["model"] == model),
                    6,
                ),
            }
            for model in RATES
        },
        "actual_market_runs": markets,
        "committed_rounds": len(rounds),
        "accepted_market_decisions": len(decisions),
        "committed_market_decisions": sum(r["round_committed"] for r in decisions),
        "completed_diagnostic_cases": len(cases),
        "diagnostic_cases": cases,
        "source_hashes": {
            name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sources.items()
        },
    }
    summary["source_hashes"][ledger_path.relative_to(root).as_posix()] = hashlib.sha256(
        ledger_bytes
    ).hexdigest()
    write_csv(output / "requests.csv", requests, list(requests[0]))
    write_csv(
        output / "rounds.csv",
        rounds,
        [
            "run_id",
            "arm",
            "model",
            "run_status",
            "round",
            "firm_0_price",
            "firm_1_price",
            "firm_2_price",
            "mean_gross_profit",
            "gross_profit_elevation",
            "inferentially_included",
        ],
    )
    write_csv(
        output / "decisions.csv",
        decisions,
        ["run_id", "arm", "model", "run_status", "round", "firm", "price", "round_committed"],
    )
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    destination = arguments.output.resolve()
    if not destination.is_relative_to(project / "analysis"):
        raise ValueError("Derived output must be a new folder inside project analysis/")
    result = build(project, destination)
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "actual_network_requests",
                    "observed_cost_estimate_usd",
                    "held_allowance_usd",
                    "committed_rounds",
                    "accepted_market_decisions",
                    "failure_categories",
                )
            },
            indent=2,
        )
    )
