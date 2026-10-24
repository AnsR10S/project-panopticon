"""Audit assembled requests and keep disclosure separate from enforcement."""

import hashlib
import json

import pytest

from auditors_paradox.adapters.collusionlab import system_prompt
from auditors_paradox.config import StudyConfig, load_config
from auditors_paradox.evaluation.validate import validate_run
from auditors_paradox.pricing import pricing_templates
from auditors_paradox.runner import run_experiment
from auditors_paradox.sanction_disclosure import NO_SANCTIONS, OPAQUE_NOTICE


def financial(root, arm, timing, disclosure, profile="qualitative_v2"):
    data = load_config(root / "configs/v2/sanctions-event.yaml").model_dump()
    data.update(rounds=10, analysis_last_rounds=5, arm=arm)
    data["feedback"]["schedule_rounds"] = []
    data["agent"].update(scripted_policy="coordinated", prompt_profile=profile)
    data["v2"].update(
        sanction_timing=timing,
        hypothesis_family=f"sanctions_{timing}",
        sanction_disclosure=disclosure,
        index_enabled=False,
    )
    return StudyConfig.model_validate(data)


@pytest.mark.parametrize("arm", ["fine_only", "private_fine", "public_fine"])
@pytest.mark.parametrize("timing", ["event", "continuing"])
@pytest.mark.parametrize("profile", ["qualitative_v2", "explicit_economics_v2"])
def test_actual_requests_and_finances_are_separate(root, tmp_path, arm, timing, profile):
    traces = []
    comparison_keys = []
    for disclosure in ("opaque", "transparent"):
        cfg = financial(root, arm, timing, disclosure, profile)
        path = run_experiment(
            cfg, root, output_root=tmp_path, run_id=f"{arm}-{timing}-{profile}-{disclosure}"
        )
        checked = validate_run(path)
        assert checked["valid"], checked
        manifest = json.loads(path.read_text())
        comparison_keys.append(manifest["comparison_key"])
        events = [
            json.loads(line) for line in path.with_name("events.jsonl").read_text().splitlines()
        ]
        requests = [e["payload"] for e in events if e["type"] == "model_call_started"]
        assert len(requests) == 30
        for request in requests:
            system = request["messages"][0]["content"]
            assert ("exp(" in system) == (profile == "explicit_economics_v2")
            assert NO_SANCTIONS not in system
            assert "across 10 rounds" not in system and " of 10" not in system
            assert (
                hashlib.sha256(system.encode("utf8")).hexdigest()
                == manifest["assembled_system_prompt_sha256"][str(request["agent_id"])]
            )
            if disclosure == "opaque":
                assert OPAQUE_NOTICE in system
                full = json.dumps(request["messages"])
                for secret in (
                    "Elevation=",
                    "0.1201119467",
                    "0.25",
                    "at most 5 ticks",
                    "cooldown",
                    "qualifying assessments",
                    "10%",
                    "20%",
                    "40%",
                    "Sanctions use",
                    "resets escalation",
                ):
                    # Observed own gross profit can equal .25; the system alone must omit references.
                    assert secret not in system
                assert "behavioral screen" not in full and "Announcement cooldown" not in full
            else:
                assert "Elevation=" in system and f"Sanctions use {timing}" in system
        rows = [
            (e["type"], e["payload"])
            for e in events
            if e["type"]
            in {"round", "audit", "penalty_assessment", "delivery", "delivery_consumed"}
        ]
        traces.append(sorted(rows, key=lambda row: json.dumps(row, sort_keys=True)))
        outcomes = [e["payload"] for e in events if e["type"] == "round"]
        penalized = [o["round"] for o in outcomes if any(o["penalties"])]
        assert penalized == ([3, 9] if timing == "event" else list(range(3, 11)))
        consumed = [e["payload"] for e in events if e["type"] == "delivery_consumed"]
        assert len(consumed) == (0 if arm == "fine_only" else 6)
        assert all(d["round"] in {4, 10} for d in consumed)
        assert manifest["sanction_disclosure"] == disclosure
    assert traces[0] == traces[1]
    assert comparison_keys[0] != comparison_keys[1]


def test_opaque_prompt_invariant_to_hidden_algorithms_and_all_fine_arms(root):
    base = financial(root, "fine_only", "event", "opaque")
    template, _ = pricing_templates(base, lambda p: (root / p).read_text())
    expected = system_prompt(base, template, 0)
    for arm in ("fine_only", "private_fine", "public_fine"):
        data = base.model_dump()
        data["arm"] = arm
        data["auditor"].update(
            window=7,
            elevation_threshold=0.431,
            maximum_spread=19,
            cooldown_rounds=11,
            mode="per_firm",
        )
        data["v2"].update(
            sanction_timing="continuing", hypothesis_family="sanctions_continuing", graduated=False
        )
        data["sanctions"]["positive_profit_rate"] = 0.37
        assert system_prompt(StudyConfig.model_validate(data), template, 0) == expected


def test_missing_disclosure_reproduces_original_transparent_prompt(root):
    cfg = financial(root, "public_fine", "event", "transparent", "price_only")
    data = cfg.model_dump()
    del data["v2"]["sanction_disclosure"]
    old = StudyConfig.model_validate(data)
    assert old.v2.sanction_disclosure == "transparent"
    template, _ = pricing_templates(cfg, lambda p: (root / p).read_text())
    assert system_prompt(old, template, 0) == system_prompt(cfg, template, 0)


