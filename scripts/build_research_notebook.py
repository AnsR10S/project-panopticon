"""Build the clean control notebook without saved outputs, credentials or machine paths."""

from __future__ import annotations

import argparse
import ast
import copy
import json
from pathlib import Path
from textwrap import dedent

V2_SETTINGS = {
    "sanction_timing": "event",
    "sanction_disclosure": "opaque",
    "graduated": True,
    "severity_levels": [
        {"name": "low", "qualifying_rounds": 1, "rate": 0.1},
        {"name": "moderate", "qualifying_rounds": 3, "rate": 0.2},
        {"name": "high", "qualifying_rounds": 6, "rate": 0.4},
    ],
    "warning_template": "warning-v2",
    "placebo_template": "uncertainty-v2",
    "manual_enabled": True,
    "manual_max_chars": 2000,
    "index_enabled": True,
    "index_path": "runs/research.sqlite3",
    "history_format": "table",
    "note_max_chars": 240,
    "breakdown_rounds": 3,
    "hypothesis_family": "warning",
    "meaningful_effect": 0.10,
    "power_target": 0.80,
    "pacing_successes": 3,
    "pacing_recovery_floor_seconds": 60,
}


def spending_config_text() -> str:
    """Keep the optional money switch in the same editable configuration cell."""
    return """
# Optional per-run/workflow money caps; accounting and the $1,000 total remain active.
ENFORCE_SPENDING_CAPS = False
STUDY["budget"]["enforce_spending_caps"] = ENFORCE_SPENDING_CAPS
CONTROL.setdefault("lifetime_cost_limit_usd", 1000)
"""


def model_config_text() -> str:
    """One model choice controls wiring and its editable accounting rates."""
    return """# Model aliases and accounting rates from project records, not current gateway quotations.
AGENTROUTER_MODEL = "gpt-5.6-sol"  # gpt-6.1-sol, gpt-5.6-sol, gpt-6-astra
AGENTROUTER_MODELS = {
    "gpt-6.1-sol": {"input_usd_per_million": 3, "output_usd_per_million": 15},
    "gpt-5.6-sol": {"input_usd_per_million": 8, "output_usd_per_million": 40},
    "gpt-6-astra": {"input_usd_per_million": 16, "output_usd_per_million": 80},
}
if AGENTROUTER_MODEL not in AGENTROUTER_MODELS:
    raise ValueError("Choose one of the three configured AgentRouter models.")
"""


def decision_config_text() -> str:
    """New prompt choices are explicit and inactive under a historical V1 preset."""
    return """
# These are new exploratory prompt versions; never change them in a running trajectory.
MARKET_KNOWLEDGE = "explicit"  # qualitative or explicit; the latter reuses the existing equations.
PROMPT_PROFILE_OVERRIDE = None  # Optional: explicit_economics or economics_private_note, etc.
ENABLE_MANUAL_AUDITOR = True
HISTORY_FORMAT = "table"  # table, prose or compact; same fields and numerical precision.
if MARKET_KNOWLEDGE not in {"qualitative", "explicit"}:
    raise ValueError("Choose qualitative or explicit market knowledge.")
if STUDY.get("v2") is not None:
    STUDY["agent"]["prompt_profile"] = PROMPT_PROFILE_OVERRIDE or {"qualitative": "qualitative_v2", "explicit": "explicit_economics_v2"}[MARKET_KNOWLEDGE]
    STUDY["v2"].update(history_format=HISTORY_FORMAT, manual_enabled=ENABLE_MANUAL_AUDITOR)
if CONNECTION == "agentrouter":
    STUDY["agent"]["model"] = AGENTROUTER_MODEL
    STUDY["budget"].update(AGENTROUTER_MODELS[AGENTROUTER_MODEL])
"""


def v2_config_text() -> str:
    """Show every V2 setting explicitly without silently converting saved V1 research."""
    import pprint

    return (
        "\n# V2 is opt-in for a saved V1 study: set schema_version=2 and STUDY['v2']=V2_SETTINGS before validation.\nV2_SETTINGS = "
        + pprint.pformat(V2_SETTINGS, sort_dicts=False, width=95)
        + "\nRUN_V2_DIAGNOSTICS = False\nV2_EXECUTE_LOCAL_DIAGNOSTICS = False\nV2_DIAGNOSTIC_CASE_LIMIT = 56\nPOWER_SCENARIO = None  # See the V2 walkthrough for independent-block scenario inputs.\n"
        + 'V2_DIAGNOSTIC_KIND = "competence"  # Or "warning-response" for synthetic receipt cases.\n'
    )


def prompt_controls_text() -> str:
    """Expose parser choice and frozen custom template inputs for either study version."""
    return """
# DeepSeek R1 auto uses a final-answer parser; other models keep legacy parsing.
ACTION_PARSER = "final_answer_v4" if STUDY["agent"]["model"].lower().startswith("deepseek-r1") else "auto"  # legacy, auto, final_answer_v1, final_answer_v2, final_answer_v3, final_answer_v4
SYSTEM_PROMPT_PATH = None  # e.g. prompts/pricing/my-system-v1.txt
ACTION_PROMPT_PATH = None  # e.g. prompts/pricing/my-action-v1.txt
PROMPT_VARIABLES = {}  # e.g. {"custom_guidance": "Your extra instruction"}
LOCAL_THINKING = STUDY["agent"].get("local_thinking")
LOCAL_REPEAT_PENALTY = STUDY["agent"].get("local_repeat_penalty")  # None keeps server default; 1.2 discourages loops.
LOCAL_REPEAT_LAST_N = STUDY["agent"].get("local_repeat_last_n")  # None keeps server default; 512 remembers repetition.
LOCAL_OUTPUT_FORMAT = STUDY["agent"].get("local_output_format", "text")  # text or price_json (native Ollama).
LOCAL_TRUNCATION_RETRIES = STUDY["agent"].get("local_truncation_retries", 0)  # 0, 1 or 2; local only.
LOCAL_RETRY_OUTPUT_TOKENS = STUDY["agent"].get("local_retry_output_tokens")  # e.g. 6144 with 4096 initial / 8192 context.
if PROMPT_PROFILE_OVERRIDE is not None:
    STUDY["agent"]["prompt_profile"] = PROMPT_PROFILE_OVERRIDE
STUDY["agent"].update(action_parser=ACTION_PARSER,
    system_prompt_path=SYSTEM_PROMPT_PATH, action_prompt_path=ACTION_PROMPT_PATH,
    prompt_variables=PROMPT_VARIABLES)
if CONNECTION == "ollama":
    STUDY["agent"].update(local_thinking=LOCAL_THINKING,
        local_repeat_penalty=LOCAL_REPEAT_PENALTY, local_repeat_last_n=LOCAL_REPEAT_LAST_N,
        local_output_format=LOCAL_OUTPUT_FORMAT,
        local_truncation_retries=LOCAL_TRUNCATION_RETRIES,
        local_retry_output_tokens=LOCAL_RETRY_OUTPUT_TOKENS)
"""


def local_prompt_config_text(default: int | None = 3) -> str:
    """Choose one local qualitative prompt without scheduling cycles."""
    return '\n# One local prompt per study. None restores the historical/custom prompt controls above.\nPROMPT_NUMBER = 3  # 1, 2 or 3; no cycles or automatic prompt switching.\nif PROMPT_NUMBER is not None:\n    if type(PROMPT_NUMBER) is not int or PROMPT_NUMBER not in (1, 2, 3):\n        raise ValueError("PROMPT_NUMBER must be 1, 2, 3 or None.")\n    if PROMPT_PROFILE_OVERRIDE is not None or SYSTEM_PROMPT_PATH is not None or ACTION_PROMPT_PATH is not None or PROMPT_VARIABLES:\n        raise ValueError("Use PROMPT_NUMBER, or set it to None before using custom/historical prompt overrides.")\n    from auditors_paradox.prompt_trials import apply_trial\n    STUDY = apply_trial(STUDY, f"prompt_{PROMPT_NUMBER}_v1")\n    print(f"Selected local prompt {PROMPT_NUMBER}; one configured study, no cycles.")\n'.replace("PROMPT_NUMBER = 3", f"PROMPT_NUMBER = {default!r}", 1)


def disclosure_config_text(default: str = "opaque") -> str:
    """Disclosure is a separate information treatment, never a sanction algorithm change."""
    return f"""\nSANCTION_DISCLOSURE = "{default}"  # opaque or transparent; new study/protocol when changed.
if STUDY.get("v2") is not None:
    STUDY["v2"]["sanction_disclosure"] = SANCTION_DISCLOSURE
"""


