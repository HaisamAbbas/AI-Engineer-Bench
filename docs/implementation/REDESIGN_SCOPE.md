# AI Engineer Bench v2.0 scope boundary

Date: 2026-09-21  
Applies to: Prompt 02 (Refactor scope safely)

The file-level dependency and cleanup decisions are recorded in
`V2_CODE_CLEANUP_MANIFEST.md`.

## Decision

The v2.0 product boundary is enforced by separation and authorization, not by
deleting the existing hosted implementation. The public surface is the
read-only results website and public API views. Campaign, worker, publication,
review, correction, and kill-switch operations remain private,
authenticated operator/reviewer/administrator workflows.

## Trace results

- No dedicated Hermes-like package, private-application evaluator, arbitrary
  customer-repository workflow, public task-submission flow, or chat-based
  benchmark controller was found in the repository.
- `scripts/seed_local_interactive.py` and `scripts/start_local_api.py` are
  local disposable fixture/demo tools. They do not expand the public product
  boundary; they remain user material and are retained as development-only
  tooling.
- `apps/web` contains `/admin/*` routes, but those routes are explicitly
  labeled operator-only, hide campaign navigation for users without an
  operator/reviewer/administrator role, and rely on API authorization for
  writes. They are not public benchmark controls and are retained.
- FastAPI mutation routers use role dependencies and idempotency/fencing
  controls. Public results/registry/trial views remain read-only. No route was
  removed or reclassified without evidence.

## Preservation and isolation rules

The following remain outside official v2.0 admission until separately reviewed:

- the untracked `suites/real/` RAG-05 work and its fixtures/scripts;
- `suites/dev` development tasks and their maintainer evaluators;
- historical v1 implementation, migrations, evidence, and release fixtures;
- local state (`.aieb`, caches, virtual environment, and tooling artifacts).

Private holdouts, official campaign execution, provider calls, publication,
and deployment remain authorization-gated. This prompt made no such changes.

## Prompt 02 completion report

- Implemented: this boundary record; corrected the README wording so the
  existing admin/publication UI is not falsely described as unimplemented.
- Preserved: all pre-existing tracked and untracked user work; no source,
  task, fixture, migration, database, Docker volume, or history was deleted.
- Verification: API route/auth inspection, public/admin route inspection, and
  `git diff --check`.
- Pending: v2 task admission, holdout curation, model-track execution, and
  official release gates remain for later prompts.
- Next: Prompt 03 — Define the v2 task package and admission contracts.
