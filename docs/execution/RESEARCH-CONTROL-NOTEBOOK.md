# Run and understand a complete experiment from the notebook

Open [the research control notebook](../../notebooks/00_research_control.ipynb).
It controls the existing runner rather than building another simulator. Every current study,
workflow and analysis setting appears in its central configuration cell, with a generated
80-row explanation table showing current values, permitted ranges, inactive settings and
what changing each setting means. All detector thresholds and analysis cutoffs are editable.

See [rate limits and explicit continuation](../reviews/2026-10-23-notebook/RATE-LIMIT-RECOVERY.md)
for the October 23 recovery update. `CONTROL["resume_sweep"]` defaults to `None`; select an
explicit failed sweep, or use `control.last_failed_sweep(ROOT, STUDY)`, before preview/freeze
to request continuation. Only compatible rate-limit failures can continue. Older failures
preceding source changes remain preserved and blocked. The launch panel shows shared
cooldown and adaptive spacing; provider limits stay unknown when the gateway omits them.

The template selects scripted agents and leaves execution and paid execution disabled.
An interface upgrade preserves your existing configuration and switches: review `CONNECTION`,
`EXECUTE` and `ALLOW_PAID` before Run All. Preview and freeze never load credentials.
Local deterministic diagnostics are optional. The notebook itself has no stored outputs,
personal-machine paths or API keys. Its executed verification copies stay in ignored runs.

## Open it

From the project root in PowerShell:

```powershell
$env:OPENBLAS_NUM_THREADS = '1'
$env:OMP_NUM_THREADS = '1'
uv sync --frozen --all-extras
.\.venv\Scripts\python.exe -m jupyterlab notebooks/00_research_control.ipynb
```

Alternatively open the file in VS Code and select this project's `.venv` Python kernel.
Jupyter is now an optional, locked project dependency; it is not needed by the ordinary CLI.
The initial setup cell finds the root using the existing upstream lock file and never uses
a hardcoded computer path.

If validation rejects `agent.decision_workers` or `agent.credential_mode` as extra inputs,
the kernel may still hold the configuration class from before the parallel update. In VS Code,
choose **Restart Kernel**, confirm this project's `.venv` kernel, and rerun from the setup cell.
Keep both settings. Setup now checks the loaded schema and project location before loading
the controller. Rerunning an import alone does not clear Python's cached modules. Review
`EXECUTE` and `ALLOW_PAID` before Run All; set `EXECUTE=False` for validation without a launch.

## Which experiment should we run?

The default exploratory full batch is **50 rounds for each of four arms**, with one block.
That is four complete trajectories, 200 market rounds and 600 accepted firm decisions.
Retries and formatting corrections add requests. One block cannot support paired p-values;
it is useful for execution, feasibility, behavior and actual warning-exposure diagnostics.
A full batch is not synonymous with a full confirmatory study.

| Condition | Intervention | Central question |
|---|---|---|
| Silent | No notices delivered; detector records flags | What happens without delivered auditing? |
| Private | Truthful behavior-triggered warning, privately framed | Does private auditing change outcomes relative to silence? |
| Public | Equivalent warning body, explicitly public delivery | Does public auditing change outcomes relative to silence or private auditing? |
| Placebo | Truthful neutral public notices on the frozen schedule | Does neutral notification affect outcomes relative to silence? |

The default primary outcome is **gross-profit elevation over the final 25 rounds**. The core
family contains Public − Silent, Private − Silent, Public − Private and Placebo − Silent.
Public and Private have no fines. The implemented fine and numeric-warning arms remain
available as explicit optional extensions, with different causal questions.

## Three AgentRouter keys and parallel decisions

The AgentRouter notebook preset now selects three concurrent firm workers and per-firm
credentials. The three keys belong in the project-root ignored `.env`, never in the notebook:

```dotenv
AGENTROUTER_API_KEY_FIRM_0=YOUR_FIRST_KEY
AGENTROUTER_API_KEY_FIRM_1=YOUR_SECOND_KEY
AGENTROUTER_API_KEY_FIRM_2=YOUR_THIRD_KEY
```

All three required variables are validated before this launcher can send a request. Each firm
receives only its selected key in its child process. The existing `AGENTROUTER_API_KEY` remains
available for configurations selecting shared credentials; it is not a fallback for a missing
firm key. Nonempty process variables take precedence over `.env`.

| Setting | Effect |
|---|---|
| `STUDY["agent"]["decision_workers"]` | `3` runs the three firms concurrently within each round; `1` restores sequential collection |
| `STUDY["agent"]["credential_mode"]` | `per_firm` selects the three variables above; `shared` selects `AGENTROUTER_API_KEY` |
| `CONTROL["requests_per_minute"]` | Shared across all firms, keys and retries; the parallel notebook preset uses `3` |
| `CONTROL["tokens_per_minute"]` | Shared conservative token reservations; stays at `20000` unless explicitly changed |
| `STUDY["agent"]["min_request_interval_seconds"]` | With three workers, minimum spacing for each individual firm, including its retries; `60` in the AgentRouter preset |

