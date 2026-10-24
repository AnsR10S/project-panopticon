"""Select small, deterministic research algorithms through the frozen configuration."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from auditors_paradox.action_parsing import parser_identity
from auditors_paradox.config import AuditorConfig, StudyConfig


class PriceReferences(Protocol):
    """Only the economic reference prices are needed by price-window detection."""

    @property
    def nash_price(self) -> int: ...

    @property
    def joint_price(self) -> int: ...


class DetectionStrategy(Protocol):
    """Qualification is separate from announcement cooldown under every detector."""

    identity: str

    def qualifying(
        self,
        config: AuditorConfig,
        references: PriceReferences,
        history: Sequence[tuple[float, float]],
        prices: Sequence[list[int]],
    ) -> list[int]: ...


class MarketWindowStrategy:
    """The original market-wide, consecutive elevation and spread rule."""

    identity = "behavior-window-v1"

    def qualifying(
        self,
        config: AuditorConfig,
        references: PriceReferences,
        history: Sequence[tuple[float, float]],
        prices: Sequence[list[int]],
    ) -> list[int]:
        return (
            list(range(3))
            if config.enabled
            and len(history) == config.window
            and all(
                level > config.elevation_threshold and width <= config.maximum_spread
                for level, width in history
            )
            else []
        )


class PerFirmWindowStrategy:
    """The original per-firm mean-price rule; spread does not enter this algorithm."""

    identity = "per-firm-price-window-v1"

    def qualifying(
        self,
        config: AuditorConfig,
        references: PriceReferences,
        history: Sequence[tuple[float, float]],
        prices: Sequence[list[int]],
    ) -> list[int]:
        cutoff = references.nash_price + config.elevation_threshold * (
            references.joint_price - references.nash_price
        )
        return [
            i
            for i in range(3)
            if config.enabled
            and len(prices) == config.window
            and sum(row[i] for row in prices) / config.window > cutoff
        ]


DETECTION_STRATEGIES: dict[str, DetectionStrategy] = {
    "market": MarketWindowStrategy(),
    "per_firm": PerFirmWindowStrategy(),
}


class SanctionStrategy(Protocol):
    """Financial timing is independent of how a notice is rendered or suppressed."""

    identity: str

    def eligible(self, firm: int, qualifying: list[int], flagged: list[int]) -> bool: ...


class EventSanctionStrategy:
    """The historical event timing: cooldown-eligible flags also control deductions."""

    identity = "event"

    def eligible(self, firm: int, qualifying: list[int], flagged: list[int]) -> bool:
        return firm in flagged


class ContinuingSanctionStrategy:
    """Deduct on each qualified assessment while announcements retain their cooldown."""

    identity = "continuing"

    def eligible(self, firm: int, qualifying: list[int], flagged: list[int]) -> bool:
        return firm in qualifying


SANCTION_STRATEGIES: dict[str, SanctionStrategy] = {
    "event": EventSanctionStrategy(),
    "continuing": ContinuingSanctionStrategy(),
}


def algorithm_selection(cfg: StudyConfig) -> dict[str, Any]:
    """Expose the chosen algorithms and historical alternatives in every preview."""
    return {
        "schema_version": cfg.schema_version,
        "action_parser": parser_identity(cfg.agent.action_parser, cfg.agent.model),
        "detector": DETECTION_STRATEGIES[cfg.auditor.mode].identity,
        "sanction_timing": SANCTION_STRATEGIES[
            cfg.v2.sanction_timing if cfg.v2 else "event"
        ].identity,
        "sanction_rate": (
            "persistence_tiers" if cfg.v2 and cfg.v2.graduated else "legacy_fixed_fraction"
        ),
        "sanction_disclosure": cfg.v2.sanction_disclosure if cfg.v2 else "legacy_v1",
        "memory": "authorized_outcomes_v2" if cfg.v2 else "upstream_rolling_v1",
        "warning_template": cfg.v2.warning_template if cfg.v2 else "warning",
        "placebo_template": cfg.v2.placebo_template if cfg.v2 else "neutral",
        "switches": "auditor.mode; v2.sanction_timing; v2.graduated; v2.warning_template; v2.placebo_template; schema_version with matching v2 group",
    }
