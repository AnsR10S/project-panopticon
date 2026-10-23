# AgentRouter through Qwen Code: setup and recovery

**October 23 update:** GPT-6.1-sol completed three market rounds (nine decisions) without failed market calls. Following fresh $0.15 approval, all six fixed connection cases passed on the updated guard using eight requests, including two rate failures recovered by paced retries. Read the [completed validation](../reviews/2026-10-23-qwen-live/POST-UPDATE-VALIDATION.md) and [optimization investigation](../reviews/2026-10-23-qwen-live/REQUEST-OPTIMIZATION.md). This establishes connection execution, not scientific readiness.

Qwen Code is the connection client, not the pricing model. GPT-6.1-sol is now a working pricing route at the owner's cheaper screenshot rates. A short successful run does not guarantee sustained availability or research readiness.

## Current optimized calibration configuration

A portable, credential-free configuration is checked in at `configs/pilot/agentrouter-qwen-price-only.yaml`; the private working copy is `configs/local/agentrouter-qwen-price-only.json`. It keeps the 20-round window and hidden horizon, uses `price_only`, requests low effort, and caps pricing at 4096 output tokens. The provider still reports maximum effort. Trivial frozen isolation checks use at most 512 tokens.

The shared validator defaults to one request attempt and 20,000 conservatively estimated input/output tokens per rolling minute. Its reservations, provider cooldown and rate-failure streak persist across restarts. Do not raise throughput simply to get around upstream throttling. Exact provider rate/reset hints are honored when available. Neither setting guarantees that upstream shared capacity is available.

The qualified launcher supports the following workflow for authorized runs:

```powershell
.\scripts\agentrouter.ps1 -Client qwen_code -Action Validate -Config configs/pilot/agentrouter-qwen-price-only.yaml -Model gpt-6.1-sol -ReuseProbe -RequestsPerMinute 1 -TokensPerMinute 20000
```

The latest bounded validation is complete. The fresh $0.15 approval covered that verification only; plan and authorize any additional paid workload before executing it. `-ReuseProbe` can reuse a successful matching-route price reply as connection evidence, a completed matching smoke, and exact passed isolation cases from the same recorded client identity. A guard change requires local requalification and cannot reuse incompatible cached isolation evidence. `-PromptProfile` and `-OutputTokens` allow explicit exploratory overrides; do not silently change a frozen research specification.

One request per minute means a 50-round three-firm trajectory needs at least about 2.5 hours, and four such arms need at least about ten hours, before long responses or retries. The three-round success does not justify promising that workload will finish. Inspect the technical and scientific gates before expanding.

The remainder describes the preserved original preparation path and general operation. Its old 8192-token price-first configuration is retained separately from the new price-only calibration.

## 1. Put the replacement token in the private file

From the project root, open `.env` locally. Its one required line is:

```dotenv
AGENTROUTER_API_KEY=your_new_token
```

No quotes or spaces are needed. Save it. Never paste the value in chat, a command argument, config or report. The file is ignored by Git and excluded from source snapshots. The loader reads only this setting, with no expression evaluation or interpolation. An existing process `AGENTROUTER_API_KEY` takes precedence; remove a stale process value if necessary:

```powershell
Remove-Item Env:\AGENTROUTER_API_KEY -ErrorAction SilentlyContinue
```

`Validate`, `Diagnose` and `Pilot` read the file directly. SDK actions remain separate and may use the older hidden prompt. No second key is needed for the three firms; each has isolated memory while sharing the same authorized billing account.

## 2. Install and qualify locally — no API spending

The Python environment is project-local. If missing, follow `scripts/setup.ps1` and the [first-run walkthrough](FIRST-RUN-WALKTHROUGH.md). Then:

```powershell
.\scripts\setup_qwen_code.ps1
.\scripts\agentrouter.ps1 -Client qwen_code -Action Check
```

Node 24 is the verified runtime; Qwen Code 0.26.0 and npm integrity are pinned in `tools/qwen-code`. No personal coding-client configuration is changed. Check exercises the actual client against a local simulated server, including native Responses transport, private-memory boundaries, parsing correction, warning delivery after t, timeouts, missing usage, tool rejection and cancellation. It does not use a paid token. A skipped acceptance check cannot qualify the client. A changed adapter/guard identity requires requalification.

The runtime compatibility hook extends `skipStartupContext` to suppress Git/date reminders. It is recorded and hashed; vendor files on disk are unchanged. All built-in tools are explicitly excluded. The network guard rejects additional client requests, unexpected destinations, redirects, models, token ceilings, tools or conversation additions before transmission. This is an intentionally constrained use of the genuine client, not a claim of stock runtime behavior.

## 3. Understand the active configuration

