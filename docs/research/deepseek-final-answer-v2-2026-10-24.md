# DeepSeek final-answer V2 exploratory execution policy

This protocol versions an output-handling repair, not a new economic hypothesis.
The preceding failed sweep `nb-exploratory-four-arm-00f2d43f43` committed zero rounds.
Its complete Placebo replies ended with `DECISION: PRICE: 137`, which the V1 final-answer
parser rejected. A separate Silent request exhausted its output allowance.

The new `final_answer_v2` parser recognizes a standalone `DECISION:` heading as a final
decision section. It retains the previous parser's range, ambiguity, quoted-text and unfinished
thinking checks. Conflicting final decisions still fail. It never takes an arbitrary last number.
Historical `final_answer_v1`, `legacy` and `auto` meanings are unchanged.

New local runs explicitly select V2 and bounded exact-observation output recovery: initial
4096 tokens, at most one restart with 6144 tokens, context 8192. Each request is counted and
recorded. Insufficient context headroom or repeated truncation aborts the undecidable round.
The existing parsing-correction limit remains two; increasing it is not the repair criterion.

The notebook retains the existing model, prompts, economics, arms, detector, horizon, memory,
round count and endpoint window. It uses a new study ID and this new protocol. Execution
remains disabled until the researcher explicitly launches a fresh frozen plan. Do not resume
an old source identity with the new parser or retrospectively relabel historical outputs.

A bounded three-round, nine-decision local Silent smoke may verify execution and saved-evidence
replay. It is a diagnostic, not research replication or evidence of collusion. No paid API calls
are required. A successful smoke does not guarantee that every long trajectory will complete.
