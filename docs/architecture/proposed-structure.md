# Recommended project structure

Status: **Accepted and implemented**, October 16, 2026. This is the V1 destination map; optional future files are added only when needed.

Use a research-owned Python repository with a small `auditors_paradox` package.
Keep CollusionLab in a separate pinned checkout. Keep original materials intact
under `docs/source-materials/` after the verified documentation migration.

```text
AI Collusion/
|-- README.md
|-- pyproject.toml                  # our installable package and console entry point
|-- uv.lock                         # exact versions after the compatibility pass
|-- .gitignore
|-- docs/
|   |-- README.md                   # navigation, current status, sources of authority
|   |-- source-materials/           # preserved inputs, not active specifications
|   |   |-- Chat_original.md
|   |   |-- research-package-v1/     # whole extracted package; internal paths unchanged
|   |   |-- archives/               # original ZIP
|   |   |-- diagram-drafts/         # the five loose PNGs, original names preserved
|   |   `-- source-manifest.json    # original path, current path, bytes, SHA-256
|   |-- research/
|   |   |-- question-and-scope.md
|   |   |-- protocol.md             # versioned experimental design and estimands
|   |   |-- metrics-and-inference.md
|   |   |-- literature.md           # claim, source, relevance, verification status
|   |   `-- limitations.md
|   |-- architecture/
|   |   |-- system-design.md
|   |   |-- proposed-structure.md
|   |   |-- decisions/             # ADRs: proposed, accepted, superseded
|   |   `-- diagrams/
|   |       |-- *.mmd              # canonical editable architecture diagrams
|   |       `-- rendered/          # generated SVG/PNG, never a competing source
|   |-- execution/
|   |   |-- implementation-plan.md
|   |   |-- runbook.md
|   |   |-- decision-log.md
|   |   `-- pre-sprint-contributions.md
|   `-- reviews/2026-10-16/        # this review, probes, evidence, original hashes
|-- external/
|   |-- collusion-lab.lock.json    # URL, commit, license, import path
|   `-- collusion-lab/             # ignored checkout restored by bootstrap script
|-- src/auditors_paradox/
|   |-- __init__.py
|   |-- cli.py                     # validate, run, aggregate, reproduce
|   |-- config.py                  # study config, separate from upstream config
|   |-- contracts.py               # typed records and the few actual boundaries
|   |-- runner.py                  # explicit round sequence owned by this study
|   |-- observations.py            # exactly what each agent may see
|   |-- auditing.py                # deterministic monitor; no reward mutation
|   |-- feedback/
|   |   |-- composer.py
|   |   |-- router.py
|   |   `-- schedules.py           # live triggers and independent fixed schedules
|   |-- adapters/
|   |   |-- collusionlab.py        # market, agents, memory, and backend conversion
|   |   `-- model_calls.py         # records visible inputs/outputs and usage
|   |-- evidence.py                # run directory, events, manifest, integrity
|   `-- evaluation/
|       |-- validate.py            # completed runs and treatment fidelity
|       |-- metrics.py
|       `-- inference.py           # run-level comparisons and uncertainty
|-- configs/
|   |-- controls/                  # scripted competitive and coordinated cases
|   |-- pilot/                     # editable exploratory configurations
|   `-- confirmatory/              # frozen study-specific configurations
|-- prompts/
|   |-- pricing/                   # versioned study-owned market instructions
|   `-- feedback/                  # substance, visibility headers, neutral bulletins
|-- tests/
|   |-- unit/
|   |-- integration/               # adapter, prompt isolation, full round sequence
|   `-- fixtures/                  # small synthetic records and scripted outputs
|-- scripts/
|   |-- bootstrap_upstream.ps1
|   `-- run_checks.ps1
|-- notebooks/                     # exploration; reusable computations live in src/
|-- runs/
|   |-- smoke/
|   |-- pilot/
|   `-- confirmatory/              # immutable completed run directories
|-- analysis/
|   |-- tables/                    # reproducible derived outputs
|   `-- figures/                   # actual result figures linked to source runs
`-- paper/                         # official template source, bibliography, exports
```

The runtime, adapter, feedback functions, evidence writer, evaluation, CLI, docs and checks are implemented. The BR/phase map and release evidence record the acceptance results.

## Why these boundaries

| Boundary | Practical benefit | Cost |
|---|---|---|
| Original materials vs active docs | Historical claims survive; current protocol has one location | A small amount of indexing and source linking |
| Study package vs upstream | Clear attribution and full control over experimental information | We own the round orchestration and adapter compatibility |
| Configs and prompts vs Python | Reviewable, hashable treatments | Schema validation must prevent inconsistent combinations |
| Smoke vs pilot vs confirmatory data | Engineering fixtures cannot become empirical findings accidentally | Manifests must identify the run class explicitly |
| Raw runs vs analysis vs paper | Derived outputs can always be regenerated | Figure generation must record its inputs and analysis revision |

Use one Python process per run and local files initially. A web backend,
microservices, message broker, vector database, agent orchestration framework,
and GPU training add no necessary capability to the V1 question.

## Upstream dependency ownership

Recommended V1 mechanism: an ignored clone at `external/collusion-lab`, restored
at the exact SHA in the tracked lock file by a bootstrap script. A root test
bootstrap adds that checkout's `src` directory to the import path explicitly;
our package itself remains installable. A future packaging shim or submodule is
an alternative, not an additional required layer.

Before a run, check the actual upstream SHA against the lock and reject a dirty
checkout unless the patch and its hash are recorded. Do not use a moving `main`
branch as the run's dependency identity. Do not `pip install -e` the upstream
checkout today: this revision has no packaging metadata.

The current clone is an ordinary local Git checkout. There is no GitHub fork,
submodule, root Git repository, or published project created by this review.

## Migration of the supplied files

| Current input | Proposed destination | Handling |
|---|---|---|
| `Chat_original.md` | `docs/source-materials/Chat_original.md` | Preserve bytes and original name |
| `Auditors_Paradox_Complete_Research_Package/` | `docs/source-materials/research-package-v1/` | Move as a unit so `figures/` and `mermaid/` links survive |
| Package ZIP | `docs/source-materials/archives/` | Preserve independently of the extracted package |
| Five `mermaid-diagram (...).png` files | `docs/source-materials/diagram-drafts/` | Preserve names; describe content in an index |
| New architecture diagrams | `docs/architecture/diagrams/` | New sources and deterministic exports, linked to the current specification |
| `08_fictional_results.png` | Remains inside preserved package | Index as illustrative; exclude from actual results and paper figures |

Before any move, use `source-inventory.json` from this review to verify every
input. Afterward, write a path mapping and recheck every SHA-256 and all local
Markdown links. Never replace a historical figure with a repaired image under
the same original path. Keep the package's original all-in-one document as the
historical handoff; extract current decisions into the active research and
architecture documents with links back to sections of that handoff.

## Authority after the migration

An accepted, versioned protocol and frozen configs govern a study. Accepted ADRs
govern the implementation. The original package explains the prior working
proposal. The conversation explains its evolution; it contains older options,
unresolved suggestions, and assistant claims, so it cannot override an accepted
current decision merely by mentioning another design.

Keep Ghost Cartel, internal activation monitoring, and LWM in a future-work note.
They do not need runtime modules in V1.