The prepared private smoke config is `configs/local/agentrouter-qwen-smoke.json`. Historical configs and failed evidence are retained. Preserved price-first values (the optimized configuration above is separate):

| Setting | Value / reason |
|---|---|
| backend / client | `agentrouter` / `qwen_code` |
| model | `gpt-5.6-sol`; cheaper 6.1-sol only after a healthy connection |
| base_url / api_protocol | `https://agentrouter.org/v1` / `responses` |
| prompt_profile | `price_first`; optional bounded guidance has no successful live result |
| max_output_tokens / timeout_seconds | 8192 / 300; even this ceiling once exhausted without a price |
| reasoning_effort | `low` requested; provider response reported `max`, so actual control is unresolved |
| min_request_interval_seconds | 20, with requests sequential |
| transport_attempts | At most 3 recorded attempts for eligible temporary failures |
| max_action_attempts | At most 2 parsing attempts; no repeated-price fallback |
| memory_window / disclose_horizon | 20 / false |
| smoke | Silent, 3 rounds, 9 intended pricing decisions |
| economic mechanism | Calvano market, no fines in the four core arms |

The Sol models explicitly rejected Chat Completions and required Responses. The documented alternate `co.agentrouter.org` host rejected this replacement credential with HTTP 401; do not assume hosts share account authentication.

For a **new installation only**, Setup creates private configs and refuses to overwrite existing ones:

```powershell
.\scripts\agentrouter.ps1 -Action Setup -Client qwen_code -Model gpt-5.6-sol -InputRate 8 -OutputRate 40 -SmokeBudget 10
```

Those are screenshot account rates in USD per million tokens, not a guarantee of current billing. Check your account before future spending. `-Model` on live actions updates both selected model and its recorded screenshot rate pair: 6.1-sol 3/15, 5.6-sol 8/40, Astra 16/80. Do not mix models within one comparison family.

## 4. Finish live smoke validation only after service recovery

The following command is ready for a healthy route. It is **not a recommendation to keep retrying the current HTTP 405 block**:

```powershell
.\scripts\agentrouter.ps1 -Client qwen_code -Action Validate -Config configs/pilot/agentrouter-qwen-price-only.yaml -Model gpt-6.1-sol -ReuseProbe
```

ReuseProbe uses the saved successful OK probe for that model. On a new installation without one, omit that switch. Validation runs nine pricing decisions through the existing three-round Silent market, then six frozen-observation isolation calls. Failed rounds stop; no price is invented. Only a fully completed matching smoke artifact can be reused after a later isolation failure. A failed partial smoke is preserved and a new run gets a new ID.

The six simple isolation calls check execution/session boundaries; they do not establish strategic competence or warning responsiveness. Local exact-conversation tests provide stronger direct evidence of memory isolation.

Validation now has a **cumulative $10 held-allowance / 64-attempt sublimit**, including prior ledger entries. This supersedes the initial $3/31 plan because observed reasoning cost was much larger. Diagnose and Pilot share the authorized **$50 total** ledger; none resets it. The old Qwen `Smoke` and `Full` actions are blocked to avoid a separate spending path; use Validate/Pilot.

## 5. Diagnostics, then the smallest informative four-arm pilot

After operational validation, run the frozen diagnostics:

```powershell
.\scripts\agentrouter.ps1 -Client qwen_code -Action Diagnose -Config configs/pilot/agentrouter-qwen-price-only.yaml -Model gpt-6.1-sol
```

This administers six own-payoff-table competence cases and six explicitly hypothetical warning-response cases, capped at 2048 output tokens per case. The live attempt completed three cases before a gateway block. These are neither real warnings nor market trajectories. Inspect correctness, missing cases, visible replies and usage before proceeding. A wrong case or absence of response is evidence to investigate, not an instruction to force higher prices.

Once smoke, diagnostics and remaining allowance support it, a small exposure pilot can use:

```powershell
.\scripts\agentrouter.ps1 -Client qwen_code -Action Pilot -Config configs/pilot/agentrouter-qwen-price-only.yaml -Model gpt-6.1-sol -Rounds 10 -Replicates 1
```

This is **10 rounds per arm**, 40 market rounds and 120 intended decisions across Silent, Private, Public and Placebo. Arm order is reproducibly randomized within the block. The runner preserves natural triggers, truthful neutral placebo notices, next-round delivery and no fines. The placebo schedule is frozen before the run and hidden from agents. The actual resolved config/source snapshot is authoritative. The pilot refuses to start without completed matching operational validation; scientific diagnostic readiness still requires inspecting the saved evidence.