def algorithm_config_text() -> str:
    """Keep historical algorithm presets explicit and inactive until selected."""
    return """
# Strategies are selected from these validated settings; configured keeps your edits.
ALGORITHM_PRESET = "configured"  # configured, historical_v1, v2_warning, v2_event, v2_continuing, v2_legacy_fines
ALGORITHM_PRESETS = {
    "historical_v1": {"schema_version":1, "v2":None, "arm":"silent", "batch_arms":["silent","private","public","placebo"]},
    "v2_warning": {"schema_version":2, "v2":{**V2_SETTINGS,"hypothesis_family":"warning"}, "arm":"silent", "batch_arms":["silent","private","public","placebo"]},
    "v2_event": {"schema_version":2, "v2":{**V2_SETTINGS,"sanction_timing":"event","hypothesis_family":"sanctions_event"}, "arm":"silent", "batch_arms":["silent","fine_only","private_fine","public_fine"]},
    "v2_continuing": {"schema_version":2, "v2":{**V2_SETTINGS,"sanction_timing":"continuing","hypothesis_family":"sanctions_continuing"}, "arm":"silent", "batch_arms":["silent","fine_only","private_fine","public_fine"]},
    "v2_legacy_fines": {"schema_version":2, "v2":{**V2_SETTINGS,"sanction_timing":"event","graduated":False,"warning_template":"warning","placebo_template":"neutral","hypothesis_family":"sanctions_event"}, "arm":"silent", "batch_arms":["silent","fine_only","private_fine","public_fine"]},
}
if ALGORITHM_PRESET != "configured":
    import copy
    STUDY.update(copy.deepcopy(ALGORITHM_PRESETS[ALGORITHM_PRESET]))
# Detector strategy: STUDY["auditor"]["mode"] = "market" or "per_firm".
# Exact historical memory uses historical_v1; V2 legacy fines retain V2 accounting.
# Pick a new study_id/protocol for every changed research design before freezing.
"""


