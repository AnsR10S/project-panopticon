"""Check Calvano accounting, bounded retries, parsing stops and token forecasts."""

import json
import sys
import time
import uuid
from email.utils import formatdate
from types import SimpleNamespace as NS

import pytest

from auditors_paradox import cli
from auditors_paradox.adapters.model_calls import (
    Budget,
    ProviderBackend,
    ProviderError,
    provider_error,
)
from auditors_paradox.batch import run_sweep
from auditors_paradox.config import AgentConfig, BudgetConfig, StudyConfig, load_config
from auditors_paradox.contracts import LEGACY_ARMS as EXTENDED_ARMS
from auditors_paradox.contracts import Completion
from auditors_paradox.economics import calvano_references
from auditors_paradox.evaluation.metrics import run_metrics
from auditors_paradox.evaluation.validate import validate_run
from auditors_paradox.matrix import run_matrix
from auditors_paradox.runner import RunFailed, run_experiment
from auditors_paradox.usage import profile_usage


def change(config, **values):
    """Revalidate every modified study setting."""
    return StudyConfig.model_validate({**config.model_dump(mode="json"), **values})


@pytest.mark.parametrize("profile", ["bounded_price_first", "price_only"])
def test_optional_bounded_guidance_preserves_hidden_horizon(root, tmp_path, profile):
    cfg = load_config(root / "configs/controls/calvano-competitive.yaml")
    cfg = change(
        cfg,
        disclose_horizon=False,
        agent={**cfg.agent.model_dump(mode="json"), "prompt_profile": profile},
    )
    path = run_experiment(cfg, root, run_id="bounded-guidance", output_root=tmp_path)
    assert validate_run(path)["valid"]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if profile == "bounded_price_first":
        guidance = (root / "prompts/pricing/bounded-guidance.txt").read_text(encoding="utf-8")
        assert guidance in manifest["pricing_templates"]["action"]
    else:
        assert "exactly one line" in manifest["pricing_templates"]["action"]
    assert manifest["config"]["disclose_horizon"] is False


def test_calvano_equations_grid_and_saved_evidence(root, tmp_path):
    """Both benchmark controls execute the logit market and validate independently."""
    for policy, expected in (("competitive", 0), ("coordinated", 1)):
        cfg = load_config(root / f"configs/controls/calvano-{policy}.yaml")
        path = run_experiment(cfg, root, run_id=policy, output_root=tmp_path)
        assert validate_run(path)["valid"]
        assert run_metrics(path)["gross_profit_elevation"] == pytest.approx(expected)
        saved = json.loads(path.read_text())
        assert saved["benchmarks"]["nash_price"] == 137
        assert saved["benchmarks"]["joint_price"] == 200
        saved["benchmarks"]["joint_reward"] = 0.5
        path.write_text(json.dumps(saved))
        assert any("benchmarks" in e for e in validate_run(path)["errors"])


@pytest.mark.parametrize("arm", EXTENDED_ARMS)
@pytest.mark.parametrize("mode", ["market", "per_firm"])
def test_calvano_treatments_keep_currency_and_fidelity(root, tmp_path, arm, mode):
    """Fine arithmetic and notices stay valid with fractional currency rewards."""
    cfg = load_config(root / "configs/controls/calvano-coordinated.yaml")
    cfg = change(
        cfg,
        arm=arm,
        disclose_horizon=False,
        auditor={"mode": mode, "window": 3, "maximum_spread": 5},
    )
    path = run_experiment(cfg, root, run_id="treatment", output_root=tmp_path)
    assert validate_run(path)["valid"]
    assert run_metrics(path)["gross_profit_elevation"] == pytest.approx(1)
    assert (run_metrics(path)["total_penalties"] > 0) == arm.endswith("fine")


def test_calvano_bad_grid_and_live_fallback_rejected(root):
    """A grid without the required references and a live fallback both fail early."""
    cfg = load_config(root / "configs/pilot/local-calvano-smoke.yaml")
    with pytest.raises(ValueError):
        change(cfg, agent={**cfg.agent.model_dump(), "parse_failure_policy": "nash"})
    market = cfg.market.model_copy(update={"price_min": 200, "price_max": 250})
    with pytest.raises(ValueError):
        calvano_references(market.model_dump_json())
    with pytest.raises(ValueError):
        change(cfg, budget={"stop_file": "../outside"})


