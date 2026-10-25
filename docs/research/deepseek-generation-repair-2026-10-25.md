# DeepSeek generation reliability pilot — 2026-10-25

This is a fresh exploratory warning-only study, not continuation or confirmatory evidence.
The failed V2-parser trajectory and its unfinished responses remain unchanged.

The previous local DeepSeek R1 7.6B Q4_K_M response at Placebo round 3 repeated competing
candidate prices until both the 4,096- and 6,144-token ceilings were exhausted. Neither
response contained a completed final answer. Parsing considered numbers would invent a decision.

The prospective generation configuration uses temperature 0.6, repetition penalty 1.2,
and a 512-token repetition window. These change the sampling distribution and are recorded
experimental settings, not an information-equivalent repair. The candidate was selected for
completion reliability, not price elevation or treatment effects. The initial ceiling remains
4,096, with one exact-observation restart capped at 8,192 and context 12,288. Exhaustion still
aborts the undecidable round; partial reasoning never supplies a price.

The explicit `final_answer_v4` strategy (retaining V3 unchanged) accepts consistent final declarations and unambiguous
price commands. Historical parsers remain available and unchanged. Model thinking is archived
but never copied into another firm's prompt. All firms see only completed-round prices and
their authorized own outcomes/notices.

The local verification workload is four arms (Silent, Private, Public, Placebo), one independent
trajectory each, 50 rounds each, final 25-round gross-profit elevation endpoint. One block
supports descriptive comparisons only. No scientific readiness or significance is inferred
from successful execution. Warnings need truthful eligibility and next-round consumption;
no warning is inserted merely to exercise treatment.

The Kaggle cycle uses the same generation controls with separate V2 warning/event/continuing
study identities. These are distinct financial regimes and must not be pooled. The new
package/campaign cannot silently resume trajectories collected with old source or settings.

Model authors recommend temperature around 0.6 to reduce repetitive degeneration:
[DeepSeek model card](https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B).
The exact repetition settings are project development choices, not validated economic parameters.

The first generation-repair development attempt stopped after a clear chosen-action wrapper
was rejected twice: `The chosen action is: PRICE: 185`. V4 normalizes only that anchored
explicit wrapper, then applies unchanged V3 conflict/bounds/thinking checks. The stopped
attempt remains under `deepseek-generation-v1`; the new batch is `deepseek-generation-v2`.

## Structured local response candidate

The second prose-format development attempt selected 265 outside the 100–250 grid, then
corrected itself to 239 using another unsupported wording. Both are preserved in
`deepseek-generation-v2`. The next separately identified study is `deepseek-json-v1`.

Its native Ollama request carries a JSON schema enumerating **every** valid integer action
from the configured market grid. It excludes no valid choice and suggests no target price.
The prompt requests only `{"price": <integer>}`; parser `json_price_v1` rejects duplicate
keys, nonintegers, extra fields, prose, incomplete objects and out-of-range values. This is
constrained decoding, not semantic extraction from unfinished reasoning. Thinking channels
are still archived separately; `done_reason=length` remains an error even if part of a JSON
object is visible. Exact-observation recovery retains the schema and consumes the budget.

This changes the prompt and sampling support over output strings. It is not silently pooled
with prose-format evidence. The old text strategies remain available with
`local_output_format="text"`; custom/note prompts are incompatible with this price-only mode.
All firms receive the same response-format treatment. Market observations, timing and warning
content remain as configured. The local full-run verification uses the existing qualitative
V1 economic information; Kaggle V2 presets retain their separately selected knowledge profile.
