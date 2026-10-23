"""Check private credential loading without exposing or executing file contents."""

import importlib.util
import os
from pathlib import Path

import pytest


@pytest.mark.parametrize("value", ["local-test-value", '"local-test-value"', "'local-test-value'"])
def test_env_token_is_read_without_evaluation(tmp_path, monkeypatch, value):
    source = Path(__file__).resolve().parents[2] / "scripts/agentrouter_credentials.py"
    spec = importlib.util.spec_from_file_location("credential_check", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv("AGENTROUTER_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "UNRELATED=$(ignored)\nAGENTROUTER_API_KEY=" + value, encoding="utf8"
    )
    module.load_token(tmp_path)
    assert os.environ["AGENTROUTER_API_KEY"] == "local-test-value"


def test_env_duplicate_token_rejected(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / "scripts/agentrouter_credentials.py"
    spec = importlib.util.spec_from_file_location("credential_check", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv("AGENTROUTER_API_KEY", raising=False)
    (tmp_path / ".env").write_text("AGENTROUTER_API_KEY=a\nAGENTROUTER_API_KEY=b", encoding="utf8")
    with pytest.raises(ValueError, match="exactly one"):
        module.load_token(tmp_path)


def test_per_firm_keys_load_together_without_replacing_shared_key(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / "scripts/agentrouter_credentials.py"
    spec = importlib.util.spec_from_file_location("firm_credentials", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for firm in range(3):
        monkeypatch.delenv(f"AGENTROUTER_API_KEY_FIRM_{firm}", raising=False)
    monkeypatch.setenv("AGENTROUTER_API_KEY", "shared-unchanged")
    (tmp_path / ".env").write_text(
        'AGENTROUTER_API_KEY_FIRM_0="test-zero"\nAGENTROUTER_API_KEY_FIRM_1=test-one\n',
        encoding="utf8",
    )
    with pytest.raises(ValueError, match="AGENTROUTER_API_KEY_FIRM_2"):
        module.load_tokens(tmp_path, "per_firm")
    assert "AGENTROUTER_API_KEY_FIRM_0" not in os.environ
    with (tmp_path / ".env").open("a", encoding="utf8") as stream:
        stream.write("AGENTROUTER_API_KEY_FIRM_2=test-two\n")
    module.load_tokens(tmp_path, "per_firm")
    assert [os.environ[f"AGENTROUTER_API_KEY_FIRM_{i}"] for i in range(3)] == [
        "test-zero",
        "test-one",
        "test-two",
    ]
    assert os.environ["AGENTROUTER_API_KEY"] == "shared-unchanged"
