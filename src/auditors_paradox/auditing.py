"""The auditor, it applies the warning rule to the market outcome after each round."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from auditors_paradox.adapters.collusionlab import Benchmarks
from auditors_paradox.config import AuditorConfig
from auditors_paradox.contracts import AuditEvent, QualificationAssessment
from auditors_paradox.strategies import DETECTION_STRATEGIES


@dataclass
class BehavioralAuditor:
    """Keeps the rolling market history and the cooldown for each firm."""

    config: AuditorConfig
    benchmarks: Benchmarks
    history: deque[tuple[float, float]] = field(default_factory=deque)
    price_history: deque[list[int]] = field(default_factory=deque)
    last_flagged: list[int] = field(default_factory=lambda: [-1000000] * 3)
    v2: bool = False
    qualification_streaks: list[int] = field(default_factory=lambda: [0] * 3)

    def evaluate(self, round_number: int, actions: list[int], rewards: list[float]) -> AuditEvent:
        """Update the history, then flag firms using the selected rule and cooldown."""
        elevation = (sum(rewards) / len(rewards) - self.benchmarks.nash_reward) / (
            self.benchmarks.joint_reward - self.benchmarks.nash_reward
        )
        spread = float(max(actions) - min(actions))
        self.history.append((elevation, spread))
        self.price_history.append(list(actions))
        while len(self.history) > self.config.window:
            self.history.popleft()
            self.price_history.popleft()
        strategy = DETECTION_STRATEGIES[self.config.mode]
        qualifying_agents = strategy.qualifying(
            self.config, self.benchmarks, list(self.history), list(self.price_history)
        )
        flagged_agents = [
            i
            for i in qualifying_agents
            if round_number - self.last_flagged[i] > self.config.cooldown_rounds
        ]
        self.qualification_streaks = [
            self.qualification_streaks[i] + 1 if i in qualifying_agents else 0 for i in range(3)
        ]
        for i in flagged_agents:
            self.last_flagged[i] = round_number
        flagged = bool(flagged_agents)
        return AuditEvent(
            f"audit-r{round_number:04d}",
            round_number,
            strategy.identity,
            max(0.0, min(1.0, elevation)),
            self.config.elevation_threshold,
            flagged,
            {
                "reward_elevation": elevation,
                "price_spread": spread,
                "window_rounds": len(self.history),
                "enabled": self.config.enabled,
                "maximum_spread": self.config.maximum_spread,
                "flagged_agents": flagged_agents,
                "prices": list(actions),
                "auditor_mode": self.config.mode,
                "cooldown_rounds": self.config.cooldown_rounds,
                **(
                    {
                        "qualifying_agents": qualifying_agents,
                        "qualification_streaks": list(self.qualification_streaks),
                    }
                    if self.v2
                    else {}
                ),
            },
            (
                QualificationAssessment(
                    qualifying_agents, flagged_agents, list(self.qualification_streaks)
                )
                if self.v2
                else None
            ),
        )
