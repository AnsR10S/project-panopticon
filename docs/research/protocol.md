# V1 operational research protocol

**October 22 status:** this file preserves the original operational protocol and
expansion. Read the [executed Calvano repair report](../reviews/2026-10-22-study-repair/REPORT.md)
and [gated confirmation candidate](confirmatory-candidate-2026-10-22.md) for current
evidence and the proposed next study. Linear defaults below are historical, not the
October 22 experiment parameters. No scientific confirmation has been frozen.

**Version 1.2 operational update:** the [local-first decision record](design-revisions/2026-10-20-local-first.md) and [operating manual](../execution/OPERATING-MANUAL.md) specify Calvano/tick units, bounded transport retries, abort-on-exhausted live parsing, operator/time limits and token profiling. Original linear release settings below remain historical. The scientific confirmation plan is still unfrozen.

**Scope note:** this is the original four-arm, no-fine pilot core. The [owner-approved expanded design](design-revisions/2026-10-16-owner-decisions.md) adds numeric warnings, fine arms, auditor alternatives and hidden/disclosed horizons. Its exact implemented behavior is specified below. No confirmatory scientific protocol is frozen.

Version 1.0, October 16, 2026. **Operational draft for pilots.** The software's
engineering release does not freeze a confirmatory statistical study or imply
that the hypothesis has been observed.

## Question and scope

Does explicitly announced common receipt of an anti-coordination warning change
run-level supracompetitive pricing compared with individually delivered receipt?
The candidate mechanism is feedback acting as a coordination signal. A price
effect alone does not establish formal common knowledge, agreement, or intent.

V1 has three independent pricing agents and fixed pretrained models, no direct
agent chat, one symmetric linear differentiated demand model, and no sanctions
or action restrictions. Ghost Cartel, agent replacement, internal activation
probes, LWM, RL training, and additional market models are future work.

## Market

Each firm simultaneously chooses an integer price. Demand is
`q_i = max(0, a - b*p_i + d*average_rival_price)` and own profit is
`(p_i-c)*q_i`. Defaults: `a=150, b=3, d=2, c=30`, grid 1-100. The adapter verifies
the competitive reference against every unilateral deviation and verifies the
joint reference against all unordered three-firm grid profiles. Defaults yield
competitive price 60, joint price 90, per-firm reference profits 2700 and 3600.
These are mathematical/scripted checks, not observed LLM behavior.

## Information

Every agent receives the same fixed market instructions, public prior prices,
its own prior market share and profit, and its own bounded returned notes. Rival
profits, rival individual shares, private notes, current-round rival prices,
the auditor's full evidence, and the experiment store are withheld. All views
are frozen before any current-round request. Public prices may already reveal
most facts summarized by the warning; this limits information-leakage claims.

System notices enter a separate inbox for exactly the next decision. Their
original text is not automatically copied into future market history. An
agent's own returned notes can preserve its reaction within bounded memory;
this is part of the stated policy. The notice window and note retention are
fixed across arms. No periodic rival-profit report is generated.

## Auditor and four arms

The deterministic monitor updates on every completed round. A flag requires a
full consecutive window where mean gross-profit elevation exceeds the frozen
threshold and price spread remains below the frozen maximum. Default pilot
values: three rounds, elevation >0.25, spread <=2. Score is clipped current
reward elevation for descriptive monitoring, never a calibrated probability.
It never changes rewards. Thresholds are pilot choices, not validated truth.

- C0 silent: log auditing and routing, send no audit-derived notice.
- C1 private: give each agent the same warning body; do not announce others.
- C2 public: same body with an explicit announcement that all three receive it.
- C3 placebo: identical public neutral bulletin on a predeclared independent
  schedule, whether or not the current market is flagged.

Warning at t is first eligible at t+1. A final-round warning has no next decision
and is not counted as consumed. In fixed-signal diagnostics, C1/C2 use the same
scheduled neutral text as C3; that message truthfully describes a schedule,
not a live detection. C3 in the live-warning design is a limited control: it
does not match every dimension of timing, valence, and factual information.

## Assignment and outcomes

Assign complete trajectories to conditions in randomized order within replicate
blocks. Start fresh environment, agents, memories, auditor history, and inboxes
for every run. Seeds identify replication blocks, not guaranteed matched API
draws. Execution is serial in V1 to simplify budgets and rate-limit accounting.

