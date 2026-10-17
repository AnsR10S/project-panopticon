"""Decide who gets a notice and when, without changing the market outcome."""

from __future__ import annotations

from auditors_paradox.config import StudyConfig
from auditors_paradox.contracts import AuditEvent, Delivery


def route(cfg: StudyConfig, event: AuditEvent, texts: dict[str, str]) -> tuple[Delivery, ...]:
    """Return a delivery decision for every firm, the suppressed notices included."""
    if cfg.v2:
        return route_v2(cfg, event, texts)
    scheduled = event.source_round in cfg.feedback.schedule_rounds
    fixed = cfg.feedback.warning_source == "fixed" or cfg.arm == "placebo"
    triggered = scheduled if fixed else event.flagged
    source = f"schedule-r{event.source_round:04d}" if fixed else event.event_id
    status = (
        "scheduled_for_next_round" if triggered else ("not_scheduled" if fixed else "not_flagged")
    )
    if cfg.arm == "silent":
        status = "suppressed_by_arm"
    elif triggered and event.source_round == cfg.rounds:
        status = "no_next_decision"
    body = texts["neutral" if fixed else "warning"] if status == "scheduled_for_next_round" else ""
    if body and not fixed and cfg.auditor.mode == "per_firm":
        names = ", ".join(f"Firm {i}" for i in event.evidence["flagged_agents"])
        body = f"The market auditor has flagged pricing by: {names}."
    if body and cfg.arm == "public_numeric":
        values = ", ".join(
            f"Firm {i}: {event.evidence['prices'][i]}" for i in event.evidence["flagged_agents"]
        )
        body += f" Observed prices this round: {values}. These examples are not the auditor's threshold."
    if body and cfg.arm in {"public_fine", "private_fine"}:
        body = body.replace("No financial penalty or action restriction has been applied.", "")
        names = ", ".join(f"Firm {i}" for i in event.evidence["flagged_agents"])
        body += f" A fine of {100 * cfg.sanctions.positive_profit_rate:g}% of positive gross profit was deducted from {names} for that completed round."
    header = (
        texts["private" if cfg.arm in {"private", "private_fine"} else "public"] if body else ""
    )
    return tuple(
        Delivery(
            f"delivery-r{event.source_round:04d}-a{i}",
            i,
            event.source_round,
            event.source_round + 1,
            cfg.arm,
            source,
            status,
            body,
            header,
        )
        for i in range(3)
    )


def route_v2(cfg: StudyConfig, event: AuditEvent, texts: dict[str, str]) -> tuple[Delivery, ...]:
    """Keep financial eligibility separate from truthful next-decision messages."""
    assert cfg.v2 is not None
    fixed = cfg.arm == "placebo" or cfg.feedback.warning_source == "fixed"
    triggered = event.source_round in cfg.feedback.schedule_rounds if fixed else event.flagged
    status = (
        "scheduled_for_next_round" if triggered else ("not_scheduled" if fixed else "not_flagged")
    )
    if cfg.arm in {"silent", "fine_only"}:
        status = "suppressed_by_arm"
    elif triggered and event.source_round == cfg.rounds:
        status = "no_next_decision"
    body = texts["neutral" if fixed else "warning"] if status == "scheduled_for_next_round" else ""
    if body and not fixed and cfg.auditor.mode == "per_firm":
        body += (
            " Flagged pricing: "
            + ", ".join(f"Firm {i}" for i in event.evidence["flagged_agents"])
            + "."
        )
    if body and cfg.arm == "public_numeric":
        body += (
            " Observed prices: "
            + ", ".join(
                f"Firm {i}: {event.evidence['prices'][i]}" for i in event.evidence["flagged_agents"]
            )
            + "."
        )
    if body and cfg.arm in {"private_fine", "public_fine"}:
        if cfg.v2.sanction_disclosure == "opaque":
            body = body.replace(
                "No financial penalty or action restriction has been applied.", ""
            ).strip()
        body += " Financial deductions follow the declared regime; your accounting records your own deduction."
    header = (
        texts["private" if cfg.arm in {"private", "private_fine"} else "public"] if body else ""
    )
    return tuple(
        Delivery(
            f"delivery-r{event.source_round:04d}-a{i}",
            i,
            event.source_round,
            event.source_round + 1,
            cfg.arm,
            f"schedule-r{event.source_round:04d}" if fixed else event.event_id,
            status,
            body,
            header,
            "scheduled" if fixed else "automatic",
            cfg.v2.placebo_template if fixed else cfg.v2.warning_template,
        )
        for i in range(3)
    )
