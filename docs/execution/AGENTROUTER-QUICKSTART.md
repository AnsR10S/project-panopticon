# AgentRouter: where to paste the token and how to start

**Current access finding, October22:** a live check on the main host returned401
with `unauthorized client detected` for the standard Python SDK. The gateway rejected
the application client; this does not establish an invalid token or unavailable model.
Request authorization for this custom research application before further inference.
See the [diagnosis](../reviews/2026-10-22-agentrouter-connectivity/REPORT.md).

The selected model is **`gpt-6.1-sol`**. Your latest account screenshot lists **$3 per
million input tokens and $15 per million output tokens**. These are account-displayed
rates, not an independently verified bill. Make sure your token permits this exact
model; an older restriction to `gpt-5.6-sol` must be changed before using it.
One token serves all three firms, which retain separate histories. Do not paste the
token in chat, a YAML/JSON config, source code or a tracked file. **Paste it only in
the masked PowerShell prompt displayed when you run Smoke or Full.** The project
reads `AGENTROUTER_API_KEY` at runtime; `.env` is not loaded automatically.

## 1. Prepare the two configs once

From the project root:

```powershell
.\scripts\agentrouter.ps1 -Action Setup -Model gpt-6.1-sol -InputRate 3 -OutputRate 15
```

The command uses the prices from your latest screenshot. Recheck your account before
spending if prices change. Omitting the rate arguments makes setup ask for them; use
a decimal point without a currency symbol. This preparation makes no model calls
and never asks for a secret. If the two configs already exist, skip Setup and inspect
them instead; Setup deliberately refuses to overwrite them.

This creates two ignored files:

- `configs/local/agentrouter-smoke.json`
- `configs/local/agentrouter-full.json`

