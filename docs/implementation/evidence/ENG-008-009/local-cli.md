# ENG-008/009 accounting and local CLI evidence

Date: 2026-09-14

## Implemented scope

`aieb` is a local-first development CLI with versioned JSON output and JSONL state events. It supports `doctor`, `task validate`, `task verify`, `plan`, `run`, `resume`, `inspect`, and `report` for RAG-01 baseline/reference development candidates. State is stored at `.aieb/runs/<campaign-id>/` with a frozen campaign manifest, controller lock, immutable attempt evidence, result JSON, JSONL event record, local artifact store, and static HTML report.

`aieb-runner` has a role-separated usage ledger for engineer, development application, verifier application, and verifier judge. It keys receipts by attempt, role, request ID, and physical retry; broker and adapter observations reconcile one request rather than double-charge it. Lost responses remain unknown/billing-uncertain. Hard cost is enforced only when a provider supports a conservative reservation; otherwise the profile is explicitly `estimated_time_limited`.

## Runtime-verified behavior

```powershell
uv sync --all-packages --locked
.\.venv\Scripts\aieb.exe --json --no-color doctor
.\.venv\Scripts\aieb.exe task validate suites\dev\rag.document-freshness
.\.venv\Scripts\python.exe -m unittest tests.test_accounting_and_cli -v
```

The tests validate duplicate-receipt reconciliation, lost-response unknown accounting, physical retry separation, unsupported hard-cost reservation labeling, RAG-01 validation/planning/run/inspect/report, zero-success unavailable cost display, controller-lock interruption safety, and invalid frozen-manifest resume rejection.

Exit code `0` means command completion, even when a completed baseline evaluation fails. `2` is invalid configuration/state, `3` missing capability, `4` infrastructure-incomplete execution, `5` explicit `--fail-on-unsolved`, and `130` controller interrupt.

## Limitations

No provider credential is accepted by the CLI, printed, or stored in local task/campaign state. The currently supported `reference` editor is deterministic development material, not proof of real-agent success. Provider billing, live application-model billing, hard monetary reservation, and complete Harbor tool/cost traces are unavailable and not claimed. Real-agent RAG-01 execution remains blocked pending existing authorization, credentials, and approved cap.
