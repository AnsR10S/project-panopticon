"""Constrain native local output without inventing a price or accepting unfinished answers."""

import io
import json

import pytest

from auditors_paradox.action_parsing import parse_action
from auditors_paradox.config import AgentConfig, StudyConfig, load_config
from auditors_paradox.evaluation.validate import validate_run
from auditors_paradox.runner import run_experiment


@pytest.mark.parametrize(
    "text,price",
    [
        ('{"price":137}', 137),
        (' { "price" : 140 } ', 140),
        ('{"price":137,"price":140}', None),
        ('{"price":137,"note":"x"}', None),
        ('{"price":true}', None),
        ('{"price":137.0}', None),
        ('{"price":"137"}', None),
        ('{"price":251}', None),
        ('{"price":99}', None),
        ('{"price":140', None),
        ("PRICE: 140", None),
        ('{"price":140}\n{"price":145}', None),
        ('<think>{"price":140}</think>', None),
        ('{"price":NaN}', None),
    ],
)
def test_json_parser_rejects_ambiguity(text, price):
    assert parse_action(text, 100, 250, "json_price_v1").price == price


@pytest.mark.parametrize("version", [1, 2])
def test_complete_structured_round_recovery_and_archived_replay(
    root, tmp_path, monkeypatch, version
):
    sent = []

    def request(req, **kwargs):
        payload = json.loads(req.data)
        sent.append(payload)
        truncated = len(sent) == 1
        return io.BytesIO(
            json.dumps(
                {
                    "model": payload["model"],
                    "done": True,
                    "done_reason": "length" if truncated else "stop",
                    "message": {
                        "content": '{"price":' if truncated else '{"price":140}',
                        "thinking": "PRIVATE_NOT_REPLAYED",
                    },
                    "prompt_eval_count": 500,
                    "eval_count": 4096 if truncated else 10,
                }
            ).encode()
        )

    monkeypatch.setattr("urllib.request.urlopen", request)
    data = load_config(root / "configs/v2/warning.yaml").model_dump()
    data.update(rounds=2, analysis_last_rounds=1)
    data["auditor"]["window"] = 2
    data["feedback"]["schedule_rounds"] = []
    data["agent"].update(
        backend="ollama",
        api_protocol="ollama",
        model="deepseek-r1:7b",
        action_parser="json_price_v1",
        local_output_format="price_json",
        prompt_profile="explicit_economics_v2",
        decision_workers=1,
        max_output_tokens=4096,
        context_tokens=12288,
        local_truncation_retries=1,
        local_retry_output_tokens=8192,
    )
    if version == 1:
        data.update(schema_version=1, v2=None)
        data["agent"]["prompt_profile"] = "price_only"
    else:
        data["v2"]["index_enabled"] = False
    manifest = run_experiment(
        StudyConfig.model_validate(data), root, output_root=tmp_path, run_id="json-prices"
    )
    checked = validate_run(manifest)
    assert checked["valid"], checked
    assert len(sent) == 7
    assert sent[0]["messages"] == sent[1]["messages"]
    assert all(p["format"]["properties"]["price"]["enum"] == list(range(100, 251)) for p in sent)
    assert all(p["format"]["additionalProperties"] is False for p in sent)
    assert "Return exactly one line: PRICE:" not in sent[0]["messages"][0]["content"]
    assert '"price": <integer>' in sent[0]["messages"][-1]["content"]
    assert "PRIVATE_NOT_REPLAYED" not in json.dumps([p["messages"] for p in sent])
    events = [
        json.loads(line)
        for line in manifest.with_name("events.jsonl").read_text(encoding="utf8").splitlines()
    ]
    assert not any(e["type"] == "price_only_format_deviation" for e in events)


def test_structured_controls_are_explicit_and_compatible():
    assert AgentConfig().local_output_format == "text"
    with pytest.raises(ValueError, match="native local"):
        AgentConfig(local_output_format="price_json")
    with pytest.raises(ValueError, match="price-only"):
        AgentConfig(action_parser="json_price_v1")
