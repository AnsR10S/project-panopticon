"""Here we test the config validation, the economic benchmarks and Windows paths."""

import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from auditors_paradox.adapters.collusionlab import calibrate, verify_upstream
from auditors_paradox.config import (
    MarketConfig,
    StudyConfig,
    load_config,
    project_root,
    safe_identifier,
)
from auditors_paradox.contracts import canonical
from auditors_paradox.runner import preflight


@pytest.mark.parametrize("identifier", ["../escape", "CON", "a/b", "", ".hidden", "LPT9", "x" * 97])
def test_BR_001_safe_portable_identifiers(identifier):
    """Only safe names that work as folders on any system are accepted."""
    with pytest.raises(ValueError):
        safe_identifier(identifier)


def test_BR_001_bom_paths_and_strict_config(root, tmp_path):
    """Configs with a BOM load fine and unknown fields are rejected."""
    path = tmp_path / "study.yaml"
    path.write_text("\ufeffrounds: 12\narm: public\n", encoding="utf-8")
    assert load_config(path).arm == "public"
    assert project_root(path) == root
    path.write_text("- invalid\n")
    with pytest.raises(ValueError):
        load_config(path)
    with pytest.raises(ValidationError):
        StudyConfig(unknown="reject")
    with pytest.raises(FileNotFoundError):
        project_root(Path("C:/"))


@pytest.mark.parametrize(
    "data",
    [
        {"analysis_last_rounds": 100},
        {"feedback": {"schedule_rounds": [3, 2]}},
        {"feedback": {"schedule_rounds": [0]}},
        {"feedback": {"schedule_rounds": [99]}},
        {"agent": {"scripted_replies": [["90"]]}},
        {"run_kind": "confirmatory"},
        {"protocol_sha256": "bad"},
        {"market": {"b": 2, "d": 3}},
        {"market": {"a": 10}},
        {"market": {"price_max": 500}},
        {"agent": {"backend": "openai", "model": "model"}},
        {
            "agent": {"backend": "openai"},
            "budget": {"max_cost_usd": 1, "input_usd_per_million": 1, "output_usd_per_million": 1},
        },
        {"market": {"a": float("nan")}},
    ],
)
def test_BR_001_invalid_and_unfunded_settings_fail_early(data):
    """Invalid settings, or live runs without a budget, fail before anything runs."""
    with pytest.raises(ValidationError):
        StudyConfig.model_validate(data)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
@pytest.mark.parametrize("port", [1, 11434, 11436, 65535])
def test_local_ollama_accepts_custom_loopback_ports(host: str, port: int) -> None:
    """Keep local servers working on a different port without allowing remote hosts."""
    url = f"http://{host}:{port}/v1"
    cfg = StudyConfig.model_validate(
        {"agent": {"backend": "ollama", "model": "local-model", "base_url": url}}
    )
    assert cfg.agent.base_url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://remote:11436/v1",
        "http://192.168.1.10:11436/v1",
        "http://0.0.0.0:11436/v1",
        "http://[::]:11436/v1",
        "http://[2001:db8::1]:11436/v1",
        "http://localhost.example:11436/v1",
        "http://127.0.0.1.example:11436/v1",
        "http://localhost@remote:11436/v1",
        "http://user:password@localhost:11436/v1",
        "http://localhost:0/v1",
        "http://localhost:65536/v1",
        "http://localhost:-1/v1",
        "http://localhost:abc/v1",
        "http://localhost:/v1",
        "http://localhost/v1",
        "http://localhost:11436/api/chat",
        "http://localhost:11436/v1/",
        "http://localhost:11436/v1?target=remote",
        "http://localhost:11436/v1#fragment",
        "http://local\nhost:11436/v1",
        " http://localhost:11436/v1",
        "https://localhost:11436/v1",
        "file://localhost:11436/v1",
        "",
    ],
)
def test_local_ollama_rejects_remote_or_malformed_urls(url: str) -> None:
    """Reject remote addresses, credentials and invalid ports before any model request."""
    with pytest.raises(ValidationError, match="local loopback"):
        StudyConfig.model_validate(
            {"agent": {"backend": "ollama", "model": "local-model", "base_url": url}}
        )


def test_BR_002_grid_best_responses_and_joint_optimum(root, config, monkeypatch):
    """The competitive price is a real grid best response and the joint price is the optimum."""
    benchmarks = calibrate(config.market.model_dump_json())
    assert benchmarks.nash_price == 60 and benchmarks.joint_price == 90
    assert benchmarks.nash_reward == 2700 and benchmarks.joint_reward == 3600
    assert benchmarks.joint_grid_verified
    with pytest.raises(ValueError, match="unclipped"):
        calibrate(MarketConfig(price_min=1, price_max=50).model_dump_json())
    from collusionlab.environments.pricing.demand import LinearDifferentiatedDemand

    with monkeypatch.context() as patch:
        patch.setattr(LinearDifferentiatedDemand, "nash_price", lambda self: 65)
        with pytest.raises(ValueError, match="best response"):
            calibrate(MarketConfig(a=148.3).model_dump_json())
    with monkeypatch.context() as patch:
        patch.setattr(LinearDifferentiatedDemand, "monopoly_price", lambda self: 99)
        with pytest.raises(ValueError, match="joint optimum"):
            calibrate(MarketConfig(a=147.1).model_dump_json())


def test_BR_001_pin_and_protocol_preflight_guards(root, config, monkeypatch):
    """Preflight stops a wrong upstream pin or a bad protocol file."""
    original = subprocess.check_output
    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda args, **kwargs: "wrong-sha\n" if "rev-parse" in args else "",
    )
    with pytest.raises(ValueError, match="pinned"):
        verify_upstream(root)
    monkeypatch.setattr(subprocess, "check_output", original)
    with pytest.raises(ValueError, match="call budget"):
        preflight(
            StudyConfig.model_validate({**config.model_dump(), "budget": {"max_calls": 1}}), root
        )
    with pytest.raises(ValueError, match="Protocol"):
        preflight(
            StudyConfig.model_validate({**config.model_dump(), "protocol": "../outside.md"}), root
        )
    with pytest.raises(ValueError, match="Frozen protocol"):
        preflight(
            StudyConfig.model_validate({**config.model_dump(), "protocol_sha256": "0" * 64}), root
        )
    live = StudyConfig.model_validate(
        {
            **config.model_dump(),
            "agent": {"backend": "openai", "model": "chosen-by-user"},
            "budget": {"max_cost_usd": 1, "input_usd_per_million": 1, "output_usd_per_million": 1},
        }
    )
    with pytest.raises(ValueError, match="allow-paid"):
        preflight(live, root)


def test_BR_007_finite_canonical_json():
    """Canonical JSON keeps a stable order and refuses infinity."""
    assert canonical({"b": 1, "a": "notice"}) == '{"a":"notice","b":1}'
    with pytest.raises(ValueError):
        canonical({"value": float("inf")})