The model, prompts, rolling memory, arms, thresholds, round counts and cost caps are preserved.
Every round freezes all three views, obtains three valid prices, and only then updates the
market and auditor once. A failed firm cancels its siblings; no unfinished round is executed.
A retry concerns the same firm's decision, without regenerating successful rival decisions.
Warnings produced after round t still first affect decisions in t+1. The implementation uses
threads around the existing synchronous agent interface, with synchronized budgets/evidence;
it does not replace the market runner or introduce a new experiment framework.

Three keys do not guarantee three separate upstream rate allowances. Global pacing, provider
cooldowns and the existing total $50 ceiling remain shared. At most three firm requests can
be active, and TPM or upstream limits can reduce actual overlap. Cost and independent
replication counts do not fall or rise merely because inference is parallel.

Restart the notebook kernel after loading the updated files and create a new frozen plan.
Historical plans/configurations remain unchanged. The operator's `.stop` file is intentionally
preserved; when ready to permit a new run, remove that file explicitly from the project root:

```powershell
Remove-Item -LiteralPath .\.stop
```

The new key routing has been exercised with fake credentials and the genuine client against
a local simulated server. That check does not establish that each real key has model access,
available quota or an independently sufficient upstream allowance.

Natural behavior-triggered timing is endogenous. Public − Private estimates the difference
between the two audit policies, including subsequent differences in exposure, rather than
isolating common knowledge alone. Placebo's default source rounds `[10, 30]` are fixed
prospectively, effective at rounds 11 and 31. This is not independently yoked warning timing.
The notebook says so explicitly; it does not claim a causal limitation was solved by a UI.

## Configure, preview, freeze, launch

1. Run setup and edit the **configuration cell**. Choose `CONNECTION`, the full `STUDY`
   dictionary, whole-batch `CONTROL` limits, `ANALYSIS`, execution switches and `DISPLAY`.
   Rerun the parameter table after edits.
2. Run preview. It checks the actual Pydantic models, upstream pin, protocol, economic
   references, workload, stop file, ledger and paid budget blockers. No inference occurs.
3. Optionally run the local-server readiness or synthetic auditor-diagnostic cells. A server
   version and installed-model listing do not demonstrate strategic competence.
4. Freeze a plan. This creates a new `runs/notebook-control/nb-.../plan.json`, resolved
   `study.json`, controls, analysis settings, source archive and launcher/notebook copies.
5. Review it. Set `EXECUTE=True` in configuration. For a paid AgentRouter batch, also set
   `ALLOW_PAID=True` deliberately after reviewing the expense. A notebook value does not
   supply additional spending authorization to an assistant operating on your behalf.
6. Press **Run All**. The launch cell stays active, updating its display until execution and
   analysis finish. It then advances to results, figures and the selected offline stages.
   Provider/startup failures appear in the panel and halt Run All. A fresh run uses a new plan.

The launch display shows saved-round percentage, per-condition/block counters, accepted
decisions, completed/failed/in-flight requests, notices consumed, elapsed time, observed-rate
ETA, known token-cost estimate, recent returned agent text and a worker-terminal drawer.
It follows the complete assignment, so the first active condition and queued conditions are
visible before any full trajectory finishes. Responses are labeled accepted versus merely
returned; partial-round decisions do not inflate the committed-round percentage.

Expand **Recent prompts by firm and request attempt** for the full recorded messages. This
drawer is a persistent widget, separate from the refreshing progress HTML. Its prompt snapshot
stays fixed while open, preserving expanded request/JSON sections. Click **Refresh prompts** or
close/reopen the drawer to load the latest attempts. Progress continues updating independently.
The separate **Browse saved prompts** panel remains stable while progress refreshes. Select a
condition/block, round and firm (0, 1, 2), click **Load saved prompts**, then select an attempt.
Click Load again to include newly saved evidence. The browser also appears in the results
section and can be run manually after a failed launch. It shows complete system/user messages,
formatting corrections, replies, notice IDs and acceptance status. A captured outgoing JSON
body is available for guarded Qwen requests; authorization headers and credentials are never
loaded. A recorded start alone does not prove that a request reached the provider.

To inspect the saved failed run without launching another experiment, run the setup cell and:

```python
PLAN_PATH = ROOT / "runs/notebook-control/nb-exploratory-four-arm-a472ffd8fe/plan.json"
PROMPTS = progress.prompt_viewer(ROOT, PLAN_PATH)
```

