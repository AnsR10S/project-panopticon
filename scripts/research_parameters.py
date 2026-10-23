"""Explain each exposed setting next to the schema the runner actually validates."""

from __future__ import annotations

import json
from typing import Any

from research_control import Controls

from auditors_paradox.config import StudyConfig, V2Config
from auditors_paradox.evaluation.inference import AnalysisSettings

EXPLANATIONS = {
    "schema_version": (
        "identity",
        "File format version, fixed at 1; changing it is not an experiment.",
    ),
    "study_id": (
        "identity",
        "Output folder label. A new name alone does not make evidence scientifically independent.",
    ),
    "run_kind": (
        "identity",
        "smoke, pilot or confirmatory evidence folder. Confirmatory requires a frozen protocol hash; a label alone does not establish readiness.",
    ),
    "study_purpose": (
        "research",
        "research versus diagnostic interpretation. Scripted evidence always remains synthetic, whatever this label says.",
    ),
    "arm": (
        "research",
        "Arm for a standalone run. In this notebook's sweep, batch_arms supplies each actual arm; this base value is overwritten by assignment.",
    ),
    "n_agents": (
        "fixed",
        "Exactly three simultaneous firms. Changing the number needs a different validated implementation, not just a new value.",
    ),
    "rounds": (
        "research",
        "Decisions per firm per trajectory. Longer play changes opportunities for adaptation; it does not add independent replicates.",
    ),
    "seed": (
        "design",
        "First block seed; successive blocks add 1. Remote model sampling is not guaranteed reproducible or synchronized by this seed.",
    ),
    "memory_window": (
        "research",
        "Most recent rounds supplied to each firm, at most 100. More history changes information and token cost; old private notes are not magically restored.",
    ),
    "disclose_horizon": (
        "research",
        "True tells firms the final round; False hides the final count in their prompts. Compare horizons in separate frozen batches, not pooled as one treatment.",
    ),
    "primary_outcome": (
        "analysis",
        "gross_profit or price elevation is the declared run score. Switching after seeing results changes the analysis question.",
    ),
    "batch_arms": (
        "research",
        "Conditions assigned once per block in randomized order. Dropping an arm makes its contrasts unavailable. Numeric/fine extensions change mechanisms.",
    ),
    "analysis_last_rounds": (
        "analysis",
        "Number of final rounds averaged into each trajectory's score. Shorter tails emphasize late behavior and may be noisier; no new replication.",
    ),
    "protocol": (
        "identity",
        "Existing project-relative Markdown under docs/research defining the study. Choose a prospectively frozen protocol for confirmatory work.",
    ),
    "protocol_sha256": (
        "identity",
        "Exact protocol byte hash, or None for a pilot. A mismatch blocks execution. The notebook additionally freezes source and analysis.",
    ),
    "market.price_min": (
        "research",
        "Lowest permitted integer price tick. Changes the action space, reference solutions and possible losses.",
    ),
    "market.price_max": (
        "research",
        "Highest permitted price tick. Grid width is limited to 201 logit or 101 linear prices; references are recalculated.",
    ),
    "market.a": (
        "research",
        "Logit product quality/intercept, or linear demand intercept. Changing it changes demand and competitive/joint references.",
    ),
    "market.b": (
        "research",
        "Own-price slope in the historical linear market only; inactive for Calvano logit. Larger b makes own demand more price sensitive.",
    ),
    "market.d": (
        "research",
        "Rival-price slope in the historical linear market only; inactive for Calvano logit. Changes substitution across firms.",
    ),
    "market.c": (
        "research",
        "Marginal cost in the same integer ticks as price. Changes profits, incentives and reference prices.",
    ),
    "market.demand_model": (
        "research",
        "calvano is the current logit market; linear_differentiated is historical. Switching is a different economic environment.",
    ),
    "market.price_scale": (
        "research",
        "Ticks per currency unit for profit scaling and prompt explanation. Linear mode requires 1; coordinate units with all market parameters.",
    ),
    "market.mu": (
        "research",
        "Positive logit differentiation/noise parameter in ticks, inactive for linear demand. Changes demand sensitivity and strategic incentives.",
    ),
    "market.a_0": (
        "research",
        "Outside-option quality in logit, inactive for linear demand. Higher outside attractiveness reduces firms' demand.",
    ),
    "agent.backend": (
        "research",
        "scripted is synthetic; ollama is local inference; other backends are remote. Changing the backend/model changes the population being studied.",
    ),
    "agent.model": (
        "research",
        "Exact installed/local or gateway model ID. Qwen Code is a client, not the model; gpt-6.1-sol still selects prices.",
    ),
    "agent.client": (
        "execution",
        "sdk or genuine pinned qwen_code. Paid notebook runs require the qualified Qwen path and shared ledger; Ollama/scripted use sdk.",
    ),
    "agent.decision_workers": (
        "execution",
        "1 collects decisions sequentially; 3 runs isolated firms concurrently within a round. All valid prices are required before the market advances. Shared pacing and budgets still apply.",
    ),
    "agent.credential_mode": (
        "execution",
        "shared uses AGENTROUTER_API_KEY; per_firm uses AGENTROUTER_API_KEY_FIRM_0/1/2. Values stay in ignored .env/process memory, never in the notebook or study config. Extra keys do not establish independent provider quotas.",
    ),
    "agent.base_url": (
        "execution",
        "Gateway route or explicit local loopback URL ending /v1. Remote Ollama destinations are rejected. Route changes require renewed compatibility checks.",
    ),
    "agent.api_protocol": (
        "execution",
        "Wire format: chat/responses/anthropic/ollama. Current GPT gateway preset uses responses; native local Ollama exposes thinking/context controls.",
    ),
    "agent.temperature": (
        "research",
        "Sampling temperature or None to omit it. Model support varies. Changing sampling changes agent behavior, not just speed.",
    ),
    "agent.max_output_tokens": (
        "research",
        "Per-request generated-token ceiling, including provider reasoning where applicable. Too small can truncate before a price; larger limits increase reservations.",
    ),
    "agent.chat_token_parameter": (
        "execution",
        "max_tokens or max_completion_tokens for SDK chat requests. Inactive for Responses/native Ollama; guard may reject unsupported combinations.",
    ),
    "agent.max_action_attempts": (
        "design",
        "Total formatting attempts per pricing decision, including the first. Corrections continue that decision only; exhausted live parsing aborts the round.",
    ),
    "agent.timeout_seconds": (
        "execution",
        "Maximum time for one model attempt. Small timeouts can select out slow decisions and induce missing runs.",
    ),
    "agent.scripted_policy": (
        "diagnostic",
        "competitive fixes Nash prices; coordinated fixes joint-reference prices. Scripted only: these prove plumbing, not emergent collusion.",
    ),
    "agent.scripted_replies": (
        "diagnostic",
        "Optional three independent lists of exact scripted replies, overriding the scripted policy. Never use synthetic replies as empirical model evidence.",
    ),
    "agent.parse_failure_policy": (
        "design",
        "abort is mandatory for live agents. nash fallback is allowed only for scripted engineering checks and can contaminate economic outcomes.",
    ),
    "agent.action_parser": (
        "design",
        "legacy preserves pinned extraction; final_answer_v1 accepts explicit final commitments; final_answer_v2 also accepts a standalone DECISION heading; final_answer_v3 / final_answer_v4 accepts consistent repeated final sections and explicit Set my/the price commands. auto keeps its historical v1 choice for DeepSeek R1. Final parsers reject ambiguity and unfinished thinking; new parser versions require a new frozen study.",
    ),
    "agent.system_prompt_path": (
        "research",
        "Optional project-relative .txt inside prompts/pricing replacing the profile's system template. None keeps the selected profile; full contents are frozen and archived.",
    ),
    "agent.action_prompt_path": (
        "research",
        "Optional project-relative .txt replacing the decision template. Must retain round_number, memory_context and recent_messages. None keeps the profile template.",
    ),
    "agent.prompt_variables": (
        "research",
        "Literal custom_ named substitutions for custom template placeholders. Missing/unused names fail before inference. Values become agent-visible study inputs, never put credentials or other firms' private data here.",
    ),
    "agent.transport_attempts": (
        "execution",
        "Up to eight recorded transport attempts per parsing attempt. One attempt permits one transmission; retries preserve the same observation and consume the shared call/cost/time allowance.",
    ),
    "agent.retry_initial_seconds": (
        "execution",
        "Initial exponential delay for retryable transport errors. Provider delay and shared pacing can impose longer waits.",
    ),
    "agent.retry_max_seconds": (
        "execution",
        "Largest provider/requested transport retry delay accepted by the runner. Larger required delays cause failure; shared gateway cooldown is additional.",
    ),
    "agent.min_request_interval_seconds": (
        "execution",
        "Minimum dispatch spacing: shared in sequential mode, per firm with three workers. Qwen additionally enforces one shared rolling-minute RPM/TPM allowance across keys, retries and restarts.",
    ),
    "agent.context_tokens": (
        "research",
        "Native Ollama context allowance, not the rolling-memory length. Inactive for remote/Qwen; insufficient context can truncate information.",
    ),
    "agent.local_thinking": (
        "research",
        "Native Ollama thinking switch or None to use model default. Inactive for remote clients and SDK chat; changes inference behavior.",
    ),
    "agent.local_num_gpu": (
        "execution",
        "Native Ollama GPU-layer request, None leaves server default. Inactive for remote/SDK chat; allocation is not proof of actual GPU use.",
    ),
    "agent.local_repeat_penalty": (
        "research",
        "Native Ollama repetition penalty: None retains the server default; 1.2 discourages repeated text. Changes the sampling distribution and requires a new frozen study.",
    ),
    "agent.local_repeat_last_n": (
        "research",
        "Number of generated tokens inspected for repetition, not market-memory rounds. None retains the server default; 512 is the exploratory DeepSeek candidate.",
    ),
    "agent.local_output_format": (
        "research",
        "text keeps historical output; price_json asks native Ollama to constrain output to one price within the configured grid. Requires json_price_v1 and a standard price-only profile. This changes formatting and decoding, so start a new study.",
    ),
    "agent.local_truncation_retries": (
        "research",
        "Native local only: 0 preserves historical abort; 1 or 2 restarts the identical observation after output truncation. Every attempt is recorded and counted. Freeze this selection before a new study.",
    ),
    "agent.local_retry_output_tokens": (
        "research",
        "Explicit larger output ceiling for bounded local restarts. Must exceed max_output_tokens and leave room in context_tokens; never accepts a partial price or carries partial thinking into memory.",
    ),
    "agent.reasoning_effort": (
        "research",
        "Responses reasoning setting or None to omit. Must use a compatible model/protocol; changing it can change strategic competence.",
    ),
    "agent.prompt_profile": (
        "research",
        "qualitative_v2 and explicit_economics_v2 share strict price-only output and alternative-evaluation instructions; only the latter adds the existing exact equations. Historical profiles remain unchanged. economics_private_note explicitly permits a latest firm-private strategy note. Prompt changes are separate exploratory designs.",
    ),
    "auditor.enabled": (
        "research",
        "False disables real detector flags. Scheduled neutral Placebo/fixed notices still occur; disabling flags is not equivalent to Silent under every schedule.",
    ),
    "auditor.mode": (
        "research",
        "market checks sustained gross-profit elevation plus narrow price spread; per_firm checks each firm's window-mean price above an interpolated price cutoff. These are distinct detectors.",
    ),
    "auditor.cooldown_rounds": (
        "research",
        "A firm can be flagged again only when current round minus last flag is greater than this value. Five means the next flag is eligible six rounds later.",
    ),
    "auditor.window": (
        "research",
        "Market mode requires every round in the window to pass both tests; per_firm uses mean own price across the window. Larger windows reject brief spikes but delay detection.",
    ),
    "auditor.elevation_threshold": (
        "research",
        "Market: strict gross-profit elevation > threshold in every window round. Per-firm: mean price > Nash + threshold*(joint minus Nash). Same numeric value has different meaning across modes.",
    ),
    "auditor.maximum_spread": (
        "research",
        "Market mode requires max price minus min price <= this many ticks in every window round. Inactive for per_firm mode; smaller values require tighter alignment.",
    ),
    "feedback.warning_source": (
        "research",
        "live sends truthful behavior-triggered warnings; fixed replaces warnings with scheduled neutral/hypothetical notices. Fixed is a notification diagnostic, not the natural-auditing question.",
    ),
    "feedback.schedule_rounds": (
        "research",
        "Preselected notice SOURCE rounds for Placebo or fixed feedback, effective the next round. Inactive for live Public/Private/Silent. Same schedule across blocks is not independently yoked timing.",
    ),
    "sanctions.positive_profit_rate": (
        "research",
        "Fraction of positive gross profit deducted only in fine arms. Inactive in the four core observe-only conditions; adding fines changes the causal question.",
    ),
    "budget.max_calls": (
        "execution",
        "Maximum recorded attempts per trajectory, including failed transports and formatting corrections. Must cover at least 3*rounds; a tight cap can stop early.",
    ),
    "budget.enforce_spending_caps": (
        "execution",
        "Notebook ENFORCE_SPENDING_CAPS defaults to False: optional per-run/workflow money caps are ignored while accounting and the cumulative research allowance remain active. True enables those optional caps. Request, rate, Stop and time limits are separate. Legacy files without this field retain their original cap enforcement.",
    ),
    "budget.max_cost_usd": (
        "execution",
        "Optional per-trajectory conservative reservation cap; ignored when ENFORCE_SPENDING_CAPS is False. Not a predicted bill. When enabled, the batch allowance must cover its sum across arms and blocks.",
    ),
    "budget.input_usd_per_million": (
        "execution",
        "User-entered input-token rate. No automatic price verification; gateway multipliers/discounts can differ. Inactive for local/scripted charges.",
    ),
    "budget.output_usd_per_million": (
        "execution",
        "User-entered output/reasoning-token rate. Update both rates when changing model; unknown failed-request usage retains its hold.",
    ),
    "budget.request_overhead_tokens": (
        "execution",
        "Extra input reservation beyond UTF-8 request bytes for cost and shared token pacing. A conservative accounting margin, not measured model tokens.",
    ),
    "budget.max_elapsed_seconds": (
        "execution",
        "Wall-time limit for one trajectory, including pacing/retry waits. None means no per-run limit; the shared paid workflow has another limit.",
    ),
    "budget.stop_file": (
        "execution",
        "Project-relative cooperative kill-switch file. Existing file blocks launch. The notebook never silently deletes it or resumes a failed trajectory.",
    ),
    "control.replicates": (
        "design",
        "Independent trajectory blocks, each containing all selected arms. This increases evidence for inference; rounds and firms are not substitutes.",
    ),
    "control.assignment_seed": (
        "design",
        "Randomizes arm execution order within blocks. Does not randomize model internals or neutral-notice timing.",
    ),
    "control.requests_per_minute": (
        "execution",
        "Shared rolling-minute request allowance across every key and retry. The parallel preset uses 3; this does not establish the gateway's actual quota. Lower values can queue concurrent firm workers.",
    ),
    "control.tokens_per_minute": (
        "execution",
        "Shared conservative input-byte-plus-output-cap reservation per minute, not a quota learned from the gateway. One request exceeding it is rejected.",
    ),
    "control.new_request_limit": (
        "execution",
        "Additional paid workflow attempts, counting every transmission reservation. Also subject to the existing cumulative 4000-attempt guard.",
    ),
    "control.new_cost_limit_usd": (
        "execution",
        "Optional additional workflow allowance; ignored when ENFORCE_SPENDING_CAPS is False. Cost accounting and the cumulative research allowance remain active.",
    ),
    "control.lifetime_cost_limit_usd": (
        "execution",
        "Cumulative research allowance, default $1,000 including prior costs and unresolved-charge holds. This total remains enforced even when optional per-run/workflow caps are disabled.",
    ),
    "control.workflow_seconds": (
        "execution",
        "Whole paid Qwen workflow timeout. Local execution uses per-run max_elapsed_seconds; this shared setting is inactive for scripted/Ollama.",
    ),
    "control.label": (
        "identity",
        "Portable label for new immutable plan/run folders. A random suffix prevents overwriting historical evidence.",
    ),
    "control.resume_sweep": (
        "execution",
        "None starts fresh. A saved failed sweep continues compatible exploratory rate-limit or known native-local output-limit failures in a new evidence folder. Completed rounds/accepted decisions are reused without requests. Restore the frozen settings; changed source or ambiguous requests still block continuation.",
    ),
    "analysis.alpha": (
        "analysis",
        "Family error probability for bounded simultaneous intervals; confidence is 1-alpha. Holm-adjusted p-values are compared to this if a prospectively specified decision is appropriate.",
    ),
    "analysis.bootstrap_samples": (
        "analysis",
        "Resamples for approximate percentile intervals. More resamples reduce numerical noise, not uncertainty from too few independent blocks.",
    ),
    "analysis.bootstrap_min_pairs": (
        "analysis",
        "Minimum retained pairs for an exploratory bootstrap interval, default 10. Lowering this does not create information or validate the interval.",
    ),
    "analysis.exact_sign_flip_max_pairs": (
        "analysis",
        "Enumerate all 2^n sign patterns up to this pair count; use Monte Carlo above it. Changes computation, not experimental replication.",
    ),
    "analysis.monte_carlo_samples": (
        "analysis",
        "Number of random sign flips beyond the exact-enumeration limit, with plus-one correction. More draws improve numerical precision, not power from missing blocks.",
    ),
    "analysis.seed": (
        "analysis",
        "Reproducible resampling seed; keep fixed prospectively. Trying seeds for better p-values is invalid analysis selection.",
    ),
}

