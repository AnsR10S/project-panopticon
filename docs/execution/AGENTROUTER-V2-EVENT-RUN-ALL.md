# AgentRouter V2 Event — Run All

Open `notebooks/00_research_control.ipynb`, reload the saved file if your editor still shows
the previous cells, and select **Run All**. The notebook is deliberately saved with paid
execution enabled at the user's request. No paid requests were sent while preparing it.

| Setting | Saved value |
|---|---|
| Connection / client | AgentRouter / pinned Qwen Code |
| Model / protocol | `gpt-5.6-sol` / Responses, low reasoning |
| Preset | `v2_event` |
| Arms | Silent, Fine Only, Private Fine, Public Fine |
| Workload | One block, 50 rounds per arm, 600 accepted decisions minimum |
| Outcome | Gross-profit elevation over the final 25 rounds |
| Memory / horizon | 20-round authorized history / hidden final round |
| Knowledge / history | Explicit economics V2 / table |
| Credentials | Three firm-specific names from the ignored `.env` |
| Concurrent decisions | Three, behind the existing simultaneous-round barrier |
| Pacing | Existing shared 3 requests/minute and 20,000 token reservations/minute |
| Automatic readiness | Enabled, at most 31 additional validation requests |
| Optional spending caps | Existing disabled selection preserved; accounting/cumulative ceiling retained |
| Execution switches | `EXECUTE=True`, `ALLOW_PAID=True` |

The required credential names are `AGENTROUTER_API_KEY_FIRM_0`,
`AGENTROUTER_API_KEY_FIRM_1` and `AGENTROUTER_API_KEY_FIRM_2`. Their presence and the local
client qualification were checked without printing values or making inference requests.
The gateway still controls authentication, model access, availability and billing.

## Run All sequence

1. Find the project root and load the helpers. Older control helpers can refresh if no owned
   worker is active; active work is never silently reloaded.
2. Validate the configuration and show the full plan, parameters and initial prompts.
3. Freeze a new immutable plan. Old failed plans remain intact.
4. Reuse matching completed AgentRouter validation, or run the missing operational checks.
5. Launch the four-arm event-sanction experiment only after readiness succeeds.
6. Keep the launch cell active while progress and analysis finish, then show saved exports.

The previous failure occurred because GPT-6.1-sol's completed validation did not qualify
GPT-5.6-sol. The readiness cell retains this restriction instead of bypassing it. Matching
checks require the same model, route, protocol, prompt profile, output allowance, temperature
and reasoning effort. Successful connection-only probes do not qualify a full study.

Missing validation runs separate connection, three-round Silent smoke and six frozen
observation checks. Existing successful probes may be reused. Diagnostics are labeled and
kept outside research trajectories; their forced-price tasks never enter the financial study.
They count against the same recorded allowance and pacing controls. Failure stops Run All
before the experiment, with an explicit validation error and a saved log.

Inspect `agentrouter-validation-<id>.log` and `agentrouter-readiness.json` beside the frozen
plan for readiness progress. The actual study uses the existing `worker.log` and launch UI.
Provider failures are not hidden, and unfinished or ambiguous answers are never invented.

## Meaning of V2 Event

The detector still uses the existing window, elevation, spread and cooldown settings.
Financial deductions occur on eligible events, with the existing configured severity tiers.
Fine Only applies the financial regime without an event message; Private Fine and Public Fine
add differently framed notices. This is a financial-regime study, distinct from the warning-only
Public/Private/Placebo experiment. Settings remain editable in the central configuration cell.

There is one independent trajectory per arm. This provides exploratory descriptions, not
adequate inferential replication. Accepted manual interventions remain recorded and excluded
from untouched contrasts. Change source/configuration before freezing a new plan, never midway
through a running batch.

## Verification performed

The actual saved notebook executed all 18 code cells in an offline verification with scripted
agents: four complete 50-round trajectories and 600 accepted decisions, with analysis and CSV
exports. Readiness tests simulate success, failure, cached validation, model/settings mismatch,
preview and paid-permission boundaries. Local Qwen qualification and credential-name checks
passed. This is not a claim that the selected model passed new paid validation; Run All performs
that check against the gateway.

| Concept | Plain-language meaning |
|---|---|
| Readiness | Operational checks required before the research batch |
| Arm | One assigned financial/message condition |
| Event sanction | A deduction restricted to a cooldown-eligible event |
| Block | Fresh trajectories grouped for comparison |
| RPM / TPM | Shared request and token-reservation pacing limits |
| Frozen plan | Saved settings/source that cannot silently change during execution |
