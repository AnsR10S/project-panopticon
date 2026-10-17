\"\"\"Early in-memory key-value state store for tracking pricing round observations.\"\"\"

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class RoundObservation:
    round_number: int
    prices: dict[str, float]
    profits: dict[str, float]
    intervention: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryStore:
    records: list[RoundObservation] = field(default_factory=list)
    session_id: str = "default_session"

    def append(
        self,
        round_number: int,
        prices: dict[str, float],
        profits: dict[str, float],
        intervention: Optional[str] = None,
        **metadata: Any,
    ) -> None:
        obs = RoundObservation(
            round_number=round_number,
            prices=prices,
            profits=profits,
            intervention=intervention,
            metadata=metadata,
        )
        self.records.append(obs)

    def last(self, n: int = 5) -> list[RoundObservation]:
        return self.records[-n:]

    def get_price_history(self, firm: str) -> list[float]:
        return [r.prices.get(firm, 0.0) for r in self.records if firm in r.prices]

    def export_json(self, path: Path) -> None:
        data = [
            {
                "round": r.round_number,
                "prices": r.prices,
                "profits": r.profits,
                "intervention": r.intervention,
                "metadata": r.metadata,
            }
            for r in self.records
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def clear(self) -> None:
        self.records.clear()
