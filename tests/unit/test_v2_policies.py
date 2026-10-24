"""Check economic rules, private memory and prospective V2 configuration boundaries."""

import io
import json
import sys
import urllib.error

import pytest

from auditors_paradox.adapters.collusionlab import Market, system_prompt
from auditors_paradox.adapters.model_calls import ProviderBackend, ProviderError
from auditors_paradox.auditing import BehavioralAuditor
from auditors_paradox.config import AgentConfig, StudyConfig, load_config
from auditors_paradox.diagnostics import competence_battery, sanction_calibration, score_response
from auditors_paradox.economics import quantities, rewards
from auditors_paradox.feedback.composer import load_feedback
from auditors_paradox.feedback.router import route
from auditors_paradox.interventions import InterventionDraft, read_state, resolve_draft
from auditors_paradox.observations import ResearchMemory, memory_record
from auditors_paradox.sanctions import assess_sanctions


@pytest.fixture
def v2(root):
    return load_config(root / "configs/v2/warning.yaml")


def select(cfg, **kwargs):
    return StudyConfig.model_validate({**cfg.model_dump(mode="json"), **kwargs})


@pytest.mark.parametrize("timing", ["event", "continuing"])
def test_sanction_equivalence_and_persistence(v2, timing):
    financial = ["fine_only", "private_fine", "public_fine"]
    cfg = select(
        v2,
        batch_arms=["silent", *financial],
        v2={
            **v2.v2.model_dump(),
            "hypothesis_family": "sanctions_" + timing,
            "sanction_timing": timing,
        },
    )
    benchmark = Market.build(cfg).benchmarks
    all_assessments = []
    for arm in financial:
        current = select(cfg, arm=arm)
        auditor = BehavioralAuditor(cfg.auditor, benchmark, v2=True)
        groups = [
            assess_sanctions(current, auditor.evaluate(r, [200] * 3, [0.25] * 3), [0.25] * 3)
            for r in range(1, 13)
        ]
        all_assessments.append([[a.to_dict() for a in group] for group in groups])
        assert groups[2][0].severity == "low"
        assert groups[4][0].severity == "moderate"
        assert groups[7][0].severity == "high"
        assert sum(g[0].deduction > 0 for g in groups) == (2 if timing == "event" else 10)
        reset = auditor.evaluate(13, [137] * 3, rewards(cfg.market, [137] * 3))
        assert reset.evidence["qualification_streaks"] == [0] * 3
        assert assess_sanctions(current, reset, [-1, 0, 1])[0].deduction == 0
    assert all_assessments[0] == all_assessments[1] == all_assessments[2]


def test_fixed_calibration_and_family_boundaries(v2):
    result = sanction_calibration(v2)
    assert result["one_shot_best_reply_to_joint"] == 162
    assert result["constant_joint_scenarios"]["continuing"]["penalized_rounds"] == 48
    with pytest.raises(ValueError, match="separate sanction"):
        select(v2, arm="fine_only")
    with pytest.raises(ValueError, match="exploratory only"):
        select(
            v2,
            run_kind="confirmatory",
            protocol_sha256="a" * 64,
            v2={**v2.v2.model_dump(), "manual_enabled": True},
        )
    with pytest.raises(ValueError):
        select(v2, schema_version=1)


def test_private_notes_and_history_equivalence(v2):
    cfg = select(
        v2,
        memory_window=3,
        agent={**v2.agent.model_dump(), "prompt_profile": "economics_private_note"},
    )
    memories = [ResearchMemory(cfg) for _ in range(3)]
    for r in range(1, 26):
        prices = [150, 160, 170]
        gross = rewards(cfg.market, prices)
        for firm, memory in enumerate(memories):
            memory.update(
                memory_record(
                    r,
                    firm,
                    {
                        "prices": prices,
                        "quantities": quantities(cfg.market, prices),
                        "profits": gross,
                    },
                    f"PRICE: {prices[firm]}\nPRIVATE_NOTE: CANARY_FIRM_{firm}",
                    cfg=cfg,
                )
            )
    text = memories[0].to_prompt_context()
    assert "Round 22:" not in text and "Round 23:" in text
    assert "CANARY_FIRM_0" in text and "CANARY_FIRM_1" not in text
    assert "gross profit=" in text and "net profit=" in text
    compact = select(cfg, v2={**cfg.v2.model_dump(), "history_format": "compact"})
    other = ResearchMemory(compact)
    for record in memories[0].records():
        other.update(record)
    assert json.loads(other.to_prompt_context().split("\n")[0])[0]["public_prices"] == [
        150,
        160,
        170,
    ]