EXPLANATIONS["agent.client_heap_mb"] = (
    "execution",
    "Qwen Code's bounded Node old-generation heap in MiB; startup auto-relaunch is disabled. This is client RAM, not the model context or firm history. Ignored by SDK/local models. Too small can stop the client before transmission; a change needs a new frozen operational configuration.",
)
EXPLANATIONS["schema_version"] = (
    "identity",
    "1 preserves historical behavior; 2 requires the explicit v2 group. Switching versions changes message, memory and financial semantics.",
)
for name, explanation in {
    "sanction_disclosure": "opaque withholds detector and penalty details; transparent preserves original V2 instructions. Missing historical settings mean transparent. This is independent of market knowledge and requires a separate study identity/protocol.",
    "sanction_timing": "event deducts only on cooldown-eligible flags; continuing deducts every qualifying round. Separate study families and baselines are required.",
    "graduated": "True selects persistence tiers; False uses sanctions.positive_profit_rate. These are diagnostic settings, not validated deterrence rates.",
    "severity_levels": "Ordered tiers: name, qualifying_rounds (minimum consecutive qualifying assessments), rate (fraction of positive gross profit). Failed qualification resets persistence. Announcement cooldown does not alter severity.",
    "warning_template": "Versioned factual warning substance. Changing wording is a treatment change; V1 wording remains available.",
    "placebo_template": "uncertainty-v2 is an active uncertainty/salience control; neutral preserves historical wording. Neither automatically matches endogenous warning timing.",
    "manual_enabled": "Enables exploratory boundary interventions. Accepted edits mark the trajectory researcher_intervened and exclude it from untouched treatment comparisons; forbidden in confirmation.",
    "manual_max_chars": "Maximum custom-message characters. Longer messages consume context and can change salience; all requests still face budget/context guards.",
    "index_enabled": "Maintain a reconstructible SQLite index after each boundary. Raw evidence remains authoritative; index failure is reported as degraded.",
    "index_path": "Project-relative local SQLite file. Use a local disk, not a network share. Changing this affects storage only.",
    "history_format": "table, prose or compact JSON, with identical authorized fields and precision. The table labels own share among sellers separately from demand including the outside option. Formatting can affect behavior; freeze the version.",
    "note_max_chars": "Maximum latest own private strategy note, at most 240 characters. Deterministic clipping is recorded; rivals never receive it.",
    "breakdown_rounds": "Consecutive nonqualifying rounds after an episode required to declare operational breakdown. Changes a descriptive definition, not the detector.",
    "hypothesis_family": "warning declares the four core contrasts; sanctions_event and sanctions_continuing declare the four separate financial contrasts. This is a prospective analysis choice.",
    "meaningful_effect": "Provisional smallest meaningful normalized elevation effect for planning, a researcher choice. Does not establish power by itself.",
    "power_target": "Desired marginal power for a planning scenario. Joint power and simulation assumptions must also be reported before fixing replication.",
    "pacing_successes": "Eligible successes after the last rate limit/cooldown needed to halve adaptive spacing. Old in-flight successes cannot cause recovery.",
    "pacing_recovery_floor_seconds": "Clear adaptive spacing when its next halving falls below this floor; configured RPM/TPM caps still apply.",
}.items():
    EXPLANATIONS["v2." + name] = (
        "execution" if name.startswith("index_") or name.startswith("pacing_") else "research",
        explanation,
    )


