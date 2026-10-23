# Exploratory Qwen-client market pilot — October 23

This is a development pilot, not confirmatory evidence. Client qualification uses
synthetic server responses; those fixtures are excluded from economic results.

The live pilot compares Silent, Private, Public and Placebo in randomized blocks
using the existing sweep runner. Each condition has three firms and the same
Calvano market, 20-round rolling memory, price-first prompt, hidden final round,
auditor definition and no financial fines. Actual resolved configs and source
hashes determine the implemented specification.

The primary descriptive outcome is gross-profit elevation averaged over the final
half of each trajectory. Onset and warning exposure are secondary diagnostics.
Report Public–Silent, Private–Silent, Public–Private and Placebo–Silent, run counts,
missing/excluded assignments, notices generated and consumed, and uncertainty.
The independent unit is the trajectory/block, not a market round. One block is
insufficient for inference; the existing analyzer must retain that limitation.
No test, threshold or sample size is tuned to obtain a desired effect or p-value.

Real warnings follow behavior and become usable only on the next decision.
Placebo gets truthful neutral notices on two distinct source rounds drawn once
from the complete-window eligible rounds using seed 20261023. The schedule is
fixed before the pilot, reused across blocks, and never shown to agents. This
pilot does not establish robustness over independently sampled placebo schedules.
Natural-trigger comparisons estimate policy differences and do not isolate warning
publicity from endogenous exposure timing. Frozen hypothetical diagnostic cases
are separate evidence and do not fabricate actual misconduct allegations.

Model choice follows availability and price, not favorable market behavior.
GPT-6.1-sol is preferred when operational; the initial working pricing route is
GPT-5.6-sol. Astra is a fallback. Models are never mixed within one comparison
family. Gateway-reported model identity and usage are preserved; underlying model
identity is not independently attested. The gateway has reported maximum reasoning
effort despite our low-effort request. This discrepancy must be disclosed.

Calibration exhausted 256 and 2048 output tokens entirely in reasoning. An 8192-token
pricing replay returned a valid price using 3312 output tokens. The corresponding
output ceiling and 300-second request timeout are frozen before pilot execution.
The existing maximum of two parsing attempts remains; malformed output never
creates a repeated or invented price. Only bounded recorded transport retries for
rate limits and explicitly temporary upstream resource failures are permitted.
Ordinary permission failures and refusals stop. Every failed attempt remains saved.
Technical attrition is reported, not quietly replaced or treated as competition.

The owner authorized $50 total across setup, calibration and pilots. All stages
share the same persistent ledger, stop file and workflow lock. Known unused
reservation is released only after provider token usage is reported; unknown
billing retains its conservative allowance. Actual billing remains the gateway's.
The first workload should be the smallest informative matched block after smoke
and competence/exposure checks pass. Additional blocks require feasible remaining
allowance and unchanged within-family design. Pilot-driven choices do not become
confirmatory findings. A future confirmatory protocol must be independently frozen.
