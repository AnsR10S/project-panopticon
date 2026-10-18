# V1 build requirements (BRs)

BR means **build requirement** here. The user authorized implementing the
recommended structure and routine design decisions on October 16, 2026. BRs
describe the software release; live empirical research remains separately gated
by model choice, a budgeted pilot, and a frozen statistical protocol.

| BR | Requirement and acceptance condition | Implementation / verification |
|---|---|---|
| BR-001 | Preserve sources; strict UTF-8/BOM-safe config; portable identifiers; exact clean upstream pin | Source manifest, `config.py`, adapter preflight, doctor; config/boundary tests |
| BR-002 | Three-agent simultaneous pricing; valid competitive and global joint reference on the actual integer grid | Market adapter; unilateral and exhaustive symmetric-profile checks; economic boundary tests |
| BR-003 | Deterministic consecutive-round auditing; no reward modification or misleading sanctions | `auditing.py`, study runner; audit on/off reward equality and control tests |
| BR-004 | Silent, private, public conditions with fixed substance and explicit visibility | Feedback composer/router; exact prompt and body-matching tests |
| BR-005 | Independent placebo/fixed-signal schedule, truthful neutral text | Configured schedule and hash; audit-disabled placebo tests |
| BR-006 | All observations frozen before current decisions; no rival-profit summaries; t-to-t+1 delivery only | Observation assembly, actual request snapshots, recipient and round-11 privacy tests |
| BR-007 | Exclusive outputs, hash chain, source/prompt/config snapshot, checksums, semantic fidelity validation | Evidence writer and verifier; overwrite refusal and rehashed-tamper tests |
| BR-008 | Record parsing, fallback, failed requests, interruptions and partial attempts | Recorded clients and terminal manifests; exhaustion, budget, fallback, interrupt tests |
| BR-009 | Optional provider transports, explicit spend/call guards, no hidden SDK retries or stored credentials | SDK adapters, configured-rate reservation; fake-SDK and budget tests; no live-model claim |
| BR-010 | Randomized replicate blocks, every assigned arm retained, complete/partial/failed sweep statuses | Batch runner and assignment manifest; complete/partial/failed/interrupted tests |
| BR-011 | Independent run-level metric, paired public/private inference, explicit missingness and synthetic labels | Evaluation modules; known-value and seeded inference tests; all-attempts table |
| BR-012 | Installable CLI, locked setup, one-command checks and demo, offline inspection | `pyproject.toml`, `uv.lock`, PowerShell scripts; public CLI workflow tests and installed smoke |
| BR-013 | Coherent docs, accepted ADRs, phases, separate paper workspace, readable editable diagrams | Docs index, link/source verification, generated architecture figures and visual review |

All BRs must have release evidence; passing a mock-provider test does not satisfy
live compatibility, strategic emergence, power, or scientific confirmation.

## Explicitly outside the software acceptance claim

- Selecting the final provider/model and purchasing API capacity.
- Confirming that LLM agents coordinate, or that public warnings increase it.
- Establishing novelty, detector truth labels, adequate power, or broad welfare impact.
- Completing/submitting a competition paper or publishing a repository.
- GPU training, internal probes, multi-market studies, agent replacement, or LWM.

These are recorded in the research backlog, not silently marked completed by
the engineering release. The baseline study supports negative and null findings.
