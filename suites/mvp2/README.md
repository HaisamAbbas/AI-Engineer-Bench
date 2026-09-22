# MVP-2 public-repository bug-finding track

This directory is a separate development track. It evaluates findings in a
fixed, license-compatible repository snapshot; it is never blended with Track
A engineering scores. Each task must pin repository/documentation digests,
allowed commands, hidden labels, baseline/reference/alternative controls, and a
negative no-bug control.

The two modes are distinct:

- `finding-only`: score true positives, false positives, duplicates, severity,
  reproduction quality, and confidence. Speculative lists receive no credit.
- `patch`: applies the same finding schema but additionally scores patch
  correctness and regression safety.

The development catalog currently references one digest-pinned Apache-2.0
repository task under `suites/real/rag.corpus-index-drift/`. It has public
controls and an example, but its hidden label set and evaluator remain private
placeholders until provisioned outside this repository. Run
`scripts/validate_bugfinding_track.py` before any release operation.

No task in this track is official or publication-eligible without independent
review, hidden-label protection, admission evidence, and explicit campaign
authorization.
