# RAG-01 development admission report

Date: 2026-09-14  
Scope: development fixture `rag.document-freshness` version `0.1.0`; this is not a benchmark score or an official admission.

## Evidence boundary

The candidate application is started in a fresh copied repository and evaluated over its documented HTTP API. The evaluator is a maintainer-only module and does not import candidate application modules. Held-out event data is generated in `tests/maintainer/rag01/`, outside the contestant repository package. The external ledger is an HTTP service that observes backend mutation writes; it is not a candidate-process import or an assertion against handcrafted success JSON.

## Public requirement mapping

| Requirement ID | Authoritative external check |
| --- | --- |
| `api-ready` | `/health` responds ready |
| `latest-version-visible` | higher-version update is searchable before its mutation response returns |
| `citation-version-mapping` | response citation has the current version and chunk ID |
| `incremental-unaffected-write-scope` | mutation ledger records no rebuild/write of an unaffected document |
| `idempotent-event-replay` | identical event replay returns idempotently and does not duplicate hits |
| `lower-version-rejected` | lower version does not supersede current state |
| `equal-version-conflict` | same version with different payload returns conflict |
| `deleted-content-absent` | delete plus stale insertion cannot expose deleted text |
| `higher-version-recreation` | higher version recreates a deleted document |
| `unaffected-documents-preserved` | unrelated current document remains searchable |

## Observed outcomes

`python scripts/run_rag01_admission.py --output docs/implementation/evidence/ENG-004-005/admission-report.json` exercised fresh environments. The machine-readable report is [admission-report.json](admission-report.json).

| Candidate | Result | Required observed outcome |
| --- | --- | --- |
| Baseline | FAIL | Fails replay, lower-version, equal-version conflict, and deletion requirements |
| Reference repair | PASS | All ten requirements pass |
| Alternative repair | PASS | All ten requirements pass |
| Hardcoded output | FAIL | Fails current-result, citation, accounting, ordering, and unaffected-document checks |
| Visible-only deletion filter | FAIL | Fails durable deletion requirement |
| Global rebuild | FAIL | Fails unaffected-write-scope requirement |
| Stale resurrection | FAIL | Fails durable deletion requirement |
| Incorrect citation | FAIL | Fails current result/citation/recreation requirements |

The reference repair passed 10 of 10 independent fresh resets. No evaluator instability was observed in those runs.

## Remaining admission gate

Independent human review is pending. This development task is therefore not an officially admitted or released benchmark task. No paid provider, hosted service, campaign, or benchmark score was run.
