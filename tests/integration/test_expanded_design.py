"""Here we test the warning, fine, auditor and horizon settings."""

import json
import sys
from types import SimpleNamespace as NS

import pytest

from auditors_paradox import cli
from auditors_paradox.adapters.model_calls import ProviderBackend
from auditors_paradox.auditing import BehavioralAuditor
from auditors_paradox.batch import run_sweep
from auditors_paradox.config import AgentConfig, AuditorConfig, StudyConfig
from auditors_paradox.contracts import LEGACY_ARMS as EXTENDED_ARMS
from auditors_paradox.evaluation.inference import analyze_sweep
from auditors_paradox.evaluation.metrics import run_metrics
from auditors_paradox.evaluation.validate import check_events, validate_run
from auditors_paradox.matrix import run_matrix
from auditors_paradox.runner import preflight, run_experiment


def change(config, **values):
    """Copy the config with some fields changed and validate it again."""
    return StudyConfig.model_validate({**config.model_dump(mode="json"), **values})


def events(path):
    """Read all the events saved next to a run manifest."""
    return [
        json.loads(line)
        for line in path.with_name("events.jsonl").read_text(encoding="utf-8").splitlines()
    ]


@pytest.mark.parametrize("arm", EXTENDED_ARMS)
@pytest.mark.parametrize("mode", ["market", "per_firm"])
@pytest.mark.parametrize("disclosed", [False, True])
def test_expanded_arm_auditor_horizon_crossing(root, config, tmp_path, arm, mode, disclosed):
    """Every arm has to work with both auditor modes and with the horizon hidden or shown."""
    cfg = change(
        config,
        arm=arm,
        disclose_horizon=disclosed,
        auditor={"mode": mode, "window": 2, "cooldown_rounds": 1},
    )
    path = run_experiment(cfg, root, output_root=tmp_path, run_id="crossing")
    assert validate_run(path)["valid"]
    data = events(path)
    prompts = [str(r["payload"]["messages"]) for r in data if r["type"] == "model_call_completed"]
    assert all(("across 6 rounds" in p) == disclosed for p in prompts)
    assert all(("of 6." in p) == disclosed for p in prompts)
    delivery = [
        r["payload"]["body"] for r in data if r["type"] == "delivery" and r["payload"]["body"]
    ]
    if arm == "public_numeric":
        assert any("Firm 0: 90" in body for body in delivery)
    metric = run_metrics(path)
    assert metric["gross_profit_elevation"] == 1
    if arm.endswith("fine"):
        assert (
            metric["total_penalties"] > 0
            and metric["mean_tail_net_profit"] < metric["mean_tail_profit"]
        )
        assert not any("No financial penalty" in body for body in delivery)
        assert all("No financial sanctions" not in p for p in prompts)
        assert any("your reward=2880.0000" in p for p in prompts)
    else:
        assert metric["total_penalties"] == 0
    if arm == "placebo":
        assert all("scheduled monitoring bulletin" in body for body in delivery)


def test_per_firm_flags_and_exact_cooldown(root, config):
    """Per firm flags only hit the right firms and the cooldown is exact."""
    market, _ = preflight(config, root)
    auditor = BehavioralAuditor(
        AuditorConfig(mode="per_firm", window=2, cooldown_rounds=2), market.benchmarks
    )
    flags = [
        auditor.evaluate(r, [90, 60, 60], [3600, 2700, 2700]).evidence["flagged_agents"]
        for r in range(1, 7)
    ]
    assert flags == [[], [0], [], [], [0], []]
    market_auditor = BehavioralAuditor(AuditorConfig(window=2, maximum_spread=2), market.benchmarks)
    assert not market_auditor.evaluate(1, [90, 60, 60], [3600, 2700, 2700]).flagged


def test_fine_and_numeric_semantics_independent_of_rehashed_labels(root, config, tmp_path):
    """Fines and numeric notices are checked by what they do, not by labels that can be rehashed."""
    cfg = change(config, arm="public_fine")
    path = run_experiment(cfg, root, output_root=tmp_path, run_id="fine")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    data = events(path)
    row = next(
        r["payload"] for r in data if r["type"] == "round" and any(r["payload"]["penalties"])
    )
    row["net_rewards"][0] = row["gross_rewards"][0]
    assert any("sanction policy" in error for error in check_events(data, manifest))
    cfg = change(config, arm="public_numeric")
    path = run_experiment(cfg, root, output_root=tmp_path, run_id="numeric")
    data = events(path)
    next(r["payload"] for r in data if r["type"] == "delivery" and r["payload"]["body"])[
        "body"
    ] += " price=999"
    assert any("substance" in error for error in check_events(data, json.loads(path.read_text())))