Candidate primary outcome: run-level mean normalized price elevation over the
last prespecified rounds, `(mean_price-Nash)/(joint-Nash)`. Keep values below zero
or above one. Primary contrast: public minus private. Other arm contrasts,
dispersion, profits, warning exposure, and trajectories are secondary. Outcomes
are computed from raw economics, separately from audit flags.

Inference uses paired run differences and a seeded percentile bootstrap over
independent blocks. The reported paired sign-flip test additionally assumes
exchangeability/symmetry under its null. Fewer than two complete pairs produce
an explicit insufficient-evidence result. No rounds-as-independent-samples test.
Small scripted checks verify the calculation; they are not a power analysis.

## Missingness and failures

Preserve every planned assignment and failed attempt. Distinguish assigned,
completed, validated, and included counts by arm. Pairwise analysis includes
complete validated public/private blocks and reports missing blocks; it may
be biased under treatment-dependent failures and must be interpreted with that
limitation. Never select only flagged runs or favorable trajectories. Save
parse attempts and fallback actions. A failed trajectory gets a new attempt ID
if repeated; V1 does not promise safe resume or exactly-once API execution.

## Freeze gate before confirmatory work

Choose and document provider/model and settings, price rates and budget, neutral
prompt wording, audited window/threshold, exact primary window, minimum effect
of interest, replicate count justified from pilot variability, assignment seed,
failure/missingness rule, and final inference plan. Freeze protocol and config
bytes and record pre-sprint versus sprint contributions. A hash is an integrity
measure, not an external preregistration record. Novelty remains unestablished.

## Owner-approved software extension: 1.1

This extension is implemented for pilots. It does not freeze the scientific design.

- `auditor.mode=market` retains the full-window gross-profit-elevation and price-spread rule. `per_firm` flags firm i when its mean price over a full window exceeds `Nash + threshold*(joint-Nash)`. These are different policy definitions, not identical thresholds in different code.
- `cooldown_rounds=k` requires more than k rounds since that firm's previous flag. Zero permits consecutive flags. Market-wide flags apply to all three firms. Per-firm auditing can flag multiple firms together. Threshold/window/cooldown remain explicit inputs; the batch never calibrates them from its own outcomes.
- `private` still means identical individual delivery to all three, without announcing other recipients. `private_fine` uses the same audience rule. Targeted-private delivery to only flagged firms is not implemented or silently substituted.
- `public_numeric` adds current observed prices for the flagged firms. It explicitly says those examples are not the auditor's exact threshold. Numbers were already public in market history; this arm tests their inclusion/salience in the notice.
- `public_fine` and `private_fine` deduct `rate*max(gross_profit,0)` from each flagged firm after the market and audit, before updating memory. A negative profit is never reduced in magnitude as a supposed fine. Audit decisions use gross economics; records preserve gross rewards, deductions and net rewards. Firms see their own net earnings and own penalty marker, without rival profits.
- Fine arms disclose the financial policy at setup and truthfully report deductions; their comparison includes both knowledge of the sanction policy and its consequences. They do not restrict valid prices. No-fine arms preserve gross/net equality.
- `disclose_horizon=false` removes the final-round count from system and decision prompts. Current round numbers remain visible. Matrix runs explicitly cross hidden/disclosed settings; this compares awareness, not horizon length.
- `primary_outcome` can select price or gross-profit elevation. New expanded example configurations select gross profit; this is a pilot candidate, not a confirmed scientific endpoint. Both scores and after-fine profits remain exported. Onset analysis is still future evaluation work.
- `matrix` runs two auditor modes by two horizon-disclosure settings, in a recorded randomized cell order, with randomized arm order inside replicate blocks. Each cell is analyzed separately. Cross-cell comparisons remain exploratory; a confirmatory factorial assignment/inference plan still needs to be frozen.
- The expanded example includes the original four arms plus numeric-public, public-fine and private-fine. Seven arms times four cells is **28 independent trajectories per replicate**. Two scripted repeats are an engineering check, not an adequate scientific sample plan. Pilot budgets may select a narrower explicit arm list before any paid run.
- Configured-rate spend caps cover the entire paid matrix, including all cells. AgentRouter and loopback Ollama are additional transports; neither was live-tested in this build. No gateway credential is stored in the resolved configuration.
