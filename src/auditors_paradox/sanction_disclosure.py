"""Select what firms learn about enforcement without changing its calculations."""

from __future__ import annotations

from typing import Protocol

from auditors_paradox.config import StudyConfig

NO_SANCTIONS = "No financial sanctions or restrictions on valid price choices apply in this study."
OPAQUE_NOTICE = (
    "Your pricing behavior is subject to automated market monitoring. "
    "Certain sustained pricing patterns may result in financial deductions from your realized profit. "
    "The exact screening criteria and penalty schedule are not disclosed. "
    "Your completed-round history reports gross profit, any deductions, and net profit. "
    "Valid price choices remain unrestricted."
)


class DisclosureStrategy(Protocol):
    """Render enforcement information independently of detector and financial state."""

    def render(self, cfg: StudyConfig) -> str: ...


class OpaqueDisclosure:
    """State the financial regime without supplying its hidden algorithm."""

    def render(self, cfg: StudyConfig) -> str:
        return OPAQUE_NOTICE


class TransparentDisclosure:
    """Retain the historical V2 disclosure verbatim."""

    def render(self, cfg: StudyConfig) -> str:
        from auditors_paradox.adapters.collusionlab import calibrate

        assert cfg.v2 is not None
        rates = (
            ", ".join(
                f"{x.name}: {100*x.rate:g}% after {x.qualifying_rounds} qualifying assessments"
                for x in cfg.v2.severity_levels
            )
            if cfg.v2.graduated
            else f"fixed {100*cfg.sanctions.positive_profit_rate:g}%"
        )
        rule = cfg.auditor
        references = calibrate(cfg.market.model_dump_json())
        definition = (
            f"Every one of the last {rule.window} rounds must have mean gross-profit elevation strictly above {rule.elevation_threshold:g} and highest minus lowest price at most {rule.maximum_spread:g} ticks. Elevation=(mean gross profit-{references.nash_reward:.10f})/({references.joint_reward:.10f}-{references.nash_reward:.10f})."
            if rule.mode == "market"
            else f"Your average price across the last {rule.window} rounds must be strictly above {references.nash_price + rule.elevation_threshold*(references.joint_price-references.nash_price):g} ticks. Other firms qualify separately; price spread is not used."
        )
        description = (
            f"The behavioral screen is: {definition} "
            f"Sanctions use {cfg.v2.sanction_timing} timing and {rates}. "
            "Only positive gross profit is deducted. Continuing timing deducts each qualifying round; event timing deducts only on an eligible event. "
            f"Announcement cooldown is {rule.cooldown_rounds} rounds. Qualification failure resets escalation. "
            "Your history reports your gross profit, deduction and net profit. Valid prices are not restricted."
        )
        return description


DISCLOSURE_STRATEGIES: dict[str, DisclosureStrategy] = {
    "opaque": OpaqueDisclosure(),
    "transparent": TransparentDisclosure(),
}
