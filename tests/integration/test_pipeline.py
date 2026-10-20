"""Here we test the round loop with the real upstream agents, memory and market."""

import json

import pytest

from auditors_paradox.config import StudyConfig
from auditors_paradox.evaluation.metrics import run_metrics
from auditors_paradox.evaluation.validate import semantic_fingerprint, validate_run
from auditors_paradox.evidence import file_hash
from auditors_paradox.runner import RunFailed, run_experiment


def change(config, **values):
    """Copy the config with some fields changed and validate it again."""
    return StudyConfig.model_validate({**config.model_dump(mode="json"), **values})


def records(path):
    """Read all the events saved next to a run manifest."""
    return [
        json.loads(x)
        for x in path.with_name("events.jsonl").read_text(encoding="utf-8").splitlines()
    ]


@pytest.mark.parametrize(
    "arm,notices", [("silent", 0), ("private", 12), ("public", 12), ("placebo", 6)]
)
def test_BR_004_005_006_actual_arm_timing_and_recipients(root, config, tmp_path, arm, notices):
    """Each arm sends its notices at the right time and only to the right firms."""
    path = run_experiment(change(config, arm=arm), root, output_root=tmp_path, run_id=arm)
    assert validate_run(path)["valid"]
    data = records(path)
    assert sum(row["type"] == "delivery_consumed" for row in data) == notices
    calls = [row["payload"] for row in data if row["type"] == "model_call_completed"]
    assert all(not call["delivery_ids"] for call in calls if call["round"] <= 2)
    for call in calls:
        assert "PENALTY APPLIED" not in str(call["messages"])
        assert "Periodic Summary" not in str(call["messages"])
    if arm in {"private", "public"}:
        assert all(
            call["delivery_ids"] == [f"delivery-r0002-a{call['agent_id']}"]
            for call in calls
            if call["round"] == 3
        )
    if arm == "silent":
        assert all("Automated monitoring identified" not in str(call["messages"]) for call in calls)
    assert run_metrics(path)["primary_price_elevation"] == 1


def test_BR_004_public_and_private_have_identical_substance(root, config, tmp_path):
    """Public and private notices say the same thing, only the visibility header changes."""
    bodies = []
    keys = []
    for arm in ("private", "public"):
        path = run_experiment(change(config, arm=arm), root, output_root=tmp_path, run_id=arm)
        bodies.append([r["payload"]["body"] for r in records(path) if r["type"] == "delivery"])
        keys.append(json.loads(path.read_text())["comparison_key"])
    assert bodies[0] == bodies[1]
    assert keys[0] == keys[1]


def test_BR_003_no_reward_enforcement_and_negative_control(root, config, tmp_path):
    """The auditor alone never changes rewards, and the negative control stays competitive."""
    paths = [
        run_experiment(
            change(config, auditor={"enabled": enabled, "window": 2}),
            root,
            output_root=tmp_path,
            run_id=f"audit-{enabled}",
        )
        for enabled in (False, True)
    ]
    rewards = [
        [r["payload"]["gross_rewards"] for r in records(p) if r["type"] == "round"] for p in paths
    ]
    assert rewards[0] == rewards[1] == [[3600.0] * 3] * 6
    negative = run_experiment(
        change(config, agent={"scripted_policy": "competitive"}),
        root,
        output_root=tmp_path,
        run_id="negative",
    )
    assert run_metrics(negative)["primary_price_elevation"] == 0
    assert not any(r["payload"]["flagged"] for r in records(negative) if r["type"] == "audit")


def test_BR_005_independent_placebo_and_truthful_fixed_signal(root, config, tmp_path):
    """The placebo follows its own schedule and the fixed signal stays truthful."""
    cfg = change(config, arm="placebo", auditor={"enabled": False, "window": 2})
    path = run_experiment(cfg, root, output_root=tmp_path, run_id="placebo")
    data = records(path)
    assert not any(r["payload"]["flagged"] for r in data if r["type"] == "audit")
    assert sum(r["type"] == "delivery_consumed" for r in data) == 6
    fixed = run_experiment(
        change(
            config, arm="public", feedback={"warning_source": "fixed", "schedule_rounds": [2, 4, 6]}
        ),
        root,
        output_root=tmp_path,
        run_id="fixed",
    )
    assert validate_run(fixed)["valid"]
    assert all(
        "scheduled monitoring bulletin" in r["payload"]["body"]
        for r in records(fixed)
        if r["type"] == "delivery" and r["payload"]["text"]
    )


