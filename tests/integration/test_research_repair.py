"""Check diagnosis-driven prompt, exposure, inference and information boundaries."""

import json

import pytest

from auditors_paradox.batch import run_sweep
from auditors_paradox.config import StudyConfig, load_config
from auditors_paradox.contracts import Completion, fingerprint
from auditors_paradox.evaluation.inference import analyze_sweep, holm_adjust, paired_estimate
from auditors_paradox.evaluation.validate import check_events, validate_run
from auditors_paradox.feedback.schedules import randomized_schedule
from auditors_paradox.runner import run_experiment


@pytest.mark.parametrize("profile", ["price_first", "explicit_economics"])
def test_new_profiles_and_truthful_scheduled_notices(root, tmp_path, profile):
    """Scheduled diagnostics deliver neutral notices next round with verifiable prompts."""
    cfg = load_config(root / "configs/controls/calvano-coordinated.yaml")
    data = cfg.model_dump()
    data["agent"]["prompt_profile"] = profile
    data["feedback"].update(warning_source="fixed", schedule_rounds=[2, 4])
    data["batch_arms"] = ["private", "public"]
    data["arm"] = "public"
    cfg = StudyConfig.model_validate(data)
    path = run_experiment(cfg, root, run_id=profile, output_root=tmp_path)
    assert validate_run(path)["valid"]
    events = [json.loads(line) for line in path.with_name("events.jsonl").read_text().splitlines()]
    consumed = [r["payload"]["round"] for r in events if r["type"] == "delivery_consumed"]
    assert set(consumed) == {3, 5}
    calls = [r["payload"] for r in events if r["type"] == "model_call_completed"]
    assert all("first line" in c["messages"][1]["content"] for c in calls)
    assert not any("identified a sustained pattern" in str(c["messages"]) for c in calls)
    assert any("scheduled monitoring bulletin" in str(c["messages"]) for c in calls)


def test_reconstructed_history_rejects_rehashed_private_information(root, config, tmp_path):
    """A matching forged prompt and memory cannot smuggle rival-profit or future information."""
    path = run_experiment(config, root, run_id="memory", output_root=tmp_path)
    manifest = json.loads(path.read_text())
    events = [json.loads(line) for line in path.with_name("events.jsonl").read_text().splitlines()]
    observation = next(r["payload"] for r in events if r["type"] == "observation")
    old = observation["memory_context"]
    observation["memory_context"] += "\nRival profit: private secret. Next rival action: 90."
    call = next(r["payload"] for r in events if r["type"] == "model_call_completed")
    call["messages"][1]["content"] = call["messages"][1]["content"].replace(
        old, observation["memory_context"]
    )
    previous = "0" * 64
    for event in events:
        event["previous_hash"] = previous
        event["record_hash"] = fingerprint({k: v for k, v in event.items() if k != "record_hash"})
        previous = event["record_hash"]
    assert any("unauthorized" in error for error in check_events(events, manifest))


def test_private_notes_and_twenty_round_window_stay_with_own_firm(root, config, tmp_path):
    """Track unique private notes across firms and beyond the full rolling memory window."""
    captured = {firm: [] for firm in range(3)}

    class Notes:
        def __init__(self, firm):
            self.firm = firm

        def complete(self, messages):
            captured[self.firm].append(json.loads(json.dumps(messages)))
            decision = len(captured[self.firm])
            return Completion(
                f"PRICE: {60 + 10 * self.firm}\nPRIVATE_FIRM_{self.firm}_TURN_{decision}_END",
                20,
                10,
                "scripted-notes",
            )

    data = config.model_dump()
    data.update(rounds=26, memory_window=20, disclose_horizon=False)
    cfg = StudyConfig.model_validate(data)
    path = run_experiment(
        cfg,
        root,
        output_root=tmp_path,
        run_id="private-notes-26",
        backend_factory=lambda config, market, firm: Notes(firm),
    )
    assert validate_run(path)["valid"]
    for firm, calls in captured.items():
        for turn, messages in enumerate(calls, 1):
            text = json.dumps(messages)
            assert f"PRIVATE_FIRM_{firm}_TURN_{turn}_END" not in text
            assert all(f"PRIVATE_FIRM_{rival}_" not in text for rival in range(3) if rival != firm)
            assert "across 26 rounds" not in text and " of 26" not in text
            if turn > 1:
                assert f"PRIVATE_FIRM_{firm}_TURN_{turn - 1}_END" in text
        last = json.dumps(calls[-1])
        assert "Round 6:" in last and "Round 5:" not in last
        assert f"PRIVATE_FIRM_{firm}_TURN_5_END" not in last
        assert f"PRIVATE_FIRM_{firm}_TURN_6_END" in last


