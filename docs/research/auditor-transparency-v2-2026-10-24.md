# Auditor Transparency as an Experimental Information Treatment

Status: exploratory design, 24 October 2026. No paid inference is authorized by this file.

The primary Auditor's Paradox question remains whether public warnings change behavior
relative to silence and equivalent private warnings. Transparency is a separate secondary
question: **Does revealing the auditor's exact detection and punishment algorithm change
agents' pricing behavior, including remaining below known detection thresholds?**

The earlier V2 fine prompts disclosed the complete behavioral screen, economic references,
graduated rates, cooldown and deduction timing. This was an implemented information regime,
not evidence that agents actually exploited it. Historical results must retain that meaning.
Lower prices alone cannot establish threshold avoidance, economic incompetence or deterrence.

## Information treatment

`v2.sanction_disclosure` selects a rendering strategy independently of enforcement:

| Setting | Information supplied | Interpretation |
|---|---|---|
| `opaque` | Monitoring, possible deductions, own gross/deduction/net accounting, unrestricted valid choices | Adaptation under undisclosed screening and penalty rules |
| `transparent` | Original V2 screen, references, tiers and timing, verbatim | Adaptation with the enforcement algorithm supplied |
| Missing in historical V2 | Interpreted as `transparent` | Compatibility, never a retrospective relabeling |

New sanction presets explicitly choose opaque, use new study IDs and reference this separate
protocol. Existing presets and completed evidence remain unchanged. A notebook upgrade preserves
saved disclosure; review the visible switch before adopting a new design. Switching disclosure
requires a **new study ID, protocol and frozen plan**, even if prices, thresholds and rates stay
the same. Ordinary continuations cannot change source or settings. Configuration/comparison hashes
separate the regimes; do not merge their trajectories into one treatment estimate.

Opacity means the algorithm is not directly supplied. Agents can learn from deductions and
warnings; own accounting is intentionally available. Public historical prices and an initial
competitive reference record also remain visible. Do not describe this as complete ignorance
of economic reference values or as a guarantee that rules cannot be inferred.

`MARKET_KNOWLEDGE` is independent: qualitative profiles describe demand direction; explicit
profiles provide the actual demand/profit equations. Economic knowledge is not knowledge of
the detector. Custom system templates can deliberately override profile content and must be
reviewed as separate experimental stimuli.

## Controls and outcomes

Silent has no sanctions and no notices. Fine Only permits deductions without a separate event
notice. Private Fine and Public Fine have identical financial information and warning bodies;
receipt framing differs. Exact own financial fields are the same across the fine arms.

Auditor qualification, escalation, reset, cooldown, event/continuing deductions and economic
outcomes are unchanged. Event and continuing timing remain separate study families. Financial
regime versus Silent comparisons include the initial expectation of possible deductions, even
when no deductions occur. Distinguish:

- Expected-sanction deterrence before exposure.
- Learning about unknown monitoring from observations.
- Adaptation to disclosed thresholds; requires additional controlled evidence.
- Responses after actual warning consumption, beginning at t+1.
- Responses to actual financial deductions.

Do not select disclosure, thresholds or prompts because they produce elevated prices or a small
p-value. Retain the existing gross-profit endpoint, trajectory-level replication and declared
comparison family. A transparency factorial is optional secondary work, not an automatic batch
expansion or a new confirmatory claim.

## Verification before research

Use the offline notebook prompt preview for every selected arm and firm, and the live recorded
request viewer for later rounds and parsing corrections. Raw manifests and SQLite may contain
all algorithms; they are researcher evidence, not model input. Custom notices can introduce any
information an operator types, so manual intervention remains explicitly exploratory.

Ten-round scripted tests at [200,200,200] must have identical economic/audit/financial traces
under both disclosure modes. With window 3 and cooldown 5, event deductions occur at rounds 3
and 9; continuing deductions occur at rounds 3–10. Notices are consumed at rounds 4 and 10,
except Fine Only, which has none. These are software checks, not findings about LLM behavior.

| Concept | Plain meaning |
|---|---|
| Disclosure | Information supplied about enforcement |
| Qualification | Observable behavior meets the declared screen |
| Cooldown | Waiting rule between eligible announcements |
| Gross / net profit | Profit before / after deductions |
| Leakage | Information reaching a firm outside its declared information policy |
| Transparency confound | Behavior may reflect known screening boundaries rather than the intended warning mechanism |
