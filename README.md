# AI Engineer Bench

AI Engineer Bench is a proposed benchmark for whether coding agents can diagnose and repair runnable AI applications while preserving required behavior. It is designed around versioned tasks, isolated engineering and verification environments, immutable evidence, and reproducible reports.

The repository now has a validated deterministic execution-foundation fixture and a strict core-contract package. Harbor 0.22.0 is pinned behind an AIEB-owned adapter; `aieb-core` provides versioned contracts, canonical SHA-256 identities, JSON Schemas, and pure frozen-campaign planning. No benchmark tasks, API, website, campaign execution, or published results exist yet.

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

See [STATUS.md](docs/implementation/STATUS.md), the [ENG-001 compatibility report](docs/implementation/evidence/ENG-001/compatibility-report.md), and [ENG-002 verification](docs/implementation/evidence/ENG-002/verification.md). ENG-001's real installed-agent smoke remains blocked pending explicit provider/model authorization, credentials, and an existing cap. The recommended next implementation phase is ENG-003: safe local artifact storage and extraction.
