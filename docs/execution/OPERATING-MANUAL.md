# Operating manual — local first, AgentRouter later

Version 1.2.0 · October 20, 2026

For a step-by-step first session with explanations, use the [first-run walkthrough](FIRST-RUN-WALKTHROUGH.md). This manual remains the complete operational reference.

This walkthrough explains setup, operation, stopping, inspection, recovery and code changes. Run commands from the project root. All filesystem paths below are project-relative.

Start with offline accounting checks, then a local model, then measurements at the intended memory length. Prepare AgentRouter with actual account rates and use a tiny paid calibration only when you choose to spend. A larger study comes after feasibility and a frozen analysis plan.

## Contents

1. [System map](#1-system-map)
2. [Project installation](#2-project-installation)
3. [Local model setup](#3-local-model-setup)
4. [First local run](#4-first-local-run)
5. [Tokens and cost estimates](#5-tokens-and-cost-estimates)
6. [AgentRouter setup](#6-agentrouter-setup)
7. [Batch size and seeds](#7-batch-size-and-seeds)
8. [Every stop mechanism](#8-every-stop-mechanism)
9. [Failures and saved data](#9-failures-and-saved-data)
10. [Troubleshooting](#10-troubleshooting)
11. [Settings to edit](#11-settings-to-edit)
12. [Code changes and exceptions](#12-code-changes-and-exceptions)
13. [Reading results](#13-reading-results)
14. [Reproducibility and publication](#14-reproducibility-and-publication)
15. [Primary references](#15-primary-references)

## 1. System map

Three separate agent histories share a simulated market. Each firm calls the selected model with its own prompt and memory. One loaded local model can serve all three firms; three model copies are unnecessary.

Agents choose prices. They cannot execute tools, read the evidence directory, directly message a rival, or see its private notes. They receive public past prices and their own market share/profit. The initial observed prices are explicitly identified as the competitive reference.

~~~text
Read configuration and check inputs
  → freeze all three views from completed history
  → obtain three valid prices, with bounded logged attempts
  → execute the simultaneous price vector once
  → calculate gross profit
  → evaluate the auditor
  → apply any fines and calculate net profit
  → prepare notices for the next decision
  → update each firm's private memory
  → commit the round to disk
  → continue or seal the run
~~~

Sequential HTTP requests still implement simultaneous information: all views are frozen before the first request. A notice after round t can first affect round t+1.

Direct notice text enters one decision only. The model can preserve a reaction in its own returned notes; the upstream memory clips each note to 240 characters and retains the configured number of records. This is bounded context memory, not weight training. A hidden horizon removes the final-round count from prompts; current round numbers and the actual fixed stopping round remain.

| Component | Responsibility | File |
|---|---|---|
| CLI | Commands and exit codes | [cli.py](../../src/auditors_paradox/cli.py) |
| Config | Validate settings before requests | [config.py](../../src/auditors_paradox/config.py) |
| Model recorder | Attempts, retry decisions, usage and budgets | [model_calls.py](../../src/auditors_paradox/adapters/model_calls.py) |
| Adapter | Pinned agents/market and study prompts | [collusionlab.py](../../src/auditors_paradox/adapters/collusionlab.py) |
| Economics | Independent equations and grid checks | [economics.py](../../src/auditors_paradox/economics.py) |
| Runner | Ordering, rejected actions, fines and persistence | [runner.py](../../src/auditors_paradox/runner.py) |
| Auditor | Trigger history and cooldown | [auditing.py](../../src/auditors_paradox/auditing.py) |
| Router | Content, audience and next-round delivery | [router.py](../../src/auditors_paradox/feedback/router.py) |
| Evidence | Hash chain, round commits, atomic manifest | [evidence.py](../../src/auditors_paradox/evidence.py) |
| Evaluation | Fidelity, economic scores and run-level comparisons | [evaluation](../../src/auditors_paradox/evaluation) |
| Usage profile | Measured tokens and conditional forecasts | [usage.py](../../src/auditors_paradox/usage.py) |

### Markets and units

Historical configurations use linear differentiated demand. New local-first examples use Calvano-style multinomial logit with an outside option:

~~~text
q_i = exp((a - p_i)/mu) /
      [exp(a_0/mu) + sum_j exp((a - p_j)/mu)]

gross_profit_i = ((p_i - c) / price_scale) * q_i
~~~

The new example sets a=200, c=100, mu=25, a_0=0 and integer prices 100..250. With price_scale=100, 137 ticks means 1.37 currency units. Agents see prices and costs in ticks; recorded profit is in currency units. Quantities include the outside option; displayed own market share is relative to the active firms.

The actual-grid Nash reference is 137 and joint-profit reference is 200. Per-firm rewards are approximately 0.1201119467 and 0.25. Calibration checks every unilateral grid deviation and every three-firm grid profile.

This is a familiar demand family, not an automatic replication of another paper's prompts, firm count, precision, memory, model or training. Changing one demand-name string would leave units, scoring and validation inconsistent.

### Conditions

| Name | Intervention |
|---|---|
| silent | No audit notice |
| private | Identical individual notices to all three; other recipients unannounced |
| public | Same warning body with an announcement that all three receive it |
| placebo | Truthful neutral bulletin on an independent fixed schedule |
| public_numeric | Public notice repeating flagged firms' observed prices |
| public_fine | Public notice and actual positive-profit deductions |
| private_fine | Individual-to-all notice with the matching deduction rule |

The first local pilot selects four core arms. The expanded configuration retains all seven. Starting small does not remove numeric warnings or fines from the approved scope.

## 2. Project installation

Install Git and uv. The supported launcher is Windows PowerShell.

~~~powershell
git --version
uv --version
.\scripts\setup.ps1
.\scripts\run_checks.ps1
~~~

Setup restores the exact upstream commit from the vendored bundle when present, otherwise from the public repository, and installs uv.lock into .venv. New checks write to .cache/checks instead of overwriting historical release evidence.

~~~powershell
$ap = '.\.venv\Scripts\auditors-paradox.exe'
& $ap --version
& $ap doctor
~~~

A public checkout can pass doctor with private-source verification unavailable. Explicit verify-sources returns 2 when the private archive is absent. A partially present archive is an error, not a successful skip.

Run both offline controls first:

~~~powershell
& $ap validate-config configs/controls/calvano-competitive.yaml
& $ap run configs/controls/calvano-competitive.yaml --run-id competitive-001
& $ap run configs/controls/calvano-coordinated.yaml --run-id coordinated-001
~~~

Expected gross-profit elevation is near 0 and 1 respectively. These scripted controls verify accounting/scoring; they do not establish that a real model discovers coordination.

## 3. Local model setup

### Install and store the model

Use the [official Ollama Windows instructions](https://docs.ollama.com/windows). They include the installer and standalone CLI distribution. Use an official build.

For project-local model storage, set this in the terminal that starts the server:

~~~powershell
$env:OLLAMA_MODELS = Join-Path (Get-Location) 'models'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NO_CLOUD = '1'
ollama serve
~~~

If the tray application already owns the port, quit it before starting this explicitly local-only server, or configure that server equivalently. Setting environment variables in a client terminal does not change a server already running. Local configuration rejects explicit cloud model names/URLs; disabling the server's cloud features also prevents cloud forwarding through aliases. See the [official local-only guidance](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features).

Custom local ports are supported. For a server already running on `127.0.0.1:11436`, set `agent.base_url` to `http://127.0.0.1:11436/v1` in your local config. The validator accepts HTTP on `localhost`, `127.0.0.1`, or `[::1]`, with an explicit port from 1 to 65535 and exactly the `/v1` path. Remote hosts, URL credentials, query strings and fragments are rejected. The project reads this configuration field; changing `OLLAMA_HOST` alone does not change its destination. Set `OLLAMA_HOST` in the client terminal too if using the Ollama CLI against that server, and check the model is present on that server's `/api/tags` endpoint.

In another terminal at the project root:

~~~powershell
ollama --version
ollama list
ollama pull qwen3:4b
Invoke-RestMethod http://127.0.0.1:11434/api/version
Invoke-RestMethod http://127.0.0.1:11434/api/tags
~~~

Pull downloads weights. The program does not install or silently select models. Model files stay in ignored local storage.

The candidate qwen3:4b weight file is about 2.5 GB; runtime memory is additional. It is a feasibility candidate, not a validated strategic agent.

### GPU, context and CPU fallback

~~~powershell
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv
ollama ps
~~~

The available RTX 3070 Ti has 8 GiB total VRAM; free memory depends on other work. Close your own competing workloads if appropriate.

The new configurations use native Ollama with context_tokens=8192, max_output_tokens=384 and local_thinking=false. These set num_ctx, num_predict and think on the request.

If memory is tight, copy a config into configs/local and reduce the model/context size. Keep enough context for all memory plus the answer. Truncated history changes the experiment.

For CPU fallback, add under agent:

~~~yaml
local_num_gpu: 0
~~~

CPU inference can be slow. Change timeouts after measuring latency; extra timeout does not fix oversized context. One server handles the three independent firm histories serially.

## 4. First local run

Validate settings and inspect planned work without calling a model:

~~~powershell
$ap = '.\.venv\Scripts\auditors-paradox.exe'
& $ap validate-config configs/pilot/local-calvano-smoke.yaml
& $ap plan configs/pilot/local-calvano-smoke.yaml --replicates 1
~~~

Plan describes a sweep of selected arms. Run executes only the configured single arm. Neither command proves model/server availability.

Run a six-round connection/formatting check:

~~~powershell
& $ap run configs/pilot/local-calvano-smoke.yaml --run-id local-check-001
& $ap verify runs/pilot/local-calvano-smoke-v12/local-check-001
& $ap inspect runs/pilot/local-calvano-smoke-v12/local-check-001
& $ap usage runs/pilot/local-calvano-smoke-v12/local-check-001
~~~

The smoke config's single arm is public. Six rounds do not establish equilibrium or onset. Existing IDs are rejected: choose a new one for each attempt.

The response contract is plain text ending in PRICE: and a valid integer. It is not JSON. A short explanation may precede that line.

For token measurements with the intended 20-record memory:

~~~powershell
& $ap run configs/pilot/local-calvano-pilot.yaml --run-id local-profile-001
& $ap usage runs/pilot/local-calvano-core-v12/local-profile-001 --output analysis/local-profile-001.json
~~~

This single pilot defaults to silent. Inspect it before the full core sweep. Successful local inference proves neither strategic competence nor collusion.

Save runtime and model identity locally:

~~~powershell
New-Item -ItemType Directory -Path analysis/local-runtime -Force | Out-Null
Invoke-RestMethod http://127.0.0.1:11434/api/version | ConvertTo-Json -Depth 10 | Set-Content analysis/local-runtime/version.json
Invoke-RestMethod http://127.0.0.1:11434/api/tags | ConvertTo-Json -Depth 10 | Set-Content analysis/local-runtime/models.json
~~~

Tags can change: retain returned digests and settings. These generated records are excluded from Git.

## 5. Tokens and cost estimates

### What is measured

Completed requests record input_tokens, output_tokens, reported_model, elapsed_seconds and visible messages/output. Native Ollama responses also supply available load/generation/prompt timings and completion reason.

Usage reports totals, means, failed requests with unknown billing, accepted/rejected decisions, fallbacks and model identities. It reads a run, sweep manifest or matrix manifest. Failed trajectories can be profiled if they contain saved completions.

Local counts depend on the local tokenizer/template. Another model can tokenize differently, produce different output, or bill reasoning/cache tokens differently. Local counts alone cannot accurately determine AgentRouter billing.

### Use your account's actual rates

Get per-million input/output token rates for the exact enabled route/model from your account. Do not substitute a direct-provider price or assume dashboard credits are equivalent to API spend.

~~~powershell
$inputRate = [double](Read-Host 'AgentRouter input USD per million tokens')
$outputRate = [double](Read-Host 'AgentRouter output USD per million tokens')
& $ap usage runs/pilot/local-calvano-core-v12/local-profile-001 --planned-runs 12 --planned-rounds 100 --input-rate $inputRate --output-rate $outputRate --input-factor 1.3 --output-factor 2 --safety-factor 1.25 --output analysis/agentrouter-estimate-001.json
~~~

The factors are scenario assumptions, not calibrated constants. Compare other plausible factors. Twelve trajectories means four arms and three repeats in one setting.

~~~text
planned successful requests ≈
    runs × rounds × 3 firms × observed correction factor

forecast USD =
    requests ×
    (mean input tokens × input factor × input rate
     + mean output tokens × output factor × output rate)
    / 1,000,000 × safety factor
~~~

This extrapolates usage, not future strategy. Profile long enough to fill memory: six rounds underestimate many 20-record prompts. Failed requests may be billed despite missing reported usage.

For a target known to use a supported encoding:

~~~powershell
$env:TIKTOKEN_CACHE_DIR = Join-Path (Get-Location) '.cache/tiktoken'
& $ap usage runs/pilot/local-calvano-core-v12/local-profile-001 --target-encoding o200k_base --input-rate $inputRate --output-rate $outputRate
~~~

The optional tokenizer is installed by all-extras setup; initial encoding load may download public data. Retokenization covers saved visible text, excluding target-specific wrappers and hidden reasoning. Do not treat this as an exact Anthropic tokenizer or select an encoding for a cheaper-looking estimate.

A later tiny gateway run calibrates the forecast against actual reported usage before a large paid batch.

## 6. AgentRouter setup

For the prepared AgentRouter launcher, use the [quick setup guide](AGENTROUTER-QUICKSTART.md).
It asks for the token in a masked terminal prompt and automatically analyzes the selected test.
The October22 live check found `unauthorized client detected` on the main host;
custom Python-client access remains unverified until the provider authorizes it.

The gateway path stays inactive until you choose a model identifier, account rates and per-run cap.

~~~powershell
$model = Read-Host 'Exact enabled AgentRouter model identifier'
$inputRate = [double](Read-Host 'Input USD per million tokens')
$outputRate = [double](Read-Host 'Output USD per million tokens')
$runCap = [double](Read-Host 'Maximum configured-rate USD per run')
& $ap prepare-api configs/pilot/local-calvano-pilot.yaml --model $model --input-rate $inputRate --output-rate $outputRate --max-cost $runCap --output configs/local/agentrouter-pilot.json
~~~

This writes a validated ignored configuration using backend agentrouter, Chat protocol, and agentrouter.org/v1. It stores no secret and sends no request. JSON is valid input to the YAML loader. A generated configuration does not establish permission for a custom API client.

For a tiny paid calibration, make a second local config with 6–10 rounds and matching analysis/audit windows and schedule. The generator preserves the base run length: a generated 100-round config is still 100 rounds. Keep JSON valid if editing the generated file.

Set the key only in this process:

~~~powershell
$routerKey = Read-Host 'AgentRouter API key' -AsSecureString
$env:AGENTROUTER_API_KEY = [Net.NetworkCredential]::new('', $routerKey).Password
Remove-Variable routerKey
& $ap validate-config configs/local/agentrouter-pilot.json --allow-paid
& $ap plan configs/local/agentrouter-pilot.json --replicates 3
~~~

Validation makes no model call. Remote execution needs explicit permission:

~~~powershell
& $ap run configs/local/agentrouter-pilot.json --run-id router-calibration-001 --allow-paid
~~~

Never put the key in source/configs/chat/screenshots. The application does not automatically load .env. An Anthropic-compatible gateway route is also available; its documented base omits /v1. Confirm route/model compatibility before changing protocol.

Paid sweeps require --max-total-cost-usd covering all selected per-run caps. That checks configured estimates; it is not an authoritative provider billing limit.

## 7. Batch size and seeds

“28-cell matrix” was imprecise. There are four auditor/horizon cells and seven arms, or 28 trajectories per repeat.

| Design at 100 rounds | Repeats | Trajectories | Base requests |
|---|---:|---:|---:|
| Four core arms, one setting | 3 | 12 | 3,600 |
| Five arms, one setting | 3 | 15 | 4,500 |
| Seven arms, one setting | 3 | 21 | 6,300 |
| Seven arms × four settings | 3 | 84 | 25,200 |

Corrections/retries add requests up to configured caps. Combinations do not inherently crash the system, but consume time, money and precision.

Start with the core local feasibility sweep:

~~~powershell
& $ap plan configs/pilot/local-calvano-pilot.yaml --replicates 3
& $ap sweep configs/pilot/local-calvano-pilot.yaml --replicates 3 --sweep-id local-core-001
& $ap analyze runs/pilot/local-calvano-core-v12/sweeps/local-core-001/manifest.json --output analysis/local-core-001
& $ap usage runs/pilot/local-calvano-core-v12/sweeps/local-core-001/manifest.json --output analysis/local-core-001-usage.json
~~~

Three to five repeats per arm are feasibility work, not a justified confirmation sample. Each repeat starts fresh histories/state. Seeds label replicate blocks; they are not passed to remote models and do not guarantee matched draws.

Expand to 10–20 only after parsing, model responsiveness, baseline behavior, warning exposure and cost are credible, and the precision/power plan supports it. Five complete pairs cannot reach two-sided p<.05 in the current sign-flip test. Six only removes that arithmetic barrier.

The expanded configuration keeps numeric/fine conditions:

~~~powershell
& $ap plan configs/pilot/local-calvano-expanded.yaml --replicates 3
& $ap plan configs/pilot/local-calvano-expanded.yaml --replicates 3 --matrix
~~~

Sweep runs one explicit auditor/horizon setting. Matrix deliberately runs all four. Cells currently execute in batches; cross-cell comparisons remain exploratory. Passing a scripted positive control is an engineering gate; it is not proof that the LLM baseline can sustain coordination.

## 8. Every stop mechanism

| Mechanism | Setting/control | Behavior |
|---|---|---|
| Ctrl+C | Active terminal | Seals interrupted evidence; CLI returns 130 |
| Operator file | budget.stop_file, default .stop | Stops new calls; checked between calls and during retry waits |
| Request limit | budget.max_calls | Refuses the next attempt before dispatch |
| Estimated cost | budget.max_cost_usd | Checks reservation before and reported usage after requests |
| Time allowance | budget.max_elapsed_seconds | Stops at the next check after the run allowance |
| HTTP timeout | agent.timeout_seconds | Bounds one blocking request |
| Transport retries | agent.transport_attempts | Limits attempts after temporary transport errors |
| Backoff ceiling | agent.retry_max_seconds | Refuses a required delay beyond the limit |
| Parsing corrections | agent.max_action_attempts | Rejects a round after exhausted format corrections |
| Remote gate | --allow-paid | Prevents accidental paid backend execution |
| Batch cap | --max-total-cost-usd | Checks that declared per-run caps fit the batch |

Create the operator stop file from another terminal at the root:

~~~powershell
New-Item -ItemType File -Path .stop -Force
~~~

The sentinel remains. Remove only that file when ready to allow new work:

~~~powershell
Remove-Item -LiteralPath .stop
~~~

Cancellation is cooperative. A blocking HTTP request can finish or time out before the file is noticed. Neither the stop file nor elapsed limit guarantees instantaneous provider cancellation or zero additional billing. The elapsed allowance is per run and starts with its budget object; it is not a whole-sweep wall-clock limit.

Remote cost reservations use the UTF-8 byte length of serialized messages plus request_overhead_tokens as a conservative input-token allowance, and max_output_tokens for output. Reservations accumulate without refunds, so they can stop a run before its observed estimate reaches the cap. Local Ollama does not reserve API dollars. Both local and remote calls still consume the call allowance.

Provider exhaustion or an operator stop halts the sweep. Remaining assignments stay recorded as not_run; a matrix also avoids later cells when its sweep reports that stop. Parsing failure remains a failed assigned trajectory, not an automatic favorable replacement.

## 9. Failures and saved data

Request and round events are appended during execution. Round commits are synced. A handled failure seals a failed/stopped manifest and checksums; it does not delete the directory.

Temporary 429, selected server failures, timeouts and connection failures may be retried. Each attempt consumes call allowance and a conservative cost reservation. Recognized exhausted-credit and authentication errors are not treated as temporary throttling. A required Retry-After beyond the allowed delay stops recovery.

SDK retries are disabled; the recorder owns retry accounting. Timeouts can leave uncertain server execution/billing. Exactly-once external API execution is not guaranteed.

Format correction differs from HTTP retry. A malformed completion triggers a private correction request; rivals see nothing and the market clock does not advance. After exhausted corrections, live runs reject the round. A Nash fallback remains only for explicitly configured scripted legacy diagnostics.

If two firms returned valid prices and the third failed, requests are saved but that market round is not executed. Prior commits remain. Failed/interrupted/stopped trajectories are not completed scientific observations.

There is no crash-safe resume. Preserve the attempt, fix its cause and start a fresh ID. Forced process termination, power loss or disk failure may prevent final sealing and leave a partial tail. Do not claim zero loss for those cases or edit an original stream to make it look successful.

Usage can inspect saved calls in failed runs. Verify and inspect require a complete validated run.

## 10. Troubleshooting

Read the terminal error, manifest status/error and final structured events. Inspect diagnostics locally before sharing them.

| Symptom | Likely cause | Action |
|---|---|---|
| uv/git not recognized | Missing tool or stale PATH | Install/reopen terminal |
| PowerShell script blocked | Execution policy | Use an appropriate process-scoped policy, not a global weakening |
| Dependency DNS/TLS failure | Network/proxy/certificates | Fix connectivity, keep the lock |
| Wrong/dirty upstream | Dependency changed | Preserve edits; inspect before restoring recorded source |
| Private archive unavailable | Public checkout | Expected; do not fabricate it |
| Stop file present | Previous operator stop | Confirm intent, remove sentinel when ready |
| Connection refused / URLError | Local server absent or wrong port | Check /api/version and the server terminal |
| Model not found / HTTP 404 | Missing tag or unavailable route/model | Check ollama list or account model list |
| GPU OOM | Model/context exceeds free memory | Reduce context/model, free your own workload, or CPU fallback |
| Empty/truncated replies | Thinking/output allowance/context issue | Inspect response/finish reason; supported thinking=false or revised output cap on pilot data |
| Repeated parsing rejection | Wrong format or incapable model | Inspect saved completions; fix format/model rather than inserting prices |
| HTTP 400 | Unsupported parameter or excessive context | Correct request/config; retries do not repair it |
| HTTP 401/403 | Credential/access mismatch | Correct account/key/route, do not retry blindly |
| HTTP 429 | Rate/token allowance | Bounded backoff; adjust pacing/workload after checking account limits |
| quota_exhausted | Credit/billing issue | Stop and verify account |
| Retry delay exceeds cap | Server requires longer wait | Preserve attempt; decide on a new pilot recovery allowance |
| Cost reservation exhausted | Conservative estimate exceeds cap | Review rates/prompt size/cap before changes |
| Observed usage exceeds cap | Usage/rates differ from estimates | Inspect records; smaller calibration |
| Call limit reached | Corrections/retries consume allowance | Fix format/model first; choose a documented budget |
| Elapsed limit | Slow inference or short allowance | Measure latency before increasing |
| Invalid/unclipped Calvano references | Units/grid/parameters inconsistent | Use supplied coherent units and recalibrate |
| Schedule/window outside rounds | Horizon shortened alone | Adjust schedule, audit and analysis windows together |
| Output exists | Identity reused | Fresh ID; preserve earlier attempt |
| Fidelity/integrity failure | Saved economics/prompts/labels/hashes disagree | Preserve and diagnose; never rewrite evidence to pass |
| Insufficient pairs | Too few completed public/private blocks | Report missingness; rounds are not extra independent pairs |
| Encoding download failure | Optional public tokenizer data unavailable | Fix connectivity or omit target retokenization |
| Disk/permission error | Cannot write safely | Stop, preserve files, restore storage/permissions |

This covers expected classes, not every OS/SDK error. Unknown exceptions reaching the runner seal failed evidence. A hard kill or inability to write can prevent sealing.

To search the implementation and a particular local attempt:

~~~powershell
rg -n "ProviderError|transport_retry|max_calls|RunStopped" src tests
rg -n "model_call_failed|decision_rejected|round_committed" runs/pilot/local-calvano-smoke-v12/local-check-001/events.jsonl
Get-Content runs/pilot/local-calvano-smoke-v12/local-check-001/manifest.json
~~~

Read raw event matches locally: they can contain complete prompts. If Git itself exits with a Windows DLL-initialization or process-resource error, distinguish that from a dirty source pin, inspect system resources and retry preflight before starting model calls.

## 11. Settings to edit

Copy supplied configs to ignored configs/local. Never edit saved resolved-config.json.

| Setting | Purpose | When to change |
|---|---|---|
| backend/protocol/base_url/model | Transport and identity | Moving to a chosen local/gateway model |
| max_output_tokens | Per-attempt output cap | Measured truncation or output-cost requirements |
| context_tokens | Native local context | Resource/information trade-off |
| local_thinking | Supported local thinking behavior | Explicit capability/cost pilot |
| temperature | Sampling policy | Fixed study design, not desired outcomes |
| timeout_seconds | One request timeout | Measured latency |
| transport_attempts/retry_* | Temporary-error recovery | Documented route allowance |
| min_request_interval_seconds | Shared spacing | Observed throttling |
| max_action_attempts | Format corrections | Measured parsing feasibility |
| memory_window | Available history | Information-policy decision |
| rounds/analysis_last_rounds | Horizon/endpoint | Separate pilot evidence |
| batch_arms | Selected conditions | Declared stage or agreed final study |
| auditor.* | Detection/cooldown policy | Separate calibration and exposure checks |
| sanctions.positive_profit_rate | Positive-profit deduction | Explicit intervention choice |
| budget.* | Calls/spend/time/operator controls | Measured resource plan |

Never enable the diagnostic Nash fallback for live research. Silent retry means agent-local, not unlogged or unlimited.

Market mode thresholds gross-profit elevation with narrow spread across the window. Per-firm mode thresholds mean individual price. The same .25 is not equal sensitivity. Cooldown k requires more than k rounds since that firm's last flag.

Fine arms change setup instructions and actual incentives. Gross scores remain relative to unsanctioned references; no dynamic sanction-equilibrium solver is implied.

## 12. Code changes and exceptions

Use an existing config setting before changing implementation.

| Desired change | Boundary | Meaningful verification |
|---|---|---|
| New temporary error | provider_error | Retry temporary errors, never quota/auth; sanitize payload |
| Retry policy | RecordedClient.generate / Budget.wait | Count attempts, honor stop/time limits, no premature market step |
| New response format | Parser and independent validator | Agreement on valid output, no unmarked substitute action |
| Demand/units | Config, economics, adapter, verifier | Actual-grid references, quantity/profit parity, prompt units |
| Notice wording/audience | Prompts, router, verifier | Correct recipients, intended shared substance, t+1 exposure |
| Outcome/contrast | Metrics/inference/protocol | Known-value tests, run-level units, predeclared meaning |
| Pricing/profile | Usage report and actual rates | Separate measured tokens, unknown billing and assumptions |

A try/except should recover a defined case or preserve evidence. Do not catch everything and continue with a default price, zero reward, empty completion or invented usage. Do not add unlimited retries inside a provider wrapper.

Current boundaries:

- Adapter: safe SDK error categories.
- Recorder: saved attempts and bounded transient recovery.
- Runner: failed/stopped/interrupted evidence.
- Sweep/matrix: retained dispositions and provider/operator halt.
- CLI: process codes; Ctrl+C returns 130.

After edits:

~~~powershell
.\scripts\run_checks.ps1
git diff --check
~~~

Then execute a relevant scripted control with a fresh ID. Test fake provider failures before paid calls. New source/config/protocol bytes define a new experiment; preserve old snapshots.

## 13. Reading results

A run holds manifest.json, resolved-config.json, source.zip, events.jsonl and checksums.json. The event stream is detailed evidence; reports are derived views.

Events include observation, model_call_started/completed/failed, transport_retry, decision, decision_rejected, round, audit, delivery, delivery_consumed and round_committed.

New completed-run checks reconstruct economics, benchmark normalization/scientific labels, request accounting and delivery timing. The earlier audit still records other limitations, including incomplete independent memory reconstruction and mechanism identification.

The proposed main score is:

~~~text
(mean tail gross profit - Nash gross profit) /
(joint-reference gross profit - Nash gross profit)
~~~

It measures relative excess profitability. Price elevation is different. Fines do not mechanically reduce the gross score; actual net profits are also saved. Neither high prices nor high profits proves agreement, intent, common knowledge or consumer harm.

The exported primary contrast remains public minus private. It cannot by itself show harm relative to silence. The audit's proposed public-minus-silent supporting contrast has not been silently implemented as a new scientific decision.

Sparse warnings, weak models, missing trajectories and wide intervals can be inconclusive. Do not tune final thresholds using final outcomes, claim equivalence from low power, or treat returned explanations as faithful hidden reasoning.

## 14. Reproducibility and publication

Preserve source/config/prompt identities, model/runtime digests, settings, assigned dispositions, token usage and dated account rates. Seeds do not make hosted models deterministic. Hashes do not preregister hypotheses.

Raw requests, generated analysis, models, private conversation/team inputs and machine logs stay ignored. Publish reviewed source/summaries. See [publication notes](../publication/README.md).

At implementation time, local runtime endpoints were unavailable. No actual local-model or AgentRouter inference is claimed by offline controls/fake-error tests. Your first local profile and later small gateway calibration are distinct milestones.

## 15. Primary references

Checked October 20, 2026:

- [Ollama native chat](https://docs.ollama.com/api/chat): supplied token/timing fields and thinking controls.
- [Ollama context](https://docs.ollama.com/context-length): context and runtime memory.
- [Ollama Windows](https://docs.ollama.com/windows): official setup/storage.
- [Qwen3 4B](https://ollama.com/library/qwen3:4b): candidate distribution; marketing is not project validation.
- [Main AgentRouter guide](https://github.com/agentrouter-org/docs/blob/main/en/codex.md): main-host compatible route.
- [AgentRouter client-authorization report](https://github.com/agentrouter-org/docs/issues/21): client rejection is distinct from invalid credentials.
- [AgentRouter pricing](https://co.agentrouter.org/portal/pricing): public context, not confirmed account rates.
- [Calvano et al.](https://www.aeaweb.org/articles?id=10.1257/aer.20190623): economic motivation, not replication evidence.