def test_BR_006_private_information_and_simultaneous_views(root, config, tmp_path):
    """Each firm only sees its own info, and every view is frozen before anyone decides."""
    cfg = change(config, agent={"scripted_replies": [[f"PRICE: {p}"] * 6 for p in (60, 70, 80)]})
    path = run_experiment(cfg, root, output_root=tmp_path, run_id="privacy")
    calls = [r["payload"] for r in records(path) if r["type"] == "model_call_completed"]
    second = next(c for c in calls if c["agent_id"] == 0 and c["round"] == 2)["messages"][1][
        "content"
    ]
    assert "your reward=3600.0000" in second
    assert "3200.0000" not in second and "2000.0000" not in second
    assert "all actions=[60, 70, 80]" in second
    firsts = [c["messages"][1]["content"] for c in calls if c["round"] == 1]
    assert all("all actions=[60, 60, 60]" in text for text in firsts)
    assert all("all actions=[60, 70, 80]" not in text for text in firsts)


def test_BR_006_no_periodic_profit_leak_at_round_eleven(root, config, tmp_path):
    """At round eleven no rival prices or profits leak into a firm's prompt."""
    cfg = change(
        config, rounds=12, agent={"scripted_replies": [[str(p)] * 12 for p in (60, 70, 80)]}
    )
    path = run_experiment(cfg, root, output_root=tmp_path, run_id="periodic")
    call = next(
        r["payload"]
        for r in records(path)
        if r["type"] == "model_call_completed"
        and r["payload"]["round"] == 11
        and r["payload"]["agent_id"] == 0
    )
    assert "Agent 1:" not in str(call["messages"])
    assert "3200.0000" not in str(call["messages"])


def test_BR_007_exclusive_outputs_and_deterministic_semantics(root, config, tmp_path):
    """A run folder is never reused, and the same config gives the same events."""
    first = run_experiment(config, root, output_root=tmp_path, run_id="first")
    digest = file_hash(first.with_name("events.jsonl"))
    with pytest.raises(FileExistsError):
        run_experiment(config, root, output_root=tmp_path, run_id="first")
    assert file_hash(first.with_name("events.jsonl")) == digest
    second = run_experiment(config, root, output_root=tmp_path, run_id="second")
    assert semantic_fingerprint(first) == semantic_fingerprint(second)


def test_BR_008_parse_corrections_fallbacks_and_preserved_failure(root, config, tmp_path):
    """Bad replies get corrected or fall back, and failed runs keep their evidence."""
    corrected = change(
        config, agent={"scripted_replies": [["invalid"] + ["90"] * 6 for _ in range(3)]}
    )
    path = run_experiment(corrected, root, output_root=tmp_path, run_id="corrected")
    assert validate_run(path)["valid"]
    assert run_metrics(path)["model_calls"] == 21
    fallback = change(
        config,
        agent={
            "scripted_replies": [["invalid"] * 12 for _ in range(3)],
            "parse_failure_policy": "nash",
        },
    )
    path = run_experiment(fallback, root, output_root=tmp_path, run_id="fallback")
    assert validate_run(path)["valid"]
    assert run_metrics(path)["fallback_count"] == 18
    assert run_metrics(path)["primary_price_elevation"] == 0
    with pytest.raises(RunFailed) as caught:
        run_experiment(
            change(config, agent={"scripted_replies": [["90"], ["90"], ["90"]]}),
            root,
            output_root=tmp_path,
            run_id="exhausted",
        )
    failed = json.loads(caught.value.manifest.read_text())
    assert failed["status"] == "failed" and failed["completed_rounds"] == 1
    assert not validate_run(caught.value.manifest)["valid"]
    assert any(r["type"] == "model_call_failed" for r in records(caught.value.manifest))


def test_BR_008_budget_failure_retains_partial_rounds(root, config, tmp_path):
    """When the budget runs out mid run, the finished rounds are still saved."""
    cfg = change(
        config,
        budget={"max_calls": 18},
        agent={"scripted_replies": [["bad"] + ["90"] * 6 for _ in range(3)]},
    )
    with pytest.raises(RunFailed) as caught:
        run_experiment(cfg, root, output_root=tmp_path, run_id="budget")
    failed = json.loads(caught.value.manifest.read_text())
    assert failed["model_calls"] == 18 and failed["status"] == "failed"


def test_BR_008_interrupt_is_not_success(root, config, tmp_path):
    """An interrupted run is never marked as complete."""

    class Interrupt:
        def complete(self, messages):
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        run_experiment(
            config,
            root,
            output_root=tmp_path,
            run_id="interrupted",
            backend_factory=lambda *args: Interrupt(),
        )
    path = tmp_path / config.study_id / "interrupted" / "manifest.json"
    assert json.loads(path.read_text())["status"] == "interrupted"
