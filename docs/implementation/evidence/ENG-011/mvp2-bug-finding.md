# Prompt 11 — MVP-2 public-repository bug finding

Implemented a separate, versioned bug-finding contract layer. Repository
snapshots pin revision/content/documentation digests, compatible license and
provenance, bounded commands, and network policy. Findings require a safe
location, reproducible steps, observed/expected behavior, impact, severity,
evidence, and bounded confidence. Finding-only and patch submissions are
mutually exclusive; patch mode additionally requires a patch digest and paths.

Release manifests use an explicit `mvp2-bugfinding-*` cohort and cannot be
marked official by the contract. Baseline, reference, alternative, and
negative-control references are required on every task revision. The catalog
is empty and development-only until a real license-compatible repository is
curated and independently admitted; no speculative findings or official
scores are generated.

Verification: MVP-2 contract tests and the fail-closed catalog audit pass.
