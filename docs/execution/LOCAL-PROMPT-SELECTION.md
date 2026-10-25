# Local prompt selection — installed

The update was initially staged while the experiment ran. After that experiment ended
and the prompt edit was approved, it was installed locally. Originals and the installation
receipt are preserved under `runs/maintenance/local-prompts-160c88e4b4/`.

## Open the updated local notebook

Reload `notebooks/00_research_control.ipynb` from disk and restart its local kernel.
Then run from setup. Its configuration selects Prompt 3. The installation command is
idempotent and does not launch experiments:

```powershell
.\.venv\Scripts\python.exe packaging\apply_local_prompt_update.py --check
.\.venv\Scripts\python.exe packaging\apply_local_prompt_update.py
```

On first installation the installer refuses active workers, modified staged files, untested updates and local
files changed since staging. It saves originals and an installation receipt under
`runs/maintenance/local-prompts-<id>/` and restores them if copying fails. It does not
launch any experiment. If local settings change before installation, rebuild/review the
pending update rather than forcing it over those edits.

Restarting the **local** kernel loads the updated configuration validator and prompt
selection module. Existing in-memory settings can otherwise retain the old explicit profile.

## One selector, no local cycles

In the configuration cell:

```python
PROMPT_NUMBER = 3  # 1, 2 or 3
```

This selects one qualitative system/action prompt bundle for the whole selected study.
It never loops over prompts or automatically runs Warning, Event and Continuing.
`ALGORITHM_PRESET` independently selects the algorithm, and the existing model, market,
round count, memory size, sampling, budget, execution switches and arm settings remain.

Set `PROMPT_NUMBER = None` to use historical profile/custom-template controls instead.
Do not combine a numbered prompt with custom paths, custom variables or a profile override.
The numbered defaults contain no equations. Native local JSON mode keeps the JSON price
contract; text parsers keep the PRICE contract, consistently across the three choices.

The updated notebook uses a new study name/protocol for the changed instructions. Each
new selection requires a fresh plan. Do not resume the old sweep with changed prompts.
The original notebook's execution switches remain visible and preserved; installation
does not grant additional paid-inference authorization.

## Memory and traceability

V1 retains its existing outcome fields and prose record layout; V2 retains its configured
history layout. In numbered and custom price-only V1 prompt runs, price outputs are no longer repeated
as `you reasoned: PRICE: ...`. The manifest records the new
`price_only_observations_v1` memory policy, and replay validation supports it explicitly.
Old policies and historical evidence are unchanged. Full model replies remain archived.

The resolved config contains the selected template paths and output instruction. Run
manifests, runs.csv, rounds.csv and requests.csv record prompt number/version. Local
outputs have no cycle assignment. Exact prompt previews make no inference requests.

The implementation was exercised in an owned copy using all three selectors:
12 scripted trajectories, 48 committed market rounds and 144 requests. These checks are
software verification, not new live-model research or proof of strategic competence.
See `dist/local-prompts/verification.json` for the saved evidence path.

## Use your own edited system file

New plans may use edited prompts without a separate hash-approval step. To use the
qualitative text currently in the file named `explicit-economics.txt`, edit the existing
assignments in the main configuration cell:

```python
PROMPT_NUMBER = None
PROMPT_PROFILE_OVERRIDE = "price_only"
SYSTEM_PROMPT_PATH = "prompts/pricing/explicit-economics.txt"
ACTION_PROMPT_PATH = "prompts/pricing/json-price-action-v1.txt"
PROMPT_VARIABLES = {}
```

That example matches the current local `json_price_v1` parser. Text parsers should use
a matching PRICE-only action template instead. A custom system override is now loaded
directly; the loader no longer attempts to extract equations from an unused default first.
The exact edited text is retained. The new numbered default does not modify your edited file.

`MARKET_KNOWLEDGE="explicit"` with no override still means the equation-based profile.
Its missing equation error is a content mismatch, not a demand for approval. Use the
numbered/qualitative/custom selection for qualitative text. Prompts are frozen only when
creating a plan, so editing before a fresh plan is permitted. Existing runs keep their
original source and cannot silently continue under different instructions.

## Completed experiment recovered separately

The historical worker reported analysis failure after all four 50-round trajectories
completed. Its recovered files are under:

`analysis/nb-v2-event-gpt56-2989aa66bc-source-reviewed-20261025/`

Open `report.md`, `runs.csv`, `rounds.csv` or `requests.csv`. No experiments were repeated.
The source review and raw-evidence preservation records explain the recovery; do not
rerun the launch cell merely to regenerate these existing results.
