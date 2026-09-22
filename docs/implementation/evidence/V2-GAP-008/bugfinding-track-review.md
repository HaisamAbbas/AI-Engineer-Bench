# V2-GAP-008 MVP-2 bug-finding audit

The MVP-2 contracts and evaluator-side scoring are now implemented, but this
track is not admitted or official. Run the catalog audit with:

```text
PYTHONPATH=packages/aieb-core/src python scripts/validate_bugfinding_track.py \
  --output docs/implementation/evidence/V2-GAP-008/bugfinding-track-audit.json
```

The development catalog contains one fixed, digest-pinned Apache-2.0
repository task (`mvp2.rag.corpus-index-drift`) with baseline, reference,
alternative, a public example, public self-check, adversarial controls, and a
separate no-bug control. The task remains blocked because the private label
digest is a placeholder and no Harbor/evaluator end-to-end evidence is
recorded.

`aieb_core.bugfinding_v2` now provides:

- strict repository/task/release contracts with a required separate track mode;
- private, digest-bound hidden-label sets;
- finding-only versus patch submission separation;
- component scoring for true positives, false positives, duplicates, severity,
  reproduction quality, precision and recall; unmatched/speculative findings
  receive no credit.

The audit reports `tasks_admitted: 0`, `hidden_label_workflow_complete: false`,
`execution_evidence_complete: false`, and `official_release_eligible: false`.
Private label storage/access audits, trusted evaluator execution, independent
review, and an authorized cohort are still required.

## Review follow-up: evaluator and label binding

The scoring boundary now requires the frozen `BugTaskRevision`, its exact
`hidden_label_digest`, and a matching `BugHiddenLabelSet` task/revision and
repository. The isolated evaluator must return a digest-bound
`BugEvaluationResult` whose evaluator identity, finding-evidence digests, and
reproduction-evidence digests match the private labels. Reproduction quality
and patch correctness are no longer accepted as scoring-function arguments;
patch bytes must also hash to the submitted `patch_digest`, and any hidden
expected patch digest is checked when an evaluator marks a patch correct.
These controls are contract-level defenses, not evidence that a private label
corpus or real Harbor evaluator has run. The catalog therefore remains
development-only and `PARTIAL`.
