"""Use synthetic independent blocks to check resolution, families and planning claims."""

import pytest

from auditors_paradox.evaluation.planning import power_scenario


def test_small_n_cannot_escape_holm_resolution():
    pilot = [[0, 0.1, 0.2, 0], [0, 0.1, 0.2, 0], [0, 0.1, 0.2, 0]]
    value = power_scenario(pilot, [0, 0.1, 0.2, 0], pairs=5, simulations=20)
    assert all(p == 0 for p in value["marginal_power"].values())
    assert value["joint_rejection_probability"] == 0


def test_compatible_arm_means_and_ties():
    pilot = [[0, 0, 0, 0]] * 3
    value = power_scenario(pilot, [0, 0.2, 0.4, 0], pairs=8, simulations=20)
    assert value["marginal_power"]["public-silent"] == 1
    assert value["marginal_power"]["public-private"] == 1
    assert value["marginal_power"]["placebo-silent"] == 0
    assert value["classification"].startswith("exploratory")
    null = power_scenario(pilot, [0, 0, 0, 0], pairs=8, simulations=20)
    assert not any(null["marginal_power"].values())


def test_planning_failure_sensitivity_and_financial_family():
    pilot = [[0, 0.1, 0.2, 0.3], [0.02, 0.09, 0.22, 0.28], [-0.02, 0.11, 0.18, 0.32]]
    value = power_scenario(
        pilot,
        [0, 0.1, 0.2, 0.3],
        pairs=8,
        simulations=20,
        variance_multiplier=2,
        failure_probability=0.9,
        family="sanctions_event",
    )
    assert "fine_only-silent" in value["marginal_power"]
    assert value["failure_probability"] == 0.9
    with pytest.raises(ValueError):
        power_scenario(pilot[:2], [0, 0.1, 0.2, 0.3], pairs=8, simulations=20)
    with pytest.raises(ValueError):
        power_scenario(pilot, [0, 0.1, 0.2, 0.3], pairs=8, simulations=20, variance_multiplier=0.5)
