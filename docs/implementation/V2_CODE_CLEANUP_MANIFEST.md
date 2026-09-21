# v2.0 code cleanup manifest

Date: 2026-09-21  
Status: dependency trace complete; no destructive code removal performed yet

This manifest is the gate for cleaning the large v1 codebase. A path is not
removed merely because it predates v2.0: shared imports, migrations, evidence,
and tests are part of the redesign's reproducibility boundary.

## Retain as active v2 foundation

| Path | Reason |
|---|---|
| `packages/aieb-core` | Versioned task, campaign, cohort, protocol, identity, and planning contracts. |
| `packages/aieb-runner/src/aieb_runner/backends/harbor` | The pinned Harbor adapter and sandbox/network enforcement boundary. |
| `packages/aieb-runner/src/aieb_runner/model_loop.py` and `model_providers` | Fixed model-track loop and provider seam; imported by Harbor execution. |
| `packages/aieb-runner/src/aieb_runner/artifacts.py` | Candidate collection, integrity, replay, and evidence boundary. |
| `packages/aieb-analysis` | Frozen-plan aggregation, validity, cost, coverage, and uncertainty calculations. |
| `services/api/src/aieb_api/worker` | Hosted leasing, reconciliation, artifact persistence, credentials, and runner bridge. |
| `services/api/src/aieb_api/models.py`, migrations, auth, aggregation, publication/evidence modules | Persistent contracts and provenance; deleting them would invalidate historical evidence. |
| `apps/web` public pages and generated API types | Read-only product surface required by v2. |
| `tests/maintainer` and `tests/fixtures` | Trusted evaluator and isolation evidence; retain privately and never bundle publicly. |

## Retain but refactor behind explicit v2/dev boundaries

| Path | Action |
|---|---|
| `packages/aieb-runner/src/aieb_runner/lifecycle.py::LocalAttemptRunner` | Keep temporarily: `services/api/worker/runner_bridge.py` imports it for BUILD/VERIFY and candidate artifact handling. Later replace the local engineering path with a Harbor-backed bridge; do not delete in isolation. |
| `packages/aieb-cli` | Keep contracts and diagnostics; mark local campaign execution as development-only while v2 commands are introduced. |
| `suites/dev` and admission scripts | Keep as development/admission fixtures, never official release inputs; add explicit v2 catalog labels. |
| `services/api` operator routes and `/admin/*` pages | Keep private/authenticated; they are not public controls. |
| `examples/` | Keep fixtures needed by tests, relabel old campaigns as historical/proposed. |

## Historical documentation boundary

The pinned copies under `docs/specs/` are retained because `scripts/dev.py` and
`tests/test_dev_bootstrap.py` verify their digests as repository source inputs.
They are historical references, not active v2 specifications, but removing
them would break the integrity gate. The ignored root-level duplicates were
removed from the active root and are represented by the archive index:

- `AI-Engineer-Bench-Architecture-v0.1.md`
- `AI-Engineer-Bench-Implementation-Spec-v1.0.md`
- `AI-Engineer-Bench-Codex-Prompt-Pack.md`
- historical `docs/implementation/evidence/` and old ENG ledger entries

The tracked `docs/specs/` copies remain the canonical historical source;
`docs/legacy-v1/README.md` records this distinction.

## Do not touch in cleanup

- untracked `suites/real/`, RAG-05 scripts/fixtures, and related tests;
- `tests/maintainer/rag05/` and private evaluator material;
- database migrations, Docker volumes, or published evidence;
- `.aieb`, `.venv`, caches, `.commandcode`, or permission-protected pytest
  directories without a separately verified disposable-artifact operation.

## Required sequence

1. Rewrite links and labels for historical v1 documents.
2. Move historical documents into `docs/legacy-v1/` with a manifest and
   redirect/index page.
3. Add v2 catalog labels to development fixtures and scripts.
4. Introduce a Harbor-backed v2 execution entrypoint.
5. Only after import/test tracing and replacement tests pass, retire the local
   `LocalAttemptRunner` campaign path.
6. Run the full regression suite and inspect `git diff --check`.

## Current gate

The trace proves that a big-bang deletion of the “non-Harbor” code would also
delete shared worker, artifact, API, and test functionality. The safe cleanup
can begin with document archiving and explicit dev labels; code retirement is
blocked until the Harbor-backed replacement exists.
