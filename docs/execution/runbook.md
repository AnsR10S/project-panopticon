# V1 runbook

**Current local-first workflow:** use the [version 1.2 operating manual](OPERATING-MANUAL.md) for installation, Calvano/local pilots, token estimates, AgentRouter configuration, stop controls and troubleshooting. The workflows below preserve the earlier release instructions.

New readers should start with [the priority guide](../READING-ORDER.md). Version 1.1 adds the expanded matrix below; the original demo remains available.

## Expanded auditor and horizon matrix

Run `scripts/run_matrix_demo.ps1` from the project root. The default is two scripted repeats of seven arms in each of four auditor/horizon cells: 56 independent trajectories, with no paid API calls. Each cell has its own analysis; source/config/prompt snapshots are preserved. The manifest under `runs/smoke/expanded-engineering-v11/matrices/` links all four sweeps and analyses.

Equivalent CLI: `.venv/Scripts/auditors-paradox.exe matrix configs/pilot/expanded-scripted-matrix.yaml --replicates 2`.

The expanded arms include public numeric content and actual public/private fines. `cooldown_rounds=2` in this scripted example is a test input, not a scientifically calibrated value. Separate runs/configurations can compare zero cooldown or another prespecified duration. The matrix crosses market/per-firm auditing and hidden/disclosed final rounds; it does not automatically select thresholds, horizon length or sample size.

For local feasibility, `configs/pilot/local-ollama-feasibility.yaml` is a starting configuration using the loopback Ollama server. The named model must already be installed and running. This 100-round/20-record example requires validation of actual context use and strategic behavior; it is not a replication protocol. No key is needed for this local path, and the call budget still applies. Setup does not install Ollama or download a model.

For AgentRouter, select backend `agentrouter`, the actual available model, and either Chat with a documented `/v1` base URL or Anthropic Messages with the corresponding base URL without `/v1`. The runtime reads only `AGENTROUTER_API_KEY` for this transport. Choose measured gateway token rates and a per-run ceiling, then supply `--allow-paid` and `--max-total-cost-usd` for an explicitly authorized matrix. The total cap must cover all four cells and all selected arms. A screenshot balance is not an approved spend ceiling. Neither gateway protocol was live-tested in the offline build.

The chosen primary score is configurable; the new example uses gross-profit elevation while retaining price elevation and net profit as separate outputs. Numeric/fine contrasts are exploratory until the scientific multiplicity and power plan is frozen. Read [the plain-language explanation](../research/START-HERE.md) before interpreting p-values.

## Install and verify

From the project root in PowerShell, run `scripts/setup.ps1`. It verifies or
clones the pinned dependency, installs the frozen lock in `.venv`, and runs
doctor. It preserves an existing mismatched/dirty checkout and fails instead
of resetting it. `scripts/run_checks.ps1` runs formatting, lint, strict typing,
docs/source checks, and the complete offline coverage suite.

## Engineering demonstration

Run `scripts/run_demo.ps1 -Replicates 3`. It assigns all four arms in randomized
order within each fresh block, makes only scripted decisions, verifies each run,
and writes analysis into a fresh `analysis/demo-v1-.../` directory. Inspect
`summary.json`, `runs.csv`, and `report.md`; the plot must say scripted fixture.

## Single run and verification

```powershell
.\.venv\Scripts\auditors-paradox.exe validate-config configs/controls/competitive.yaml
.\.venv\Scripts\auditors-paradox.exe run configs/controls/competitive.yaml --run-id my-negative-control
.\.venv\Scripts\auditors-paradox.exe verify runs/smoke/competitive-control-v1/my-negative-control
.\.venv\Scripts\auditors-paradox.exe inspect runs/smoke/competitive-control-v1/my-negative-control
```

`inspect` reads recorded outcomes. `compare-scripted` compares stable trajectory
fingerprints while excluding durations/timestamps. Neither computes an LLM
counterfactual. Existing output directories are rejected. Never delete an old
attempt to retry its identifier; choose a new attempt ID.

## Model pilot

Derive a configuration from the operational protocol. Set `run_kind: pilot`,
the selected backend/model, output-token cap, parse-attempt cap, timeout, explicit
input/output rates per million tokens, maximum calls, and maximum estimated cost.
Do not put credentials in YAML, prompts, notebooks, or reports. Credential setup
and reuse are a separate user decision. The backend reads its selected provider's
environment only when a paid run is explicitly invoked.

`validate-config ... --allow-paid` performs config/economic/source checks without
dispatching a provider request. `run ... --allow-paid` executes the budgeted run.
A live sweep also requires `--max-total-cost-usd`; the sum of per-run caps must
fit that total. Rates are supplied and dated by the researcher, not silently
guessed from a source-code price table. Reservations use visible request byte
length plus overhead and the output cap. Observed usage is costed at those rates;
this is an estimate, not an invoice guarantee. No implicit SDK retries occur.

Provider APIs can change, reject temperature/model combinations, or return empty
text. A real compatibility pilot is necessary even when fake-SDK tests pass.
Transport errors stop a trajectory and preserve failed evidence. Invalid action
text receives bounded correction attempts and then the explicit competitive
fallback, with that event retained. Predeclare how fallback-heavy attempts enter
the scientific analysis.

## Frozen and failed work

After scientific review, `freeze-config` writes a new JSON/YAML-compatible
confirmatory config with the exact current protocol digest. It refuses to
overwrite the destination. Hashing is not independent preregistration or power
analysis. Save the reviewed sample plan externally if preregistration is desired.

Interrupted runs are not complete and cannot be resumed safely in V1. Restart
under a new ID; preserve failed/partial evidence. Sweep analysis includes every
assigned attempt's disposition, excludes invalid evidence, and reports complete
public/private pairs. Treatment-dependent failures can bias the resulting paired
estimate and must be discussed.

## Updating dependencies

Never advance the upstream SHA during a frozen study. Evaluate a candidate SHA
separately, repeat adapter/fidelity checks, update the lock deliberately, and
start a new protocol/run identity. Keep the upstream license and attribution.
Provider and Python versions are locked by `uv.lock`; old run source snapshots
preserve the exact study code/prompts/protocol used when they executed.
