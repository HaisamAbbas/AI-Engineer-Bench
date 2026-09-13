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
