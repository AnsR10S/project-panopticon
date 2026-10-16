# Raw run attempts

`smoke/`, `pilot/`, and `confirmatory/` are separate run classes. Every run has
an exclusive identity, source/config/prompt snapshot, hash-chained events, and a
terminal status. Preserve complete, failed and interrupted attempts. Sweep
manifests retain randomized assignments and per-run dispositions.

Raw generated runs are ignored by default in Git to avoid accidentally
publishing large/private traces. Source code, source-material manifests, configs,
and release summaries remain reviewable. A future publication needs an explicit
data review and selection; no artifacts have been uploaded by this build.
