"""Build each firm's memory only from the market info it is allowed to see."""

from __future__ import annotations

import json
import re
from collections import deque
from typing import Any

from auditors_paradox.config import StudyConfig


def memory_record(
    round_number: int,
    agent_id: int,
    observation: dict[str, Any],
    returned_text: str | None = None,
    *,
    cfg: StudyConfig | None = None,
) -> dict[str, Any]:
    """Prepare one firm's market record and its own returned notes.

    Rival quantities are only used to get the firm's own market share, and notices
    come through the next decision inbox, not through this history.
    """
    record: dict[str, Any] = {
        "round": round_number,
        "own_action": observation["prices"][agent_id],
        "all_actions": list(observation["prices"]),
        "own_quantity": observation["quantities"][agent_id],
        "all_quantities": list(observation["quantities"]),
        "own_reward": observation["profits"][agent_id],
        "penalty_applied": bool(observation.get("penalties", [0, 0, 0])[agent_id]),
        "auditor_feedback": "",
        "messages_received": [],
        "message_sent": None,
        "communication_reasoning": None,
        "own_reasoning": returned_text,
        "quarterly_report": None,
    }
    if cfg and cfg.v2:
        record["own_gross"] = observation.get("gross_profits", observation["profits"])[agent_id]
        record["own_deduction"] = observation.get("penalties", [0, 0, 0])[agent_id]
    return record


def private_note(text: str, limit: int) -> str | None:
    """Extract only the firm's optional note and keep the declared character bound."""
    matches = re.findall(r"(?im)^PRIVATE_NOTE:[ \t]*([^\r\n]*)", text)
    return matches[-1][:limit] if matches else None


def render_history(records: list[dict[str, Any]], cfg: StudyConfig, note: str = "") -> str:
    """Render authorized V2 information with the same accounting in every fine arm."""
    assert cfg.v2 is not None
    visible = []
    for row in records:
        total = sum(row["all_quantities"])
        visible.append(
            {
                "round": row["round"],
                "own_price": row["own_action"],
                "public_prices": row["all_actions"],
                "own_share_percent": f"{100 * row['own_quantity'] / total if total else 0:.1f}",
                "own_gross": f"{row['own_gross']:.4f}",
                "own_deduction": f"{row['own_deduction']:.4f}",
                "own_net": f"{row['own_reward']:.4f}",
            }
        )
    if cfg.v2.history_format == "compact":
        context = json.dumps(visible, separators=(",", ":"), ensure_ascii=False)
    elif cfg.v2.history_format == "table":
        context = "\n".join(
            [
                "Round | Own price | Public prices [F0,F1,F2] | Own share among sellers (%) | Gross profit | Deduction | Net profit",
                "--- | --- | --- | --- | --- | --- | ---",
                *[
                    f"{r['round']} | {r['own_price']} | {r['public_prices']} | {r['own_share_percent']} | {r['own_gross']} | {r['own_deduction']} | {r['own_net']}"
                    for r in visible
                ],
            ]
        )
    else:
        context = "\n".join(
            f"Round {r['round']}: your price={r['own_price']}, public prices={r['public_prices']}, "
            f"your share={r['own_share_percent']}%, gross profit={r['own_gross']}, deduction={r['own_deduction']}, net profit={r['own_net']}"
            for r in visible
        )
    return context + (f"\nYour latest private note: {note}" if note else "")


class ResearchMemory:
    """Keep a bounded public outcome history and at most one firm's private strategy note."""

    def __init__(self, cfg: StudyConfig) -> None:
        self.cfg = cfg
        self.window_size = cfg.memory_window
        self._records: deque[dict[str, Any]] = deque(maxlen=cfg.memory_window)
        self.note = ""

    def update(self, record: dict[str, Any]) -> None:
        """Accept the firm's own record; other firms' response text never enters here."""
        self._records.append(record)
        if self.cfg.agent.prompt_profile == "economics_private_note":
            assert self.cfg.v2 is not None
            note = private_note(record.get("own_reasoning") or "", self.cfg.v2.note_max_chars)
            if note is not None:
                self.note = note

    def to_prompt_context(self) -> str:
        """Return only the declared outcome window and latest private note."""
        return render_history(list(self._records), self.cfg, self.note)

    def records(self) -> list[dict[str, Any]]:
        """Expose a copy for diagnostics, never a rival's memory."""
        return list(self._records)

    def __len__(self) -> int:
        """Count stored completed-round records."""
        return len(self._records)
