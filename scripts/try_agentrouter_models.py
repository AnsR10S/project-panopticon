"""Try one small reply per model and preserve diagnostic evidence separately."""

import argparse
import csv
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from auditors_paradox.adapters.model_calls import (
    Budget,
    ProviderBackend,
    ProviderError,
    RecordedClient,
    RunStopped,
)
from auditors_paradox.config import AgentConfig, BudgetConfig, StudyConfig, load_config
from auditors_paradox.evidence import EvidenceWriter, atomic_json

# These are the account prices in the owner's screenshot, per million tokens.
RATES = {"gpt-6.1-sol": (3, 15), "gpt-5.6-sol": (8, 40), "gpt-6-astra": (8, 40)}


def run_probes(cfg: StudyConfig, root: Path) -> tuple[Path, list[dict]]:
    """Keep the three attempts bounded and never replace a research model or config."""
    if cfg.agent.backend != "agentrouter" or cfg.agent.api_protocol != "chat":
        raise ValueError("Model probes require the AgentRouter chat configuration")
    identity = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    directory = root / "runs/diagnostics/agentrouter-model-probes" / identity
    directory.mkdir(parents=True, exist_ok=False)
    budget_cfg = BudgetConfig(
        max_calls=3,
        enforce_spending_caps=cfg.budget.enforce_spending_caps,
        max_cost_usd=0.10,
        input_usd_per_million=3,
        output_usd_per_million=15,
        max_elapsed_seconds=180,
        stop_file=cfg.budget.stop_file,
    )
    budget = Budget(budget_cfg, cfg.agent, root=root)
    rows = []
    stop = None
    messages = [{"role": "user", "content": "Reply with exactly OK. No explanation."}]
    for model, (input_rate, output_rate) in RATES.items():
        row = {
            "model": model,
            "status": "not_attempted",
            "http_status": None,
            "error_kind": stop,
            "input_tokens": 0,
            "output_tokens": 0,
            "reported_model": None,
            "estimated_cost_usd": 0.0,
        }
        if stop is None:
            agent = AgentConfig.model_validate(
                {
                    **cfg.agent.model_dump(),
                    "model": model,
                    "temperature": None,
                    "max_output_tokens": 256,
                    "timeout_seconds": 30,
                    "transport_attempts": 1,
                    "min_request_interval_seconds": 0.3,
                }
            )
            budget.agent = agent
            budget.config.input_usd_per_million = input_rate
            budget.config.output_usd_per_million = output_rate
            writer = EvidenceWriter(
                directory / model,
                {
                    "data_class": "diagnostic",
                    "diagnostic": "single_reply_probe",
                    "config": agent.model_dump(),
                    "input_usd_per_million": input_rate,
                    "output_usd_per_million": output_rate,
                },
            )
            before = budget.observed_usd
            error = None
            client = None
            try:
                budget.check_stop()
                client = RecordedClient(ProviderBackend(agent), model, 0, writer, budget)
                text = client.generate(messages)
                row["status"] = "reply_received" if text.strip() else "empty_reply"
                row["exact_ok"] = text.strip() == "OK"
            except (Exception, KeyboardInterrupt) as exc:
                row["status"] = "failed"
                row["error_kind"] = (
                    exc.kind if isinstance(exc, ProviderError) else type(exc).__name__
                )
                row["http_status"] = exc.status_code if isinstance(exc, ProviderError) else None
                error = {"type": type(exc).__name__, "message": str(row["error_kind"])}
                if (
                    isinstance(exc, (RunStopped, KeyboardInterrupt))
                    or row["http_status"] in {401, 429}
                    or row["error_kind"] in {"quota_exhausted", "client_not_authorized"}
                ):
                    stop = str(row["error_kind"])
            finally:
                if client is not None:
                    row["input_tokens"] = client.input_tokens
                    row["output_tokens"] = client.output_tokens
                row["estimated_cost_usd"] = budget.observed_usd - before
                events = [
                    json.loads(line)
                    for line in (directory / model / "events.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ]
                completed = [
                    event["payload"] for event in events if event["type"] == "model_call_completed"
                ]
                if completed:
                    row["reported_model"] = completed[-1]["reported_model"]
                writer.manifest["result"] = row
                writer.finish("complete" if error is None else "failed", error)
        rows.append(row)
        atomic_json(
            directory / "results.json",
            {
                "models": rows,
                "request_attempts": budget.calls,
                "reserved_usd": budget.reserved_usd,
                "observed_estimate_usd": budget.observed_usd,
                "reservation_ceiling_usd": 0.10,
                "billing": "Account rates from screenshot; failed-call billing unknown",
            },
        )
    fields = [
        "model",
        "status",
        "http_status",
        "error_kind",
        "input_tokens",
        "output_tokens",
        "reported_model",
        "estimated_cost_usd",
        "exact_ok",
    ]
    with (directory / "results.csv").open("x", encoding="utf-8", newline="") as stream:
        writer_csv = csv.DictWriter(stream, fieldnames=fields)
        writer_csv.writeheader()
        writer_csv.writerows(rows)
    return directory, rows


def main() -> int:
    """Use a runtime token and an existing config without editing either."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    args = parser.parse_args()
    if not os.environ.get("AGENTROUTER_API_KEY", "").strip():
        print("Missing runtime AgentRouter token; use the launcher hidden prompt.")
        return 2
    directory, rows = run_probes(load_config(args.config), Path(__file__).resolve().parents[1])
    for row in rows:
        print(json.dumps(row))
    print(f"Results: {directory / 'results.csv'}")
    return 0 if any(row["status"] == "reply_received" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
