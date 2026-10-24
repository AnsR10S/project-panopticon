"""Exercise final-choice extraction against thinking, alternatives and actual failure formats."""

import json

import pytest

from auditors_paradox.action_parsing import parse_action, parser_identity
from auditors_paradox.adapters.model_calls import ProviderBackend, ProviderError
from auditors_paradox.config import AgentConfig, StudyConfig, load_config
from auditors_paradox.evaluation.validate import validate_run
from auditors_paradox.pricing import pricing_templates
from auditors_paradox.runner import run_experiment


@pytest.mark.parametrize(
    "text,expected",
    [
        ("PRICE: 130", 130),
        ("137", 137),
        ("**Price: 140**", 140),
        ("My chosen price is: 137", 137),
        ("I decide to set my price at 140 for the next round. This is my decision.", 140),
        ("I have decided to set my price at 130.", 130),
        ("I will set my price at 140.", 140),
        ("I may go with 130 but I select 140", 140),
        ("I might choose 130.\nPRICE: 140", 140),
        ("<think>I choose 130. PRICE: 145</think>\nPRICE: 140", 140),
        ("<think><think>PRICE: 130</think>140</think>\nPRICE: 150", 150),
        ("PRICE: 130\n**Final Decision:**\nSet a price of 140 to balance profit and demand.", 140),
        ("Final answer: 145", 145),
        ("Final price: PRICE: 145", 145),
        ("```text\nPRICE: 145\n```", 145),
        ("> PRICE: 130\nPRICE: 140", 140),
        ("PRICE: 137\nMy chosen price is: 137", 137),
        ("PRICE: 100", 100),
        ("PRICE: 250", 250),
        ("My cost is 100, previous price 137 and rival prices are 150, 160.", None),
        ("I may choose 130 or 140.", None),
        ("I choose 130. I select 140.", None),
        ("PRICE: 130\nPRICE: 140", None),
        ("Final decision: PRICE: 130\nI select 140", None),
        ("<think>PRICE: 130", None),
        ("</think>PRICE: 130", None),
        ("<think>PRICE: 130</think>", None),
        ("PRICE: 99", None),
        ("PRICE: 251", None),
        ("PRICE: 137.5", None),
        ("PRICE: 137 and 140", None),
        ("Final answer: 130\nFinal answer: 140", None),
    ],
)
def test_final_answer_strategy(text, expected):
    assert parse_action(text, 100, 250, "final_answer_v1").price == expected


def test_historical_strategy_and_auto_remain_explicit():
    assert parse_action("PRICE: 130\nPRICE: 140", 100, 250).price == 140
    assert parse_action("My chosen price is: 137", 100, 250).price is None
    assert parser_identity("auto", "deepseek-r1:7b-qwen-distill-q4_K_M") == "final_answer_v1"
    assert parser_identity("auto", "qwen3.5:9b") == "legacy"
    with pytest.raises(ValueError):
        parser_identity("unknown", "deepseek-r1")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("**DECISION: PRICE: 137**", 137),
        ("DECISION PRICE: 120", 120),
        ("Answer price: 137", 137),
        ("DECISION PRICE: 120\nI select 140", None),
        ("DECISION PRICE: 120 or 140", None),
        ("**Answer:** PRICE: 137", 137),
        ("**Decision Round 1:**\nChoose your own price: 137", 137),
        ("Choose your price: 137", 137),
        ("Choose your own price: 130\nI select 140", None),
        ("Choose your own price: 137.5", None),
        ("> Choose your own price: 137", None),
        ("Discussion: I may choose 130.\n**DECISION: PRICE: 137**", 137),
        ("PRICE: 130\nDECISION: PRICE: 137", 137),
        ("Decision: 140", 140),
        ("Decision: I choose 140", 140),
        ("Decision round 1: 140", None),
        ("> DECISION: PRICE: 137", None),
        ("<think>DECISION: PRICE: 137</think>", None),
        ("<think>DECISION: PRICE: 137", None),
        ("DECISION: PRICE: 137\nI choose 140", None),
        ("DECISION: PRICE: 137\nDECISION: PRICE: 140", None),
        ("DECISION: PRICE: 137\nFinal answer: 140", None),
        ("DECISION: PRICE: 137.5", None),
        ("DECISION: PRICE: 251", None),
        ("DECISION: I may choose 130 or 140", None),
    ],
)
def test_versioned_decision_heading(text, expected):
    assert parse_action(text, 100, 250, "final_answer_v2").price == expected


