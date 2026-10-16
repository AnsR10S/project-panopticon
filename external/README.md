# Pinned research dependency

`collusion-lab/` is an ignored, separately versioned checkout restored from the
vendored Git bundle snapshot at `collusion-lab.bundle`. The tracked lock file
(`collusion-lab.lock.json`) records its repository URL, exact pinned SHA,
bundle path, bundle SHA-256 checksum, Python import path, and MIT license.

Vendoring the dependency as an offline Git bundle guarantees that fresh clones
can bootstrap without relying on the upstream GitHub repository remaining
available, while preventing accidental commits of nested Git repositories, local
modifications, or bytecode caches. Bootstrap verifies the checkout and refuses
to reset a dirty or differently pinned one.

Our production runner reuses public market, agent, memory and scripted-client
components. It does not call the upstream experiment/oversight runner. Historical
upstream test failures are recorded in `docs/reviews/2026-10-16`; we have not
changed upstream source or represented its original suite as entirely passing.
