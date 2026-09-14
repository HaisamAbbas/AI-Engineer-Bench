# Implementation decisions

This log records implementation choices made while executing the source specifications. It does not amend or replace the unchanged specifications in `docs/specs/`. Changes to scoring, isolation, artifact submission, or reproducibility require a dedicated ADR before implementation.

## BOOT-001 — Use the workspace root as the repository root

- Date: 2026-09-13
- Status: accepted
- Decision: initialize the local Git repository at `D:\AI-Engineer-Bench` because the supplied documents were already placed there and no project or repository existed.
- Consequence: no nested project directory, remote, fork, commit, deployment, or publication was created.

## BOOT-002 — Preserve byte-identical canonical specification copies

- Date: 2026-09-13
- Status: accepted
- Decision: copy the supplied architecture and implementation specification to `docs/specs/` without transforming their encoding or content. Preserve the original workspace attachments and ignore them as repository inputs.
- Evidence: SHA-256 is `1acd59c8be999ae750d864415a5eb41bba441e19e0a499e6984c3b98261b1017` for the architecture and `8f1556ac93e2f38af926923e6d13b33e452e9f969528cfcb89e6cfb7d6a2150c` for the implementation specification. `scripts/dev.py check` verifies these hashes.

## BOOT-003 — Apply the refined implementation layout

- Date: 2026-09-13
- Status: accepted
- Decision: use `infra/` (not architecture draft `infrastructure/`) and include `packages/aieb-analysis` when those components are implemented, because Implementation Specification sections 10–11 explicitly refine the layout. Create only structural roots needed for orientation now. Do not scaffold API, web, or empty Python distributions.
- Consequence: `packages/`, `suites/dev/`, `manifests/releases/`, and `infra/` document ownership without pretending their future components exist. `services/api` and `apps/web` are deferred until their tickets.

## BOOT-004 — Pin only the verified bootstrap Python tools

- Date: 2026-09-13
- Status: provisional
- Decision: use Python 3.12.10 and uv 0.11.27 for repository bootstrap because both were present and exercised locally. Record observed Git, Docker, Compose, and Node versions without treating them as compatibility acceptance. Do not select pnpm while no web package exists.
- Consequence: `.python-version`, `pyproject.toml`, and `toolchain.json` make the bootstrap reproducible. ENG-001 may revise Python only when actual Harbor compatibility evidence requires it. Harbor remains deliberately unpinned until Prompt 02.

## BOOT-005 — Keep the development entrypoint dependency-free

- Date: 2026-09-13
- Status: accepted
- Decision: expose `./dev.ps1 doctor|check|test`, backed by Python standard-library code, rather than creating product packages before their tickets.
- Consequence: bootstrap integrity and environment observations are executable now; the entrypoint performs no network calls, provider calls, evaluation, deployment, or publication.

## Open decisions

| Decision | Owner | Current state |
| --- | --- | --- |
| Real installed-agent entrant/model for final ENG-001 smoke | ENG-001 | Blocked pending explicit provider/model authorization, credentials, and existing cap |
| Harbor public-egress/metadata adversarial validation | ENG-019 | Deferred; application allowlist and verifier no-network paths pass, broader isolation is not claimed |
| Node and pnpm pins | Web implementation preparation | Deferred; no web package exists |
| Official VM provider | ENG-019 | Deferred |
| Provider prices and enforceable caps | ENG-008 | Deferred |
| Task resource budgets | ENG-012 | Deferred until pilot measurement |
| Official sample size | ENG-021 | Deferred until variance and budget evidence |
| Primary live semantic scoring | Task admission | Deferred; deterministic checks remain primary initially |

## ENG001-001 — Pin Harbor 0.22.0 behind an AIEB adapter

- Date: 2026-09-13
- Status: accepted for the local execution foundation; real-agent acceptance remains blocked
- Decision: pin Python 3.12.10, Harbor 0.22.0, and uv-build 0.8.4. The Harbor release resolves to commit `4407eb5227a2ff4f0d3f16b2eb48849382fdf276`; the PyPI wheel SHA-256 is `4c4c6571b3d160ed0cb45b82918136751fb08e7b8596412723ac00dde12eeabb`. Keep Harbor-specific imports behind the AIEB runner backend boundary.
- Evidence: `docs/implementation/evidence/ENG-001/compatibility-report.md` and `deterministic-run-summary.json`.
- Consequence: the deterministic Docker contract supports multi-service editing, deadline collection, separate replay, and scoped teardown. It does not establish real-agent compatibility, complete usage accounting, production isolation, or adversarial egress enforcement.
- Supply-chain note: the release tag is annotated but unsigned. The tag object, commit, wheel hash, and lockfile are all recorded so this limitation is explicit rather than silently trusted.

## ENG002-001 — Canonical JSON is the permanent content-identity boundary

