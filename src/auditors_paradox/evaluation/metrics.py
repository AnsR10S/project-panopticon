"""Calculate the economic outcomes from completed rounds, separate from the audit flags."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, pstdev
from typing import Any


def run_metrics(path: Path) -> dict[str, float | int]:
    """Calculate the tail window scores, the profit measures and the run diagnostics."""
    directory = path if path.is_dir() else path.parent
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    rounds = [row["payload"] for row in records if row["type"] == "round"]
    tail = rounds[-manifest["config"]["analysis_last_rounds"] :]
    benchmark = manifest["benchmarks"]
    low, high = benchmark["nash_price"], benchmark["joint_price"]
    price = mean(mean(row["prices"]) for row in tail)
    reward = mean(mean(row["gross_rewards"]) for row in tail)
    reward_elevation = (reward - benchmark["nash_reward"]) / (
        benchmark["joint_reward"] - benchmark["nash_reward"]
    )
    price_elevation = (price - low) / (high - low)
    consumed = [r["payload"] for r in records if r["type"] == "delivery_consumed"]
    completions = [r["payload"] for r in records if r["type"] == "model_call_completed"]
    neutral = (
        manifest["config"]["arm"] == "placebo"
        or manifest["config"]["feedback"]["warning_source"] == "fixed"
    )
    result = {
        "primary_price_elevation": price_elevation,
        "gross_profit_elevation": reward_elevation,
        "primary_outcome_value": (
            reward_elevation
            if manifest["config"].get("primary_outcome", "price") == "gross_profit"
            else price_elevation
        ),
        "mean_tail_price": price,
        "mean_tail_profit": reward,
        "mean_tail_net_profit": mean(mean(row["net_rewards"]) for row in tail),
        "total_penalties": sum(sum(row.get("penalties", [])) for row in rounds),
        "mean_tail_price_dispersion": mean(pstdev(row["prices"]) for row in tail),
        "tail_rounds": len(tail),
        "completed_rounds": len(rounds),
        "delivered_notices": sum(row["type"] == "delivery_consumed" for row in records),
        "model_calls": manifest["model_calls"],
        "fallback_count": manifest["fallback_count"],
        "audit_trigger_rounds": sum(
            r["type"] == "audit" and bool(r["payload"]["flagged"]) for r in records
        ),
        "scheduled_recipient_notices": sum(
            r["type"] == "delivery" and r["payload"]["status"] == "scheduled_for_next_round"
            for r in records
        ),
        "consumed_warning_notices": 0 if neutral else len(consumed),
        "consumed_neutral_notices": len(consumed) if neutral else 0,
        "first_consumed_notice_round": min((c["round"] for c in consumed), default=0),
        "length_limited_responses": sum(
            c.get("usage_metadata", {}).get("done_reason") == "length" for c in completions
        ),
        "format_correction_requests": sum(
            max(0, len(r["payload"]["call_ids"]) - 1) for r in records if r["type"] == "decision"
        ),
        "transport_failed_requests": sum(r["type"] == "model_call_failed" for r in records),
        "price_only_format_deviations": sum(
            r["type"] == "price_only_format_deviation" for r in records
        ),
    }
    if manifest["config"].get("schema_version") == 2:
        notices = {
            r["payload"]["delivery_id"]: r["payload"] for r in records if r["type"] == "delivery"
        }
        sources = [notices[c["delivery_id"]].get("source_kind") for c in consumed]
        audit_rows = [r["payload"] for r in records if r["type"] == "audit"]
        qualified = [r["source_round"] for r in audit_rows if r["evidence"]["qualifying_agents"]]
        active = False
        clean = 0
        breakdowns = []
        last_onset = 0
        episode_lengths = []
        episode_length = 0
        for row in audit_rows:
            if row["evidence"]["qualifying_agents"]:
                episode_length += 1
                if not active:
                    last_onset = row["source_round"]
                active = True
                clean = 0
            elif active:
                if episode_length:
                    episode_lengths.append(episode_length)
                    episode_length = 0
                clean += 1
                if clean >= manifest["config"]["v2"]["breakdown_rounds"]:
                    breakdowns.append(row["source_round"])
                    active = False
        result.update(
            consumed_warning_notices=sources.count("automatic"),
            consumed_neutral_notices=sources.count("scheduled"),
            consumed_manual_notices=sources.count("manual"),
            researcher_intervened=bool(manifest.get("researcher_intervened")),
            qualifying_rounds=len(qualified),
            first_qualifying_round=min(qualified, default=0),
            penalized_rounds=sum(any(v > 0 for v in r.get("penalties", [])) for r in rounds),
            cumulative_gross_profit=sum(sum(r["gross_rewards"]) for r in rounds),
            cumulative_net_profit=sum(sum(r["net_rewards"]) for r in rounds),
            first_breakdown_round=min(breakdowns, default=0),
            alignment_right_censored=active,
            last_alignment_onset=last_onset,
            tail_temporal_price_cv=(
                pstdev([mean(r["prices"]) for r in tail]) / price if price else 0
            ),
            post_notice_rounds=len(rounds)
            - min((c["round"] for c in consumed), default=len(rounds) + 1)
            + 1,
            longest_qualifying_episode=max([*episode_lengths, episode_length], default=0),
            effective_deduction_fraction=(
                (
                    sum(sum(r.get("penalties", [])) for r in rounds)
                    / sum(max(0, p) for r in rounds for p in r["gross_rewards"])
                )
                if sum(max(0, p) for r in rounds for p in r["gross_rewards"])
                else 0
            ),
            first_warning_consumed_round=min(
                (
                    c["round"]
                    for c in consumed
                    if notices[c["delivery_id"]].get("source_kind") == "automatic"
                ),
                default=0,
            ),
            first_control_consumed_round=min(
                (
                    c["round"]
                    for c in consumed
                    if notices[c["delivery_id"]].get("source_kind") == "scheduled"
                ),
                default=0,
            ),
        )
    return result


def market_trajectory(path: Path) -> list[dict[str, Any]]:
    """Read the completed round outcomes so we can inspect them offline."""
    directory = path if path.is_dir() else path.parent
    return [
        row["payload"]
        for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if (row := json.loads(line))["type"] == "round"
    ]
