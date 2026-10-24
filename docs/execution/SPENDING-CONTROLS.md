# Spending controls

Owner change, October 24, 2026: optional application spending caps are off by default in the research notebook. The authorized cumulative research allowance is $1,000, replacing the former $50 limit. No experiment starts merely because this setting changes.

In the notebook configuration cell:

```python
ENFORCE_SPENDING_CAPS = False
STUDY["budget"]["enforce_spending_caps"] = ENFORCE_SPENDING_CAPS
CONTROL.setdefault("lifetime_cost_limit_usd", 1000)
```

| Control | False | True |
|---|---|---|
| Per-run `budget.max_cost_usd` | Ignored | Enforced |
| New-workflow `CONTROL.new_cost_limit_usd` | Ignored | Enforced |
| Small alternative-model probe cap | Ignored | Enforced |
| Cumulative research allowance | $1,000 total remains enforced | Configured total, at most $1,000 |
| Token/cost accounting and unresolved-charge holds | Recorded | Recorded |
| Call count, RPM/TPM, timeout, Stop and isolation controls | Active | Active |

Preview and the live run panel show whether optional spending caps are on or off. Inactive caps appear as `null` in preview; configured amounts remain available for inspection. A leftover $11 per-run or $44 workflow value does not restrict a notebook run while this switch is False. Token rates remain required to estimate usage costs. Estimates are not gateway invoices.

For a standalone config, set `budget.enforce_spending_caps: false` explicitly. Historical configs without that field keep their original enforcement, so importing an old file does not silently change its controls. The notebook's configuration cell explicitly supplies False by default. Set the notebook variable to True to restore optional caps.

The existing accounting ledger is retained. Known charges and conservative holds count toward the cumulative research allowance; nothing is reset or discarded. The gateway's actual billing/quota controls remain independent. The shared worker enforces the $1,000 allowance even when optional limits are disabled.

After this source upgrade, restart the kernel and rerun **Configuration, Preview, Freeze, Launch**. Use a fresh plan; source changes prevent continuation of old experiments. The saved notebook's obsolete automatic continuation assignment was set to None, matching the previously diagnosed fresh-run workflow. Market, model, arms, rounds, prompts, thresholds, output tokens and statistical settings were not changed by this update. Execution switches are preserved: review them before Run All.

This implementation change did not initiate paid inference. [Verification and offline preview](../../runs/diagnostics/spending-controls-20261024/verification.json).

Verification passed **362 tests with 92.21% statement coverage**, strict package mypy, Ruff, serial Black checks and documentation links. The actual saved notebook's offline preview has no paid-launch blockers. Research/model/analysis settings and execution switches match the pre-change notebook; prior accounting is unchanged. The original notebook is backed up under the verification directory. [Full regression](../../runs/diagnostics/spending-controls-20261024/regression-final.txt), [source checks](../../runs/diagnostics/spending-controls-20261024/quality.json).
