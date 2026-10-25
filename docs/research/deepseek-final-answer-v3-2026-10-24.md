# Exploratory parser repair: final answer V3

The previous DeepSeek trajectory failed at Placebo round 20 and Silent round 16 after
two completed responses each. The responses clearly selected 135 and 137 respectively.
V2 rejected repeated Decision/Answer headings even when they agreed, and did not recognize
the imperative “Set my price at 137”. This is an extraction defect, distinct from token
truncation, economic competence, treatment responsiveness or a research finding.

`agent.action_parser="final_answer_v3"` examines every nonempty declared final section,
accepts only consistent explicit choices, and recognizes complete Set/Maintain/Keep my/the
price at/to commands. Alternatives, conflicting declarations, decimals, bounds violations
and unfinished thinking remain rejected. Empty formatting headings do not constitute choices.
The parser never uses the last observed price as a replacement or extracts arbitrary numbers.

Legacy, final_answer_v1, final_answer_v2 and the historical `auto` selection remain unchanged.
V3 is explicitly selected in a new study/campaign. Prior research configurations, responses,
decisions and failed attempts are not rewritten. Offline re-extraction is exploratory and
must not be presented as a completed historical market trajectory.

Changing extraction changes the accepted-action policy. Keep a new study ID and source/config
snapshot. Do not resume the orphaned V1 attempt or substitute a retrospectively extracted
price into it. A model response can be correctly parsed while its economic reasoning is wrong.
Smoke/corpus/replay diagnostics do not establish full-length live-model reliability or
scientific validity. Confirmatory collection still requires independent development evidence.