def parameter_rows(
    study: dict[str, Any], controls: dict[str, Any], analysis: dict[str, Any]
) -> list[dict[str, Any]]:
    """Require an explanation for every supported field and show its current value and bounds."""
    result = []
    for prefix, model, values in (
        ("", StudyConfig, StudyConfig.model_validate(study).model_dump()),
        ("control.", Controls, Controls.model_validate(controls).model_dump()),
        ("analysis.", AnalysisSettings, AnalysisSettings.model_validate(analysis).model_dump()),
    ):
        schema = model.model_json_schema()

        def visit(properties: dict[str, Any], current: dict[str, Any], stem: str) -> None:
            for name, spec in properties.items():
                path = stem + name
                if name == "v2" and stem == "":
                    visit(
                        schema["$defs"]["V2Config"]["properties"],
                        current[name] or V2Config().model_dump(),
                        "v2.",
                    )
                    continue
                if "$ref" in spec:
                    visit(
                        schema["$defs"][spec["$ref"].split("/")[-1]]["properties"],
                        current[name],
                        path + ".",
                    )
                    continue
                category, explanation = EXPLANATIONS[path]
                constraints = {
                    k: v for k, v in spec.items() if k not in {"title", "default", "description"}
                }
                result.append(
                    {
                        "parameter": path,
                        "value": current[name],
                        "kind": category,
                        "meaning_and_change": explanation,
                        "validated_values": json.dumps(constraints, ensure_ascii=False),
                    }
                )

        visit(schema["properties"], values, prefix)
    if {row["parameter"] for row in result} != set(EXPLANATIONS):
        raise ValueError("Parameter explanations and supported schema disagree")
    return result