This researcher display is not supplied to any agent. Previous-round rival prices are shared
by design. Other firms' returned notes, direct profit records and same-round choices are not
supplied. Known market equations and public prices nevertheless permit calculating rivals'
profits. Local tests verify prompt/session boundaries, not the remote provider's internal
isolation. Separate API keys do not by themselves establish independent model memory or quota.

The **Stop experiment** button requests the frozen plan's stop file while the launch cell is
busy. A kernel interrupt during the live launch also requests cooperative stop. Operator stops
preserve partial evidence and allow viewing saved analysis. A server/kernel crash is different:
if the notebook process disappears entirely, use the frozen stop file from another terminal.
The progress display never sends model requests or changes the scientific settings.

| DISPLAY setting | Meaning |
|---|---|
| refresh_seconds | Read-only UI refresh interval; default 2 seconds |
| response_limit | Most recent replies displayed; default 9 |
| response_characters | Preview length; complete replies remain in raw evidence |
| show_prompts | Display complete recent prompts and the live prompt browser; default True |
| prompt_limit | Recent prompt attempts shown in progress; 1–12, default 3. Older attempts remain selectable in saved evidence |
| terminal_lines | Worker-log lines shown in the terminal drawer |
| startup_timeout_seconds | Time allowed for initial worker status before requesting stop |
| stop_grace_seconds | Shutdown wait after interrupting the launch cell |

Display settings do not change the experiment or its analysis. Percentage tracks committed
market rounds; at 100% the panel may still show **Analyzing saved evidence** until analysis
finishes. ETA is an estimate that includes observed pauses/retries, not a completion promise.
The worker uses this project's `.venv` interpreter when present, regardless of the notebook
kernel's executable. The setup cell reloads the updated helper modules between kernel sessions.

Changing any draft study, workflow or statistical setting invalidates the prior frozen plan
at launch. Source, protocol and frozen file changes also block it. A new batch needs a new
plan identity. Old evidence and failed attempts are never overwritten or silently resumed.
Do not modify source/protocol files during a batch: run snapshots must remain comparable.

## First local and first paid runs

For a free **engineering** full run, leave `CONNECTION="scripted"` and enable execution.
The competitive script chooses reference prices; it is not an LLM finding. Selecting
`scripted_policy="coordinated"` fixes joint-reference prices and exercises delivery plumbing;
it cannot demonstrate emergent collusion. Keep those fixtures separate from empirical runs.

For local **empirical** play, choose `CONNECTION="ollama"`, keep the server running on the
configured loopback port and ensure its exact model ID is installed. The preset is
`qwen3.5:4b-q4_K_M` on port `11436`, native Ollama, thinking disabled and 8192 context tokens.
These are exposed defaults, not a claim that this model has passed strategic diagnostics.
Local thinking/context/GPU settings are inactive for remote clients.

For an AgentRouter smoke before a larger batch, edit:

```python
CONNECTION = "agentrouter"                 # then rerun the entire configuration cell
```

In that same cell set `STUDY["rounds"]` and `analysis_last_rounds` to 3,
`batch_arms` to `["silent"]`, and `feedback.schedule_rounds` to `[]`. Keep at least three
auditor rounds, and review the smaller workload's request/cost/time caps. Freeze and launch
with both explicit switches enabled. This requests nine accepted pricing decisions.

For the full four-arm pilot, restore rounds 50, tail 25, the four arms and a prospectively
chosen Placebo schedule. Preserve the verified Responses / price_only / low reasoning /
4096 output combination unless you are deliberately testing a new configuration.
The genuine Qwen Code client connects to `gpt-6.1-sol`; it does not substitute a Qwen model.

The paid worker uses the existing private `.env` reader, local qualification, matching
completed live validation, exclusive workflow lock, cumulative allowance, paced attempts,
request guard and client cleanup. The new `--sweep-config` path preserves the exact frozen
configuration. It does not apply the older convenience pilot's arm, schedule, tail or budget
overrides. Other SDK backends can be represented/inspected but paid notebook execution is
explicitly restricted to the guarded AgentRouter/Qwen workflow.

## Budget and time are separate controls

At one request per minute, the 600 minimum decisions require about ten hours of dispatch
spacing, before response time, token pacing, corrections and provider cooldown. More blocks
multiply that workload. The gateway's actual quota remains unknown; increasing RPM is not
a validated rate-limit fix.

The default per-trajectory reservation cap is $11, summing to $44 for four trajectories.
The shared new-workflow cap is also $44, subject to the existing $50 cumulative ceiling
including earlier known usage and unknown held costs. Preview reads the current ledger;
the numbers are not hardcoded as a claim about today's remaining balance. If the existing
holds leave too little allowance, launch is blocked. Never reset the ledger to create room.

