"""Exploratory power scenarios resample whole independent development blocks."""

from __future__ import annotations

from itertools import product
from typing import Any

import numpy as np


def power_scenario(
    pilot_blocks: list[list[float]],
    arm_means: list[float],
    *,
    pairs: int,
    simulations: int = 1000,
    seed: int = 7021,
    variance_multiplier: float = 1.0,
    failure_probability: float = 0.0,
    alpha: float = 0.05,
    family: str = "warning",
) -> dict[str, Any]:
    """Estimate marginal and joint rejection under declared, compatible arm means."""
    pilot = np.asarray(pilot_blocks, dtype=float)
    means = np.asarray(arm_means, dtype=float)
    if (
        pilot.ndim != 2
        or pilot.shape[1] != 4
        or len(pilot) < 3
        or means.shape != (4,)
        or not np.isfinite(pilot).all()
        or not np.isfinite(means).all()
    ):
        raise ValueError(
            "Supply at least three complete independent four-arm blocks and four finite scenario means"
        )
    if (
        not 2 <= pairs <= 64
        or not 20 <= simulations <= 10000
        or variance_multiplier < 1
        or not 0 <= failure_probability < 1
        or not 0 < alpha < 1
    ):
        raise ValueError(
            "Use bounded planning settings, variance multiplier >=1 and valid error/failure probabilities"
        )
    if family not in {"warning", "sanctions_event", "sanctions_continuing"}:
        raise ValueError("Unknown prospective hypothesis family")
    rng = np.random.default_rng(seed)
    residuals = (pilot - pilot.mean(axis=0)) * np.sqrt(variance_multiplier)
    sample = residuals[rng.integers(len(pilot), size=(simulations, pairs))] + means
    specifications = (
        [(2, 0), (1, 0), (2, 1), (3, 0)]
        if family == "warning"
        else [(1, 0), (2, 1), (3, 1), (3, 2)]
    )
    labels = (
        ["public-silent", "private-silent", "public-private", "placebo-silent"]
        if family == "warning"
        else [
            "fine_only-silent",
            "private_fine-fine_only",
            "public_fine-fine_only",
            "public_fine-private_fine",
        ]
    )
    present = rng.random((simulations, pairs, 4)) >= failure_probability
    pvalues = np.ones((simulations, 4))
    exact_patterns: dict[int, Any] = {}
    for k, (a, b) in enumerate(specifications):
        for trial in range(simulations):
            differences = (sample[trial, :, a] - sample[trial, :, b])[
                present[trial, :, a] & present[trial, :, b]
            ]
            n = len(differences)
            if n < 2:
                continue
            if n <= 16:
                if n not in exact_patterns:
                    exact_patterns[n] = np.asarray(list(product((-1, 1), repeat=n)))
                signs = exact_patterns[n]
            else:
                signs = rng.choice((-1, 1), size=(20000, n))
            extreme = int(
                np.count_nonzero(np.abs(signs @ differences / n) >= abs(differences.mean()) - 1e-12)
            )
            pvalues[trial, k] = (
                extreme / len(signs) if n <= 16 else (extreme + 1) / (len(signs) + 1)
            )
    rejected = np.zeros_like(pvalues, dtype=bool)
    for trial in range(simulations):
        order = np.argsort(pvalues[trial])
        for rank, k in enumerate(order):
            if pvalues[trial, k] > alpha / (4 - rank):
                break
            rejected[trial, k] = True
    power = rejected.mean(axis=0)
    return {
        "classification": "exploratory_planning_not_confirmatory_evidence",
        "family": family,
        "arm_order": (
            ["silent", "private", "public", "placebo"]
            if family == "warning"
            else ["silent", "fine_only", "private_fine", "public_fine"]
        ),
        "arm_means": arm_means,
        "pairs": pairs,
        "simulations": simulations,
        "seed": seed,
        "pilot_blocks": len(pilot),
        "variance_multiplier": variance_multiplier,
        "failure_probability": failure_probability,
        "marginal_power": dict(zip(labels, power.tolist(), strict=True)),
        "monte_carlo_se": dict(
            zip(labels, np.sqrt(power * (1 - power) / simulations).tolist(), strict=True)
        ),
        "joint_rejection_probability": float(rejected.all(axis=1).mean()),
        "assumptions": "Independent development blocks; empirical residual resampling; random missingness. Informative failure needs separate worst-case bounds. Small pilot distributions can seriously misstate power. Sign-exchangeability assumptions still apply.",
        "test": "Two-sided paired sign flips, exact <=16 pairs; otherwise 20,000 Monte Carlo flips with plus-one; Holm across four slots",
    }
