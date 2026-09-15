# AI Engineer Bench

AI Engineer Bench is a proposed benchmark for whether coding agents can diagnose and repair runnable AI applications while preserving required behavior. It is designed around versioned tasks, isolated engineering and verification environments, immutable evidence, and reproducible reports.

The repository now has a validated deterministic execution-foundation fixture, strict core contracts, a local safe candidate-artifact boundary, and three local development tasks: RAG-01, EXT-02 batch alignment, and TOOL-01 false completion. Harbor 0.22.0 is pinned behind an AIEB-owned adapter; `aieb-core` provides versioned contracts, canonical SHA-256 identities, JSON Schemas, and pure frozen-campaign planning; `aieb-runner` provides content-addressed artifacts plus a deadline-first local execution/replay lifecycle. The task families have runnable intentionally broken baselines and external evaluators; they are not official benchmark admissions or published scores.

## Development

The tested foundation uses Python 3.12.10, uv 0.11.27, Harbor 0.22.0, Docker 29.7.2, and Docker Compose 5.5.1. Run the standard checks from PowerShell:

```powershell
uv sync --all-packages --locked
./dev.ps1 doctor
./dev.ps1 check
```

Run the explicit disposable Docker integration with Docker Desktop active:

```powershell
$env:AIEB_RUN_HARBOR_INTEGRATION='1'
.\.venv\Scripts\python.exe -m unittest tests.integration.test_eng001_harbor -v
```

Run the RAG-01 local development-admission matrix with:

```powershell
.\.venv\Scripts\python.exe scripts/run_rag01_admission.py --output docs/implementation/evidence/ENG-004-005/admission-report.json
```

Run the supported local CLI vertical path with the deterministic development reference candidate:

```powershell
.\.venv\Scripts\aieb.exe --json --no-color doctor
.\.venv\Scripts\aieb.exe task validate suites\dev\rag.document-freshness
.\.venv\Scripts\aieb.exe plan --campaign examples\rag01-local-campaign.json
.\.venv\Scripts\aieb.exe run --campaign examples\rag01-local-campaign.json
.\.venv\Scripts\aieb.exe inspect --trial rag01-reference-local
.\.venv\Scripts\aieb.exe report --campaign rag01-reference-local --format html
```

This writes local state under `.aieb/runs/`; it contains a controller lock, frozen manifest, JSONL events, artifact references, attempt evidence, result JSON, and static HTML report. `aieb run` returns success when the command completes even if a benchmark task fails; use `--fail-on-unsolved` to request exit code 5 for an unsolved completed task. Use `aieb resume <campaign-id> --campaign <file>` only with the unchanged frozen campaign manifest.

The equivalent development runs for EXT-02 and TOOL-01 use [ext02-local-campaign.json](examples/ext02-local-campaign.json) and [tool01-local-campaign.json](examples/tool01-local-campaign.json). The offline 18-cell pilot plan is [development-pilot-18.json](examples/development-pilot-18.json); it is prepared but not authorized or executed.

See [STATUS.md](docs/implementation/STATUS.md), the [RAG-01 admission report](docs/implementation/evidence/ENG-004-005/admission-report.md), [execution/replay evidence](docs/implementation/evidence/ENG-006-007/vertical-lifecycle.md), [CLI/accounting evidence](docs/implementation/evidence/ENG-008-009/local-cli.md), [EXT/TOOL admission evidence](docs/implementation/evidence/ENG-010/admission-report.md), [analysis evidence](docs/implementation/evidence/ENG-011/analysis.md), and [pilot preparation](docs/implementation/evidence/ENG-012/pilot-preparation.md). ENG-001's real installed-agent smoke remains blocked pending explicit provider/model authorization, credentials, and an existing cap.

## Hosted API and website (ENG-014/015/016)

`services/api` is a FastAPI service persisting task/evaluator/entrant/campaign/trial/publication
data in PostgreSQL, with OIDC-based auth and server-side role bindings; `services/api/src/aieb_api/worker`
is a PostgreSQL-leased execution worker with crash recovery. `apps/web` is the public, read-only
React/TypeScript website (Results, Compare, entrant profiles, task catalog/detail, run evidence,
Methodology, Releases, Corrections, Run locally) built against `services/api`'s generated OpenAPI
types - it recomputes nothing client-side. Run it locally against a real API instance:

```powershell
# services/api (see docs/implementation/evidence/ENG-014/api-service.md for full setup)
cd services/api; ..\..\.venv\Scripts\python.exe -m alembic upgrade head; cd ..\..
.\.venv\Scripts\python.exe -m uvicorn aieb_api.app:create_app --factory --app-dir services/api/src --port 8000

# apps/web
cd apps/web
npm install
npm run dev
```

Admin/publication workflows (`/admin/*`) are ENG-017/018 scope, not implemented in this website.
See [STATUS.md](docs/implementation/STATUS.md) for what each hosted page can and cannot show yet
given the current API surface. The recommended next implementation phase is ENG-017 (admin
campaigns and budget reservations) and ENG-015's directed leasing-granularity split (ENG015-007).