- Date: 2026-09-13
- Status: accepted
- Decision: model versioned core contracts with Pydantic 2.13.5 and generate Draft 2020-12 JSON Schema artifacts. Compute content hashes from UTF-8 canonical JSON with sorted keys, explicit nulls, finite integer values, and normalized decimal strings; do not hash YAML bytes or use floating-point values.
- Consequence: equivalent YAML formatting yields the same digest, while a semantic change yields a new digest. Draft configuration and frozen resolved campaign are distinct immutable types. Core imports neither Harbor nor API/web packages.
- Evidence: `docs/implementation/evidence/ENG-002/verification.md`, `tests/test_core_contracts.py`, and generated `schemas/`.

## ENG003-001 — Store bytes by digest, authorize through references

- Date: 2026-09-14
- Status: accepted for local artifact storage
- Decision: place local content-addressed bytes in `aieb-runner` behind a narrow store interface. Keep candidate file operations in the existing `aieb-core` CandidateManifest; store access is granted only by explicit scoped/public references, not by knowledge of a deduplicated digest.
- Consequence: local collection/replay preserves allowed untracked additions, modifications, and deletions without relying on Git. Invalid workspace entries reject the submission rather than being omitted. Hosted object storage remains deferred behind the same interface.
- Evidence: `docs/implementation/evidence/ENG-003/artifact-store.md` and `tests/test_candidate_artifacts.py`.

## ENG004-005-001 - Keep RAG-01 evaluation out-of-process and HTTP-only

- Date: 2026-09-14
- Status: accepted for the development task
- Decision: implement the RAG-01 candidate application as a small standard-library HTTP service. Start it from a fresh copied candidate repository and have the maintainer evaluator use only the public `/health`, `/docs`, and `/search` endpoints. The evaluator records backend mutation writes through a separate local HTTP ledger and never imports candidate modules.
- Consequence: the authoritative result comes from live application behavior, while trusted fixture generation, evaluator logic, answer expectations, and repairs remain outside the contestant build context. The ledger is limited to the published incremental-write constraint; it is not a general security monitor or cost ledger.
- Evidence: `suites/dev/rag.document-freshness/`, `tests/maintainer/rag01/`, and `docs/implementation/evidence/ENG-004-005/admission-report.md`.

## ENG004-005-002 - Admit the fixture locally but retain the independent-review gate

- Date: 2026-09-14
- Status: accepted
- Decision: mark ENG-004 and ENG-005 complete after the baseline, two independent valid repairs, five targeted counterexamples, and ten fresh reference resets pass their defined local gates. Record independent human review as pending rather than treating local automated admission as an official release approval.
- Consequence: RAG-01 may serve the next local execution/replay phase, but must not be represented as an officially admitted task, benchmark result, or published score.

## ENG006-007-001 - Make stop precede artifact freeze and fresh replay mandatory

- Date: 2026-09-14
- Status: accepted for deterministic local development
- Decision: own the attempt lifecycle in `aieb-runner` rather than relying on backend lifecycle hooks. Stop the owned engineering process tree before collecting candidate bytes; reconstruct only the collected artifact over the frozen base in a separate build allocation before external evaluation.
- Consequence: verification cannot become extra editing time, and an empty candidate remains an explicit submission rather than a fallback to the engineering workspace. Content-addressed artifacts and attempt evidence persist while writable allocations are removed.
- Evidence: `packages/aieb-runner/src/aieb_runner/lifecycle.py`, `tests/test_attempt_lifecycle.py`, and `docs/implementation/evidence/ENG-006-007/vertical-lifecycle.md`.

## ENG006-007-002 - Do not present the local adapter as official isolation

- Date: 2026-09-14
- Status: accepted
- Decision: use host processes and filesystem copies only for deterministic development lifecycle coverage. Record every unsupported isolation guarantee as blocked instead of implying it through the word “sandbox”.
- Consequence: official egress, metadata, secrets, host filesystem, VM/container, kernel resource, and multi-tenant protections remain ENG-019 work; real-agent compatibility remains the separate ENG-001 authorization gate.

## ENG008-009-001 - Reconcile receipts by physical request and preserve unknown billing

- Date: 2026-09-14
- Status: accepted for local accounting
- Decision: use attempt, role, request ID, and physical retry as the receipt identity. Broker observations reconcile adapter observations for the same identity and do not add a second charge. A lost response remains billing-uncertain with unavailable cost/tokens.
- Consequence: reports cannot mistake unknown for zero or double-count broker and adapter data. Hard-cost profiles fall back to explicit estimated/time-limited handling when conservative provider reservation is unavailable.

## ENG008-009-002 - Keep the first CLI local, frozen, and narrow

- Date: 2026-09-14
- Status: accepted
- Decision: implement a standard-library `aieb` CLI only for RAG-01 baseline/reference deterministic development candidates. Persist local versioned JSON/JSONL state under `.aieb/runs`, lock one controller, require matching frozen manifests on resume, and generate static HTML without a hosted dependency.
- Consequence: the full local vertical path is executable and inspectable without claiming generic campaign support, provider billing, credentials, real-agent success, or official results.