def test_exhausted_parse_does_not_execute_a_market_round(root, config, tmp_path):
    """Save invalid replies and stop without inserting a competitive or repeated price."""
    cfg = change(config, agent={"scripted_replies": [["not JSON or PRICE"] * 2] * 3})
    with pytest.raises(RunFailed) as caught:
        run_experiment(cfg, root, run_id="parse-abort", output_root=tmp_path)
    manifest = json.loads(caught.value.manifest.read_text())
    assert manifest["completed_rounds"] == 0 and manifest["model_calls"] == 2
    rows = [
        json.loads(x)
        for x in caught.value.manifest.with_name("events.jsonl").read_text().splitlines()
    ]
    assert any(r["type"] == "decision_rejected" for r in rows)
    assert not any(r["type"] == "round" for r in rows)


def test_temporary_failure_recovers_with_every_attempt_saved(root, config, tmp_path):
    """A retry uses the same frozen decision and still passes run verification."""

    class Flaky:
        def __init__(self):
            self.count = 0

        def complete(self, messages):
            self.count += 1
            if self.count == 1:
                raise ProviderError("rate_limit", retryable=True)
            return Completion("PRICE: 90", 20, 3, "fake-model")

    cfg = change(config, agent={"retry_initial_seconds": 0})
    path = run_experiment(
        cfg, root, run_id="retry", output_root=tmp_path, backend_factory=lambda *args: Flaky()
    )
    assert validate_run(path)["valid"]
    assert json.loads(path.read_text())["model_calls"] == 21
    report = profile_usage(
        path, root, planned_runs=12, planned_rounds=100, input_rate=1, output_rate=2
    )
    assert report["failed_requests_with_unknown_billing"] == 3
    assert report["input_tokens"]["total"] == 360
    assert report["projection"]["estimated_successful_requests"] == 3600
    assert report["projection"]["estimated_usd"] > 0


@pytest.mark.parametrize(
    "status,code,retry",
    [
        (429, None, True),
        (503, None, True),
        (401, None, False),
        (429, "insufficient_quota", False),
        (400, None, False),
    ],
)
def test_provider_error_classification(status, code, retry):
    """Quota and authentication failures are not mistaken for temporary throttling."""

    class Error(Exception):
        status_code = status
        body = {"code": code}
        response = NS(headers={"retry-after": "2"})

    error = provider_error(Error("secret payload must not appear"))
    assert error.retryable is retry
    assert "secret" not in str(error)


def test_retry_after_http_date_is_honored():
    """An HTTP-date backoff is not mistaken for no wait."""

    class Error(Exception):
        status_code = 429
        response = NS(headers={"Retry-After": formatdate(time.time() + 5, usegmt=True)})

    error = provider_error(Error())
    assert error.retryable and 0 < error.retry_after <= 5


def test_cloud_model_is_not_an_unmetered_local_run(root):
    """Keep the free local path from selecting an explicit cloud model."""
    cfg = load_config(root / "configs/pilot/local-calvano-smoke.yaml")
    with pytest.raises(ValueError, match="cloud"):
        change(cfg, agent={**cfg.agent.model_dump(), "model": "example:cloud"})


def test_provider_exhaustion_halts_batch_and_preserves_unrun_assignments(root, config, tmp_path):
    """Do not burn more requests in later arms after the provider stops responding."""

    class Broken:
        def complete(self, messages):
            raise ProviderError("temporary", retryable=True)

    cfg = change(config, agent={"retry_initial_seconds": 0, "transport_attempts": 2})
    path = run_sweep(
        cfg,
        root,
        sweep_id="halt",
        replicates=1,
        output_root=tmp_path,
        backend_factory=lambda *args: Broken(),
    )
    manifest = json.loads(path.read_text())
    assert len(manifest["runs"]) == 4
    assert sum(r["status"] == "not_run" for r in manifest["runs"]) == 3


def test_stop_file_keeps_previous_round_and_last_response(root, config, tmp_path):
    """A stop between rounds preserves the committed round and the in-flight response."""
    stop = tmp_path / "stop"
    count = [0]

    class Stopping:
        def complete(self, messages):
            count[0] += 1
            if count[0] == 4:
                stop.touch()
            return Completion("PRICE: 90", 2, 2, "fake")

    cfg = change(config, budget={"stop_file": stop.relative_to(root).as_posix()})
    with pytest.raises(RunFailed) as caught:
        run_experiment(
            cfg, root, run_id="stop", output_root=tmp_path, backend_factory=lambda *args: Stopping()
        )
    manifest = json.loads(caught.value.manifest.read_text())
    assert manifest["status"] == "stopped"
    assert manifest["completed_rounds"] == 1 and manifest["model_calls"] == 4
    local_budget = Budget(
        BudgetConfig(max_elapsed_seconds=0.001), AgentConfig(backend="ollama"), started=0
    )
    with pytest.raises(RuntimeError, match="elapsed"):
        local_budget.check_stop()


