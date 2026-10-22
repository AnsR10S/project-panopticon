"""Identify the fixed notice schedules separately from how the market behaves."""

from __future__ import annotations

import random

from auditors_paradox.contracts import fingerprint


def schedule_hash(rounds: list[int]) -> str:
    """Hash the source rounds together with the one round delivery delay."""
    return fingerprint({"source_rounds": rounds, "effective_delay": 1})


def randomized_schedule(rounds: int, count: int, seed: int) -> list[int]:
    """Choose source rounds before play, independently of prices and with no final-round notice."""
    if not 0 <= count < rounds:
        raise ValueError("Notice count must fit the nonterminal source rounds")
    return sorted(random.Random(seed).sample(range(1, rounds), count))
