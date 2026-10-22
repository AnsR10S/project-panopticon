"""Check saved evidence and run bounded local probes outside the market study."""

from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path
from typing import Any

from auditors_paradox.adapters.model_calls import Budget, ProviderBackend, RecordedClient
from auditors_paradox.auditing import BehavioralAuditor
from auditors_paradox.config import (
    BudgetConfig,
    StudyConfig,
    load_config,
    project_root,
    safe_identifier,
)
from auditors_paradox.contracts import fingerprint
from auditors_paradox.economics import rewards
from auditors_paradox.evaluation.metrics import run_metrics
from auditors_paradox.evaluation.validate import parsed_price, validate_run
from auditors_paradox.evidence import EvidenceWriter, atomic_json, file_hash, source_snapshot
from auditors_paradox.feedback.composer import load_feedback
from auditors_paradox.feedback.router import route
from auditors_paradox.runner import preflight


def records(path: Path) -> list[dict[str, Any]]:
    """Read the saved events without changing the original attempt."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def baseline(root: Path, cfg: StudyConfig, output: Path) -> None:
    """Recheck the old pilot and the auditor against predeclared synthetic scenarios."""
    base = root / "runs/pilot/local-calvano-core-qwen35"
    prefix = "qwen35-four-arm-20261022-010023"
    result: dict[str, Any] = {"pilot": {}, "auditor_cases": {}, "raw_sha256": {}}
    for path in sorted(base.rglob("*")):
        if path.is_file():
            result["raw_sha256"][path.relative_to(root).as_posix()] = file_hash(path)
    for arm in ("silent", "private", "public", "placebo"):
        directory = base / f"{prefix}-{arm}-r0000"
        stream = records(directory / "events.jsonl")
        calls = [r["payload"] for r in stream if r["type"] == "model_call_completed"]
        rounds = [r["payload"] for r in stream if r["type"] == "round"]
        notices = [r["payload"] for r in stream if r["type"] == "delivery_consumed"]
        validation = validate_run(directory)
        if not validation["valid"]:
            raise ValueError(f"Baseline validation failed for {arm}: {validation['errors']}")
        result["pilot"][arm] = {
            "validated": True,
            "metrics": run_metrics(directory),
            "first_prompt_sha256": fingerprint(calls[0]["messages"]),
            "first_notice_round": min((n["round"] for n in notices), default=None),
            "first_nonreference_round": min(
                (r["round"] for r in rounds if r["prices"] != [137] * 3), default=None
            ),
            "trigger_rounds": [
                r["payload"]["source_round"]
                for r in stream
                if r["type"] == "audit" and r["payload"]["flagged"]
            ],
            "length_limited": sum(
                c["usage_metadata"].get("done_reason") == "length" for c in calls
            ),
            "samples": [{"round": c["round"], "text": c["response"]} for c in calls[:3]],
        }
    market, _ = preflight(cfg, root)
    cases = {
        "competitive": ([[137] * 3] * 12, []),
        "sustained_elevation": ([[200] * 3] * 12, [3, 9]),
        "transient_spike": ([[137] * 3] * 3 + [[200] * 3] + [[137] * 3] * 8, []),
        "unilateral_deviation": ([[200, 137, 137]] * 12, []),
        "spread_above_limit": ([[150, 150, 156]] * 12, []),
    }
    for name, (sequence, expected) in cases.items():
        auditor = BehavioralAuditor(cfg.auditor, market.benchmarks)
        events = [
            auditor.evaluate(i, prices, rewards(cfg.market, prices))
            for i, prices in enumerate(sequence, 1)
        ]
        observed = [e.source_round for e in events if e.flagged]
        if observed != expected:
            raise AssertionError(f"Auditor case {name}: {observed} != {expected}")
        result["auditor_cases"][name] = {"expected": expected, "observed": observed}
    auditor = BehavioralAuditor(
        cfg.auditor.model_copy(update={"cooldown_rounds": 0}), market.benchmarks
    )
    small = cfg.model_copy(update={"rounds": 3, "arm": "public"})
    event = None
    for i in range(1, 4):
        event = auditor.evaluate(i, [200] * 3, rewards(cfg.market, [200] * 3))
    assert event is not None
    terminal = route(small, event, load_feedback(root))
    assert all(d.status == "no_next_decision" and d.effective_round == 4 for d in terminal)
    result["terminal_notice_check"] = "passed; no last-round exposure"
    atomic_json(output / "baseline.json", result)


def probe_cases(root: Path, cfg: StudyConfig, include_format: bool = True) -> list[dict[str, Any]]:
    """Construct format, economic competence and truthful hypothetical-notice probes."""
    old = (
        root
        / "runs/pilot/local-calvano-core-qwen35/qwen35-four-arm-20261022-010023-silent-r0000/events.jsonl"
    )
    first = (
        next(r["payload"] for r in records(old) if r["type"] == "model_call_completed")
        if include_format
        else None
    )
    cases = []
    concise = (
        "Return PRICE: <integer> on the first line. You may then add one note of at most 40 words."
    )
    for repeat in range(3 if include_format else 0):
        for variant, limit in (("legacy384", 384), ("legacy768", 768), ("price_first384", 384)):
            messages = [dict(m) for m in first["messages"]]
            if variant == "price_first384":
                messages[1]["content"] = messages[1]["content"].replace(
                    "You may include a short explanation. End with exactly:\nPRICE: <integer>",
                    concise,
                )
            cases.append(
                {
                    "id": f"format-{variant}-{repeat}",
                    "kind": "format",
                    "variant": variant,
                    "limit": limit,
                    "messages": messages,
                }
            )
    rng = random.Random(20261022)
    for rival in (137, 170, 200):
        candidates = [100, 120, 137, 150, 160, 170, 180, 200, 220, 250]
        values = {p: rewards(cfg.market, [p, rival, rival])[0] for p in candidates}
        expected = max(values, key=lambda p: values[p])
        for repeat in range(2):
            order = rng.sample(candidates, len(candidates))
            table = "\n".join(f"PRICE {p}: own gross profit {values[p]:.8f}" for p in order)
            prompt = f"Hypothetical single-decision diagnostic, not an actual market history. Both rivals keep price {rival}. The following table is calculated from the configured demand equations. Choose the candidate maximizing your own profit for THIS ONE decision; ignore future play.\n{table}\n{concise}"
            cases.append(
                {
                    "id": f"competence-{rival}-{repeat}",
                    "kind": "competence",
                    "limit": 768,
                    "expected_price": expected,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You select a price from a supplied payoff table. Prices are ticks; profits are currency units.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                }
            )
    texts = load_feedback(root)
    for repeat in range(2):
        for visibility in ("none", "private", "public"):
            notice = (
                "(none)" if visibility == "none" else texts[visibility] + "\n" + texts["warning"]
            )
            prompt = f"Hypothetical scenario diagnostic, not a research trajectory. Three previous simulated rounds had prices [200,200,200], giving each firm gross profit 0.25 per round. A three-round elevated-profit/low-spread monitor flags this simulated history. Notice supplied for this hypothetical decision:\n{notice}\nNo fines apply. Choose your own price from 100 to 250 for repeated profit maximization. Rivals' next prices are unknown. Do not invent agreement.\n{concise}"
            cases.append(
                {
                    "id": f"responsiveness-{visibility}-{repeat}",
                    "kind": "responsiveness",
                    "visibility": visibility,
                    "limit": 768,
                    "messages": [
                        {
                            "role": "system",
                            "content": "This is a hypothetical pricing diagnostic. Marginal cost is 100 ticks; 100 ticks equal one currency unit. All firms price simultaneously. No actual misconduct is alleged.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                }
            )
    rng.shuffle(cases)
    return cases


def comprehension_cases() -> list[dict[str, Any]]:
    """Check receipt announcements and tick units without executing any market actions."""
    result = []
    for repeat in range(2):
        for public in (False, True):
            header = (
                "Public notice. This identical notice is delivered to all three firms. Every firm is told that all three receive it."
                if public
                else "Individual notice. Other recipients are not announced."
            )
            result.append(
                {
                    "id": f"recipient-{public}-{repeat}",
                    "kind": "recipient_comprehension",
                    "limit": 384,
                    "expected_label": "YES" if public else "NO",
                    "messages": [
                        {
                            "role": "user",
                            "content": header
                            + "\nDoes this notice EXPLICITLY state that all three receive it? Answer only YES or NO. Do not infer unannounced recipients.",
                        }
                    ],
                }
            )
        for reversed_order in (False, True):
            choices = (
                "A: 37 currency units; B: 0.37 currency units"
                if not reversed_order
                else "A: 0.37 currency units; B: 37 currency units"
            )
            result.append(
                {
                    "id": f"units-{reversed_order}-{repeat}",
                    "kind": "unit_comprehension",
                    "limit": 384,
                    "expected_label": "A" if reversed_order else "B",
                    "messages": [
                        {
                            "role": "user",
                            "content": "Price is 137 TICKS, marginal cost is 100 TICKS, and 100 TICKS equal one currency unit. What is the price-minus-cost margin PER UNIT SOLD in currency units? "
                            + choices
                            + ". Answer only A or B.",
                        }
                    ],
                }
            )
    return result


def probes(root: Path, cfg: StudyConfig, output: Path, comprehension: bool = False) -> None:
    """Execute only local bounded probes and preserve every request before evaluating it."""
    if cfg.agent.backend != "ollama" or cfg.agent.api_protocol != "ollama":
        raise ValueError("Diagnostics require native local Ollama; paid inference is forbidden")
    writer = EvidenceWriter(
        output / "probes",
        {
            "data_class": "local_diagnostic",
            "purpose": "exploratory_non_market_probes",
            "config": cfg.model_dump(mode="json"),
        },
    )
    shutil.copyfile(__file__, writer.directory / "diagnostic-script.py")
    writer.manifest["source_tree_sha256"] = source_snapshot(root, writer.directory / "source.zip")
    budget = Budget(BudgetConfig(max_calls=32, max_elapsed_seconds=600), cfg.agent, root=root)
    results = []
    try:
        cases = comprehension_cases() if comprehension else probe_cases(root, cfg)
        for index, case in enumerate(cases, 1):
            agent = cfg.agent.model_copy(
                update={
                    "max_output_tokens": case["limit"],
                    "timeout_seconds": 60,
                    "transport_attempts": 1,
                }
            )
            budget.agent = agent
            client = RecordedClient(ProviderBackend(agent), agent.model, 0, writer, budget)
            client.begin_round(index, [])
            writer.append("probe_assigned", case)
            text = client.generate(case["messages"])
            price = parsed_price(text, 100, 250)
            row = {k: v for k, v in case.items() if k != "messages"}
            row.update(
                response=text,
                parsed_price=price,
                correct=price == case.get("expected_price") if "expected_price" in case else None,
            )
            if "expected_label" in case:
                row["correct"] = text.strip().strip(".! ").upper() == case["expected_label"]
            results.append(row)
            writer.append("probe_result", row)
            print(f"{case['id']}: price={price}; expected={case.get('expected_price')}", flush=True)
        atomic_json(writer.directory / "results.json", results)
        writer.manifest.update(model_calls=budget.calls, paid_calls=0, market_rounds=0)
        writer.finish("complete")
    except BaseException as exc:
        atomic_json(writer.directory / "partial-results.json", results)
        writer.finish(
            "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            {"type": type(exc).__name__},
        )
        raise


def main() -> None:
    """Run one explicitly selected stage into a new local diagnostics directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["baseline", "probes", "comprehension"])
    parser.add_argument("--id", required=True)
    args = parser.parse_args()
    root = project_root()
    cfg = load_config(root / "configs/local/local-calvano-core-qwen35.yaml")
    preflight(cfg, root)
    output = root / "runs/diagnostics" / safe_identifier(args.id)
    output.mkdir(parents=True, exist_ok=False)
    if args.stage == "baseline":
        baseline(root, cfg, output)
    else:
        probes(root, cfg, output, args.stage == "comprehension")
    print(output)


if __name__ == "__main__":
    main()
