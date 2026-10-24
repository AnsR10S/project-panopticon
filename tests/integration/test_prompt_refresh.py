"""Check prompt information, units, model controls and actionable manual availability."""

import importlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from auditors_paradox.adapters.collusionlab import system_prompt
from auditors_paradox.config import StudyConfig, load_config
from auditors_paradox.economics import quantities
from auditors_paradox.evaluation.inference import AnalysisSettings
from auditors_paradox.evaluation.validate import validate_run
from auditors_paradox.observations import ResearchMemory, memory_record, render_history
from auditors_paradox.pricing import pricing_templates
from auditors_paradox.runner import run_experiment


@pytest.fixture
def refreshed(root):
    data = load_config(root / "configs/v2/warning.yaml").model_dump(mode="json")
    data.update(rounds=4, analysis_last_rounds=2)
    data["feedback"]["schedule_rounds"] = [2]
    data["v2"].update(history_format="table", index_enabled=False)
    data["agent"]["prompt_profile"] = "explicit_economics_v2"
    return StudyConfig.model_validate(data)


def read(root):
    return lambda name: (root / name).read_text(encoding="utf8")


def test_new_profiles_share_everything_except_existing_equations(root, refreshed):
    explicit, action = pricing_templates(refreshed, read(root))
    data = refreshed.model_dump()
    data["agent"]["prompt_profile"] = "qualitative_v2"
    qualitative, other_action = pricing_templates(StudyConfig.model_validate(data), read(root))
    existing = (root / "prompts/pricing/explicit-economics.txt").read_text(encoding="utf8")
    math_block = (
        "The actual demand equation is:\n"
        + existing.split("The actual demand equation is:\n", 1)[1].split(
            "The outside option is included in total demand.", 1
        )[0]
    )
    assert explicit.replace(math_block, "") == qualitative
    assert action == other_action
    assert "heuristic" not in explicit + action
    assert "exp(" not in qualitative and "exp(" in explicit
    assert "maintaining, increasing, or decreasing" in qualitative
    assert "PRICE: 137" not in explicit + action and "PRICE: 200" not in explicit + action
    assert "No additional text." in action


def test_original_explicit_profile_is_unchanged(root, refreshed):
    data = refreshed.model_dump()
    data["agent"]["prompt_profile"] = "explicit_economics"
    system, action = pricing_templates(StudyConfig.model_validate(data), read(root))
    assert system == (root / "prompts/pricing/explicit-economics.txt").read_text(encoding="utf8")
    assert action == (root / "prompts/pricing/price-first.txt").read_text(encoding="utf8")


def test_exact_equations_and_hidden_horizon_follow_config(root, refreshed):
    system, action = pricing_templates(refreshed, read(root))
    text = system_prompt(refreshed, system, 2)
    assert "Firm 2" in text and "a=200, a_0=0, mu=25" in text
    assert "(your price in ticks - 100) / 100" in text
    assert "final round is not disclosed" in text
    assert "across 4 rounds" not in text
    prices = [137, 153, 200]
    terms = [math.exp((200 - p) / 25) for p in prices]
    assert quantities(refreshed.market, prices) == pytest.approx(
        [v / (1 + sum(terms)) for v in terms]
    )


def test_table_preserves_fields_precision_and_removes_fake_reasoning(refreshed):
    memory = ResearchMemory(refreshed)
    for r in range(25):
        memory.update(
            memory_record(
                r,
                0,
                {
                    "prices": [137, 145, 153],
                    "quantities": [0.123456, 0.234567, 0.345678],
                    "profits": [0.123456, 0.2, 0.3],
                    "gross_profits": [0.223456, 0.2, 0.3],
                    "penalties": [0.1, 0, 0],
                },
                "PRICE: 137\nNot a strategy note",
                cfg=refreshed,
            )
        )
    table = memory.to_prompt_context()
    records = memory.records()
    assert len(records) == 20 and records[0]["round"] == 5
    assert (
        "you reasoned" not in table and "PRICE:" not in table and "Not a strategy note" not in table
    )
    data = refreshed.model_dump()
    data["v2"]["history_format"] = "compact"
    compact = json.loads(render_history(records, StudyConfig.model_validate(data)))
    cells = [line.split(" | ") for line in table.splitlines()[2:]]
    for row, parts in zip(compact, cells, strict=True):
        assert parts == [
            str(row["round"]),
            str(row["own_price"]),
            str(row["public_prices"]),
            row["own_share_percent"],
            row["own_gross"],
            row["own_deduction"],
            row["own_net"],
        ]
    assert cells[-1][-3:] == ["0.2235", "0.1000", "0.1235"]


