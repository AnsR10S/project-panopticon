# First run: local agents, AgentRouter, and what the research can tell us

Written October 21, 2026 · Software 1.2.0 · Windows PowerShell

**October 22 evidence update:** [local Qwen experiments have now been executed](../reviews/2026-10-22-study-repair/REPORT.md).
Read that report and its confirmation gates before launching a research batch. The
tracked `configs/diagnostics/local-repair-*.json` files reproduce the current short
local calibration settings; each new generation run needs a new identity.

This is the guided starting path. Follow it from the project root, one section at a time. Each step explains what you are operating, what it checks, and when to move on. The [operating manual](OPERATING-MANUAL.md) contains the full settings and recovery reference.

Creating this guide did not install a model or make a paid request. Subsequent local Qwen calibration is recorded in the report above; live gateway behavior and billing still need separate validation. All paths below are inside the project or are placeholders; no personal machine paths or credentials are included.

## 1. Understand the experiment before starting it

The research question is: **does announcing an auditor's warning publicly change pricing behavior compared with delivering the warning privately or remaining silent?** A possible result is that an anti-collusion warning accidentally helps agents coordinate. That is a hypothesis to test, not a result we already have.

Three firms sell in a simulated Calvano logit market. Each firm has a separate history and asks the selected model for a price. The project calculates demand and profit, checks the auditor's rule, and routes any notice to the next round.

~~~text
Three separate firm histories
  -> three model requests using the same selected model
  -> one simultaneous market round
  -> demand, gross profit, audit, and any fines
  -> notices available at the next decision
  -> saved evidence and the next round
~~~

Calls are sent serially, but all three firms' observations are frozen beforehand. Firm 2 cannot see Firm 1's new price before choosing its own. The auditor is code, not a fourth model. Local agents use one Ollama server; they do not require three copies of the weights. This is inference with bounded memory, not training model weights.

### What is an arm?

An **arm** is one version of the experiment. It contains all three firms. We change the feedback policy while keeping the market and agent settings comparable.

| Initial arm | What changes | Why include it? |
|---|---|---|
| `silent` | No notices are delivered | Baseline behavior without feedback |
| `private` | The same warning is delivered individually to all firms, without announcing other recipients | Compare individually delivered feedback with public announcement |
| `public` | The warning announces that all three firms receive it | Test the announcement policy |
| `placebo` | Neutral bulletins arrive on a fixed schedule | Check whether receiving messages changes behavior even without warning content |

Private here does not mean only one firm is warned. All three receive individual notices. Also, the placebo schedule differs from behavior-triggered warnings, so the placebo does not isolate every possible attention or timing mechanism.

The expanded study also has `public_numeric`, `public_fine`, and `private_fine`. Both market-wide/per-firm auditors and hidden/disclosed final rounds remain in scope. We start with a smaller pilot rather than running every combination immediately.

**One run** is a whole market trajectory. **One round** is one set of three price decisions. **One repeat** runs another block of the selected arms. One hundred rounds in one trajectory are not one hundred independent research samples.

## 2. Install the project and check it without model calls

Open PowerShell in the folder containing `pyproject.toml`. If opening a terminal elsewhere, navigate with `Set-Location '<project-root>'`, replacing the placeholder. Keep every subsequent terminal at that same project root.