def test_v1_does_not_reinterpret_historical_decision_wrapper():
    assert parse_action("**DECISION: PRICE: 137**", 100, 250, "final_answer_v1").price is None
    assert parser_identity("auto", "deepseek-r1:7b") == "final_answer_v1"


@pytest.mark.parametrize("version", [1, 2])
def test_decision_wrapper_end_to_end_replays(root, tmp_path, version):
    data = load_config(root / "configs/v2/warning.yaml").model_dump()
    data.update(rounds=3, analysis_last_rounds=1)
    data["feedback"]["schedule_rounds"] = []
    data["agent"].update(
        action_parser="final_answer_v2",
        prompt_profile="price_only",
        scripted_replies=[["**DECISION: PRICE: 137**"] * 3 for _ in range(3)],
    )
    if version == 1:
        data.update(schema_version=1, v2=None)
    else:
        data["v2"]["index_enabled"] = False
    path = run_experiment(
        StudyConfig.model_validate(data), root, output_root=tmp_path, run_id=f"decision-v{version}"
    )
    checked = validate_run(path)
    assert checked["valid"], checked
    manifest = json.loads(path.read_text())
    assert manifest["completed_rounds"] == 3 and manifest["model_calls"] == 9


@pytest.mark.parametrize("length", [False, True])
def test_native_ollama_keeps_thinking_separate_and_rejects_truncation(monkeypatch, length):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps(
                {
                    "done": True,
                    "done_reason": "length" if length else "stop",
                    "model": "deepseek-r1:7b-qwen-distill-q4_K_M",
                    "message": {"content": "PRICE: 140", "thinking": "I might choose PRICE: 130"},
                    "prompt_eval_count": 50,
                    "eval_count": 100,
                }
            ).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: Response())
    backend = ProviderBackend(
        AgentConfig(
            backend="ollama",
            api_protocol="ollama",
            model="deepseek-r1:7b-qwen-distill-q4_K_M",
            action_parser="auto",
        )
    )
    if length:
        with pytest.raises(ProviderError) as caught:
            backend.complete([])
        assert caught.value.kind == "incomplete_local_response"
        assert caught.value.metadata["output_tokens"] == 100
    else:
        result = backend.complete([])
        assert result.text == "PRICE: 140" and result.output_tokens == 100
        assert result.metadata["response_channels"]["thinking"].endswith("PRICE: 130")


def test_custom_templates_keep_runtime_fields_and_literal_variables(root):
    cfg = load_config(root / "configs/v2/warning.yaml")
    data = cfg.model_dump()
    data["agent"].update(
        action_prompt_path="prompts/pricing/editable-action-example-v1.txt",
        prompt_variables={"custom_guidance": "Compare {margin} and demand."},
    )
    cfg = StudyConfig.model_validate(data)
    system, action = pricing_templates(cfg, lambda p: (root / p).read_text(encoding="utf8"))
    rendered = action.format(
        round_number=3,
        n_rounds=50,
        window_size=20,
        memory_context="OWN RECORD",
        recent_messages="OWN NOTICE",
    )
    assert "Compare {margin} and demand." in rendered
    assert "OWN RECORD" in rendered and "OWN NOTICE" in rendered
    data["agent"]["prompt_variables"] = {}
    with pytest.raises(ValueError, match="must match"):
        pricing_templates(
            StudyConfig.model_validate(data), lambda p: (root / p).read_text(encoding="utf8")
        )
    for path in ("../secret.txt", "prompts/pricing/../../secret.txt", "C:/secret.txt"):
        with pytest.raises(ValueError):
            AgentConfig(system_prompt_path=path)
    with pytest.raises(ValueError):
        AgentConfig(prompt_variables={"n_rounds": "100"})


