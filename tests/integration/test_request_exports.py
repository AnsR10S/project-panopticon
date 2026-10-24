"""Round exports retain exact request attempts even when the experiment fails."""

import csv
import json
import uuid

from auditors_paradox.adapters.model_calls import ProviderError
from auditors_paradox.batch import run_sweep
from auditors_paradox.config import StudyConfig
from auditors_paradox.contracts import Completion
from auditors_paradox.evaluation.inference import analyze_sweep
from auditors_paradox.evaluation.validate import validate_run


def read_csv(path):
    csv.field_size_limit(64 * 1024 * 1024)
    with path.open(encoding="utf8", newline="") as stream:
        return list(csv.DictReader(stream))


def test_partial_export_includes_prompts_failures_and_pending_round(root, config, tmp_path):
    data = config.model_dump()
    data.update(batch_arms=["silent"], rounds=3, analysis_last_rounds=1)
    data["feedback"]["schedule_rounds"] = []
    data["agent"].update(transport_attempts=1, prompt_profile="price_only")
    cfg = StudyConfig.model_validate(data)

    class Firm:
        def __init__(self, firm):
            self.firm, self.count = firm, 0

        def complete(self, messages):
            self.count += 1
            if self.firm == 0 and self.count == 2:
                error = ProviderError("provider_rate_limit", retryable=True)
                error.metadata = {
                    "network_requests": 1,
                    "authorization": "DO_NOT_EXPORT",
                    "wire_request": {"messages": messages},
                }
                raise error
            return Completion("PRICE: 60", 20, 5, "scripted")

    sweep = run_sweep(
        cfg, root, replicates=1, output_root=tmp_path, backend_factory=lambda c, m, i: Firm(i)
    )
    out = tmp_path / "export"
    analyze_sweep(sweep, root, out)
    rounds = read_csv(out / "rounds.csv")
    requests = read_csv(out / "requests.csv")
    assert len(rounds) == 2 and len(requests) == 4
    assert rounds[0]["committed"] == "True" and rounds[1]["committed"] == "False"
    assert rounds[1]["mean_price"] == "" and rounds[1]["included_in_inference"] == "False"
    assert requests[-1]["status"] == "failed" and requests[-1]["input_tokens"] == ""
    assert requests[-1]["usage_known"] == "False"
    assert len(json.loads(rounds[0]["requests_json"])) == 3
    manifest = root / json.loads(sweep.read_text())["runs"][0]["manifest_path"]
    events = [
        json.loads(line) for line in manifest.with_name("events.jsonl").read_text().splitlines()
    ]
    starts = {
        e["payload"]["call_id"]: e["payload"] for e in events if e["type"] == "model_call_started"
    }
    assert all(json.loads(r["messages_json"]) == starts[r["call_id"]]["messages"] for r in requests)
    assert "DO_NOT_EXPORT" not in (out / "rounds.csv").read_text()
    assert "DO_NOT_EXPORT" not in (out / "requests.csv").read_text()


def test_known_local_output_failure_continues_without_repeating_committed_prices(root, config):
    data = config.model_dump()
    data.update(batch_arms=["silent"], run_kind="pilot")
    data["agent"].update(
        backend="ollama", api_protocol="ollama", model="test-local", transport_attempts=1
    )
    cfg = StudyConfig.model_validate(data)
    output = root / "runs/diagnostics" / ("local-continuation-" + uuid.uuid4().hex[:10])

    class Firm:
        def __init__(self, firm):
            self.firm, self.count = firm, 0

        def complete(self, messages):
            self.count += 1
            if self.firm == 0 and self.count == 3:
                error = ProviderError("incomplete_local_response")
                error.metadata = {
                    "network_requests": 1,
                    "done_reason": "length",
                    "input_tokens": 20,
                    "output_tokens": 256,
                }
                raise error
            return Completion("PRICE: 60", 20, 5, "test-local")

    failed = run_sweep(
        cfg, root, replicates=1, output_root=output, backend_factory=lambda c, m, i: Firm(i)
    )
    before = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
    calls = []

    class Healthy:
        def complete(self, messages):
            calls.append(messages)
            return Completion("PRICE: 60", 20, 5, "test-local")

    resumed = run_sweep(
        cfg,
        root,
        replicates=1,
        output_root=output,
        resume_from=failed,
        backend_factory=lambda *args: Healthy(),
    )
    record = json.loads(resumed.read_text())
    assert record["status"] == "complete" and len(calls) == 12
    assert "Decision round 3" in calls[0][-1]["content"]
    path = root / record["runs"][0]["manifest_path"]
    assert validate_run(path)["valid"]
    assert all(p.read_bytes() == original for p, original in before.items())
