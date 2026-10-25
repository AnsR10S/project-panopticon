# Pre-Release Audit Checklist (Draft)

## Pipeline Verification
- [x] Matrix sweeps generate complete SQLite records
- [x] Calvano benchmark prices align with analytical values
- [x] Opaque and transparent sanction prompt tokens resolve cleanly
- [x] Action parser handles unbracketed prices and reasoning text

## Provider Diagnostics
- [x] Qwen-code client handshake verified
- [x] AgentRouter route fallback operational
- [x] Retry budget stops infinite loops on 429

## Release Preparation
- [ ] Smoke test notebooks run without active network credentials
- [ ] Export run inspection tables to CSV and JSON formats
- [ ] Remove temporary scaffolding and test transport stubs