@pytest.mark.parametrize("base_url", [None, "http://127.0.0.1:11436/v1"])
def test_native_local_usage_has_real_options_and_no_api_cost(monkeypatch, base_url):
    """Record Ollama token counts and enforce context/output limits on the native route."""
    sent = []
    result = {
        "done": True,
        "model": "qwen3:4b",
        "message": {"content": "PRICE: 137"},
        "prompt_eval_count": 51,
        "eval_count": 8,
        "eval_duration": 1000000000,
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps(result).encode()

    def open_local(request, timeout):
        expected_base = (base_url or "http://127.0.0.1:11434/v1").removesuffix("/v1")
        assert request.full_url == expected_base + "/api/chat"
        sent.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", open_local)
    agent = AgentConfig(
        backend="ollama",
        api_protocol="ollama",
        model="qwen3:4b",
        base_url=base_url,
        local_thinking=False,
        local_num_gpu=0,
        temperature=0.5,
    )
    completion = ProviderBackend(agent).complete([{"role": "user", "content": "test"}])
    assert completion.input_tokens == 51 and completion.output_tokens == 8
    assert completion.metadata["eval_duration"] == 1000000000
    assert sent[0]["think"] is False and sent[0]["options"]["num_gpu"] == 0
    budget = Budget(BudgetConfig(), agent)
    assert budget.reserve([]) == 0
    budget.account(completion)
    assert budget.observed_usd == 0


def test_planning_preparation_and_profile_cli(root, config, tmp_path, capsys):
    """Plan and prepare an API config without creating a client or making a request."""
    local = root / "configs/pilot/local-calvano-pilot.yaml"
    assert cli.main(["plan", str(local), "--replicates", "3"]) == 0
    planned = json.loads(capsys.readouterr().out)
    assert planned["runs"] == 12 and planned["base_model_requests"] == 3600
    output = root / "configs/local" / f"test-{uuid.uuid4().hex}.json"
    assert (
        cli.main(
            [
                "prepare-api",
                str(local),
                "--model",
                "fake-account-model",
                "--input-rate",
                "1",
                "--output-rate",
                "2",
                "--max-cost",
                "0.1",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    prepared = load_config(output)
    assert prepared.agent.backend == "agentrouter" and prepared.budget.max_cost_usd == 0.1
    assert prepared.agent.base_url == "https://agentrouter.org/v1"
    assert (
        cli.main(
            [
                "prepare-api",
                str(local),
                "--model",
                "fake",
                "--input-rate",
                "1",
                "--output-rate",
                "2",
                "--max-cost",
                "0.1",
                "--output",
                str(tmp_path / "outside"),
            ]
        )
        == 1
    )
    sweep = run_sweep(config, root, sweep_id="usage", replicates=1, output_root=tmp_path)
    report = profile_usage(sweep, root, output=tmp_path / "usage.json")
    assert report["runs_profiled"] == 4
    with pytest.raises(ValueError):
        profile_usage(sweep, root, output=tmp_path / "usage.json")
    with pytest.raises(ValueError):
        profile_usage(sweep, root, input_factor=-1)


def test_profile_matrix_and_target_tokenization(root, config, tmp_path, monkeypatch):
    """A four-cell profile counts each trajectory once and labels retokenized text."""
    cfg = change(config, batch_arms=["private", "public"])
    matrix = run_matrix(cfg, root, matrix_id="profile", replicates=1, output_root=tmp_path)
    monkeypatch.setitem(
        sys.modules,
        "tiktoken",
        NS(get_encoding=lambda name: NS(encode=lambda text, **kwargs: list(text))),
    )
    report = profile_usage(matrix, root, target_encoding="o200k_base")
    assert report["runs_profiled"] == 8
    assert report["target_visible_text_input_mean"] > 0
    assert report["data_classes"] == ["scripted_engineering_fixture"]
    assert set(report["per_arm"]) == {"private", "public"}


def test_matrix_halts_after_a_provider_stop(root, config, tmp_path, monkeypatch):
    """A provider stop in one cell leaves the later cells explicitly unrun."""

    class Broken:
        def complete(self, messages):
            raise ProviderError("quota_exhausted")

    sweep = run_sweep(
        config,
        root,
        sweep_id="provider-stop",
        replicates=1,
        output_root=tmp_path,
        backend_factory=lambda *args: Broken(),
    )
    monkeypatch.setattr("auditors_paradox.matrix.run_sweep", lambda *args, **kwargs: sweep)
    matrix = run_matrix(config, root, matrix_id="stopped", replicates=1, output_root=tmp_path)
    result = json.loads(matrix.read_text())
    assert result["status"] == "partial"
    assert sum(c["status"] == "not_run" for c in result["cells"]) == 3
