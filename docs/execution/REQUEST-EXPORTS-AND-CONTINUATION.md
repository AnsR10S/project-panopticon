# Full request exports and safe continuation

## CSV contents

Analysis now writes `rounds.csv` for all available saved rounds, including excluded/partial
trajectories and the failed current round. Partial data does not become a completed endpoint.

| Column / file | Contents |
|---|---|
| `committed` | All three decisions were applied and the round was durably saved |
| `included_in_inference`, `exclusion` | Whether the trajectory contributes to comparisons, and why |
| `requests_json` | Every recorded request attempt in this round: firm, call ID, ordered messages, reply/thinking channels, usage, errors and raw request/terminal evidence |
| `observations_json` | Authorized firm memory and inbox records |
| `decisions_json` | Accepted prices, parsing identities and supporting call IDs |
| `market_outcome_json`, `audits_json`, `deliveries_json` | Full recorded economics, screening and message evidence |
| `events_json`, `config_json` | The round's original event records and frozen configuration |
| `requests.csv` | One row per request attempt, including corrections, transport retries, local output retries and failures |

`requests.csv` has separate `system_prompt`, `user_prompt`, `messages_json`, `response`,
`thinking`, usage, status and provenance fields. `messages_json` contains the full ordered
conversation, including previous failed-format replies and correction instructions. Nothing
is shortened for display. Unknown usage stays blank, never invented as zero. Request-start
evidence alone does not prove transmission; wire evidence is retained when available.

CSV stores structured fields as JSON text inside quoted cells. Use a CSV reader, not line
splitting; prompts can contain commas and newlines. For example:

```python
import csv, json
csv.field_size_limit(64 * 1024 * 1024)
with open(RESULTS / "rounds.csv", encoding="utf8", newline="") as file:
    rows = list(csv.DictReader(file))
attempts = json.loads(rows[0]["requests_json"])
messages = json.loads(attempts[0]["messages_json"])
print(messages[0]["content"])
```

The notebook shows compact economic columns; the full CSV remains downloadable/readable.
Analysis creates these files after a batch finishes or fails. Live evidence continues to be
written to `events.jsonl` and shown by the existing prompt viewer. Exports are researcher-only;
they are never fed back to firms. Credential fields and recognizable API-token strings are
redacted. Original raw evidence is not rewritten. Review full exports before sharing them.

## Continuing a failure

`CONTROL['resume_sweep']` now supports a known native Ollama output-limit failure as well as
the existing eligible provider rate-limit failures. For local continuation, the failed response
must report `done_reason='length'` and known token counts. Unknown in-flight requests,
unfinished parsing conversations and partial market transitions remain blocked.

Keep the exact frozen study, assignment, seeds, source, protocol and generation settings:

```python
RESUME_FROM = "runs/pilot/YOUR_STUDY/sweeps/YOUR_FAILED_SWEEP/manifest.json"
parent = control.read_json(ROOT / RESUME_FROM)
STUDY = parent["base_config"]
CONTROL.update(resume_sweep=RESUME_FROM,
               replicates=parent["replicates"],
               assignment_seed=parent["assignment_seed"])
```

Run preview and freeze a new plan before launching. Continuation writes a new attempt,
preserves the original sealed evidence, reconstructs completed rounds without model calls,
reuses accepted prices in an unfinished round and requests only missing decisions. It does
not turn a continuation into another independent replicate. Calls, usage and elapsed-time
allowances retain the parent accounting. A request can still fail again under its frozen cap.

**Changing source or configuration remains a blocker.** Do not delete hashes, edit manifests,
or overwrite source archives to get around it. A new parser, memory policy or output allowance
would change the trajectory's policy. Use a fresh study, or a separately designed and explicitly
classified exploratory branch; the ordinary resume option does not silently create such a branch.

The 24 October run ending `4491013327` used `auto`/V1, no local retries, and committed five
rounds before round 6 exhausted 4096 tokens. It predates the parser/export/continuation repairs
and therefore cannot resume under this updated source via the ordinary option. Its five rounds
and failed request can be exported offline without rerunning anything.

Restart the notebook kernel and reload the repaired notebook before a fresh run. The prepared
study uses `final_answer_v2`, one local truncation retry, and a 6144-token retry ceiling; it
retains the existing 50-round/four-arm market design. Execution is still disabled by default.