It defaults to the OpenAI-compatible URL `https://agentrouter.org/v1`, as specified
in the [main AgentRouter documentation](https://github.com/agentrouter-org/docs/blob/main/en/codex.md).
The key comes from AgentRouter, not directly from OpenAI. The `co` host has its own
guide; do not assume a key or balance applies to both hosts. Only select it if your
account explicitly instructs you to:

```powershell
.\scripts\agentrouter.ps1 -Action Setup -BaseUrl 'https://co.agentrouter.org/v1'
```

Setup refuses to overwrite existing configs. On subsequent changes, edit the relevant
JSON file. No automatic cross-host or cross-model fallback is performed.

Default allowances are$1 for Smoke and$20 **per trajectory** for Full. You can choose
other allowances at setup, for example with `-SmokeBudget 0.50 -FullBudgetPerRun 10`.
These are configured-rate reservation ceilings, not expected bills or guaranteed
provider spending limits. Four full arms at$20 each declare$80 per block. Review the
plan and, where available, set a separate gateway account/token quota.

## 2. Check access, then run the tiny paid smoke check

To try the three model IDs directly with one tiny inference request each:

```powershell
.\scripts\agentrouter.ps1 -Action TryModels
```

Paste the token once at the hidden prompt. It tries `gpt-6.1-sol`, `gpt-5.6-sol`,
then `gpt-6-astra`, asking only for `OK`. Maximum workload is three requests with
256 output tokens each, no transport retries, a180-second total limit and a$0.10
shared estimated reservation ceiling. The screenshot rates are3/15 for6.1-sol and
8/40 for the other two, per million input/output tokens. Gateway billing controls
the actual charge; failed-request billing is unknown. Recheck rates if they change.

Results and raw evidence go to a fresh folder under
`runs/diagnostics/agentrouter-model-probes/`; the command prints `results.csv`.
Each requested and returned model is recorded separately. A403 rejection of one
model permits checking the next;401,429, exhausted quota or an operator stop halt
further attempts. Your token must permit every model you want to try. No research
config is changed, no model is selected automatically, and these are connectivity
diagnostics rather than experimental observations. A reply, empty reply and rejected
request are reported distinctly. After inspecting results, deliberately select a
working model and its correct prices before a research smoke run.

Before another smoke attempt after an access failure, run:

```powershell
.\scripts\agentrouter.ps1 -Action Check
```

Paste the token in the hidden prompt. This performs one authenticated `GET /v1/models`
request to the configured host, without requesting inference. It shows only a safe
HTTP error status or whether `gpt-6.1-sol` is in the returned catalogue. It does not
prove the model's completion route works. There is no automatic host/model fallback.

Opening the base URL `/v1` in a browser can return404: that is a different request
from listing models or sending `POST /v1/chat/completions`. A browser404 at the base
path does not establish that the model endpoint is broken.

| Check result | Next action |
|---|---|
| `client_not_authorized` | The gateway rejected this application client. Contact provider support for permission; do not repeat model probes or assume the token is invalid |
|401 without a more specific classification | Verify the endpoint, credential and expiry;401 alone does not distinguish bad credentials from client authorization |
|403 | Check key permissions, model/resource access, IP policy and any gateway/WAF restriction; this status alone does not identify which one failed |
|404 | Verify the documented model-list endpoint and the account's exact base URL before sending inference |
| Catalogue accessible, model listed | Authentication/catalogue access worked; proceed to a tiny smoke check |
| Catalogue accessible, model absent | Check account resources and exact model ID with the provider; do not silently substitute a model |

The old run logs group401/403 together as `authentication_or_permission`; their exact
status cannot be recovered from those records. New model failures retain the numeric
HTTP status without storing raw provider error bodies. A dashboard expiry rendered
as1970 alone is not sufficient to diagnose expiration; check its Never-expires setting.

When the access check succeeds:

```powershell
.\scripts\agentrouter.ps1 -Action Smoke
```

The launcher validates the config and shows the plan before requesting the token.
At the line below, paste your actual AgentRouter token and press Enter:

```text
PASTE your AgentRouter API token here, then press Enter (hidden):
```

The token is assigned to `AGENTROUTER_API_KEY` only while the launcher runs. It is
removed afterward, or the earlier environment value is restored if one existed.
The pasted secret is hidden and is not part of the shell command/history.

Smoke runs **one Silent trajectory of three rounds**: nine intended decisions,
up to18 request attempts, one estimated$1 allowance by default. It is a small real
API call test, so it can spend credits. It does not generate a synthetic misconduct
warning or prove the model is ready for research. This setup has not executed it for you.

The launcher automatically analyzes terminal evidence and prints locations for
`report.md`, `runs.csv`, `rounds.csv` when valid rounds exist, and `usage.json` on success.
Look for `status=complete`, `included=True`, nine accepted decisions, zero fallback,
reasonable parsing overhead and the expected returned model identity. Read several
responses from the run's `events.jsonl` as well: correct formatting alone is insufficient.

## 3. Inspect and edit the full operational pilot

```powershell
notepad .\configs\local\agentrouter-full.json
.\scripts\agentrouter.ps1 -Action Plan -Replicates 1
```

| Setting | Default | Edit it when |
|---|---|---|
| `agent.model` | `gpt-6.1-sol` | Your token explicitly enables a different exact model ID |
| `agent.base_url` | `https://agentrouter.org/v1` | Your account documents a different supported endpoint |
| `rounds` |100 per arm | Choosing a different declared duration before a fresh pilot |
| `memory_window` |20 | Running a separate, explicitly labeled memory experiment |
| `analysis_last_rounds` |20 | Selecting the endpoint window before generation; must fit rounds |
| `batch_arms` | Silent, Private, Public, Placebo | Keep these for the four-condition core |
| `agent.max_output_tokens` |768 | Replies are truncated or contain no usable visible price; review budget too |
| `agent.chat_token_parameter` | `max_completion_tokens` | Gateway explicitly requires `max_tokens` instead |
| `budget.max_cost_usd` |20 per arm/run | You choose a different estimated allowance |
| `budget.input_usd_per_million` / `output_usd_per_million` |3 input /15 output with the command above | Account rates change or were entered incorrectly |
| `budget.max_calls` |900 per arm/run | Deliberately changing the bounded request allowance |
| `budget.max_elapsed_seconds` |3600 per arm/run | Deliberately changing the runtime allowance |
| `feedback.schedule_rounds` |10,30,50,70,90 | Duration changes require a compatible predeclared placebo schedule |

Changing rates or budgets does not require pasting the token into a config. Do not
edit study source, prompts or protocol during a sweep: mixed source identities are
rejected by analysis. A new model, memory window or duration requires a new pilot
identity and must not be pooled with the old Qwen experiment.

## 4. Run the full four-arm operational test

After inspecting the smoke evidence:

```powershell
.\scripts\agentrouter.ps1 -Action Full -Replicates 1
```

Paste the token at the masked prompt again. Default workload: **four arms ×100 rounds
×three firms =1,200 accepted decisions**, plus any bounded recovery. The allowance
is four times the configured per-run ceiling, or$80 with the setup default. Each arm
starts with fresh memory and market state. The actual bill depends on recorded usage
and gateway billing, and the experiment may stop at its conservative reservation limit.

The launcher uses a new timestamped sweep ID each time and prints the output paths.
Increasing replication later uses `-Replicates 3` or another explicit count, multiplying
workload and declared allowance. One full block is still a pilot, not a powered
confirmatory study. The [protocol](../research/agentrouter-pilot-2026-10-22.md) records
scope and unresolved scientific gates.

## 5. Stop and common failures

From another PowerShell window at the project root:

```powershell
New-Item -ItemType File -Path .stop -Force | Out-Null
```

This prevents new requests; an in-flight request can finish or time out first.
Ctrl+C also interrupts work. Evidence already saved is retained. To allow a deliberate
new run later, remove only the stop marker:

```powershell
Remove-Item -LiteralPath .stop
```

| Symptom | Action |
|---|---|
| OpenBLAS memory allocation failure before validation | The launcher now limits `OPENBLAS_NUM_THREADS` and `OMP_NUM_THREADS` to1 for its Python processes, restoring previous values on exit. Retry using the updated launcher. For direct CLI commands, set `$env:OPENBLAS_NUM_THREADS='1'` and `$env:OMP_NUM_THREADS='1'` in that terminal before running Python. If failure persists, check available RAM/pagefile and close unused workloads before retrying |
|401/403 or authentication/permission failure | Read `error_kind` first: `client_not_authorized` needs provider authorization for the application. Otherwise verify endpoint, token and model permissions; do not infer an invalid token from the status alone |
| Model not found / unsupported route | Check the model ID and documented base URL. A selected dashboard restriction alone does not prove that this route serves it |
| Unsupported `max_completion_tokens` | Change `agent.chat_token_parameter` to `max_tokens` in the affected config, preserve the failure and retry with a new run ID |
| Unsupported temperature | Generated configs already omit it via `temperature: null`; leave it null unless this route supports a chosen value |
| Empty/truncated answer or exhausted parsing | Inspect saved response and usage. Increase the output allowance only after reviewing the cost cap; no fallback price is executed |
| Estimated-cost reservation budget exhausted | Inspect completed evidence, rate units and budget. Reservation uses input bytes conservatively; it can stop before a token-only forecast predicts |
|429 / temporary gateway error | Bounded retry is active. Inspect the saved failure if retries exhaust; do not start a large replacement batch blindly |
| Missing `openai` SDK | Run `uv sync --frozen --all-extras` before retrying; it installs dependencies without inference |
| Operator stop present | Inspect previous evidence, then remove `.stop` when ready for a new run |
| `included=False` or incomplete arm | Read `runs.csv` exclusion/status and the saved sweep; do not silently replace or pool failed attempts |

## Concepts

| Term | Meaning |
|---|---|
| API token | The secret giving access to your AgentRouter account |
| Arm | One condition: Silent, Private, Public or Placebo |
| Round | One market interaction with three firm decisions |
| Replicate / block | One fresh grouped repetition of the assigned arms |
| Smoke | Tiny paid connectivity/output/evidence check |
| Full test | Larger operational pilot; does not imply confirmed research findings |
# Qwen Code adapter status

A separate genuine-client adapter has been implemented as a guarded prototype.
It failed local qualification and paid use remains blocked. See the
[Qwen client setup guide](QWEN-CLIENT-SETUP.md) and
[qualification evidence](../reviews/2026-10-22-qwen-client/REPORT.md).

