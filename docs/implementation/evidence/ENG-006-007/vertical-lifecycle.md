# ENG-006/007 local vertical lifecycle evidence

Date: 2026-09-14  
Scope: deterministic local development adapter for RAG-01. This report is not evidence of a real coding-agent run, official sandbox isolation, or a benchmark result.

## Lifecycle contract

`LocalAttemptRunner` owns the following ordered phases:

`provision → engineer → stop → collect → build → verify → finalize → cleanup`

It copies the frozen RAG-01 base to an editable engineering allocation, runs the deterministic engineering command under a deadline, stops the owned process tree, and only then calls the non-executing artifact collector. It reconstructs the collected allowed files over the original frozen base in a separately created build allocation. The trusted RAG-01 evaluator starts candidate code only from that build allocation and communicates through HTTP.

The writable engineering and build allocations are removed after every attempt. The content-addressed artifact references and immutable `attempt.json` evidence remain. An empty submission is an explicit immutable candidate manifest, not an implicit reuse of the engineering workspace.

## Tested behavior

Command:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_attempt_lifecycle -v
```

| Case | Observed result |
| --- | --- |
| Valid reference repair | PASS after a clean reconstruction containing only submitted `knowledge_service/backend.py` |
| Deadline during edits / candidate hang | owned parent and child process tree stopped; delayed child marker absent; post-deadline bytes excluded from frozen manifest |
| Partial submission | allowed added and modified files are replayed and evaluated from fresh build allocation |
| No artifact | empty manifest is evaluated as the baseline and receives FAIL, with no workspace reuse |
| Protected-path modification | valid execution with `contract_violation`; no silent collection omission |
| Candidate runtime failure | valid FAIL attributed to candidate runtime failure |
| Scorer crash | infrastructure-invalid, no verdict, retryable |
| Configuration failure | configuration attribution, no verdict |
| Failed teardown | infrastructure-invalid, retryable, with retained attempt evidence |
| Replacement policy | candidate failures are never retried; only retryable infrastructure outcomes consume the declared replacement cap and retain each prior evidence record |

## Attribution and retry policy

The adapter records `candidate_build_failure`, `candidate_runtime_failure`, `configuration_failure`, `resource_limit`, `provider_outage`, `host_failure`, `scorer_error`, `submission_contract_violation`, and `teardown_failure` distinctly. Only provider, host, scorer, and teardown failures are replacement-eligible. An engineering exit failure, candidate runtime failure, submission contract violation, or deadline/resource outcome never silently retries.

## Isolation limitations and blocked requirements

This adapter uses host processes and filesystem copies. It does **not** claim container, VM, network-egress, cloud-metadata, secret, host-filesystem, CPU/memory kernel enforcement, or multi-tenant isolation. Those official requirements remain blocked for ENG-019. Harbor remains the separately pinned execution backend from ENG-001; its real installed-agent compatibility gate is still blocked pending explicit provider/model authorization, credentials, and an approved cap.

Exact not-run real-agent RAG-01 smoke command, requiring that authorization:

```powershell
.\.venv\Scripts\harbor.exe run -p suites\dev\rag.document-freshness -a codex -m <AUTHORIZED_PINNED_MODEL> --job-name rag01-real-agent --jobs-dir .cache\rag01-real-agent --n-concurrent 1 --delete --yes
```