def test_custom_template_boundaries_and_nonrecursive_variables(root):
    data = load_config(root / "configs/v2/warning.yaml").model_dump()
    data["agent"].update(
        action_prompt_path="prompts/pricing/custom.txt",
        prompt_variables={"custom_a": "{custom_b}", "custom_b": "literal"},
    )
    cfg = StudyConfig.model_validate(data)

    def reader(name):
        if name == "prompts/pricing/custom.txt":
            return "{round_number} {memory_context} {recent_messages} {custom_a} {custom_b}"
        return (root / name).read_text(encoding="utf8")

    _, action = pricing_templates(cfg, reader)
    assert (
        action.format(round_number=1, memory_context="own", recent_messages="none")
        == "1 own none {custom_b} literal"
    )
    for invalid in (
        "{round_number} {memory_context}",
        "{round_number} {memory_context} {recent_messages} Total: {n_rounds}",
    ):
        data["agent"]["prompt_variables"] = {}
        with pytest.raises(ValueError):
            pricing_templates(
                StudyConfig.model_validate(data),
                lambda name: invalid if name == "prompts/pricing/custom.txt" else reader(name),
            )


def test_notebook_upgrade_preserves_local_model_and_exposes_controls(root, monkeypatch):
    monkeypatch.syspath_prepend(str(root / "scripts"))
    import research_control as control
    from build_research_notebook import build, upgrade

    from auditors_paradox.evaluation.inference import AnalysisSettings

    document = build()
    cell = next(c for c in document["cells"] if c["id"] == "configuration")
    cell["source"] = (
        cell["source"]
        .replace('CONNECTION = "scripted"', 'CONNECTION = "ollama"')
        .replace("qwen3.5:4b-q4_K_M", "deepseek-r1:7b-qwen-distill-q4_K_M")
    )
    upgraded = upgrade(document)
    source = next(c["source"] for c in upgraded["cells"] if c["id"] == "configuration")
    ns = {"StudyConfig": StudyConfig, "AnalysisSettings": AnalysisSettings, "control": control}
    exec(source, ns)
    assert ns["CFG"].agent.action_parser == "final_answer_v4"
    assert ns["CFG"].agent.local_thinking is False
    assert ns["CFG"].agent.model == "deepseek-r1:7b-qwen-distill-q4_K_M"
    assert source.count("ACTION_PARSER =") == 1


@pytest.mark.parametrize("v2", [False, True])
def test_scripted_parser_corrections_custom_prompt_and_archived_replay(root, tmp_path, config, v2):
    data = config.model_dump()
    if v2:
        data.update(schema_version=2, v2={"index_enabled": False})
    data["agent"].update(
        action_parser="final_answer_v1",
        prompt_profile="price_only",
        action_prompt_path="prompts/pricing/editable-action-example-v1.txt",
        prompt_variables={"custom_guidance": "Evaluate your own alternatives."},
        scripted_replies=[
            ["I might choose 60 or 70", "<think>PRICE: 70</think>\nMy chosen price is: 60"]
            + ["I select 60"] * 5
        ]
        * 3,
    )
    path = run_experiment(
        StudyConfig.model_validate(data),
        root,
        output_root=tmp_path,
        run_id="final-parser-" + str(v2),
    )
    assert validate_run(path)["valid"], validate_run(path)
    events = [
        json.loads(line)
        for line in path.with_name("events.jsonl").read_text(encoding="utf8").splitlines()
    ]
    decisions = [e["payload"] for e in events if e["type"] == "decision"]
    assert len(decisions) == 18 and all(d["price"] == 60 and not d["fallback"] for d in decisions)
    assert all(d["parsing"]["strategy"] == "final_answer_v1" for d in decisions)
    calls = [e["payload"] for e in events if e["type"] == "model_call_completed"]
    assert len(calls) == 21 and len(calls[1]["messages"]) == 4
    if v2:
        assert all("you reasoned" not in str(c["messages"]) for c in calls)