Per-run guards accumulate conservative full reservations. Shared accounting releases known
unused reservations but retains unknown failed-request holds. Therefore a trajectory can
stop under its declared cap even when its known estimated bill is lower. These caps do not
guarantee 50-round completion. The notebook exposes this existing behavior and does not hide
it by claiming precise price forecasts. Input/output rates are operator assumptions; actual
gateway billing controls charges. Token projections from short/local evidence have limits.

## Read and reanalyze output

The results cell prints exact paths for that plan:

| Artifact | How to read it |
|---|---|
| `runs/pilot/<study_id>/sweeps/<plan_id>/manifest.json` | Whole assignment, order, run status and failures |
| `runs/pilot/<study_id>/<run_id>/manifest.json` | One trajectory's resolved config, source identity and outcome status |
| `events.jsonl` beside each run manifest | Raw prompts/responses, retries, rounds, flags, deliveries and consumed-notice links |
| `analysis/<plan_id>/runs.csv` | One row per assigned trajectory; inspect exclusions and actual notice exposure |
| **`analysis/<plan_id>/rounds.csv`** | **Turn CSV**: firm prices, profit elevation, triggers and consumed notices |
| `analysis/<plan_id>/summary.json` | Four core contrasts, paired counts, p-values, uncertainty and analysis settings |
| `analysis/<plan_id>/report.md` | Text analysis; legacy top-level contrast remains Public − Private |
| `trajectories.png` / `run-outcomes.png` | Descriptive plots, one trajectory or run score per displayed series/point |
| `runs/notebook-control/<plan_id>/worker.log` | Launcher output for diagnosing failures |

The offline reanalysis cell accepts an existing sweep manifest and writes a fresh derived
directory. It does not repeat experiments or alter historical configs, tail windows or raw
evidence. Changed inference settings are recorded as a new exploratory analysis. Generic
interval fields include their actual confidence level; legacy `*_95_*` fields are populated
only at alpha .05, so changing confidence does not produce a mislabeled interval.

## What changing configuration means

Model, prompts, memory, horizon, market, arms, detector and notice schedule change the system
or intervention being studied. Outcome, tail window and inference settings change the
measurement/analysis. Pacing, limits and retries mainly change execution, but may select
which trajectories survive and therefore bias retained evidence. Folder labels and plotting
do not add independent evidence. A new fingerprint records differences; it does not itself
make changed designs comparable or scientifically valid.

The notebook documents all 80 settings rather than providing a second drifting list here.
Fixed invariants are identified: three firms, simultaneous play, t+1 delivery, private
information boundaries, one transmission per attempt, pinned dependency and the upstream
240-character note renderer. These need validated implementation changes, not arbitrary
configuration values. `price_only` supplies no free-text notes.

Study both horizon choices and both detector modes as separately frozen notebook batches.
The existing ordinary CLI matrix automates the four strata for regular backends, but does
not provide this notebook's shared paid allowance. It is not a shortcut for paid AgentRouter
execution. A pure publicity experiment with independently controlled truthful information
and independently yoked schedules remains a separate design problem, not an implemented UI
option. No added arms are necessary merely to operate the current project.

## Stop, diagnose and decide whether to continue

The stop cell writes the frozen plan's stop filename. Do not delete it automatically; first
check that workers have stopped. Interrupting the **live launch cell** now requests a stop;
interrupting another cell or closing the notebook does not guarantee worker termination.
The full notebook contains the error/action table for validation, qualification, credentials,
404/429 responses, missing prices, connection failures and exhausted limits. No broad
try/except converts a failure into an invented economic outcome.

Before confirmatory work, require competence and warning-responsiveness diagnostics, valid
calibration, meaningful exposure, independent replication and a feasible precision rationale.
One 50-round batch cannot answer that by itself. Zero warnings, genuine negative outcomes
and uninformative intervals must be reported, not tuned away. Do not add blocks until p < .05.

| Concept | Meaning |
|---|---|
| Arm | One condition / policy |
| Block | Fresh run of each selected arm, paired for comparison |
| Reference | Calculated economic comparison point |
| Nash | Competitive point where unilateral one-round deviation is unprofitable on the checked grid |
| Joint | Maximizing firms' combined one-round profit, without implying agreement |
| Elevation | Position relative to competitive 0 and joint 1 |
| Exposure | Notice actually enters a firm's accepted decision request |
| CI / alpha | Confidence interval / chosen error probability |
| p-value | Extremeness probability under the specified null and test assumptions |
| Holm | Adjustment for the declared comparison family |
| RPM / TPM | Requests / conservative token reservations per minute |
| Confirmatory | Prospectively specified test with credible design and sufficient independent evidence |

For the scientific limits and earlier findings, see the [research protocol used by the
notebook](../research/notebook-exploratory-2026-10-23.md), [metrics and inference](../research/metrics-and-inference.md),
[confirmation gates](../research/confirmatory-candidate-2026-10-22.md) and
[current client setup](QWEN-CLIENT-SETUP.md).