@pytest.mark.parametrize("profile", ["qualitative_v2", "explicit_economics_v2"])
@pytest.mark.parametrize("disclosed", [False, True])
def test_full_run_replays_new_versions(root, tmp_path, refreshed, profile, disclosed):
    data = refreshed.model_dump()
    data["agent"].update(prompt_profile=profile, scripted_policy="coordinated")
    data.update(arm="public", disclose_horizon=disclosed)
    cfg = StudyConfig.model_validate(data)
    path = run_experiment(cfg, root, output_root=tmp_path, run_id=f"{profile}-{disclosed}")
    checked = validate_run(path)
    assert checked["valid"], checked
    rows = [
        json.loads(line)
        for line in path.with_name("events.jsonl").read_text(encoding="utf8").splitlines()
    ]
    prompts = [r["payload"]["messages"] for r in rows if r["type"] == "model_call_started"]
    assert len(prompts) == 12
    assert all("heuristic" not in str(p) and "you reasoned" not in str(p) for p in prompts)
    assert ("of 4" in prompts[0][1]["content"]) == disclosed
    assert sum(r["type"] == "delivery_consumed" for r in rows) == 3


@pytest.mark.parametrize(
    "model,rate", [("gpt-6.1-sol", 3), ("gpt-5.6-sol", 8), ("gpt-6-astra", 16)]
)
def test_notebook_model_selection_and_prompt_knobs(root, monkeypatch, model, rate):
    monkeypatch.syspath_prepend(str(root / "scripts"))
    import research_control as control
    from build_research_notebook import build

    source = next(c["source"] for c in build()["cells"] if c["id"] == "configuration")
    source = source.replace('CONNECTION = "scripted"', 'CONNECTION = "agentrouter"').replace(
        'AGENTROUTER_MODEL = "gpt-5.6-sol"', f'AGENTROUTER_MODEL = "{model}"'
    )
    ns = {"StudyConfig": StudyConfig, "AnalysisSettings": AnalysisSettings, "control": control}
    exec(source, ns)
    cfg = StudyConfig.model_validate(ns["STUDY"])
    assert cfg.agent.model == model and cfg.budget.input_usd_per_million == rate
    assert cfg.agent.prompt_profile == "explicit_economics_v2"
    assert cfg.v2.manual_enabled and cfg.v2.history_format == "table"
    assert ns["AGENTROUTER_MODEL"] == model and not ns["EXECUTE"]


def test_manual_unavailability_explains_frozen_requirements(root, monkeypatch, refreshed):
    monkeypatch.syspath_prepend(str(root / "scripts"))
    module = importlib.import_module("research_v2_ui")
    assert "V1" in module.manual_availability({"schema_version": 1}, Path("run"), {})["reason"]
    cfg = refreshed.model_dump()
    assert "disabled" in module.manual_availability(cfg, Path("run"), {})["reason"]
    cfg["v2"]["manual_enabled"] = True
    assert "No running" in module.manual_availability(cfg, None, {})["reason"]
    assert (
        "not published"
        in module.manual_availability(cfg, Path("run"), {"phase": "unavailable"})["reason"]
    )
    boundary = {"phase": "paused", "enabled": True, "revision": 3}
    assert module.manual_availability(cfg, Path("run"), boundary) == boundary
    panel = module.V2Panel.__new__(module.V2Panel)
    panel.recipients = SimpleNamespace(value=(0,), disabled=False)
    panel.receipt_changed({"new": "public"})
    assert panel.recipients.value == (0, 1, 2) and panel.recipients.disabled
    panel.receipt_changed({"new": "private"})
    assert not panel.recipients.disabled


@pytest.mark.parametrize("profile", ["explicit_economics", "economics_private_note"])
def test_existing_profiles_remain_selectable_in_notebook(root, monkeypatch, profile):
    monkeypatch.syspath_prepend(str(root / "scripts"))
    import research_control as control
    from build_research_notebook import build

    source = next(c["source"] for c in build()["cells"] if c["id"] == "configuration")
    source = source.replace(
        "PROMPT_PROFILE_OVERRIDE = None", f'PROMPT_PROFILE_OVERRIDE = "{profile}"'
    )
    ns = {"StudyConfig": StudyConfig, "AnalysisSettings": AnalysisSettings, "control": control}
    exec(source, ns)
    assert ns["STUDY"]["agent"]["prompt_profile"] == profile


def test_new_model_validation_uses_an_additional_request_allowance(root, monkeypatch):
    monkeypatch.syspath_prepend(str(root / "scripts"))
    from validate_qwen_live import workflow_call_cap

    assert workflow_call_cap(162, 32, False) == 194
    assert workflow_call_cap(162, None, False) == 226
    assert workflow_call_cap(3900, 200, True) == 4000
    with pytest.raises(ValueError):
        workflow_call_cap(162, 0, False)
