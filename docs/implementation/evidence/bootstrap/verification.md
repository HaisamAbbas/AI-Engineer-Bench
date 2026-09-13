# Bootstrap verification

Date: 2026-09-13  
Environment: Windows PowerShell, local workspace only

## Verified observations

- Python 3.12.10 executed the development checks.
- uv 0.11.27 resolved the dependency-free workspace and generated `uv.lock`.
- Git 2.55.0.windows.3 initialized the local repository; no remote is configured.
- Docker 29.7.2 and Docker Compose 5.5.1 were observed, but no compatibility acceptance is inferred.
- Node 25.2.1 was observed; pnpm was absent. Both are deferred because no web package exists.
- Harbor remains unselected and untested until ENG-001.

## Passing commands

```powershell
$env:UV_CACHE_DIR = 'D:\AI-Engineer-Bench\.cache\uv'
uv lock
./dev.ps1 doctor
./dev.ps1 check
uv lock --check
python -m compileall -q scripts tests
```

`./dev.ps1 check` ran two behavioral tests: exact-byte hashing of the canonical architecture copy, and validation of the repository/specification/ticket-ledger contract. Both passed. The two source-copy SHA-256 digests match the values recorded in `DECISIONS.md`.

## Environment-specific finding

The managed sandbox denied writes to uv's default user cache, so `dev.ps1` now assigns `UV_CACHE_DIR` to the ignored repository-local `.cache/uv` path. The initial failing invocation is not a product or benchmark result. A test that tried Python's default temporary-directory API encountered the same managed ACL behavior; the final test exercises the required hashing behavior directly against the immutable spec copy without relying on temporary filesystem semantics.

An optional `git branch -m main` command could not update `.git/HEAD` because the managed workspace exposes that path read-only after initialization. The repository remains valid on its unborn `master` branch. No escalation was needed for the requested bootstrap, and no commit was requested or created.

No task, agent, provider, Harbor integration, benchmark evaluation, deployment, publication, or paid call was run.
