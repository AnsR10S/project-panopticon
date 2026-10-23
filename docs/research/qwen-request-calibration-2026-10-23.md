# Price-only response and shared request pacing calibration

This is exploratory connection/format calibration, not a test of the Auditor's
Paradox. Earlier configurations and evidence remain intact. Model availability,
valid-price completion, token use, latency and failures determine feasibility;
price elevation or the desired auditor effect do not determine prompt selection.

The optional `price_only` profile preserves the market system prompt, profit
objective, integer price bounds, project-controlled history, warning text and
hidden horizon. It requests exactly one `PRICE: <integer>` line, asks for a
practical decision rather than solving the full repeated game, and removes the
optional written note. Removing that note also changes the future private note
content: the history still retains the firm's response, but it contains only a
price if the request succeeds. This is a real prompt/memory-content change, not
a claim of an economically identical treatment. Do not pool it with older
price-first trajectories or silently promote it to confirmatory use.

First replay an initial real market observation, with its economic information
unchanged. The replay selector excludes hypothetical diagnostic cases and later
rounds. Preserve the system and the observation prefix byte-for-byte, changing
only the action/response instructions. Use 4096 output tokens, a 300-second
timeout and requested low reasoning effort. Provider reports of maximum effort
remain unresolved; a prompt cannot guarantee a particular hidden token budget.

Default shared pacing is one recorded request attempt per rolling 60 seconds
and 20,000 conservatively estimated input-plus-output tokens per rolling minute.
The output ceiling is reserved, not just expected visible answer length. Input
uses UTF-8 message bytes plus the configured overhead. This is an intentionally
conservative estimate, not an exact tokenizer count or a guarantee against shared
upstream rate limits. State is written before transmission and survives launcher
restarts. Unknown/failed requests retain pacing reservations until expiry.
Existing per-request, elapsed-time, stop-file and total-cost controls remain.

Paid checks share the existing $50 ledger and use explicit smaller per-invocation
caps. Prefer GPT-6.1-sol if operational, otherwise the cheaper working alternative
GPT-5.6-sol. A provider HTTP 405 access block stops paid checks; no host, identity
or header changes are used to bypass it. Known temporary capacity/rate failures
may have bounded, recorded backoff as already supported.

After a successful pricing replay, attempt the existing three-round Silent smoke
and six frozen isolation decisions, only within the remaining allowance. A full
fifty-round research workload is not justified before consecutive decisions are
reliable. A short smoke demonstrates execution; it does not establish warning
responsiveness, auditor exposure, learning or collusion.
