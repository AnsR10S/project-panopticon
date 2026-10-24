"""Calculate declared deductions independently from notice delivery."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from auditors_paradox.config import StudyConfig
from auditors_paradox.contracts import FINE_ARMS, AuditEvent
from auditors_paradox.strategies import SANCTION_STRATEGIES


@dataclass(frozen=True)
class PenaltyAssessment:
    """One firm's explainable financial assessment for a completed round."""

    round: int
    agent_id: int
    audit_id: str
    timing: str
    qualifying: bool
    persistence: int
    severity: str
    rate: float
    eligible: bool
    gross_profit: float
    deduction: float
    net_profit: float

    def to_dict(self) -> dict[str, Any]:
        """Keep the financial calculation in the evidence."""
        return asdict(self)


def assess_sanctions(
    cfg: StudyConfig, event: AuditEvent, gross: list[float]
) -> list[PenaltyAssessment]:
    """Use raw qualification for continuing fines and cooled events for event fines."""
    flagged = event.evidence["flagged_agents"]
    qualifying = event.evidence.get("qualifying_agents", flagged)
    streaks = event.evidence.get("qualification_streaks", [int(i in qualifying) for i in range(3)])
    timing = cfg.v2.sanction_timing if cfg.v2 else "event"
    strategy = SANCTION_STRATEGIES[timing]
    results = []
    for i, profit in enumerate(gross):
        severity, rate = "fixed", cfg.sanctions.positive_profit_rate
        if cfg.v2 and cfg.v2.graduated:
            selected = [x for x in cfg.v2.severity_levels if x.qualifying_rounds <= streaks[i]]
            severity, rate = (selected[-1].name, selected[-1].rate) if selected else ("none", 0.0)
        eligible = cfg.arm in FINE_ARMS and strategy.eligible(i, qualifying, flagged)
        deduction = max(0.0, profit) * rate if eligible else 0.0
        results.append(
            PenaltyAssessment(
                event.source_round,
                i,
                event.event_id,
                timing,
                i in qualifying,
                streaks[i],
                severity,
                rate,
                eligible,
                profit,
                deduction,
                profit - deduction,
            )
        )
    return results
