# Project notebooks

**V2:** read the [current implementation report](../docs/design/2026-10-23-v2/implementation-report.md) and [operating guide](../docs/design/2026-10-23-v2/walkthrough.md). `ALGORITHM_PRESET` switches validated historical/new strategies in the configuration cell. Saved settings and execution switches are preserved; review them before Run All.

Start with the [current project overview](../docs/PROJECT-STATUS-2026-10-23.md) and
the [research-control walkthrough](../docs/execution/RESEARCH-CONTROL-NOTEBOOK.md).

| File | Purpose | Execution boundary |
|---|---|---|
| [00_research_control.ipynb](00_research_control.ipynb) | Configure, preview, freeze, launch, monitor, stop and analyze the study | Can make local or paid provider requests when execution switches permit; review saved settings before Run All |
| [01_inspect_run_table.ipynb](01_inspect_run_table.ipynb) | Inspect an already generated run table | Offline inspection; does not request model inference |
| [qwen3vl_aggressive_kaggle_t4x2.ipynb](qwen3vl_aggressive_kaggle_t4x2.ipynb) | Auxiliary GPU/model-deployment work | Separate from pricing-study results; a notebook file alone does not establish successful multi-GPU execution |
| [kaggle_cuda_driver_fix.py](kaggle_cuda_driver_fix.py) | Auxiliary Kaggle CUDA setup helper | Deployment support, not a completed research experiment |

Reusable outcome and inference code lives in `src/auditors_paradox/evaluation`.
The control notebook uses the project runner and preserves raw evidence; do not
maintain an independent implementation of the primary statistical test here.