You need Git and [uv](https://docs.astral.sh/uv/getting-started/installation/). Install them first if these commands are unavailable:

~~~powershell
git --version
uv --version
.\scripts\setup.ps1
~~~

Setup restores the pinned CollusionLab dependency and installs the locked Python environment, including the provider libraries. It can download dependencies and Python. It does not download model weights or spend API credits.

Then check the installation:

~~~powershell
$ap = '.\.venv\Scripts\auditors-paradox.exe'
& $ap --version
& $ap doctor
.\scripts\run_checks.ps1
~~~

**Checkpoint:** the version should be `1.2.0` for this guide, doctor should accept the upstream pin, and the checks should pass. A public checkout can report the private source archive as unavailable; that is different from a corrupt or partially present archive. Doctor does not connect to a model.

If a command fails, stop at that step and use section 10. Do not paste the entire guide into one terminal and assume later commands make earlier failures harmless.

### Check the economic accounting first

These two runs use scripted prices and no model or API key:

~~~powershell
$controlTag = Get-Date -Format 'yyyyMMdd-HHmmss'
& $ap run configs/controls/calvano-competitive.yaml --run-id "competitive-$controlTag"
& $ap run configs/controls/calvano-coordinated.yaml --run-id "coordinated-$controlTag"
~~~

The first uses the competitive reference; the second uses the joint-profit reference. Their expected gross-profit elevation scores are approximately 0 and 1. This checks scoring. It does not show that an LLM can discover either strategy.

## 3. Start the local model server

Install Ollama using its [official Windows instructions](https://docs.ollama.com/windows), then open a fresh terminal so the `ollama` command is available.

We use two terminals:

| Terminal | Purpose |
|---|---|
| A | Runs the local Ollama server; leave it open |
| B | Downloads the model and runs project commands |

If Ollama's tray application is already running, quit it before starting Terminal A's server. Otherwise, the existing server will keep its old settings and may already own the port.

In **Terminal A**, from the project root:

~~~powershell
$env:OLLAMA_MODELS = Join-Path (Get-Location) 'models'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_NO_CLOUD = '1'
ollama serve
~~~

Weights go into the ignored `models/` directory. Cloud features are disabled for this server, and it listens locally. Setting these variables in another terminal does not reconfigure a server that is already running. See [Ollama's local-only guidance](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features).

In **Terminal B**, also from the project root:

~~~powershell
$ap = '.\.venv\Scripts\auditors-paradox.exe'
ollama --version
ollama pull qwen3:4b
Invoke-RestMethod http://127.0.0.1:11434/api/version
Invoke-RestMethod http://127.0.0.1:11434/api/tags
~~~

`pull` downloads the weights; it is not a paid inference request. The [listed qwen3:4b download](https://ollama.com/library/qwen3:4b) is about 2.5 GB, with additional memory needed during execution. It is our existing small-model feasibility candidate for the RTX 3070 Ti, not a proven research model or a guarantee of fit with other GPU workloads running.

**Checkpoint:** the version endpoint returns a version and the tags endpoint includes `qwen3:4b`.

### If your local server uses port 11436

Keep an already working server on its current port. In a copied config under `configs/local/`, set:

~~~yaml
agent:
  base_url: http://127.0.0.1:11436/v1
~~~

This is a field to edit within the existing `agent` block, not a replacement for its other settings. Keep `agent.model` equal to the exact installed model tag. Supported hosts are `localhost`, `127.0.0.1`, and `[::1]`, with HTTP, an explicit port from 1 to 65535, and exactly `/v1`. Remote hosts and URLs with credentials, query strings or fragments are rejected.

In Terminal B, check the same server:

~~~powershell
$env:OLLAMA_HOST = '127.0.0.1:11436'
ollama list
Invoke-RestMethod http://127.0.0.1:11436/api/version
Invoke-RestMethod http://127.0.0.1:11436/api/tags
~~~

`OLLAMA_HOST` selects the server for the Ollama CLI; the project uses `agent.base_url`. Neither client setting moves an existing server. In the following run commands, use your copied config instead of the default smoke config, and use your chosen port in subsequent metadata checks.

## 4. Execute the first real local run

First validate the configuration without sending requests:

~~~powershell
& $ap validate-config configs/pilot/local-calvano-smoke.yaml
Get-Content configs/pilot/local-calvano-smoke.yaml
~~~

This file selects local Ollama, `qwen3:4b`, the public arm, a hidden final-round count, six rounds, and four memory records. It allows 384 output tokens, two formatting attempts, and three transport attempts. The request ceiling is 72, and the per-run elapsed allowance is 600 seconds. These are limits, not expected usage or completion-time promises.

Now run it:

~~~powershell
$localId = 'local-check-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
& $ap run configs/pilot/local-calvano-smoke.yaml --run-id $localId
if ($LASTEXITCODE -ne 0) { throw 'Local run did not complete. Inspect the saved attempt before continuing.' }
$localRun = Join-Path 'runs/pilot/local-calvano-smoke-v12' $localId
& $ap verify $localRun
& $ap inspect $localRun
& $ap usage $localRun
~~~

The basic workload is **6 rounds × 3 firms = 18 requests**, with extra requests if recovery is needed. No AgentRouter key is required and no API credit is consumed. Local execution still uses your computer's time, memory, and electricity.

The model should end each answer with `PRICE: <integer>` inside the allowed 100–250 tick range. For example, `PRICE: 137` means 1.37 currency units in this configuration. This is plain text, not JSON. If formatting is wrong, correction requests stay private and the market does not advance. If corrections fail, the round is rejected; no replacement price is executed.

**Checkpoint:** the manifest says `complete`, six rounds were committed, and `verify` accepts the saved evidence. Inspect actual responses for coherent task following as well. Six rounds establish connection and formatting feasibility; they cannot establish stable pricing, a paradox, or a causal effect.

You may inspect GPU placement from another terminal while the model is loaded:

~~~powershell
ollama ps
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv
~~~

Save the runtime identity for later comparison:

~~~powershell
New-Item -ItemType Directory -Path analysis/local-runtime -Force | Out-Null
Invoke-RestMethod http://127.0.0.1:11434/api/version | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 analysis/local-runtime/version.json
Invoke-RestMethod http://127.0.0.1:11434/api/tags | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 analysis/local-runtime/models.json
~~~

These local metadata files are refreshed if you repeat this block. Original run evidence is kept separately and is not overwritten.

## 5. Measure a longer local run before forecasting API costs

Six rounds have short histories. The next configuration uses 100 rounds and a 20-record memory, making it more useful for profiling the intended context length. Its single-run default is `silent`.

~~~powershell
$profileId = 'local-profile-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
& $ap run configs/pilot/local-calvano-pilot.yaml --run-id $profileId
if ($LASTEXITCODE -ne 0) { throw 'Local profile did not complete. Keep the attempt and inspect its failure.' }
$profileRun = Join-Path 'runs/pilot/local-calvano-core-v12' $profileId
& $ap verify $profileRun
& $ap inspect $profileRun
& $ap usage $profileRun --output "analysis/$profileId-usage.json"
~~~

This is 300 base requests, with a 900-call allowance and a 3,600-second per-run allowance. Slow hardware may reach the elapsed limit. Adjust a copied local config only after inspecting the cause; do not silently shorten or truncate histories to make a failed run pass.

The usage report includes reported input/output tokens, request latency, formatting corrections, rejected decisions, and failed requests with unknown billing. A profile with no successful requests cannot produce a token estimate.

For a conditional estimate of four arms × three repeats × 100 rounds, using the screenshot's `gpt-6.1-sol` rates:

~~~powershell
& $ap usage $profileRun --planned-runs 12 --planned-rounds 100 --input-rate 3 --output-rate 15 --input-factor 1.3 --output-factor 2 --safety-factor 1.25 --output "analysis/$profileId-router-forecast.json"
~~~

The factors are deliberately explicit scenario assumptions: 30% more input, twice the output, and a further 25% margin. They are not measured conversion constants. A silent-arm local profile can also understate warning-arm context costs. Profile the core local sweep if practical and refine the forecast with actual gateway usage later.

## 6. Estimate gpt-6.1-sol cost and choose a small workload

The account screenshot supplied October 21 shows **$3 per million input tokens and $15 per million output tokens** for `gpt-6.1-sol`. Recheck the account before use. These displayed dollar units have not been reconciled with actual credit deductions or recharge conversion.

~~~text
Base requests = arms × repeats × rounds × 3 firms
Estimated displayed-dollar cost = (input tokens × 3 + output tokens × 15) / 1,000,000
~~~

The following arithmetic assumes each request averages **2,000 input and 200 output tokens**, with no retries:

| Workload | Base requests | Illustrative cost |
|---|---:|---:|
| One six-round gateway check | 18 | $0.162 |
| One 100-round trajectory | 300 | $2.70 |
| Four arms × three repeats × 100 rounds | 3,600 | $32.40 |
| Seven arms × three repeats × 100 rounds | 6,300 | $56.70 |
| Seven arms × both auditors × both horizons × three repeats × 100 rounds | 25,200 | $226.80 |

At **5,000 input and 200 output tokens**, the four-arm pilot becomes **$64.80**. At the first assumption, a simple 50% contingency makes $32.40 into $48.60, but that is a planning margin rather than an upper bound. Hidden reasoning, retries, model behavior, wrappers, cache rules, and account conversion can alter billing.

The first gateway check below has a **$1 configured-rate cap**, not a recommendation to use a $1 cap for a 100-round study. Software reservations are conservative and accumulate without refunds, so a run can stop below its observed estimated spend. The cap is not an authoritative provider-side spending limit.

## 7. Configure AgentRouter without storing the key in a file

You need **one AgentRouter key**, enabled for the chosen model. All three firms read that same key while maintaining separate histories. You do not need direct OpenAI or Anthropic keys when using this route.

In your AgentRouter account, use its API-key/token management area to create or select a key with permission for `gpt-6.1-sol`. Restrict permissions and account spending if those controls are available. Dashboard labels can change; confirm the exact enabled model identifier in your own account.

The project supports the gateway through `backend: agentrouter`. Its generated configuration uses Chat Completions at `https://agentrouter.org/v1`, matching the [main AgentRouter connection guide](https://github.com/agentrouter-org/docs/blob/main/en/codex.md). The screenshot's `openai` endpoint label describes the protocol; it does not mean you should change the backend to direct `openai`. The October22 live check received `unauthorized client detected`; provider authorization for this custom Python application is required before repeating inference. See the [current diagnosis](../reviews/2026-10-22-agentrouter-connectivity/REPORT.md).

### Prepare a six-round gateway configuration

In Terminal B:

~~~powershell
$routerTag = Get-Date -Format 'yyyyMMdd-HHmmss'
$routerConfig = "configs/local/router-smoke-$routerTag.json"
& $ap prepare-api configs/pilot/local-calvano-smoke.yaml --model gpt-6.1-sol --input-rate 3 --output-rate 15 --max-cost 1 --output $routerConfig
if ($LASTEXITCODE -ne 0) { throw 'Gateway configuration was not prepared.' }
Get-Content $routerConfig
git check-ignore $routerConfig
~~~

This creates a secret-free JSON configuration inside ignored `configs/local/`. The YAML loader accepts JSON. **It sends no request.** The generator preserves the base's six rounds, public arm, windows, and schedules; its output study ID is `agentrouter-local-pilot`. Starting from the 100-round config instead would retain 100 rounds.

The displayed rates are example account settings supplied by you; replace them if your account differs. The generated configuration should show `backend: agentrouter`, `api_protocol: chat`, the exact model identifier, and `max_cost_usd: 1`.

### Set the key for this terminal only

~~~powershell
$routerSecret = Read-Host 'AgentRouter API key' -AsSecureString
$env:AGENTROUTER_API_KEY = [Net.NetworkCredential]::new('', $routerSecret).Password
Remove-Variable routerSecret
if ([string]::IsNullOrWhiteSpace($env:AGENTROUTER_API_KEY)) { throw 'No AgentRouter key was entered.' }
& $ap validate-config $routerConfig --allow-paid
~~~

The input is hidden; the environment value is still a secret accessible to this process and its children. Do not print it, paste it into chat, put it in a tracked config, or include it in a screenshot. The project does not automatically load `.env`. A new terminal needs the key entered again.

Validation checks project settings and economics. It does not authenticate the key, prove the model exists, or spend credits. An OpenAI-compatible route also needs compatible request parameters and response usage fields; the next tiny run is where we check that.

## 8. Make the first tiny gateway run, then reconcile billing

**This block sends paid requests. Run it only after the local checks and when you choose to spend.** Record your starting account credit balance and the displayed rates privately.

~~~powershell
$routerId = 'router-check-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
& $ap run $routerConfig --run-id $routerId --allow-paid
if ($LASTEXITCODE -ne 0) { throw 'Gateway check did not complete. Inspect the preserved attempt before any retry.' }
$routerRun = Join-Path 'runs/pilot/agentrouter-local-pilot' $routerId
& $ap verify $routerRun
& $ap inspect $routerRun
& $ap usage $routerRun --input-rate 3 --output-rate 15 --planned-runs 1 --planned-rounds 6 --safety-factor 1 --output "analysis/$routerId-usage.json"
~~~

Expect 18 base requests plus any recovery attempts. A `plan` command on this smoke config would describe its two selected batch arms; it would not describe this single public-arm run. `run` executes one arm, `sweep` executes selected arms, and `matrix` crosses both auditors and both horizons.

After completion, check:

| Check | What you are looking for |
|---|---|
| Run integrity | Complete manifest, six committed rounds, successful verification |
| Response contract | Valid prices, acceptable formatting-correction rate, no substitute-price fallback |
| Model identity | Requested identifier and gateway-reported model recorded; aliases are provider reports, not independent proof of underlying weights |
| Usage | Reported input/output totals and any failed requests whose usage is unknown |
| Billing | Account deductions reconciled with token charges in the same credit units, allowing for delayed reporting or other account activity |

The `usage` command's projection is a forecast based on observed successful calls and correction frequency. Calculate successful-call token charges from the reported totals and account rates; reconcile those against the account rather than treating the projection as an invoice. Unknown usage on failed calls is not known zero.

Remove the process key when finished:

~~~powershell
Remove-Item Env:\AGENTROUTER_API_KEY -ErrorAction SilentlyContinue
~~~

If gateway parameters such as `temperature` or `max_tokens` are rejected, keep the error and inspect the supported route/model contract before editing. Do not switch models silently midway through a trajectory. The operating manual also covers the Anthropic-compatible route, whose base URL differs.

## 9. Move to a small research pilot when the checks pass

Start locally with the four core arms:

~~~powershell
& $ap plan configs/pilot/local-calvano-pilot.yaml --replicates 3
$sweepId = 'local-core-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
& $ap sweep configs/pilot/local-calvano-pilot.yaml --replicates 3 --sweep-id $sweepId
if ($LASTEXITCODE -ne 0) { throw 'Sweep is incomplete. Review failed and not-run assignments.' }
$sweepManifest = "runs/pilot/local-calvano-core-v12/sweeps/$sweepId/manifest.json"
& $ap analyze $sweepManifest --output "analysis/$sweepId"
& $ap usage $sweepManifest --output "analysis/$sweepId-usage.json"
~~~

The plan should show **12 trajectories and 3,600 base requests**, with a 10,800-call ceiling. Configured API cost is zero because this is Ollama. That says nothing about the future API bill.

The initial setting is the market-wide auditor with the final-round count hidden. This pilot does not compare horizon disclosures or auditor modes. Those comparisons remain available later through explicitly planned configurations/matrices.

Three repeats are for feasibility: can the model understand the task, produce usable outputs, react to feedback, and produce enough variation to study? Scripted coordinated-price controls validate accounting; they do not prove an LLM positive control. If the model is unresponsive, invalid, or already saturated at one boundary, inspect that before buying a larger sample. Record any pilot-driven revisions and keep pilot data separate from later confirmation.

For an API pilot, prepare a separate config from `local-calvano-pilot.yaml`, use recalibrated rates and a realistic per-run cap, re-enter the key, and inspect `plan` before execution. Paid sweeps require `--allow-paid` and `--max-total-cost-usd`; the declared total must cover the sum of all selected per-run caps. Twelve runs capped at $5 each require at least $60 of declared batch allowance, even if the expected cost is lower. Do not launch the full matrix just because the tiny gateway run worked.

## 10. Stop, diagnose, and recover without hiding failures

To request a stop, press Ctrl+C in Terminal B, or create the stop file from another terminal at the project root:

~~~powershell
New-Item -ItemType File -Path .stop -Force
~~~

The application stops cooperatively; an in-flight HTTP request can still finish or time out and incur a charge. The stop file remains. Only when you intend to permit another run, remove that one file:

~~~powershell
Remove-Item -LiteralPath .stop
~~~

| Symptom | What to do |
|---|---|
| `uv`, Git, or Ollama not found | Install the missing tool and reopen the terminal |
| PowerShell blocks a setup script | Inspect the execution-policy error and use the operating manual's process-scoped guidance; do not weaken machine-wide policy |
| Port 11434 already occupied | Check whether Ollama's tray server is running; quit it before starting the explicitly configured server |
| Connection refused / model missing | Check Terminal A, the version/tags endpoints, and the exact downloaded model name |
| GPU memory failure / very slow CPU fallback | Inspect `ollama ps` and GPU memory; close competing workloads or use a smaller local configuration and record the change |
| OpenBLAS thread/memory initialization failure | Set the two process variables below, then retry the failed command; this does not fix model VRAM exhaustion |
| Output repeatedly lacks a valid price | Inspect saved replies; adjust a copied pilot config or prompt deliberately, never invent a default price |
| 400 / 404 from gateway | Check exact model permission, protocol/base URL, and unsupported parameters |
| 401 / 403 / exhausted credits | Check key/model permissions or account balance; these are not transient rate limits |
| 429 / temporary server error | Recorded bounded recovery is automatic; inspect pacing and rate limits if it exhausts |
| Cost/call/time cap reached | Inspect usage and reservations before changing a cap; the $1 smoke allowance is not a 100-round budget |
| Existing ID or output file | Use a new ID/path; preserve the old attempt |
| `verify` refuses a failed run | Expected: verification/inspection require completed validated runs; inspect manifest/events and use `usage` if successful requests exist |

For the OpenBLAS case, in the client terminal:

~~~powershell
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
~~~

Evidence is inside the run directory: `manifest.json` holds status and resolved settings; `events.jsonl` holds attempts, replies, rounds, and notices; `checksums.json` is written when the attempt is sealed. Inspect these locally before sharing logs.

~~~powershell
Get-Content (Join-Path $localRun 'manifest.json')
Get-Content (Join-Path $localRun 'events.jsonl') -Tail 12
~~~

If the run failed before `$localRun` was assigned, construct it from `runs/pilot/local-calvano-smoke-v12` and the printed `$localId`; use the corresponding study/run IDs for other stages. A preflight failure may create no run directory at all.

Completed rounds and handled failures are preserved. There is no crash-safe resume: keep the attempt, fix the cause, and start a new ID. Forced termination, power loss, or disk failure can leave partial evidence. Increasing retries indefinitely or continuing with invented prices would change the experiment and conceal its failure.

When changing settings, copy a tracked config into `configs/local/`, edit that copy, validate it, and retain its resolved version with the new run. Do not edit original logs or the pinned upstream checkout to make an attempt look successful.

## 11. What can we achieve scientifically?

| Stage | Defensible achievement | What it does not establish |
|---|---|---|
| Scripted checks | Economic accounting, routing, persistence, and analysis behave as specified | LLM behavior or a research finding |
| First local run | This local model/runtime can execute the task and return valid decisions | Hosted-model behavior, stable coordination, or causality |
| Local profiling and tiny gateway calibration | Measured usage, operational reliability, and a better conditional budget | Exact costs for all later trajectories |
| Small controlled pilot | Observed distributions and feasibility under the specified arms; evidence to revise or retain a protocol | Adequate statistical power or a confirmed paradox |
| Adequately designed main study | A bounded estimate of feedback-policy effects for the chosen model, market, horizon, and prompts | Universal claims about AI markets, proof of intent, or guaranteed publication |

New pilot configs select **gross-profit elevation**. In plain language, this places observed pre-fine profit between the competitive reference (0) and joint-profit reference (1). We also retain prices, price dispersion, net profit, notices, and failures. A high score alone does not prove collusion or agreement.

The current analyzer's main comparison is **public minus private**. A positive difference means public announcement produced a higher configured score than private delivery under that protocol. It does not automatically mean public warnings made outcomes worse than silence. The analyzer also exports **public minus silent**, **private minus silent**, and **placebo minus silent** as exploratory comparisons when those arms were assigned. These exports were added after inspecting the first local pilot; they are not a retrospectively preregistered hypothesis family. A claim about warning delivery also requires actual warning exposure, not just the arm's assigned name.

Before confirmatory data, resolve the [adversarial review](../reviews/2026-10-17-adversarial-audit/review.md): freeze the primary outcome, contrasts/hypothesis family, meaningful effect, run count, inference assumptions, and failure/missingness handling. Repeated rounds are not independent samples, and hosted seeds do not guarantee matched random outputs. With the current two-sided paired sign-flip test, five complete pairs cannot yield p < .05: the minimum is 2/32 = .0625. Six clears that arithmetic barrier; it does not guarantee useful power or justify the test's assumptions.

A null or negative result can still be informative if the design is credible, estimates are precise enough for the question, and controls show a responsive setup. An unresponsive or noisy setup can leave the question unresolved. Engineering polish and test coverage support credibility but do not supply scientific novelty or guarantee acceptance.

Your first practical goal is therefore: **one verified local run, one useful local token profile, one calibrated tiny gateway run, and an honest feasibility decision before larger spending.**

## 12. Where to go next

- [Operating manual](OPERATING-MANUAL.md): every setting, retry rule, stop mechanism, and recovery boundary.
- [Research introduction](../research/START-HERE.md): the original idea in plain language.
- [Current concern decisions](../research/design-revisions/2026-10-20-local-first.md): what was corrected and what remains open.
- [System architecture](../architecture/system-design.md): components and responsibilities.
- [Research protocol](../research/protocol.md): pilot draft; unresolved choices are not preregistered facts.
- [V1.2 verification](../releases/v1.2/README.md): recorded engineering evidence and its limits.

Documentation validation on October 21, 2026: all 20 PowerShell blocks passed syntax parsing; four supplied configs and the generated gateway example passed offline validation; planning confirmed 12 trajectories, 3,600 base requests, and a 10,800-call ceiling. The project link check passed 211 local links, and all 29 preserved inputs retained their hashes. The temporary gateway validation file was removed. No local-model or paid request was made by these checks, so live completion and billing remain unverified.
