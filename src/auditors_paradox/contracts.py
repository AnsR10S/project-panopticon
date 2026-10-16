"""The shared event records and the stable JSON hashes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Arm = Literal[
    "silent",
    "private",
    "public",
    "placebo",
    "public_numeric",
    "public_fine",
    "private_fine",
    "fine_only",
]
ARMS: tuple[Arm, ...] = ("silent", "private", "public", "placebo")
EXTENDED_ARMS: tuple[Arm, ...] = (
    *ARMS,
    "public_numeric",
    "public_fine",
    "private_fine",
    "fine_only",
)
FINE_ARMS: tuple[Arm, ...] = ("fine_only", "private_fine", "public_fine")
LEGACY_ARMS: tuple[Arm, ...] = tuple(arm for arm in EXTENDED_ARMS if arm != "fine_only")


def canonical(value: Any) -> str:
    """Encode JSON with a stable key order so the same value always gives the same text."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def fingerprint(value: Any) -> str:
    """Hash a value using its canonical JSON."""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class QualificationAssessment:
    """Separate raw qualification and persistence from cooldown-eligible announcements."""

    qualifying_agents: list[int]
    announcement_agents: list[int]
    qualification_streaks: list[int]
    score_interpretation: str = "clipped_profit_elevation_not_probability"


@dataclass(frozen=True)
class AuditEvent:
    """Records the warning rule and its evidence, the score is not a collusion probability."""

    event_id: str
    source_round: int
    rule_version: str
    score: float
    threshold: float
    flagged: bool
    evidence: dict[str, Any]
    assessment: QualificationAssessment | None = None

    def to_dict(self) -> dict[str, Any]:
        """Turn the audit event into a JSON friendly dictionary."""
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class Delivery:
    """Records the notice for one recipient, or why no notice was sent."""

    delivery_id: str
    recipient: int
    source_round: int
    effective_round: int
    arm: Arm
    source_id: str
    status: str
    body: str
    header: str
    source_kind: str | None = None
    template_version: str | None = None
    intervention_id: str | None = None

    @property
    def text(self) -> str:
        """Header plus body, or empty text when there is no notice."""
        return f"{self.header}\n{self.body}" if self.body else ""

    def to_dict(self) -> dict[str, Any]:
        """Include the exact rendered notice in the delivery record."""
        return {**{k: v for k, v in asdict(self).items() if v is not None}, "text": self.text}


@dataclass(frozen=True)
class Completion:
    """One response, its token usage and the model the provider said it used."""

    text: str
    input_tokens: int
    output_tokens: int
    reported_model: str
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Token counts have to be nonnegative integers."""
        if any(
            type(value) is not int or value < 0 for value in (self.input_tokens, self.output_tokens)
        ):
            raise ValueError("Usage counts must be nonnegative integers")