@pytest.mark.parametrize("arm", ["silent", "private", "public", "placebo"])
def test_warning_only_and_v1_do_not_disclose_algorithm(root, arm):
    for version in (1, 2):
        data = load_config(root / "configs/v2/warning.yaml").model_dump()
        data["arm"] = arm
        if version == 1:
            data.update(schema_version=1, v2=None)
        cfg = StudyConfig.model_validate(data)
        template, _ = pricing_templates(cfg, lambda p: (root / p).read_text())
        prompt = system_prompt(cfg, template, 0)
        assert NO_SANCTIONS in prompt and OPAQUE_NOTICE not in prompt and "Elevation=" not in prompt


def test_opaque_custom_template_cannot_accidentally_omit_regime(root):
    cfg = financial(root, "fine_only", "event", "opaque")
    assert OPAQUE_NOTICE in system_prompt(cfg, "Firm {agent_id} chooses prices.", 1)


def test_opaque_legacy_notice_does_not_deny_actual_fines(root):
    from auditors_paradox.adapters.collusionlab import Market
    from auditors_paradox.auditing import BehavioralAuditor
    from auditors_paradox.feedback.composer import load_feedback
    from auditors_paradox.feedback.router import route

    data = financial(root, "public_fine", "event", "opaque").model_dump()
    data["v2"]["warning_template"] = "warning"
    cfg = StudyConfig.model_validate(data)
    auditor = BehavioralAuditor(cfg.auditor, Market.build(cfg).benchmarks, v2=True)
    for r in range(1, 4):
        event = auditor.evaluate(r, [200] * 3, [0.25] * 3)
    deliveries = route(cfg, event, load_feedback(root, cfg))
    assert all("No financial penalty" not in d.text for d in deliveries)
    assert all("your own deduction" in d.text for d in deliveries)


@pytest.mark.parametrize(
    "placeholder", ["{n_rounds:03d}", "{n_rounds!s}", "{n_rounds.real}", "{price_min:{n_rounds}}"]
)
def test_hidden_horizon_custom_formats_are_not_a_bypass(root, placeholder):
    data = financial(root, "fine_only", "event", "opaque").model_dump()
    data["agent"]["system_prompt_path"] = "prompts/pricing/custom-system.txt"
    cfg = StudyConfig.model_validate(data)
    with pytest.raises(ValueError, match="Hidden-horizon"):
        pricing_templates(
            cfg,
            lambda name: (
                "The horizon is " + placeholder
                if name.endswith("custom-system.txt")
                else (root / name).read_text()
            ),
        )


def test_offline_preview_equals_actual_first_decision(root, tmp_path):
    from auditors_paradox.runner import preview_initial_prompts

    cfg = financial(root, "public_fine", "event", "opaque")
    previews = preview_initial_prompts(cfg, root)
    path = run_experiment(cfg, root, output_root=tmp_path, run_id="prompt-preview")
    events = [json.loads(line) for line in path.with_name("events.jsonl").read_text().splitlines()]
    actual = {
        e["payload"]["agent_id"]: e["payload"]["messages"]
        for e in events
        if e["type"] == "model_call_started" and e["payload"]["round"] == 1
    }
    assert all(actual[i] == previews[i] for i in range(3))


def test_v2_private_note_canaries_never_cross_firms(root, tmp_path):
    from auditors_paradox.contracts import Completion

    data = financial(
        root, "private_fine", "continuing", "opaque", "economics_private_note"
    ).model_dump()
    data.update(rounds=26, memory_window=20)
    data["study_id"] = "UNSHARED_STUDY_ID_CANARY"
    cfg = StudyConfig.model_validate(data)
    captured = {i: [] for i in range(3)}

    class Firm:
        def __init__(self, firm):
            self.firm = firm

        def complete(self, messages):
            captured[self.firm].append(json.loads(json.dumps(messages)))
            return Completion(
                f"PRICE: 200\nPRIVATE_NOTE: PRIVATE_CANARY_{self.firm}", 12, 8, "scripted"
            )

    path = run_experiment(
        cfg,
        root,
        output_root=tmp_path,
        run_id="v2-private-canaries",
        backend_factory=lambda c, m, firm: Firm(firm),
    )
    assert validate_run(path)["valid"]
    for firm, requests in captured.items():
        text = json.dumps(requests)
        assert all(f"PRIVATE_CANARY_{other}" not in text for other in range(3) if other != firm)
        assert "UNSHARED_STUDY_ID_CANARY" not in text
        assert f"PRIVATE_CANARY_{firm}" in requests[-1][1]["content"]
        assert "Round 5:" not in requests[-1][1]["content"]
        assert "Round 6:" in requests[-1][1]["content"]


def test_notebook_disclosure_defaults_and_saved_v2_are_distinct(root, monkeypatch):
    monkeypatch.syspath_prepend(str(root / "scripts"))
    import research_control as control
    from build_research_notebook import build, upgrade

    from auditors_paradox.evaluation.inference import AnalysisSettings

    book = build()
    cell = next(c for c in book["cells"] if c["id"] == "configuration")
    namespace = {
        "StudyConfig": StudyConfig,
        "control": control,
        "AnalysisSettings": AnalysisSettings,
    }
    exec(cell["source"], namespace)
    assert namespace["STUDY"]["v2"]["sanction_disclosure"] == "opaque"
    cell["source"] = (
        cell["source"]
        .replace('\nSANCTION_DISCLOSURE = "opaque"', '\nSAVED_DISCLOSURE_UNUSED = "opaque"')
        .replace(
            '    STUDY["v2"]["sanction_disclosure"] = SANCTION_DISCLOSURE',
            '    STUDY["v2"].pop("sanction_disclosure", None)',
        )
    )
    upgraded = upgrade(book)
    saved = next(c["source"] for c in upgraded["cells"] if c["id"] == "configuration")
    assert 'SANCTION_DISCLOSURE = "transparent"' in saved
