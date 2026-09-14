# AI Engineer Bench

AI Engineer Bench is a proposed benchmark for whether coding agents can diagnose and repair runnable AI applications while preserving required behavior. It is designed around versioned tasks, isolated engineering and verification environments, immutable evidence, and reproducible reports.

The repository now has a validated deterministic execution-foundation fixture, strict core contracts, a local safe candidate-artifact boundary, and one local development task: RAG-01 (`rag.document-freshness`). Harbor 0.22.0 is pinned behind an AIEB-owned adapter; `aieb-core` provides versioned contracts, canonical SHA-256 identities, JSON Schemas, and pure frozen-campaign planning; `aieb-runner` provides content-addressed artifacts plus a deadline-first local execution/replay lifecycle. RAG-01 has a runnable intentionally broken knowledge-service baseline and an external HTTP evaluator; it is not an official benchmark admission or a published score.

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

See [STATUS.md](docs/implementation/STATUS.md), the [ENG-001 compatibility report](docs/implementation/evidence/ENG-001/compatibility-report.md), [ENG-002 verification](docs/implementation/evidence/ENG-002/verification.md), [ENG-003 artifact evidence](docs/implementation/evidence/ENG-003/artifact-store.md), the [RAG-01 admission report](docs/implementation/evidence/ENG-004-005/admission-report.md), [execution/replay evidence](docs/implementation/evidence/ENG-006-007/vertical-lifecycle.md), and [CLI/accounting evidence](docs/implementation/evidence/ENG-008-009/local-cli.md). ENG-001's real installed-agent smoke remains blocked pending explicit provider/model authorization, credentials, and an existing cap. The recommended next implementation phase is Prompt 08: additional extraction and tool-application tasks.
