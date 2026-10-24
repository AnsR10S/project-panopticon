# Prompt and model controls

For DeepSeek R1 parsing, editable template files, notebook prompt variables and exact placeholders, see [DeepSeek and prompt editing](DEEPSEEK-AND-PROMPT-EDITING.md).

In the new notebook configuration cell:

```python
AGENTROUTER_MODEL = "gpt-5.6-sol"  # Also gpt-6.1-sol or gpt-6-astra.
MARKET_KNOWLEDGE = "explicit"     # Or qualitative.
PROMPT_PROFILE_OVERRIDE = None    # Set explicit_economics to test the unchanged original option.
ENABLE_MANUAL_AUDITOR = True
HISTORY_FORMAT = "table"          # Or prose / compact.
ALGORITHM_PRESET = "v2_warning"
```

All three firms use the selected model. The model selector updates visible input/output rate assumptions from the editable model dictionary; those assumptions are not verified gateway quotes. Production credentials remain the existing per-firm/shared variables. A key restricted to another model must be updated in AgentRouter before launch. The latency test keys are not used automatically for research.

| Selection | Behavior |
|---|---|
| `explicit` | `explicit_economics_v2`: exact existing market equations plus shared new instructions |
| `qualitative` | `qualitative_v2`: the same instructions without the equation block |
| `economics_private_note` profile | Existing separately selected profile permits a bounded own private strategy note |
| `historical_v1` preset | Keeps the V1 path; new V2 history/manual/prompt controls are inactive |

The new price-only profiles ask for exactly `PRICE: <integer>`. They remove the heuristic-permission sentence and the fake reasoning duplication. The tolerant existing parser and format-deviation evidence remain: a valid parsed price is not proof that every formatting instruction was obeyed. New profiles are separately identified and hashed; historical files are preserved.

An unavailable Manual Auditor panel now gives the reason: V1 plan, disabled manual option, no active run, or a worker boundary not yet published. Changing a notebook dictionary does not alter a frozen running plan. To use manual controls, launch a new V2 exploratory plan, press Prepare while decisions are in flight, wait for pause, preview, then confirm. Public receipt automatically selects all three firms. No custom notice is sent by editing the text box.

The update was tested in an isolated checkout, then applied after confirming the earlier workers were no longer alive and their writer leases were abandoned. No experiment was stopped to install it. Latest local Ollama settings were merged, not replaced. The notebook defaults to preview-only for this new design. Restart the kernel, validate, preview and freeze a new plan. Do not reuse an old plan or attempt to resume old source with the new prompts.

Before the first new model/profile pilot, use the model/profile-specific live validation command against a three-round diagnostic config. It is operational qualification, not evidence of competence or collusion. `--reuse-probe` only reuses compatible prior evidence; it does not bypass exact model/profile checks. No trial should require a researcher intervention just to demonstrate ordinary execution.

For AgentRouter, after checking the three production keys permit GPT-5.6-sol:

```powershell
$py = '.\.venv\Scripts\python.exe'
& $py scripts/validate_qwen_live.py configs/v2/gpt56-prompt-smoke.yaml --allow-paid --new-request-limit 32 --new-cost-limit 10 --lifetime-cost-limit 1000 --requests-per-minute 3 --tokens-per-minute 20000
```

This is a paid operational check, not an automatic part of this update. Its request allowance is additional to historical usage, still subject to the cumulative 4,000-attempt guard. The old absolute 64-call validation ceiling no longer prevents a fresh model check after earlier experiments. Nothing clears the existing ledger. Optional spending limits follow the configuration's spending switch; the cumulative research allowance remains active.

Read the [exploratory protocol](../research/prompt-alternatives-v2-2026-10-24.md). Model, equation disclosure, memory format and manual intervention each affect interpretation; change one when diagnosing a mechanism and freeze all selections for a policy batch.