def build() -> dict:
    """Keep the notebook text and editable cells reproducible from this file."""
    cells = []

    def add(kind: str, text: str, label: str) -> None:
        cell = {
            "cell_type": kind,
            "id": label,
            "metadata": {},
            "source": dedent(text).strip() + "\n",
        }
        if kind == "code":
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)

    add(
        "markdown",
        r"""
    # Auditor's Paradox — research control desk

    This notebook controls the **existing V1 runner**, its market, model connection, auditor,
    memory, feedback, budgets and analysis. It does not replace the runner or its request guard.
    Work top to bottom once, then rerun only the cells you need. All editable study and analysis
    settings are in the **configuration cell**, alongside execution and display switches.
    **Run All stays at the launch cell** while it shows live progress and replies, then continues
    to results after execution and analysis finish. Failure is shown and halts Run All.

    **Opening:** from the project root run `uv sync --frozen --all-extras`, then
    `.\.venv\Scripts\python.exe -m jupyterlab notebooks/00_research_control.ipynb`.
    Choose the project's Python kernel. VS Code can also open this file using `.venv`.
    Never put a token in a notebook cell. AgentRouter reads the ignored root `.env` privately:
    `AGENTROUTER_API_KEY=your_new_token` (no spaces or quotes required).

    **Template defaults:** scripted backend; execution disabled; paid execution disabled.
    Your existing saved controls are preserved during an interface upgrade. Review **CONNECTION,
    EXECUTE and ALLOW_PAID** before Run All: enabled execution starts that configured workload.
    With execution disabled, Run All only validates and freezes the plan.
    A 50-round × four-arm run means **200 market rounds, 600 accepted firm decisions per block**.
    It does not mean 200 independent observations. A complete matrix with one block is still a pilot.

    ## What this run asks

    For three firms in this repeated pricing market, using the selected model, prompt and memory:

    1. Does **Public − Silent** change final-window gross-profit elevation?
    2. Does **Private − Silent** change it?
    3. Does **Public − Private** differ under the two auditing policies?
    4. Does **Placebo − Silent** indicate an effect of neutral public notifications?

    | Arm | What firms receive | What the contrast can tell us |
    |---|---|---|
    | Silent | No notices; detector still records flags | Economic baseline under no delivered auditing |
    | Private | A truthful warning, privately framed, after a real flag | Effect of private auditing policy |
    | Public | Same warning body, explicitly publicly delivered to all firms | Effect of public auditing policy |
    | Placebo | Neutral public notice on a preselected schedule | Effect of neutral notification at that schedule |

    Core arms have **no financial fines**. All firms act simultaneously; warnings produced after
    round t can enter decisions only at t+1. Private and Public may flag at different times after
    their behavior diverges. Their contrast identifies a **policy difference**, not a clean isolated
    effect of common knowledge. Placebo is not automatically matched in timing/exposure to warnings.
    Elevated prices/profits, coordination and intentional collusion are different claims.
    Zero warning exposure means we did not exercise the proposed warning mechanism.
    """,
        "introduction",
    )

    add(
        "code",
        """
    # Find the project and load the existing system without loading credentials.
    import os
    import sys
    import json
    import html
    import copy
    import uuid
    from pathlib import Path
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    ROOT = next((p for p in (Path.cwd(), *Path.cwd().parents)
                 if (p / "external/collusion-lab.lock.json").is_file()), None)
    if ROOT is None:
        raise RuntimeError("Open this notebook from inside the Auditor's Paradox project.")
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(ROOT / "src"))
    import auditors_paradox.config as configuration_schema
    if Path(configuration_schema.__file__).resolve() != (ROOT / "src/auditors_paradox/config.py").resolve():
        raise RuntimeError("Wrong project installation loaded. Select this project's .venv kernel, restart the kernel, and rerun from setup.")
    if (not {"decision_workers", "credential_mode", "action_parser", "prompt_variables", "local_truncation_retries", "local_retry_output_tokens", "local_repeat_penalty", "local_repeat_last_n", "local_output_format"} <= set(configuration_schema.StudyConfig.model_fields["agent"].annotation.model_fields)
        or "sanction_disclosure" not in configuration_schema.StudyConfig.model_json_schema().get("$defs", {}).get("V2Config", {}).get("properties", {})
        or "json_price_v1" not in configuration_schema.StudyConfig.model_fields["agent"].annotation.model_json_schema()["properties"]["action_parser"].get("enum", [])
        or configuration_schema.StudyConfig.model_fields["agent"].annotation.model_json_schema()["properties"]["retry_max_seconds"].get("maximum", 0) < 900
        or "enforce_spending_caps" not in configuration_schema.StudyConfig.model_fields["budget"].annotation.model_fields
        or "explicit_economics_v2" not in configuration_schema.StudyConfig.model_fields["agent"].annotation.model_json_schema()["properties"]["prompt_profile"].get("enum", [])):
        raise RuntimeError("The kernel still has the old configuration schema in memory. Restart the kernel and rerun from setup; keep the parallel settings.")
    from IPython.display import HTML, Markdown, Image, display
    import importlib
    previous_control = sys.modules.get("research_control")
    if previous_control is not None and not hasattr(previous_control, "ensure_agentrouter_ready"):
        if any(p.poll() is None for p in getattr(previous_control, "_OWNED_WORKERS", {}).values()):
            raise RuntimeError("A notebook worker is still running; stop it before loading updated helpers.")
        importlib.reload(previous_control)
    previous_validation = sys.modules.get("validate_qwen_live")
    if previous_validation is not None and not hasattr(previous_validation, "matching_live_validation"):
        if (ROOT / "runs/diagnostics/qwen-live/active.lock").exists():
            raise RuntimeError("An AgentRouter workflow may be active; finish it before loading updated helpers.")
        importlib.reload(previous_validation)
    import research_control as control
    from research_parameters import parameter_rows
    from auditors_paradox.config import StudyConfig, load_config
    from auditors_paradox.evaluation.inference import AnalysisSettings, analyze_sweep
    from auditors_paradox.usage import profile_usage
    import importlib
    import research_progress as progress
    importlib.reload(control)
    importlib.reload(progress)
    def show_table(rows, columns=None):
        if not rows:
            print("No rows available.")
            return
        columns = columns or list(rows[0])
        header = "".join("<th>" + html.escape(str(c)) + "</th>" for c in columns)
        body = "".join("<tr>" + "".join("<td>" + html.escape(str(r.get(c, ""))) + "</td>"
                       for c in columns) + "</tr>" for r in rows)
        display(HTML("<div style='overflow:auto;max-height:650px'><table><thead><tr>"
                     + header + "</tr></thead><tbody>" + body + "</tbody></table></div>"))
    print("Project found. Kernel:", sys.version.split()[0], "| No credentials read.")
    """,
        "setup",
    )

    add(
        "markdown",
        r"""
    ## 2. All study, workflow and statistical controls — edit here

    `CONNECTION` chooses wiring defaults only. Every value passed to the runner is visible in `STUDY`.
    Subsequent cells validate this dictionary; **they do not overwrite its research settings**.
    After edits, rerun this cell, the parameter table, preview and freeze, then review before launch.

    | Goal | Edits in this cell | Interpretation |
    |---|---|---|
    | Free full plumbing check | `CONNECTION="scripted"`, rounds 50 | Synthetic prices, not empirical research |
    | Local empirical pilot | `CONNECTION="ollama"`, confirm model installed and server running | Qwen local model behavior; not GPT behavior |
    | First paid smoke | `CONNECTION="agentrouter"`; rounds 3, tail 3, arms `["silent"]`, schedule `[]` | Nine accepted pricing decisions; no treatment-effect estimate |
    | Four-arm exploratory pilot | AgentRouter or Ollama; rounds 50, tail 25, four arms, replicates 1 | Tests execution and exposure; contrasts descriptive |
    | Further replication | More independent blocks after competence/exposure pass, with a new budget/plan | More information for uncertainty; not guaranteed significance |
    | Horizon or detector comparison | Change `disclose_horizon` or `auditor.mode`, freeze a separate batch | A new design stratum; do not pool as identical runs |

    AgentRouter presets retain the **verified Responses / price_only / low reasoning / 4096 output**
    combination. Changing it can require new compatibility checks. The rates 3/15 are editable
    planning inputs from the prior account display, **not a current billing quote**.
    For another model, change the ID and both rates; do not assume the gateway supports every setting.
    Local GPU, context and thinking controls apply only to native Ollama.
    `schedule_rounds` are **source** rounds: `[10,30]` becomes notices consumed at 11 and 31 if play continues.
    These defaults are a fixed exploratory Placebo schedule, not independently yoked timing.
    The AgentRouter preset uses three firm workers and `credential_mode="per_firm"`. Put the
    three keys in `.env` as `AGENTROUTER_API_KEY_FIRM_0`, `_FIRM_1`, `_FIRM_2`. The shared application
    cap is three requests/minute, with one total token/budget allowance. Extra keys do not
    establish independent upstream quotas. All three prices must arrive before the round advances.
    """,
        "configuration-guide",
    )

    add(
        "code",
        """
    # Edit the whole study here; no cell below silently changes these settings.
    CONNECTION = "scripted"  # scripted, ollama, agentrouter
    CONNECTIONS = {
        "scripted": dict(backend="scripted", client="sdk", model="scripted",
                         base_url=None, api_protocol="chat", reasoning_effort=None),
        "ollama": dict(backend="ollama", client="sdk", model="qwen3.5:4b-q4_K_M",
                       base_url="http://127.0.0.1:11436/v1", api_protocol="ollama", reasoning_effort=None),
        "agentrouter": dict(backend="agentrouter", client="qwen_code", model="gpt-6.1-sol",
                            base_url="https://agentrouter.org/v1", api_protocol="responses", reasoning_effort="low"),
    }
    wiring = CONNECTIONS[CONNECTION]
    STUDY = {
        "schema_version": 1,
        "study_id": "notebook-four-arm",
        "run_kind": "pilot",
        "study_purpose": "research",
        "arm": "silent",
        "n_agents": 3,
        "rounds": 50,
        "seed": 42,
        "memory_window": 20,
        "disclose_horizon": False,
        "primary_outcome": "gross_profit",
        "batch_arms": ["silent", "private", "public", "placebo"],
        "analysis_last_rounds": 25,
        "protocol": "docs/research/notebook-exploratory-2026-10-23.md",
        "protocol_sha256": None,
        "market": {
            "price_min": 100, "price_max": 250, "a": 200, "b": 3, "d": 2, "c": 100,
            "demand_model": "calvano", "price_scale": 100, "mu": 25, "a_0": 0,
        },
        "agent": {
            "backend": wiring["backend"], "model": wiring["model"], "client": wiring["client"],
            "client_heap_mb": 512,
            "decision_workers": 3 if CONNECTION == "agentrouter" else 1,
            "credential_mode": "per_firm" if CONNECTION == "agentrouter" else "shared",
            "base_url": wiring["base_url"], "api_protocol": wiring["api_protocol"],
            "temperature": None,
            "max_output_tokens": 4096,
            "chat_token_parameter": "max_tokens",
            "max_action_attempts": 2,
            "timeout_seconds": 300,
            "scripted_policy": "competitive",
            "scripted_replies": None,
            "parse_failure_policy": "abort",
            "transport_attempts": 8 if CONNECTION == "agentrouter" else 3,
            "retry_initial_seconds": 30 if CONNECTION == "agentrouter" else 1,
            "retry_max_seconds": 900 if CONNECTION == "agentrouter" else 60,
            "min_request_interval_seconds": 60 if CONNECTION == "agentrouter" else 0,
            "context_tokens": 8192,
            "local_thinking": False,
            "local_num_gpu": None,
            "reasoning_effort": wiring["reasoning_effort"],
            "prompt_profile": "price_only",
        },
        "auditor": {
            "enabled": True, "mode": "market", "cooldown_rounds": 5,
            "window": 3, "elevation_threshold": 0.25, "maximum_spread": 5,
        },
        "feedback": {"warning_source": "live", "schedule_rounds": [10, 30]},
        "sanctions": {"positive_profit_rate": 0.2},
        "budget": {
            "max_calls": 900,
            "max_cost_usd": 11,
            "input_usd_per_million": 3,
            "output_usd_per_million": 15,
            "request_overhead_tokens": 1024,
            "max_elapsed_seconds": 21600,
            "stop_file": ".stop",
        },
    }
    CONTROL = {
        "replicates": 1,
        "assignment_seed": 31415,
        "requests_per_minute": 3 if CONNECTION == "agentrouter" else 1,
        "tokens_per_minute": 20000,
        "new_request_limit": 3600,
        "new_cost_limit_usd": 1000,
        "lifetime_cost_limit_usd": 1000,
        "workflow_seconds": 86400,
        "label": "exploratory-four-arm",
        "resume_sweep": None,  # Or control.last_failed_sweep(ROOT, STUDY); review before freezing.
    }
    ANALYSIS = {
        "alpha": 0.05,
        "bootstrap_samples": 10000,
        "bootstrap_min_pairs": 10,
        "exact_sign_flip_max_pairs": 16,
        "monte_carlo_samples": 20000,
        "seed": 7021,
    }
    VALIDATE_AGENTROUTER = True  # Complete missing model checks before the main study.
    VALIDATION_NEW_REQUEST_LIMIT = 31  # Additional paid validation attempts, including corrections.
    REUSE_VALIDATION_PROBE = True
    EXECUTE = False
    BASELINE_MANIFESTS = []  # Explicit development Silent manifest paths; never launches a model.
    ALLOW_PAID = False
    DISPLAY = {
        "refresh_seconds": 2,
        "response_limit": 9,
        "response_characters": 1500,
        "show_prompts": True,
        "prompt_limit": 3,
        "terminal_lines": 8,
        "startup_timeout_seconds": 60,
        "stop_grace_seconds": 30,
    }
    CFG = StudyConfig.model_validate(STUDY)
    control.Controls.model_validate(CONTROL)
    AnalysisSettings.model_validate(ANALYSIS)
    print("Configuration validated. No execution requested.")
    """,
        "configuration",
    )

    add(
        "markdown",
        r"""
    ## 3. What every knob means

    The following table is generated against the actual Pydantic schemas. A newly added setting
    without an explanation fails this table instead of disappearing. It includes inactive settings
    and allowed ranges; the preview also enforces cross-field restrictions and checked economics.
    **research/design** changes alter behavior, assignment or evidence. **analysis** changes alter
    what is measured or inferred. **execution** changes can still bias retained runs if failures change.

    All configurable detector thresholds and analysis cutoffs are exposed. Three firms, simultaneous
    pricing, t+1 delivery, firm isolation, the pinned upstream, safe network destinations and one
    transmission per recorded attempt are implementation invariants. The upstream note renderer's
    240-character truncation is also fixed; `price_only` produces no free-text notes. Changing fixed
    behavior needs source changes and regression checks, not an unvalidated notebook toggle.
    """,
        "parameter-guide",
    )
    add(
        "code",
        """
    # Show the exact current values and what changing each one means.
    PARAMETERS = parameter_rows(STUDY, CONTROL, ANALYSIS)
    print(len(PARAMETERS), "documented settings")
    show_table(PARAMETERS)
    """,
        "parameters",
    )

    add(
        "markdown",
        r"""
    ## 4. Detector logic, outcomes and statistics

    **Market-wide detector:** every one of the last `window` rounds must have gross-profit
    elevation **strictly greater** than `elevation_threshold` AND price spread **at most**
    `maximum_spread`. Cooldown is checked afterward. This is a suspicious-pricing rule, not a
    legal finding of collusion. It is independent of choosing price or gross profit as the reported primary score.

    **Per-firm detector:** each firm's average price over the window must be strictly above
    `Nash price + threshold × (joint-reference price − Nash price)`. Price spread is unused.
    Therefore switching mode changes the definition, not merely the audience of the same detector.
    Do not lower thresholds to make the desired result appear. Calibrate on predeclared competitive,
    sustained-elevation, transient-spike and unilateral-deviation scenarios separately from research data.

    For each completed trajectory, average the three firms' gross profits over the final
    `analysis_last_rounds`, then normalize:

    $$E_\pi=(\overline{\pi}_{tail}-\pi_{competitive})/(\pi_{joint}-\pi_{competitive}).$$

    Zero equals the checked competitive reference; one equals the checked joint-profit reference.
    Below zero is possible. Joint means firms' combined profits are maximized; it does not mean
    the agents intentionally agreed. Choosing `primary_outcome="price"` instead normalizes mean
    price between the two reference prices. The default grid gives Nash 137 and joint price 200;
    the preview recalculates them if economics change.

    A contrast averages the **within-block difference in run scores**, e.g. Public minus Silent.
    One block supplies one difference. Rounds within a trajectory are dependent, as are firms.
    The analyzer reports all four core comparisons, missing pairs, effects, approximate exploratory
    bootstrap intervals where eligible, two-sided sign-flip p-values, Holm-adjusted p-values and
    conservative bounded simultaneous intervals. A p-value is not the probability the hypothesis is true.

    Sign flips assume independent blocks and sign-exchangeability of paired differences under the
    null; identical numeric seeds do not guarantee coupled remote sampling. Bootstrap intervals are
    withheld by default below 10 pairs or with no variation. Bounded intervals assume independent
    bounded outcomes and validated references and are usually very wide for small samples.
    With one block, p-values and bootstrap intervals are unavailable. Five nonzero pairs cannot
    produce a two-sided exact p-value below .05 (minimum .0625). More bootstrap draws cannot fix this.
    This notebook implements no valid sequential stopping test: do not keep adding blocks until p < .05.
    All analysis changes and pilot-driven choices remain exploratory until prospectively frozen.
    """,
        "methods",
    )

    add(
        "code",
        """
    # Preview the whole workload, checked references and spending blockers offline.
    PREVIEW = control.preview(ROOT, STUDY, CONTROL, ANALYSIS)
    display(Markdown("```json\\n" + json.dumps(PREVIEW, indent=2) + "\\n```"))
    """,
        "preview",
    )

    add(
        "markdown",
        r"""
    ## 5. Feasibility and readiness before spending

    At one paid request per minute, 600 decisions require **at least about 10 hours of dispatch
    spacing**, before response time, corrections, retries or cooldown. Four arms × 50 rounds × 3
    blocks require 1,800 accepted decisions and about 30 hours minimum spacing. Increasing RPM
    changes throughput, not evidence; the gateway's real quota is unknown.

    `ENFORCE_SPENDING_CAPS = False` disables optional per-trajectory and per-workflow spending caps.
    Cost/usage accounting stays visible. The cumulative research allowance is $1,000, including
    previous known costs and unresolved-charge holds. The API gateway has its own billing controls.
    Set the switch to True to enforce `budget.max_cost_usd` per trajectory and
    `CONTROL.new_cost_limit_usd` across this workflow. No fixed $50 ceiling remains.
    Unknown failed-request usage remains held; accounting history is never reset by the switch.
    Request/RPM/TPM, Stop and time controls remain active. No remote requests
    are sent by preview/freeze. Credentials are loaded only by explicit paid execution.

    Current evidence establishes a short GPT-6.1-sol connection and pricing run, not adequate
    warning responsiveness or confirmatory readiness. Before a large empirical batch, demonstrate:
    valid local client qualification, strategic competence, truthful triggering/non-triggering,
    correct t+1 delivery and an interpretable response diagnostic. Never insert artificial high
    prices into live evidence or fake factual allegations to generate exposure.

    **Local Ollama:** keep its server running on the configured port; install the exact model ID.
    Changing `local_thinking`, prompt profile, horizon or memory makes a different pilot design.
    Optional localhost readiness checks below query only the configured local server; they do not run inference.
    """,
        "feasibility",
    )

    add(
        "code",
        """
    # Query a local server only if you explicitly enable this cell.
    CHECK_LOCAL_SERVER = False
    if CHECK_LOCAL_SERVER:
        import urllib.request
        CFG = StudyConfig.model_validate(STUDY)
        if CFG.agent.backend != "ollama":
            raise ValueError("Select the validated Ollama connection first.")
        base = (CFG.agent.base_url or "http://127.0.0.1:11434/v1").removesuffix("/v1")
        for endpoint in ("version", "tags"):
            with urllib.request.urlopen(base + "/api/" + endpoint, timeout=5) as response:
                display(json.load(response))
    else:
        print("Local server check disabled.")
    """,
        "local-readiness",
    )

    add(
        "markdown",
        """
    ## Auditor transparency and exact prompt preview

    `SANCTION_DISCLOSURE="opaque"` states that deductions are possible without revealing the screen,
    tiers or timing. `transparent` preserves original V2 financial instructions. Missing historical
    settings mean transparent. Saved controls are preserved; new notebook defaults are opaque.
    This changes what agents know, so use a new study ID and a separate protocol when switching.
    `MARKET_KNOWLEDGE` independently selects qualitative economics or the exact market equation.
    Own deductions can still teach an opaque agent about enforcement through experience.

    The next cell assembles exact initial prompts through the real agent, with no inference.
    During execution, the existing prompt viewer shows every actual request, including corrections.
    Automatic and manual notices arrive later, so initial previews cannot show future inboxes.
    Custom prompts and manual text are researcher inputs: inspect them for accidental disclosures.

    DeepSeek output exhaustion is not a parsing or rate-limit error. Recovery is opt-in and frozen:
    for a new local pilot, try `LOCAL_TRUNCATION_RETRIES=1`, `LOCAL_RETRY_OUTPUT_TOKENS=6144`
    with an initial 4096-token ceiling and 8192 context. The runner retries the identical observation
    once, records both attempts, and rejects unfinished answers. It refuses recovery without input
    headroom. If that still truncates, use a separately identified diagnostic before a large batch.
        """,
        "disclosure-guide",
    )
    add(
        "code",
        """
    import html
    from IPython.display import HTML
    for arm in STUDY["batch_arms"]:
        draft = {**STUDY, "arm": arm}
        messages_by_firm = control.preview_initial_prompts(StudyConfig.model_validate(draft), ROOT)
        for firm, messages in enumerate(messages_by_firm):
            body = "\\n\\n".join(f"{m['role'].upper()}\\n{m['content']}" for m in messages)
            display(HTML(f"<details><summary>{html.escape(arm)} / Firm {firm}: exact initial prompt</summary><pre>{html.escape(body)}</pre></details>"))
        """,
        "initial-prompt-preview",
    )

    add(
        "markdown",
        r"""
    ### Optional synthetic detector checks — no model requests

    This exercises your current rules on competitive reference prices, sustained joint-reference
    prices, a one-round spike, unilateral elevation and elevated prices with a wider spread.
    It saves all synthetic prices/flags and proposed next-round deliveries. These are detector
    diagnostics, not choices made by agents and not treatment-effect evidence. Results depend on
    your selected detector: a one-round spike can satisfy a mean-price per-firm rule even when it
    cannot satisfy the market rule requiring every window round to pass. Inspect unexpected flags
    against the definition; do not tune toward the preferred research result.
    """,
        "detector-calibration-guide",
    )
    add(
        "code",
        """
    # Enable to inspect the current thresholds on preserved synthetic scenarios.
    RUN_AUDITOR_DIAGNOSTIC = False
    if RUN_AUDITOR_DIAGNOSTIC:
        calibration = control.auditor_diagnostics(ROOT, STUDY)
        print("Synthetic detector evidence:", calibration.relative_to(ROOT).as_posix())
        cases = control.read_json(calibration)["scenarios"]
        show_table([{"scenario": name, "flagged_rounds": case["flagged_rounds"],
                    "proposed_public_notice_rounds": case["public_scheduled_effective_rounds"]}
                   for name, case in cases.items()])
    else:
        print("Synthetic calibration disabled; enabling it makes no model calls.")
    """,
        "detector-calibration",
    )

    add(
        "markdown",
        """
    ### Empirical Silent assessment — separate development evidence

    Set `BASELINE_MANIFESTS` in the configuration cell to compatible, completed development
    Silent manifests. This replays the current detector, reports joint-window distributions,
    cooldown-aware flags and usable post-notice time. It changes no thresholds and makes no
    model requests. Silent alerts are not labeled false positives: Silent firms can coordinate.
    Different models, memory policies and research settings must be assessed separately.
    These checks do not replace independent development replication or held-out validation.
    """,
        "baseline-assessment-guide",
    )
    add(
        "code",
        """
    if BASELINE_MANIFESTS:
        baseline_report = control.baseline_auditor_assessment(ROOT, STUDY, BASELINE_MANIFESTS)
        display(control.read_json(baseline_report))
        print("Saved development assessment:", baseline_report.relative_to(ROOT).as_posix())
    else:
        print("No development Silent manifests selected. No requests or threshold changes.")
    """,
        "baseline-assessment",
    )

    add(
        "markdown",
        r"""
    ## 6. Freeze the complete plan

    This writes a new immutable plan under `runs/notebook-control/nb-.../`: resolved study JSON
    (also valid YAML), workflow controls, analysis settings, source archive, protocol and dependency
    fingerprints, notebook and launcher copies. Each launch uses randomized arm order within blocks.
    It refuses reused plans, changed controls/source/protocol and overwriting evidence.
    Freezing records a development decision; it is not a public preregistration or proof of validity.
    `run_kind="confirmatory"` still requires a suitable prospectively frozen protocol and its exact hash.
    Do not create a new protocol between arms: keep the source/config stable throughout a batch.
    """,
        "freeze-guide",
    )
    add(
        "code",
        """
    # Save a new plan after every design change, then inspect its preview.
    PLAN_PATH = control.freeze(ROOT, STUDY, CONTROL, ANALYSIS)
    print("Frozen plan:", PLAN_PATH.relative_to(ROOT).as_posix())
    print("Config:", (PLAN_PATH.parent / "study.json").relative_to(ROOT).as_posix())
    print("Nothing has been launched.")
    """,
        "freeze",
    )

    add(
        "markdown",
        """
    ### AgentRouter readiness before the experiment

    Run All reuses completed validation matching the selected model, route, prompt profile,
    output limit, temperature and reasoning setting. If missing, `VALIDATE_AGENTROUTER=True`
    runs separate connection/smoke/isolation diagnostics before the main batch. This can use
    up to `VALIDATION_NEW_REQUEST_LIMIT` additional paid requests. `.env` credentials remain
    private; logs and validation results are saved alongside the frozen plan and diagnostics.
    Existing pacing, accounting, local qualification and spending controls remain active.
    A validation failure stops Run All here; it never becomes research evidence or a bypass.
    Preview mode and local/scripted connections send no paid validation requests.
    """,
        "agentrouter-readiness-guide",
    )
    add(
        "code",
        """
    # Validate this exact frozen model configuration before launching the experiment.
    if PLAN_PATH is not None:
        READINESS = await control.ensure_agentrouter_ready(
            ROOT, PLAN_PATH, execute=EXECUTE, allow_paid=ALLOW_PAID,
            validate_missing=VALIDATE_AGENTROUTER,
            new_request_limit=VALIDATION_NEW_REQUEST_LIMIT,
            reuse_probe=REUSE_VALIDATION_PROBE,
        )
        display(READINESS)
    else:
        print("Standalone readiness skipped in cycle mode.")
    """,
        "agentrouter-readiness",
    )

    add(
        "markdown",
        r"""
    ## 7. Launch the selected full batch

    Start with scripted plumbing, then a bounded local pilot. For AgentRouter, local qualification
    and a matching completed live validation are checked by the existing launcher; failures remain
    blocked. `EXECUTE=True` launches a separate project-venv Python worker and **waits here** with a
    live percentage, per-condition progress, request counts, recent agent replies and terminal log.
    Run All proceeds to results only after execution and analysis finish. A failed worker raises
    visibly here. For paid runs you must also deliberately set `ALLOW_PAID=True` in configuration
    after reviewing caps and knowing you have authorized the expense. These switches do not grant an
    assistant additional spending authority. Run All with the supplied values spends nothing.

    Start a new frozen plan for a new batch. A stopped/failed trajectory is not silently resumed
    or replaced. Do not edit source, protocol or configuration files while the batch runs.

    | Display control | Meaning; no change to the research design |
    |---|---|
    | refresh_seconds | Seconds between read-only display updates; default 2 |
    | response_limit | Number of recent replies shown; default 9 |
    | response_characters | Preview length for each reply; full text stays saved |
    | terminal_lines | Number of recent worker-log lines in the terminal drawer |
    | startup_timeout_seconds | Maximum wait for initial worker status before requesting a stop |
    | stop_grace_seconds | Time to wait for cooperative shutdown after a kernel interrupt |

    Percentage counts **committed market rounds**, not returned text, retry attempts or discarded
    partial-round decisions. Market completion can reach 100% while the panel shows **Analyzing**;
    Run All still waits for the analysis result. ETA is an observed-rate estimate, not a promise.
    Use the panel's **Stop experiment** button, or interrupt the running launch cell to request
    a cooperative stop. A stop preserves partial evidence and never silently restarts the run.
    """,
        "launch-guide",
    )
    add(
        "code",
        """
    # Keep this cell active until the run and analysis finish.
    LAUNCH = control.launch(ROOT, PLAN_PATH, STUDY, CONTROL, ANALYSIS,
                            execute=EXECUTE, allow_paid=ALLOW_PAID)
    if LAUNCH["status"] == "preview_only":
        display(LAUNCH)
    else:
        STATE = await progress.watch(ROOT, PLAN_PATH, LAUNCH, **DISPLAY)
        print("Run finished:", STATE["status"])
    """,
        "launch",
    )

    add(
        "markdown",
        r"""
    ## 8. Progress and kill switches

    The launch panel refreshes automatically; this cell is a final or manually refreshed snapshot.
    It follows the full assignment, including the first active condition and queued conditions.
    Check committed rounds, not server activity
    alone. `agent.decision_workers=3` collects the three firms concurrently within each round;
    the market waits for all valid prices. Shared RPM/TPM may still queue requests. Extra requests can be recorded format corrections
    or retryable transport failures. Request pacing, token pacing, provider cooldown, per-run request/
    cost/time limits, shared request/cost/time limits and the stop file all remain active.
    The client guard still rejects unexpected requests, destinations, tools or conversations.
    A low rate limit cannot be solved by hiding transmissions.

    Stop requests use the **frozen plan's** stop filename, even if the draft cell has since changed.
    `.stop` may affect other runs configured to use it. Committed rounds persist; an unfinished round
    is not an accepted market observation. The live launch cell requests a cooperative stop when
    interrupted. This separate stop cell also works after the launch cell has returned.
    Do not kill an unrelated Ollama server or delete locks
    while a process might still be using them. An active paid request may take time to cancel.
    """,
        "monitor-guide",
    )
    add(
        "code",
        """
    # Rerun to inspect progress; this cell sends no inference requests.
    STATE = control.inspect(ROOT, PLAN_PATH)
    show_table(STATE.get("runs", []), ["arm", "replicate_id", "status", "completed_rounds", "model_calls"])
    print("Worker:", STATE.get("status"), "| Sweep:", STATE.get("sweep_status", "not created"))
    print("\\n".join(STATE.get("recent_log", [])))
    """,
        "monitor",
    )
    add(
        "code",
        """
    # Set True and run this cell to request a cooperative stop.
    STOP_NOW = False
    if STOP_NOW:
        print("Stop requested:", control.request_stop(ROOT, PLAN_PATH))
    else:
        print("No stop requested.")
    """,
        "stop",
    )

    add(
        "markdown",
        r"""
    ## 9. Results, turn CSV and warnings actually consumed

    Wait for a terminal batch before interpreting analysis. `runs.csv` has one row per assigned
    trajectory, including failures/exclusions. **`rounds.csv` is the turn-by-turn table**, including
    each firm's price, profit elevation, trigger flag and notices consumed that decision round.
    `events.jsonl` beside each run manifest contains actual prompts, raw responses, attempts,
    warning source and consumption links. An audit flag is not the same as a consumed warning.
    The live launch panel has collapsible full prompts; `DISPLAY["show_prompts"]` switches them
    on/off and `DISPLAY["prompt_limit"]` selects 1–12 recent attempts. Neither changes agent input.
    The recent-prompt drawer stays open during progress refreshes. Its contents stay fixed while
    open; click **Refresh prompts** or close/reopen it to read the newest snapshot.

    `summary.json` contains the complete four-comparison family; `report.md` explains the export;
    `trajectories.png` and `run-outcomes.png` are descriptive figures. Missing/failed pairs can bias
    complete-case comparisons. If Public/Private consume zero warnings, describe unexercised exposure,
    not a demonstrated absence of a warning effect. Figures never turn dependent rounds into replicates.
    """,
        "results-guide",
    )
    add(
        "markdown",
        """
    ### Inspect the exact prompt for any firm and round

    Select condition/block, round and firm (0, 1, 2), then **Load saved prompts** and select an
    attempt. This reads saved evidence only. It includes system instructions, supplied history,
    notices, formatting corrections and returned text. When available, expand the guard-captured
    outgoing JSON body to inspect what the client dispatched; headers and API keys are not loaded.
    A recorded start does not prove transmission, and an accepted decision does not prove the
    whole market round committed. Failed or not-yet-started selections may have no prompt.

    Past rival prices are deliberately shared. Rival profits, returned notes, private notices
    and same-round actions are excluded from the other firms' prompts. Profits can still be
    inferred from the public prices and known market equation. This is a restriction on supplied
    information, not a claim that those values are impossible to calculate. The viewer is for the
    researcher; its combined display is never fed into agents. We test local prompt/session
    boundaries; we cannot certify the remote provider's internal isolation.
    """,
        "prompt-viewer-guide",
    )
    add(
        "code",
        """
    # Read any saved firm's prompt without repeating a model request.
    PROMPTS = progress.prompt_viewer(ROOT, PLAN_PATH)
    """,
        "prompt-viewer",
    )
    add(
        "code",
        """
    # Read the saved outputs after completion; no experiments are repeated.
    STATE = control.inspect(ROOT, PLAN_PATH)
    summary_relative = STATE.get("analysis_summary")
    if summary_relative:
        SUMMARY_PATH = control.inside(ROOT, summary_relative)
        RESULTS = SUMMARY_PATH.parent
        SUMMARY = control.read_json(SUMMARY_PATH)
        for name in ("runs.csv", "rounds.csv", "summary.json", "report.md", "trajectories.png"):
            if (RESULTS / name).exists():
                print(name + ":", (RESULTS / name).relative_to(ROOT).as_posix())
            else:
                print(name + ": not generated; inspect raw evidence for incomplete trajectories.")
        show_table(control.read_table(RESULTS / "runs.csv"),
                   ["arm", "replicate_id", "status", "included", "primary_outcome_value",
                    "audit_trigger_rounds", "consumed_warning_notices", "consumed_neutral_notices"])
        contrasts = SUMMARY.get("central_family", {}).get("comparisons", {})
        show_table([{"contrast": name, **value} for name, value in contrasts.items()],
                   ["contrast", "paired_runs", "mean_difference", "sign_flip_p", "holm_p",
                    "bootstrap_interval", "simultaneous_bounded_interval", "missing_complete_pairs"])
        if (RESULTS / "rounds.csv").exists():
            show_table(control.read_table(RESULTS / "rounds.csv")[:12], columns=["arm", "round", "committed", "mean_price", "gross_profit_elevation", "request_count", "included_in_inference"])
        display(Image(filename=str(RESULTS / "trajectories.png")))
    else:
        print("No completed analysis yet. Rerun progress; inspect worker.log on failure.")
    """,
        "results",
    )

    add(
        "markdown",
        r"""
    ## 10. Reanalyze saved evidence and estimate a workload without new experiments

    Set an existing sweep manifest below to analyze its saved data into a **fresh output directory**.
    This uses the chosen `ANALYSIS` settings, while economic outcomes/tail windows come from each
    historical run's own saved configuration. It does not apply today's draft market or tail length
    retroactively. A changed analysis is explicitly exploratory and should not replace earlier exports.
    To inspect different tails you need a separately labeled offline sensitivity analysis, not edited manifests.

    Token projection is optional. It reports source-model usage, assumptions and failed unknown billing.
    Local Qwen token counts do not precisely predict GPT reasoning tokens, gateway charges or later
    long-memory prompts. The three-round GPT smoke is also too short to represent a 20-round memory.
    Safety/input/output factors are transparent **projection assumptions**, not research parameters.
    """,
        "reuse-guide",
    )
    add(
        "code",
        """
    # Point to saved evidence only when you want a fresh offline analysis or projection.
    EXISTING_SWEEP = None
    USAGE_SOURCE = None
    PROJECTION = dict(input_factor=1.0, output_factor=1.0, safety_factor=1.25,
                      target_encoding=None)
    if EXISTING_SWEEP:
        destination = ROOT / "analysis" / ("nb-reanalysis-" + uuid.uuid4().hex[:10])
        result = analyze_sweep(control.inside(ROOT, EXISTING_SWEEP), ROOT, destination,
                               settings=AnalysisSettings.model_validate(ANALYSIS))
        print("Fresh offline analysis:", result.relative_to(ROOT).as_posix())
    if USAGE_SOURCE:
        projection = profile_usage(control.inside(ROOT, USAGE_SOURCE), ROOT,
                    planned_runs=len(STUDY["batch_arms"]) * CONTROL["replicates"],
                    planned_rounds=STUDY["rounds"],
                    input_rate=STUDY["budget"]["input_usd_per_million"],
                    output_rate=STUDY["budget"]["output_usd_per_million"], **PROJECTION)
        display(projection)
    if not EXISTING_SWEEP and not USAGE_SOURCE:
        print("Offline reuse/projection disabled until you select saved evidence.")
    """,
        "offline-analysis",
    )

    add(
        "markdown",
        r"""
    ## 11. Advanced controls, optional extensions and error recovery

    All seven implemented arms can be selected in `batch_arms`. `public_numeric` includes observed
    price numbers and tests an additional focal-information mechanism. `public_fine` and `private_fine`
    introduce punishment incentives. Do not call those the same observe-only experiment.
    Fixed feedback cannot be combined with numeric/fine arms. Extra comparisons remain exploratory.
    For both auditor modes and hidden/disclosed horizons, freeze four separate notebook batches and
    analyze each stratum separately. The existing CLI `matrix` command automates that factorial for
    ordinary backends, but **bypasses this notebook's shared paid workflow**; do not use it for paid
    AgentRouter work. Four core arms across four strata multiply the workload by four, not by one.
    Randomized schedules can be generated prospectively using
    `auditors_paradox.feedback.schedules.randomized_schedule(rounds, count, seed)` and then frozen
    in `schedule_rounds`. This produces one schedule shared across blocks; it is not an independently
    yoked donor schedule. Yoking and a clean common-information mechanism are not implemented here.

    | Symptom | Meaning and next action |
    |---|---|
    | Validation error / analysis window exceeds rounds | Fix the named cell-2 field and rerun preview; no request was sent |
    | Stop file present | Inspect active workers; after they stop, explicitly remove the intended project stop file before a fresh plan |
    | Parallel fields rejected as extra inputs | Restart the kernel and rerun from setup; select the project's .venv. Do not remove decision_workers or credential_mode |
    | Configuration/source changed after freezing | Freeze a new plan; do not edit old evidence or bypass the digest |
    | Plan already started | Inspect its saved status; a retry uses a new plan and remains a separately recorded attempt |
    | Qwen qualification blocked | Run local `scripts/check_qwen_code.py`; inspect its report; do not disable the guard |
    | Matching live validation missing | Use the documented bounded client validation; a changed model/prompt/token/protocol combination is unverified |
    | active.lock exists | Another paid workflow may be active; inspect it before touching the lock |
    | 401/403 or model unavailable | Check token permissions, model ID and route locally; no token in notebook output |
    | 404 at base URL | A browser GET to /v1 does not test an inference endpoint; inspect recorded request route/status |
    | 429 / provider_rate_limit | Shared pacing and recorded retries/cooldown apply; do not start parallel workflows or increase RPM blindly |
    | Output truncated / missing price | Inspect raw response and output/reasoning ceiling; a formatting correction may occur; never insert the last price as fallback |
    | Parse attempts exhausted | Undecidable round aborts; preserve partial evidence and diagnose before a new run |
    | Ollama connection refused | Keep server running, verify exact loopback port/model; config validation alone does not test server availability |
    | Request exceeds tokens-per-minute | Conservative request reservation is larger than the shared limit; shorten information/output or review that pacing limit |
    | Cost/call/time cap exhausted | Work stopped under its declared cap; preserve incomplete assignments; more money is not statistical evidence |
    | No warnings | Check flags versus source/delivery/consumption; competitive behavior legitimately gives no exposure; do not force warnings |
    | Public − Private = 0 | Could be identical behavior, no exposure or low precision; check actual trajectories and uncertainty before interpretation |
    | All tests pass but no strategic response | Engineering validity does not establish agent competence or research novelty |

    The walkthrough also covers the [current client](../docs/execution/QWEN-CLIENT-SETUP.md),
    [operating manual](../docs/execution/OPERATING-MANUAL.md),
    [outcomes/inference](../docs/research/metrics-and-inference.md) and
    [confirmation gates](../docs/research/confirmatory-candidate-2026-10-22.md).
    """,
        "advanced-and-errors",
    )

    add(
        "markdown",
        r"""
    ## Concepts and abbreviations

    | Concept | Plain-language meaning |
    |---|---|
    | Arm / condition | One policy: Silent, Private, Public or Placebo |
    | Round / turn | One simultaneous price choice by each of the three firms |
    | Trajectory / run | A complete sequence of rounds for one condition |
    | Replicate / block | Fresh trajectories for all selected arms, grouped for paired comparison |
    | Reference | A calculated comparison point, not a separate model run |
    | Nash / competitive reference | Prices where no firm improves its one-round profit by changing alone on the validated grid |
    | Joint-profit reference | Prices maximizing the firms' combined one-round profit on the validated grid |
    | Gross profit | Revenue minus production cost, before any experimental fine |
    | Net profit | Gross profit minus a fine, if a fine arm is selected |
    | Elevation | Outcome's position between the competitive reference (0) and joint reference (1) |
    | Tail window | Last selected number of rounds used to calculate a run score |
    | Spread | Highest price minus lowest price in a round; not a statistical confidence interval |
    | Trigger | Detector says its configured suspicious-pricing rule has been met |
    | Exposure / consumption | A notice was actually included in a firm's accepted decision request |
    | Placebo | Truthful neutral notification, with no misconduct allegation |
    | Effect / contrast | Difference in mean run scores between two policies |
    | CI | Confidence interval: uncertainty procedure under its assumptions, not a probability that a fixed effect lies inside |
    | Alpha | Prespecified error-probability cutoff, normally .05; not a knob for obtaining significance |
    | p-value | Under a specified null and test assumptions, probability of a statistic at least as extreme as observed |
    | Sign flip | Paired test considering positive/negative signs of independent block differences |
    | Bootstrap | Resampling independent paired differences to approximate uncertainty |
    | Holm | Adjustment accounting for several comparisons in the declared family |
    | Bounded interval | Conservative uncertainty bound using a checked feasible outcome range |
    | Exploratory | Development or pilot analysis informing future decisions, not a frozen confirmatory discovery |
    | Confirmatory | A prospectively specified, adequately replicated test with credible design and failure handling |
    | API / backend | Network interface / service performing inference |
    | Client / model | Qwen Code connects; the selected GPT or Qwen model chooses prices |
    | RPM / TPM | Requests / token reservations per minute in the shared pacing guard |
    | Token / reservation | Model text unit / conservative allowance held before a request, not necessarily its actual bill |
    | Memory / context | Project-selected firm history / model input capacity; these are different limits |

    **What we can achieve:** establish whether selected auditing policies change measured economic
    outcomes in this setting, and whether a warning mechanism was actually exercised. With adequate
    replication and credible controls we can estimate effect sizes and uncertainty. This design alone
    does not prove intent, legal collusion, generalization to all models/markets or a pure publicity mechanism.
    """,
        "glossary",
    )
    config = next(cell for cell in cells if cell["id"] == "configuration")
    config["source"] = (
        config["source"]
        .replace('"schema_version": 1', '"schema_version": 2')
        .replace('"study_id": "notebook-four-arm"', '"study_id": "notebook-v2-warning"')
        .replace(
            "docs/research/notebook-exploratory-2026-10-23.md",
            "docs/research/prompt-alternatives-v2-2026-10-24.md",
        )
    )
    config["source"] = model_config_text() + config["source"].replace(
        'model="gpt-6.1-sol"', "model=AGENTROUTER_MODEL"
    )
    config["source"] = config["source"].replace(
        "CFG = StudyConfig.model_validate(STUDY)",
        v2_config_text()
        + '\nSTUDY["v2"] = V2_SETTINGS\n'
        + algorithm_config_text()
        + spending_config_text()
        + decision_config_text()
        + prompt_controls_text()
        + disclosure_config_text()
        + local_prompt_config_text()
        + "\nCFG = StudyConfig.model_validate(STUDY)",
    )
    cells.insert(
        next(i for i, c in enumerate(cells) if c["id"] == "preview"),
        {
            "cell_type": "markdown",
            "id": "prompt-editing-guide",
            "metadata": {},
            "source": "## Prompt and parser controls\n\nEdit `ACTION_PARSER`, `SYSTEM_PROMPT_PATH`, `ACTION_PROMPT_PATH`, `PROMPT_VARIABLES` and `LOCAL_THINKING` in the configuration cell. `auto` uses the versioned final-answer parser for DeepSeek R1 and legacy parsing for other models. A unique declared choice is required; ambiguous replies retry and exhausted corrections abort. Provider thinking is kept separate from the final answer.\n\n`PROMPT_PROFILE_OVERRIDE` selects an existing profile in V1 or V2. `MARKET_KNOWLEDGE` affects V2 only. Custom template files and literal `custom_` variables are frozen research inputs; changing them needs a new plan. See the [complete DeepSeek and prompt-editing guide](../docs/execution/DEEPSEEK-AND-PROMPT-EDITING.md) for exact filenames, placeholders and examples.\n",
        },
    )
    cells.insert(
        next(i for i, c in enumerate(cells) if c["id"] == "preview"),
        {
            "cell_type": "markdown",
            "id": "v2-guide",
            "metadata": {},
            "source": '## V2 controls and separate research programs\n\nThe live launch panel includes five stable panels: frozen plan, execution/accounting, Manual Auditor, SQLite browser and provenance. Prepare a boundary while decisions are running, wait for `paused`, preview exact per-firm text, then Confirm & Resume. Public receipt requires all three recipients. Accepted manual edits exclude that trajectory from untouched contrasts. Stop remains available while paused.\n\nSaved V1 settings are preserved. To opt into V2, put `schema_version=2` and `STUDY["v2"]=V2_SETTINGS` before validation in the configuration cell, use a new study ID and the V2 protocol. Warning-only uses the four core arms and family `warning`. Financial studies separately use Silent/Fine Only/Private Fine/Public Fine, timing `event` or `continuing` and matching family `sanctions_event` or `sanctions_continuing`. Placebo V2 is an active uncertainty signal. All tier, threshold, memory, output and pacing settings are explained in the parameter table.\n\nSee [V2 operation guide](../docs/design/2026-10-23-v2/walkthrough.md). No change in the notebook establishes confirmatory readiness.\n',
        },
    )
    cells.insert(
        next(i for i, c in enumerate(cells) if c["id"] == "preview"),
        {
            "cell_type": "code",
            "id": "v2-diagnostics",
            "metadata": {},
            "execution_count": None,
            "outputs": [],
            "source": "# Diagnostics stay separate from market trajectories and are disabled by default.\nfrom auditors_paradox.diagnostics import competence_battery, warning_response_battery, run_battery, sanction_calibration\nfrom auditors_paradox.evaluation.planning import power_scenario\nif RUN_V2_DIAGNOSTICS:\n    cfg = StudyConfig.model_validate(STUDY)\n    if V2_EXECUTE_LOCAL_DIAGNOSTICS and cfg.agent.backend not in {'scripted','ollama'}:\n        raise ValueError('This cell executes only local diagnostics. Paid work requires an authorized managed allowance.')\n    if V2_DIAGNOSTIC_KIND not in {'competence','warning-response'}:\n        raise ValueError('Choose competence or warning-response diagnostics.')\n    cases = competence_battery(cfg) if V2_DIAGNOSTIC_KIND == 'competence' else warning_response_battery(cfg)\n    diagnostic_path = ROOT / 'runs/diagnostics' / ('notebook-v2-' + uuid.uuid4().hex[:10])\n    display(run_battery(cfg, ROOT, diagnostic_path, execute=V2_EXECUTE_LOCAL_DIAGNOSTICS, cases=cases[:V2_DIAGNOSTIC_CASE_LIMIT]))\n    display(sanction_calibration(cfg))\nif POWER_SCENARIO is not None:\n    display(power_scenario(**POWER_SCENARIO))\n",
        },
    )
    for cell in cells:
        if cell["cell_type"] == "markdown":
            cell["source"] = (
                cell["source"]
                .replace("existing V1 runner", "versioned V1/V2 runner")
                .replace("neutral public notifications", "versioned scheduled notifications")
                .replace(
                    "Neutral public notice on a preselected schedule",
                    "Versioned neutral/uncertainty public notice on a preselected schedule",
                )
            )
    next(c for c in cells if c["id"] == "prompt-editing-guide")["source"] = '## Local prompt selection\n\nSet `PROMPT_NUMBER = 3` in the configuration cell. Choose `1` or `2` for the other\nversioned qualitative prompts. This launches the single selected study/batch; there are\nno prompt cycles and no automatic Warning/Event/Continuing loop. `ALGORITHM_PRESET`\ncontinues to select the algorithm independently. Other current market, model, memory,\nduration and budget settings remain editable as before.\n\nSet `PROMPT_NUMBER = None` to use the historical `PROMPT_PROFILE_OVERRIDE`,\n`MARKET_KNOWLEDGE` or custom template controls. Do not combine both selectors.\nThe new numbered prompts have no equations. Local JSON mode uses the same response\ncontract for every numbered prompt; text parsers retain the PRICE contract.\n\nThe selected template paths and response contract are frozen in the plan, with prompt\nnumber/version recorded in manifests and CSVs. Switching prompts requires a new frozen\nplan; keep `CONTROL["resume_sweep"] = None` for the new study. Never change prompts\nmid-run. Past prices are authorized observations; current rival actions/private notes,\nhidden final rounds, schedules and opaque detector criteria remain excluded.\n\nThe exact initial prompt preview is offline. A short scripted check is not proof of\nlive-model reliability. The existing incomplete-answer rejection and retry controls remain.\n' + "\n" + next(c for c in cells if c["id"] == "prompt-editing-guide")["source"]
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3 (project .venv)",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3.11"},
        },
        "cells": cells,
    }