def test_expanded_sweep_metrics_and_missing_blocks(root, config, tmp_path):
    """Sweep metrics for the new arms, and blocks with missing runs are counted right."""
    cfg = change(config, batch_arms=list(EXTENDED_ARMS), primary_outcome="gross_profit")
    sweep = run_sweep(cfg, root, sweep_id="expanded", replicates=2, output_root=tmp_path)
    output = analyze_sweep(sweep, root, tmp_path / "analysis")
    summary = json.loads(output.read_text())
    assert len(summary["counts"]) == 7
    assert summary["estimate"]["paired_runs"] == 2
    assert summary["primary_outcome"] == "gross_profit"
    assert set(summary["secondary_contrasts"]) == {
        "public_minus_silent",
        "private_minus_silent",
        "placebo_minus_silent",
        "numeric_minus_public",
        "public_fine_minus_public",
        "private_fine_minus_private",
    }
    manifest = json.loads(sweep.read_text())
    for row in manifest["runs"]:
        row["status"] = "failed"
    sweep.write_text(json.dumps(manifest), encoding="utf-8")
    result = analyze_sweep(sweep, root, tmp_path / "failed-analysis")
    assert json.loads(result.read_text())["unpaired_blocks"] == 2


def test_four_cell_matrix_and_cli(root, config, monkeypatch, tmp_path):
    # Use a unique study ID so we never overwrite the release evidence.
    """Run the four cell matrix directly and through the CLI."""
    cfg = change(config, study_id="testmatrix-" + tmp_path.name, batch_arms=["private", "public"])
    result = run_matrix(cfg, root, matrix_id="test", replicates=1, output_root=tmp_path)
    manifest = json.loads(result.read_text())
    assert manifest["status"] == "complete" and manifest["planned_runs"] == 8
    assert {(c["auditor_mode"], c["disclose_horizon"]) for c in manifest["cells"]} == {
        ("market", False),
        ("market", True),
        ("per_firm", False),
        ("per_firm", True),
    }
    monkeypatch.setattr("auditors_paradox.cli.run_matrix", lambda *args, **kwargs: result)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(cfg.model_dump_json(), encoding="utf-8")
    assert cli.main(["matrix", str(cfg_path), "--matrix-id", "cli"]) == 0
    with pytest.raises(FileExistsError):
        run_matrix(cfg, root, matrix_id="test", replicates=1, output_root=tmp_path)


@pytest.mark.parametrize(
    "backend,protocol,url",
    [
        ("ollama", "chat", "http://127.0.0.1:11434/v1"),
        ("agentrouter", "chat", "https://co.agentrouter.org/v1"),
        ("agentrouter", "anthropic", "https://co.agentrouter.org"),
    ],
)
def test_gateway_and_local_transports_without_network(monkeypatch, backend, protocol, url):
    """The AgentRouter and local Ollama routes work with fake clients and no network."""
    requests, constructors = [], []

    def create(**kwargs):
        requests.append(kwargs)
        return NS(
            usage=NS(prompt_tokens=5, completion_tokens=3, input_tokens=5, output_tokens=3),
            model="reported",
            choices=[NS(message=NS(content="PRICE: 60"))],
            content=[NS(type="text", text="PRICE: 60")],
        )

    def factory(**kwargs):
        constructors.append(kwargs)
        return NS(chat=NS(completions=NS(create=create)), messages=NS(create=create))

    monkeypatch.setenv("AGENTROUTER_API_KEY", "fake-gateway-key")
    monkeypatch.setitem(sys.modules, "openai", NS(OpenAI=factory))
    monkeypatch.setitem(sys.modules, "anthropic", NS(Anthropic=factory))
    client = ProviderBackend(
        AgentConfig(backend=backend, model="chosen", base_url=url, api_protocol=protocol)
    )
    assert (
        client.complete(
            [{"role": "system", "content": "system"}, {"role": "user", "content": "price"}]
        ).text
        == "PRICE: 60"
    )
    assert constructors[0]["base_url"] == url and constructors[0]["max_retries"] == 0
    assert len(requests) == 1


def test_new_configuration_boundaries_and_matrix_budget(root, config):
    """The new config limits hold and a paid matrix needs a budget for all four cells."""
    invalid = [
        {"batch_arms": []},
        {"batch_arms": ["public", "public"]},
        {"arm": "public_fine", "feedback": {"warning_source": "fixed"}},
        {"agent": {"backend": "ollama", "model": "qwen3:4b", "base_url": "http://remote:11434/v1"}},
        {"agent": {"backend": "ollama"}},
    ]
    for values in invalid:
        with pytest.raises(ValueError):
            change(config, **values)
    paid = change(
        config,
        agent={
            "backend": "agentrouter",
            "model": "selected",
            "base_url": "https://co.agentrouter.org/v1",
        },
        budget={
            "max_calls": 100,
            "max_cost_usd": 1,
            "input_usd_per_million": 1,
            "output_usd_per_million": 1,
        },
    )
    with pytest.raises(ValueError, match="allow-paid"):
        run_matrix(paid, root, max_total_cost_usd=1)
    with pytest.raises(ValueError, match="all four"):
        run_matrix(paid, root, allow_paid=True, max_total_cost_usd=1)
    for values in [
        {"base_url": "https://bad.example/v1"},
        {"base_url": "https://co.agentrouter.org"},
    ]:
        with pytest.raises(ValueError):
            change(paid, agent={**paid.agent.model_dump(), **values})
