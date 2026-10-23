# Decision: qualify a genuine Qwen Code adapter before paid use

Status: **candidate selected; qualification failed; paid use blocked**.

October 23 update: the owner directed continued development and authorized up to
$50 total. A recorded runtime compatibility hook extends `skipStartupContext`
to suppress the pinned client's Git/date reminders. This supersedes the earlier
decision against runtime modification; installed vendor files remain unchanged.
The guard still validates exact content before transmission. Both native Chat and
Responses sessions have passed local prompt/continuation checks. AgentRouter's
live HTTP 400 response states that GPT-6.1-sol requires `/v1/responses`; the adapter
now supports Qwen Code's native `openai-responses` route. Earlier findings below
are historical, not the current implementation status. See the updated
[setup guide](../execution/QWEN-CLIENT-SETUP.md).

The account's generic SDK requests were rejected as unauthorized clients.
[AgentRouter documents Qwen Code](https://github.com/agentrouter-org/docs/blob/main/en/qwencode.md)
with an OpenAI-compatible route and explicit model selection. This makes it a
reasonable candidate for retaining GPT-6.1-sol. Claude Code's documented integration
uses Claude models; current Codex route compatibility is uncertain; Droid adds no
clear advantage for this small Python experiment.

Use the stock pinned Qwen Code 0.26.0 application as a transport client, while the
existing experiment owns memory and notice timing. Do not adopt coding-agent
memory, tools, summaries, or auxiliary models. Separate temporary sessions by
firm and decision. Preserve existing configurations and evidence.

The genuine client has now been executed locally. The initial fetch-wrapper gap
has been corrected by intercepting the pinned client's bundled Undici transport.
Explicit tool-name exclusions also remove tools that wildcard exclusion left
enabled. The remaining blocker is prompt fidelity: the real client adds Git
context and a date reminder despite `skipStartupContext`. Inspection of the pinned
core shows that the first user query's date reminder has no configuration check.
This is evidence against using this release unchanged as a transparent transport,
not evidence against its ability to connect to AgentRouter. Do not silently strip
these additions at the network layer or patch the vendor bundle. Keep the adapter disabled until
pre-transmission request inspection, exact prompt fidelity, accounting and all
other approved acceptance tests work end to end. See the
[evidence report](../reviews/2026-10-22-qwen-client/REPORT.md).
