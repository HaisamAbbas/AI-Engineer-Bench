# EXT-02 and TOOL-01 development admission

Date: 2026-09-14. Scope: local deterministic task admission, not model-quality or official benchmark evidence.

| Task | Baseline | Reference | Alternative | Shortcut controls | Fresh resets |
| --- | --- | --- | --- | --- | --- |
| EXT-02 batch/document alignment | FAIL | PASS | PASS | hardcoded and valid-document-drop controls FAIL | 10/10 reference PASS |
| TOOL-01 false completion | FAIL | PASS | PASS | always-complete and always-fail controls FAIL | 10/10 reference PASS |

EXT-02 runs a real HTTP extraction application with shuffled output, partial failure, and last-occurrence-wins repeated IDs. The evaluator checks response IDs and valid-item preservation rather than result order.

TOOL-01 runs a real HTTP workflow application against a separate evaluator-owned operation service. The operation-service ledger, not candidate logs, establishes whether failed and successful operations were reported honestly.

Both tasks have public requirements/API contracts, visible development data, runnable baselines, distinct repairs, provenance, and maintainer-only held-out evaluators. Reference and counterexample code are outside the contestant repository tree. Both reference repairs also pass through `aieb run` fresh artifact replay. Independent human review is pending; no real agent, provider, or benchmark score was run.