`-Rounds` changes each trajectory length; `-Replicates` adds independent blocks. Pilot supports at most 100 rounds and five blocks through this bounded workflow. Do not expand merely to chase significance. A ten-round run is a short feasibility/exposure diagnostic, not evidence of long-run collusion. One block cannot support useful confirmatory inference. A 20-round four-arm workload at the full output ceiling exceeds $50 before input costs; changing a length does not make it affordable or scientifically adequate. [Exploratory protocol](../research/qwen-exploratory-2026-10-23.md).

## 6. Stop controls and errors

Ctrl+C interrupts the owner process and closes its owned client. To request a stop between or during polling from another project terminal:

```powershell
New-Item -ItemType File -Path .stop -Force | Out-Null
```

Remove `.stop` only when you deliberately intend to allow new requests:

```powershell
Remove-Item -LiteralPath .stop
```

A stop cannot undo an already transmitted/billed request. The workflow also checks elapsed time, request count, token ceilings, per-run budget and the persistent shared allowance. It uses `runs/diagnostics/qwen-live/active.lock` to prevent concurrent paid workflows. If a process was forcibly killed, verify it has ended before handling a stale lock. Never delete the allowance ledger to get around a cap.

| Error / observation | Meaning and action |
|---|---|
| Missing/duplicate credential | Correct `.env` locally; never print its value |
| Local qualification blocked | Read `runs/diagnostics/qwen-client/<id>/report.json` and acceptance log; no paid request should follow |
| HTTP 400 requires Responses | Use the native `responses` config; do not replace the client with a disguised SDK |
| HTTP 401 | Token, host or model/account permission mismatch; stop and check account settings |
| HTTP 200 with SSE error | Not success: guard records the underlying failure |
| Temporary resource/rate/capacity failure | Up to three explicitly recorded first-decision attempts with bounded backoff; exhausted attempts stop |
| Ordinary refusal/permission failure | Terminal; do not bypass policy or change identity |
| HTTP 405 HTML block | Stop paid runs; consult provider with the prepared service draft |
| Incomplete response / all tokens in reasoning | No price was decided; inspect usage and effort discrepancy; do not endlessly raise the ceiling |
| Missing usage | Unknown billing, retained reservation and failed response; never treat as zero cost |
| Bad price format | Bounded correction in that same decision's session; exhausted correction aborts the round |
| Cost/time/request stop | Evidence is retained; inspect the shared ledger before any deliberate follow-up |
| No auditor notices | Could be genuine lack of suspicious behavior or insufficient run length; verify triggers and exposure before causal interpretation |

There is no broad catch-and-continue fallback. Structured errors preserve evidence and stop undecidable rounds; continuing with a fabricated/repeated price would contaminate the experiment. A new attempted run uses a new ID; it does not overwrite failed evidence.

## 7. Open, read and export evidence

- Live workflow summaries: `runs/diagnostics/qwen-live/validation-*.json`, `diagnostics-*.json`, `pilot-*.json`.
- Shared allowance and actual request records: `runs/diagnostics/qwen-live/allowance.json`.
- Actual market evidence: `runs/pilot/agentrouter-qwen-smoke/<id>/manifest.json`, `events.jsonl`, `resolved-config.json`, `source.zip`.
- Sweep output: path recorded as `sweep_manifest` in a completed pilot summary; existing analysis reports central contrasts, missing runs and per-round CSVs.
- Current closeout exports: `analysis/qwen-live-20261023-summary/rounds.csv`, `decisions.csv`, `requests.csv`, `summary.json`.

To view the actual committed round in PowerShell:

```powershell
Import-Csv analysis/qwen-live-20261023-summary/rounds.csv | Format-Table -AutoSize
```

The decision CSV marks `round_committed=False` when a price was accepted but the whole round failed. Never pool those partial decisions with completed outcomes. The [closeout report](../reviews/2026-10-23-qwen-live/REPORT.md) explains what each result can and cannot establish. The [prepared service message](AGENTROUTER-SERVICE-ISSUE.md) is a draft; it has not been sent.

| Concept | Plain meaning |
|---|---|
| Client / model | Connection software / program choosing prices |
| Arm | One information policy being compared |
| Round / decision | Three-firm market interaction / one firm's selected price |
| Memory | Only that firm's permitted rolling history and retained own notes |
| Competitive reference | Calculated Nash benchmark price 137 |
| Joint-profit reference | Calculated symmetric combined-profit benchmark price 200 |
| Profit elevation | Profit above the competitive reference divided by the gap to the joint-profit reference |
| Exposure | A notice actually consumed in a decision |
| Replicate | Fresh whole trajectory/block, not another round within the same remembered game |
| Reservation / known charge | Allowance held conservatively / cost estimate from reported usage |
| Confirmatory readiness | A frozen valid design, reliable execution, adequate exposure and independent replication; not yet demonstrated here |