def upgrade(existing: dict) -> dict:
    """Preserve the operator's research settings and custom cells when refreshing the interface."""
    updated = build()
    old = {cell.get("id"): cell for cell in existing["cells"]}
    source = "".join(old["configuration"]["source"])
    if "AGENTROUTER_MODEL =" not in source:
        source = model_config_text() + source.replace(
            'model="gpt-6.1-sol"', "model=AGENTROUTER_MODEL"
        )
    if "MARKET_KNOWLEDGE =" not in source:
        source = source.replace(
            "CFG = StudyConfig.model_validate(STUDY)",
            decision_config_text() + "\nCFG = StudyConfig.model_validate(STUDY)",
        )
    elif "PROMPT_PROFILE_OVERRIDE =" not in source:
        source = "PROMPT_PROFILE_OVERRIDE = None  # Optional existing prompt profile.\n" + source
        source = source.replace(
            'STUDY["agent"]["prompt_profile"] = {"qualitative": "qualitative_v2", "explicit": "explicit_economics_v2"}[MARKET_KNOWLEDGE]',
            'STUDY["agent"]["prompt_profile"] = PROMPT_PROFILE_OVERRIDE or {"qualitative": "qualitative_v2", "explicit": "explicit_economics_v2"}[MARKET_KNOWLEDGE]',
        )
    if "ENFORCE_SPENDING_CAPS" not in source:
        source = source.replace(
            "CFG = StudyConfig.model_validate(STUDY)",
            spending_config_text() + "\nCFG = StudyConfig.model_validate(STUDY)",
        )
    if "V2_SETTINGS" not in source:
        source = source.replace(
            "CFG = StudyConfig.model_validate(STUDY)",
            v2_config_text() + "\nCFG = StudyConfig.model_validate(STUDY)",
        )
    if "V2_DIAGNOSTIC_KIND" not in source:
        source += '\nV2_DIAGNOSTIC_KIND = "competence"\n'
    if "ALGORITHM_PRESET" not in source:
        source = source.replace(
            "CFG = StudyConfig.model_validate(STUDY)",
            algorithm_config_text() + "\nCFG = StudyConfig.model_validate(STUDY)",
        )
    known = {
        node.targets[0].id
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    launch_source = "".join(old.get("launch", {}).get("source", ""))
    launch_flags = {}
    for node in ast.parse(launch_source).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in {"EXECUTE", "ALLOW_PAID"} and isinstance(node.value, ast.Constant):
                launch_flags[name] = bool(node.value.value)
    for name in ("EXECUTE", "ALLOW_PAID"):
        if name not in known:
            source += f"\n{name} = {launch_flags.get(name, False)!r}\n"
    for name, default in (
        ("VALIDATE_AGENTROUTER", True),
        ("VALIDATION_NEW_REQUEST_LIMIT", 31),
        ("REUSE_VALIDATION_PROBE", True),
    ):
        if name not in known:
            source += f"\n{name} = {default!r}\n"
    if "BASELINE_MANIFESTS" not in known:
        source += "\nBASELINE_MANIFESTS = []\n"
    if "resume_sweep" not in source:
        source += '\nCONTROL.setdefault("resume_sweep", None)\n'
    if "DISPLAY" not in known:
        source += '\nDISPLAY = {"refresh_seconds": 2, "response_limit": 9, "response_characters": 1500, "terminal_lines": 8, "startup_timeout_seconds": 60, "stop_grace_seconds": 30}\n'
    if '"decision_workers"' not in source:
        source = source.replace(
            "CFG = StudyConfig.model_validate(STUDY)",
            'STUDY["agent"].setdefault("decision_workers", 1)\nSTUDY["agent"].setdefault("credential_mode", "shared")\nCFG = StudyConfig.model_validate(STUDY)',
        )
    for setting, default in (("show_prompts", True), ("prompt_limit", 3)):
        if setting not in source:
            source += f'\nDISPLAY.setdefault("{setting}", {default!r})\n'
    if "ACTION_PARSER =" not in source:
        source = source.replace(
            "CFG = StudyConfig.model_validate(STUDY)",
            prompt_controls_text() + "\nCFG = StudyConfig.model_validate(STUDY)",
        )
    if "SANCTION_DISCLOSURE =" not in source:
        default_disclosure = "transparent"
        for node in ast.parse(source).body:
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "STUDY" for t in node.targets)
                and isinstance(node.value, ast.Dict)
            ):
                for key, value in zip(node.value.keys, node.value.values):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "schema_version"
                        and isinstance(value, ast.Constant)
                        and value.value == 1
                    ):
                        default_disclosure = "opaque"
        source = source.replace(
            "CFG = StudyConfig.model_validate(STUDY)",
            disclosure_config_text(default_disclosure)
            + "\nCFG = StudyConfig.model_validate(STUDY)",
        )
    if "LOCAL_TRUNCATION_RETRIES =" not in source:
        source = source.replace(
            "CFG = StudyConfig.model_validate(STUDY)",
            '\nLOCAL_TRUNCATION_RETRIES = STUDY["agent"].get("local_truncation_retries", 0)\n'
            'LOCAL_RETRY_OUTPUT_TOKENS = STUDY["agent"].get("local_retry_output_tokens")\n'
            'if CONNECTION == "ollama":\n'
            '    STUDY["agent"].update(local_truncation_retries=LOCAL_TRUNCATION_RETRIES, local_retry_output_tokens=LOCAL_RETRY_OUTPUT_TOKENS)\n'
            "CFG = StudyConfig.model_validate(STUDY)",
        )
    if "PROMPT_NUMBER =" not in source:
        source = source.replace("CFG = StudyConfig.model_validate(STUDY)",
            local_prompt_config_text(default=None) + "\nCFG = StudyConfig.model_validate(STUDY)", 1)
    next(cell for cell in updated["cells"] if cell["id"] == "configuration")["source"] = source
    template_ids = {cell["id"] for cell in updated["cells"]}
    extras: dict[str, list] = {}
    pending = []
    for cell in existing["cells"]:
        if cell.get("id") not in template_ids:
            saved = copy.deepcopy(cell)
            if saved["cell_type"] == "code":
                saved.update(execution_count=None, outputs=[])
            pending.append(saved)
        elif pending:
            extras.setdefault(cell["id"], []).extend(pending)
            pending = []
    cells = []
    for cell in updated["cells"]:
        cells.extend(extras.get(cell["id"], []))
        cells.append(cell)
    updated["cells"] = cells + pending
    return updated


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preserve-controls", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    path = root / "notebooks/00_research_control.ipynb"
    document = (
        upgrade(json.loads(path.read_text(encoding="utf8")))
        if args.preserve_controls and path.exists()
        else build()
    )
    path.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf8")
    print(path.relative_to(root))