def test_truthful_manual_public_override(v2, root):
    cfg = select(v2, arm="public")
    auditor = BehavioralAuditor(cfg.auditor, Market.build(cfg).benchmarks, v2=True)
    for r in range(1, 4):
        event = auditor.evaluate(r, [200] * 3, [0.25] * 3)
    texts = load_feedback(root, cfg)
    original = route(cfg, event, texts)
    draft = InterventionDraft(
        operation="replace", recipients=[0], audience="private", text="Custom diagnostic text"
    )
    changed, ids = resolve_draft(original, draft, "custom", texts)
    assert len(ids) == 1
    assert any(d.status == "replaced_by_manual" for d in changed)
    remaining = [
        d for d in changed if d.recipient in {1, 2} and d.status == "scheduled_for_next_round"
    ]
    assert all("All three" not in d.header for d in remaining)
    assert len([d for d in changed if d.source_kind == "manual"]) == 1
    with pytest.raises(ValueError):
        InterventionDraft(operation="append", recipients=[0], audience="public", text="bad receipt")
    with pytest.raises(ValueError):
        resolve_draft((), draft, "absent", texts)


def test_financial_instruction_equivalence(v2, root):
    template = (root / "prompts/pricing/system.txt").read_text(encoding="utf8")
    data = {**v2.v2.model_dump(), "hypothesis_family": "sanctions_event"}
    configs = [
        select(
            v2, arm=arm, batch_arms=["silent", "fine_only", "private_fine", "public_fine"], v2=data
        )
        for arm in ["fine_only", "private_fine", "public_fine"]
    ]
    prompts = [system_prompt(c, template, 0) for c in configs]
    assert prompts[0] == prompts[1] == prompts[2]
    assert "Elevation=" in prompts[0]
    assert "final round is not disclosed" in prompts[0]


def test_frozen_diagnostic_cases(v2):
    cases = competence_battery(v2)
    assert len(cases) == 56 and len({c["id"] for c in cases}) == 56
    for case in cases[:24]:
        prefix = "PRICE: " if case.get("kind") == "price" else "ANSWER: "
        assert score_response(case, prefix + case["expected"], v2)["correct"]
    assert all(c["expected"] is None for c in cases[24:])
    assert not score_response(cases[0], "not an answer", v2)["valid"]


def test_adaptive_pacing_recovers_only_after_new_eligible_successes(root):
    sys.path.insert(0, str(root / "scripts"))
    from validate_qwen_live import RequestPacer

    pacer = RequestPacer({}, 100, 100000)
    pacer.observe({"error_kind": "provider_rate_limit", "retry_after_seconds": 10}, 100)
    until = pacer.cooldown_until
    for _ in range(10):
        pacer.observe({"status": "complete", "dispatched_at_ms": 99000}, until + 5)
    assert pacer.successes_since_limit == 0 and pacer.adaptive_interval_seconds == 60
    for n in range(3):
        pacer.observe(
            {"status": "complete", "dispatched_at_ms": (until + 1 + n) * 1000}, until + 2 + n
        )
    assert pacer.adaptive_interval_seconds == 0
    assert pacer.state()["policy_version"] == "adaptive-recovery-v2"


def test_local_server_error_is_preserved_without_personal_path(monkeypatch):
    error = urllib.error.HTTPError(
        "http://127.0.0.1:11434/api/chat",
        500,
        "failure",
        {},
        io.BytesIO(
            json.dumps(
                {"error": "model loading failed at C:\\Users\\person\\models\\weights"}
            ).encode()
        ),
    )

    def failed(*args, **kwargs):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", failed)
    backend = ProviderBackend(AgentConfig(backend="ollama", model="test", api_protocol="ollama"))
    with pytest.raises(ProviderError) as caught:
        backend.complete([{"role": "user", "content": "PRICE: 137"}])
    assert caught.value.metadata["http_status"] == 500
    assert "<local-path>" in caught.value.metadata["provider_error"]["message"]
    assert "person" not in caught.value.metadata["provider_error"]["message"]


def test_boundary_read_retries_transient_windows_sharing(tmp_path, monkeypatch):
    from pathlib import Path

    directory = tmp_path / "control"
    directory.mkdir()
    state = directory / "state.json"
    state.write_text('{"phase":"paused","revision":7}', encoding="utf8")
    original = Path.read_text
    attempts = []

    def read(path, *args, **kwargs):
        if path == state:
            attempts.append(1)
            if len(attempts) < 3:
                raise PermissionError(13, "Injected transient file sharing")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    assert read_state(tmp_path) == {"phase": "paused", "revision": 7}
    assert len(attempts) == 3