def test_schedule_is_preassigned_nonterminal_and_outcome_independent():
    """Random source rounds need no prices and never schedule an unconsumable final notice."""
    schedule = randomized_schedule(12, 3, 7201)
    assert schedule == randomized_schedule(12, 3, 7201)
    assert len(set(schedule)) == 3 and all(1 <= r < 12 for r in schedule)
    with pytest.raises(ValueError):
        randomized_schedule(6, 6, 1)


def test_holm_family_keeps_unavailable_tests_and_intervals_do_not_fake_precision():
    """Multiplicity retains missing comparisons and tiny constant samples have no bootstrap band."""
    result = holm_adjust({"a": 0.01, "b": 0.04, "c": 0.03, "d": None})
    assert result == {"a": 0.04, "c": 0.09, "b": 0.09, "d": None}
    assert paired_estimate([1, 1, 1])["bootstrap_95_interval"] is None
    assert paired_estimate(list(range(12)))["bootstrap_95_interval"] is not None
    with pytest.raises(ValueError):
        holm_adjust({"bad": -0.1})
    with pytest.raises(ValueError):
        paired_estimate([float("nan")])


def test_exposure_and_simultaneous_bounds_are_exported(root, config, tmp_path):
    """The report distinguishes warning exposure from assignment and protects the core family."""
    path = run_sweep(config, root, replicates=1, sweep_id="family", output_root=tmp_path)
    summary = json.loads(analyze_sweep(path, root, tmp_path / "analysis").read_text())
    family = summary["central_family"]
    assert len(family["comparisons"]) == 4
    for contrast in family["comparisons"].values():
        assert contrast["holm_p"] is None
        assert contrast["simultaneous_95_bounded_interval"] == family["feasible_difference_bounds"]
        assert contrast["assigned_blocks"] == 1
        assert contrast["missing_complete_pairs"] == 0
        assert contrast["comparison_assigned"]
    assert (tmp_path / "analysis/rounds.csv").is_file()
    assert (tmp_path / "analysis/trajectories.png").is_file()


def test_unassigned_comparison_is_not_a_missing_run(root, config, tmp_path):
    """Leaving Placebo out of a delivery diagnostic is different from losing an assigned run."""
    data = config.model_dump()
    data["batch_arms"] = ["silent", "private", "public"]
    cfg = StudyConfig.model_validate(data)
    path = run_sweep(cfg, root, replicates=1, sweep_id="neutral", output_root=tmp_path)
    summary = json.loads(analyze_sweep(path, root, tmp_path / "report").read_text())
    comparison = summary["central_family"]["comparisons"]["placebo_minus_silent"]
    assert comparison["status"] == "not_assigned"
    assert comparison["assigned_blocks"] == comparison["missing_complete_pairs"] == 0
    with pytest.raises(ValueError, match="Calvano"):
        StudyConfig.model_validate(
            {
                **config.model_dump(),
                "agent": {**config.agent.model_dump(), "prompt_profile": "explicit_economics"},
            }
        )


@pytest.mark.parametrize("field,value", [("seed", 7654), ("arm", "public_numeric")])
def test_preserved_assignment_cannot_change_while_run_ids_stay_fixed(
    root, config, tmp_path, field, value
):
    """Keeping run IDs alone is not enough to preserve the original randomized assignment."""
    path = run_sweep(config, root, replicates=1, sweep_id="identity", output_root=tmp_path)
    assignment_path = path.with_name("assignment.json")
    assignment = json.loads(assignment_path.read_text())
    assignment[0][field] = value
    assignment_path.write_text(json.dumps(assignment))
    with pytest.raises(ValueError, match="full assignment tuples"):
        analyze_sweep(path, root, tmp_path / "analysis")
