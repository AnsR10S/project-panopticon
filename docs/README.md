# Auditor's Paradox documentation

**Latest V2 implementation (October 24):** start with the [evidence-backed implementation report](design/2026-10-23-v2/implementation-report.md), [V2 walkthrough](design/2026-10-23-v2/walkthrough.md) and [Strategy pattern/configuration guide](design/2026-10-23-v2/strategies.md). The October 23 status below is historical. No paid inference was performed during V2 implementation.

**Latest full project overview (October 23):** read the [development and current-state report](PROJECT-STATUS-2026-10-23.md) first. It covers the complete development history, architecture, implemented controls, experiment results, verification, current pilot snapshot and remaining research gaps, with a concepts table and evidence links.

**Control the study from a notebook:** open the [research control notebook](../notebooks/00_research_control.ipynb) and its [walkthrough](execution/RESEARCH-CONTROL-NOTEBOOK.md). It exposes every current study/workflow/analysis setting, explains what each change tests, freezes plans and waits at a live launch panel with percentage, replies and Stop. Run All advances to results after execution/analysis finish. Template execution is disabled; interface upgrades preserve your saved controls.

**AgentRouter readiness:** GPT-6.1-sol completed nine actual market decisions, and the updated request guard passed all six live connection cases within the fresh $0.15 allowance. Two rate-limit failures recovered through paced retries. Read the [completed live validation](reviews/2026-10-23-qwen-live/POST-UPDATE-VALIDATION.md), [prompt/pacing investigation](reviews/2026-10-23-qwen-live/REQUEST-OPTIMIZATION.md) and [client walkthrough](execution/QWEN-CLIENT-SETUP.md). Scientific diagnostics and treatment exposure remain separate gates.

**Current evidence:** read the [October 22 research-engineering report](reviews/2026-10-22-study-repair/REPORT.md), then [confirmation gates](research/confirmatory-candidate-2026-10-22.md) and [outcomes/inference](research/metrics-and-inference.md). These distinguish executed local evidence from the historical proposal.

**First actual run:** follow the [guided local and AgentRouter walkthrough](execution/FIRST-RUN-WALKTHROUGH.md). It explains arms, setup, commands, costs, checkpoints, and what the research can establish.

**Operating the current version:** [full local-first manual](execution/OPERATING-MANUAL.md), including AgentRouter preparation, pricing estimates, stop controls and error recovery.

**Start with the [reading order](READING-ORDER.md) and [plain-language introduction](research/START-HERE.md).** Your latest decisions are in the [owner revision record](research/design-revisions/2026-10-16-owner-decisions.md).

V1 implementation date: October 16, 2026. Architecture accepted for implementation under the user's autonomous build instruction. All original materials are preserved under `source-materials/`.

- [Project critique and repository assessment](reviews/2026-10-16/project-review.md)
- [Team critique comparison and proposed research revisions](reviews/2026-10-16-team-critique/comparison-and-recommendations.md)
- [Recommended folder structure and migration](architecture/proposed-structure.md)
- [V1 system architecture and contracts](architecture/system-design.md)
- [ADR-001: component reuse through an adapter](architecture/decisions/0001-collusionlab-integration.md)
- [ADR-002: synchronous experiment pipeline and evidence](architecture/decisions/0002-experiment-pipeline.md)
- Historical pre-migration diagnostic source (local archive: `docs/reviews/2026-10-16/evidence/preflight.py`)
- Runtime verification summary (local archive: `docs/reviews/2026-10-16/evidence/runtime-summary.json`)

Current proposal source: working research architecture (local archive: `docs/source-materials/research-package-v1/Auditors_Paradox_Research_Architecture.md`).
Historical context: original conversation (local archive: `docs/source-materials/Chat_original.md`).

The review evidence directory contains synthetic engineering checks. It must not
be treated as pilot or confirmatory LLM research data.

- [Build requirements](execution/build-requirements.md)
- [Phases and delivery units](execution/implementation-plan.md)
- [Runbook](execution/runbook.md)
- [V1 release evidence](releases/v1/README.md)
