"""Rebuild the market equations and check Calvano references on the actual grid."""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from auditors_paradox.config import MarketConfig


def quantities(market: MarketConfig, prices: list[int]) -> list[float]:
    """Calculate quantities from the saved market settings."""
    if market.demand_model == "linear_differentiated":
        return [
            max(0.0, market.a - market.b * p + market.d * (sum(prices) - p) / 2) for p in prices
        ]
    exponents = [(market.a - p) / market.mu for p in prices] + [market.a_0 / market.mu]
    maximum = max(exponents)
    terms = [math.exp(value - maximum) for value in exponents]
    return [value / sum(terms) for value in terms[:3]]


def rewards(market: MarketConfig, prices: list[int]) -> list[float]:
    """Keep prices in ticks and report profits in currency units."""
    return [
        (p - market.c) * q / market.price_scale
        for p, q in zip(prices, quantities(market, prices), strict=True)
    ]


@lru_cache(maxsize=16)
def calvano_references(market_json: str) -> tuple[int, int, float, float]:
    """Check every unilateral deviation and every three-firm grid profile."""
    market = MarketConfig.model_validate_json(market_json)
    grid = list(range(market.price_min, market.price_max + 1))
    equilibria = [
        p
        for p in grid
        if max(rewards(market, [own, p, p])[0] for own in grid)
        <= rewards(market, [p, p, p])[0] + 1e-10
    ]
    if len(equilibria) != 1:
        raise ValueError("Calvano grid needs one verified symmetric Nash reference")
    nash = equilibria[0]
    joint = max(grid, key=lambda p: sum(rewards(market, [p, p, p])))
    nash_reward, joint_reward = rewards(market, [nash] * 3)[0], rewards(market, [joint] * 3)[0]
    if not market.price_min < nash < joint < market.price_max or joint_reward <= nash_reward:
        raise ValueError("Calvano references must be distinct and unclipped inside the grid")
    p1, p2 = np.meshgrid(np.asarray(grid, dtype=float), np.asarray(grid, dtype=float))
    maximum_joint = -math.inf
    for p0 in grid:
        logits = np.stack(
            [
                np.full_like(p1, (market.a - p0) / market.mu),
                (market.a - p1) / market.mu,
                (market.a - p2) / market.mu,
                np.full_like(p1, market.a_0 / market.mu),
            ]
        )
        weights = np.exp(logits - logits.max(axis=0))
        profits = (
            (
                (p0 - market.c) * weights[0]
                + (p1 - market.c) * weights[1]
                + (p2 - market.c) * weights[2]
            )
            / weights.sum(axis=0)
            / market.price_scale
        )
        maximum_joint = max(maximum_joint, float(profits.max()))
    if maximum_joint > 3 * joint_reward + 1e-10:
        raise ValueError("Calvano symmetric joint reference is not the grid joint optimum")
    return nash, joint, nash_reward, joint_reward
