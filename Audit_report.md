# AI Engineer Bench v2.0 — Architecture and Implementation Audit

**Audit date:** 2026-09-22  
**Specification:** `AI-Engineer-Bench-Redesign-Spec-v2.0.md`  
**Repository state:** uncommitted working tree; this report describes the inspected snapshot and does not certify a release  
**Overall conclusion:** **the architecture is promising, but the v2.0 definition of done is not yet met**

## 1. Executive assessment

The repository has moved well beyond a prototype. It contains strong foundations for immutable campaign contracts, admission state, database-enforced transitions, independent review records, holdout metadata, analysis, and a public read-only application. The implementation generally fails closed when evidence is absent, and the gap documents usually avoid claiming external work that software cannot perform.

The most important remaining weakness is end-to-end identity continuity. A campaign can be frozen with digest-bound records, but the Harbor worker currently resolves a task from the mutable checkout by slug and does not persist all returned Harbor identity, usage, trace, and timeout evidence. Consequently, the system cannot yet prove that the exact task package and execution environment that were approved are the ones that ran.

The second systemic weakness is release provenance. Publications distinguish ranked from non-ranked output, but do not encode the specification's immutable `development`, `preview`, and `official` release stages. Without that boundary, a development-fixture campaign can approach the same publication path as an official campaign. Ephemeral signing keys and the absence of an explicit authorization/trust-root record make this especially important.

The third weakness is verification drift. The current repository does not have one trustworthy, hermetic command that proves the whole product is healthy. Backend collection currently fails in a no-PostgreSQL environment, focused tests expose a stale MVP-2 contract test, frontend tests are out of sync with the read-only layout, and TypeScript generation attempts an offline-unavailable `npx` download despite a local dependency. CI also uses `unittest` discovery in places, which silently misses plain pytest tests.

These are fixable architectural gaps, not reasons to abandon the design. The recommended order is: restore a truthful green baseline, close Harbor identity continuity, introduce release tiers and trusted authorization, then invest in deep task content and real external evidence.

## 2. Audit method and limitations

This audit compared the v2.0 specification with the current implementation, documentation, manifests, migrations, workflows, and high-risk execution/release paths. Particular attention was given to:

- campaign freezing and Harbor dispatch;
- task and environment identity;
- admission, holdout, and independent-review gates;
- publication eligibility and signatures;
- MVP-1 structural depth and MVP-2 bug-finding contracts;
- public/private product boundaries;
- CI, deployment, and reproducibility.

The following checks were executed against the inspected working tree:

| Check | Result |
|---|---|
| Focused MVP-1/MVP-2/Harbor/holdout tests | 23 passed, 1 failed |
| Full backend pytest collection | 2 collection errors |
| Frontend tests | 37 passed, 6 failed |
| Frontend TypeScript check | Passed |
| OpenAPI consistency | Passed |
| TypeScript API generation check | Could not run hermetically offline |
| Alembic heads | One head: `e7f8a9b0c1d2` |

The focused failure is a test that has not been updated for the newly required hidden-label revision field. The backend collection errors are caused by conditional `unittest` imports in database-gated test modules. The frontend failures are stale authentication expectations after `Layout` became public-only. These are test-integrity defects, not evidence that the underlying new security checks should be removed.

No live PostgreSQL migration drill, real Harbor campaign, private cloud holdout retrieval, real OIDC flow, or genuine independent human review was performed in this audit. Prior evidence claims concerning those systems were not independently certified here.

## 3. Specification conformance summary

| Area | Assessment | Main reason |
|---|---|---|
| Public read-only product boundary | **Partial** | UI is read-only, but public and privileged routes remain mounted in one service and dead token/OIDC code remains in the public bundle |
| Immutable task revisions | **Partial** | Database/manifests are digest-oriented, but dispatch uses a mutable slug directory and the task schema omits several v2 identity fields |
| Harbor execution boundary | **Partial / high risk** | Real dispatch exists, but source materialization, returned identity persistence, timeout semantics, and verifier ownership are incomplete |
| Track A end-to-end protocol | **Partial** | Protocol contracts and analysis exist; clean, isolated, independently evidenced execution has not been proven |
| Track B bug finding | **Partial** | Contracts and binding are improving; private labels, evaluator execution, complete scoring, and admitted tasks are absent |
| Track C model evaluation | **Blocked** | Authorization and real provider execution evidence remain absent |
| Admission and independent review | **Software gate implemented; operationally blocked** | No genuine complete reviewer corpus exists and PostgreSQL enforcement was not rerun here |
| Private holdouts | **Development foundation only** | Registry and local adapter exist; no production storage/IAM/retention drill or human approval evidence |
| Scoring and validity separation | **Mostly implemented** | Strong analysis model, but some track-specific metrics and end-to-end evidence remain incomplete |
| Versioned releases | **Partial / high risk** | Supersession exists, but immutable release stage, official authorization, and trusted signing policy do not |
| Public transparency | **Partial** | Many fields exist, but release provenance and a fully evidenced official cohort are missing |
| MVP-1 content quality | **Not met** | 11 of 12 tasks are structurally shallow and all three application groups fail the depth floor |
| MVP-2 content quality | **Not admitted** | Catalog exists, but audit correctly reports zero admitted tasks |
| Deployment readiness | **Not met** | No demonstrated staging/production topology, hardened Harbor profile, managed secrets, backups, or live observability |

## 4. What is already strong

The following work should be preserved and extended:

1. **Frozen contract discipline.** Pydantic contracts use strict schemas, digest binding, and rejected extra fields. Campaign identities are reconstructed and rechecked near dispatch.
2. **Database state integrity.** Admission, campaign, review, and publication transitions are guarded by constraints/triggers rather than relying only on API behavior.
3. **Fail-closed admission direction.** Missing executors, missing human evidence, placeholder holdouts, invalid review independence, and incomplete cohort evidence generally block progress.
4. **Validity separated from candidate performance.** Infrastructure failure is modeled separately from score-bearing candidate outcomes, which matches the specification.
5. **Artifact safety.** Candidate collection and normalization include path and size controls and avoid treating arbitrary worktree contents as valid submissions.
6. **Append-only review evidence.** Independent-review and holdout-review records contain useful identity and evidence fields, with self-review protections.
7. **Public UI direction.** The current layout is moving toward a read-only benchmark product rather than a public administrative console.
8. **Honest gap statuses.** MVP-1 depth and MVP-2 admission evidence currently remain partial rather than being promoted based on synthetic evidence.

## 5. Critical and high-priority findings

### F-01 — Task identity is not preserved from freeze to execution

**Severity:** Critical  
**Affected code:** `services/api/src/aieb_api/worker/runner_bridge.py`, `services/api/src/aieb_api/worker/harbor_dispatch.py`

The worker validates the frozen campaign payload, but then locates a task at `suites/dev/<task.slug>` in the current checkout. It does not materialize an immutable package by digest or hash the complete task tree immediately before dispatch. A changed directory, branch, generated file, or slug collision can therefore make the executed bytes differ from the approved bytes.

The `official_image`/repository identity carried by manifests is not demonstrated to be the identity Harbor actually consumes. Several current image values are development placeholders, while the backend is driven from a local task directory.

**Required improvement:**

- Define a canonical task-package format and tree-digest algorithm.
- Store admitted task packages in content-addressed immutable storage.
- Materialize by digest into a fresh directory; reject symlinks, path escapes, extra files, missing files, and digest mismatches.
- Bind Harbor's environment/image digest, Harbor version, task-package digest, evaluator digest, and protocol version into one execution identity record.
- Never dispatch an official cell from a mutable repository path or slug lookup.

**Acceptance evidence:** a mutation made after campaign freeze causes dispatch to fail; a clean materialization produces the same digest on two machines; the persisted trial identity contains and verifies every frozen digest.

### F-02 — Harbor outcome evidence is dropped and timeout handling is unsafe

**Severity:** Critical  
**Affected code:** `packages/aieb-runner/src/aieb_runner/backends/base.py`, `services/api/src/aieb_api/worker/runner_bridge.py`

`DispatchOutcome` carries an identity record, usage, trace, deadline status, and terminal state, and its contract explicitly makes the caller responsible for persistence. The worker currently retains the candidate and advances the job but does not persist all of this evidence. It can also continue toward verification when a candidate exists after a deadline, without first classifying the engineering run as timed out.

In addition, the dispatch deadline is computed from engineering plus verification wall time even though this worker path represents the engineering phase. That can accidentally grant the agent the verifier's time budget.

**Required improvement:** persist an append-only execution envelope before state advancement. It must include request/frozen identity, returned Harbor identity, usage, trace digest/location, terminal state, deadline status, artifact digest, backend version, and timestamps. A deadline or identity mismatch must produce a non-score-bearing infrastructure/protocol result according to a documented policy.

### F-03 — Verifier ownership and crash recovery are architecturally ambiguous

**Severity:** High  
**Affected code:** `packages/aieb-runner/src/aieb_runner/backends/harbor.py`, worker execution paths, pending Harbor recovery decision

The v2 specification requires the engineering environment to be destroyed before private verification in a fresh environment. The current Harbor adapter creates a Harbor trial with a verifier, but AIEB also persists a candidate and performs a later verification phase. This is halfway between a combined Harbor trial and a split protocol. Recovery semantics, timeout ownership, and which result is authoritative are therefore unclear.

**Decision required:** adopt the split-phase model proposed in ADR-003 below unless Harbor cannot execute agent-only tasks. Engineering should produce a candidate only; AIEB should then create a separate clean verifier environment with no agent access. If Harbor only supports combined trials, the work-item/state model must instead treat the entire Harbor trial as atomic and rerun the whole cell after an ambiguous crash.

### F-04 — Official release provenance is not represented

**Severity:** Critical  
**Affected code:** `services/api/src/aieb_api/routes/publications.py`, `services/api/src/aieb_api/schemas.py`, `services/api/src/aieb_api/signing.py`

Publications distinguish `ranked` and `non_ranked` results, but that is not the same as the required `development`, `preview`, and `official` release stages. A campaign using development fixtures is not protected by a first-class publication-tier gate. The public product therefore cannot make a durable, machine-checkable distinction between locally generated evidence and authorized official evidence.

The signing module can generate an ephemeral signing key when configuration is absent. This is useful for local development but must never authorize an official publication. A signature proves possession of a key, not that the key is trusted or that an external owner approved the release.

**Required improvement:**

- Add immutable `release_stage` and `evidence_provenance` fields to campaigns/publications.
- Require a separate append-only release-authorization record for `official` publication.
- Make official publication fail closed unless it has production holdouts, hardened isolation, durable trusted signing, complete eligible cohorts, real provider authorization where applicable, and required human approvals.
- Publish a key registry with key IDs, validity periods, rotation, revocation, and offline verification instructions.
- Display release stage prominently on every public table, comparison, export, and permalink.

### F-05 — The repository does not currently have a truthful green quality gate

**Severity:** High  
**Affected code:** `.github/workflows/*`, `scripts/dev.py`, backend and frontend tests

Several workflows and the local `check` command use `unittest` discovery. Plain pytest tests—including important Harbor and holdout cases—can be omitted while the command still reports success. The release-candidate workflow describes this as full backend discovery. The same workflow contains a deliberately failing capped-live-smoke placeholder, so its release-tag path cannot become a meaningful green signal.

Current concrete drift includes:

- backend collection errors in `tests/test_eng023_usage_accounting_repository.py` and `tests/test_metrics.py` because `unittest` is imported only when a database URL exists;
- a stale MVP-2 test missing the newly required label revision;
- six stale `Layout.test.tsx` expectations for sign-in/role UI no longer present in the public layout;
- TypeScript generation using `npx --yes`, which attempts package retrieval instead of using the checked-in lockfile/local binary.

**Required improvement:** define one `verify` command used locally and in CI. It should run all pytest tests, frontend tests, Python lint/type checks, TypeScript checks, schema/OpenAPI generation checks, migrations, and evidence regeneration checks. CI must have separate fast, PostgreSQL, Harbor, and release-authorized lanes; unavailable live smoke tests should be explicitly skipped/blocked, not represented by a permanent `exit 1` in the normal release workflow.

### F-06 — MVP-1's structural-depth gate is gameable and its content target is unmet

**Severity:** High  
**Affected code:** `scripts/validate_mvp1_suite.py`, `suites/real/*`

The audit correctly reports that 11 of 12 tasks are shallow and every application group fails the aggregate/deep-task floor. The current metric is also vulnerable to padding: most Python files except generic `server.py`/`__init__.py` are counted, and AST source spans can count multiline blanks/comments. Tests, generated code, fixtures, or dead code can increase the score without increasing application depth. `category_diversity_satisfied` is calculated but is not included in the final depth/admission conjunction.

**Required improvement:** measure admitted application source only, exclude tests/generated/vendor/migrations/fixtures, combine executable complexity with dependency graph and runtime surface measures, require reachable changed code, and include category diversity in the final gate. Most importantly, replace or deepen the current synthetic tasks: the target is at least three genuinely different application repositories with realistic state, integrations, failure modes, and nontrivial changes—not twelve wrappers around a shared harness.

### F-07 — MVP-2 is not yet an end-to-end benchmark track

**Severity:** High  
**Affected code:** `packages/aieb-core/src/aieb_core/bugfinding_v2.py`, `scripts/validate_bugfinding_track.py`, MVP-2 catalog/evidence

Recent contract changes improve binding among task revision, hidden labels, evaluator, findings, reproductions, and patches. However, tests and evidence have not caught up, no private label set is admitted, and no isolated evaluator route produces persisted official results.

The catalog validator resolves relative paths without first proving that they remain beneath the task root, and evidence references are largely checked for presence rather than immutable content. The scoring model also does not yet expose all specification components such as root-cause accuracy and regression safety. Duplicate treatment in precision needs an explicit public rule.

**Required improvement:** use the same content-addressed path safety as Track A; integrate a private label registry; run evaluator-owned reproduction and regression checks in isolation; persist per-component evidence; add root-cause and regression-safety scoring; define duplicate/abstention policy; and create a separate admitted Track-B release/cohort that cannot be blended with engineering scores.

## 6. Medium-priority weaknesses

### F-08 — Task revision schema is narrower than the v2 manifest contract

The core task revision does not fully represent required tags, difficulty, source license/commit, explicit Harbor/environment version, task-level time/resource policy, and lifecycle status in one immutable revision. Some values live at campaign level, some in JSON, and some are absent or placeholders. Introduce a versioned `TaskRevisionV2` rather than silently changing the meaning of the current schema.

### F-09 — Private holdout capability is development-only

The manifest registry, immutable review model, and capability-scoped local adapter are useful foundations. Official operation still needs private S3/GCS or equivalent storage, short-lived scoped credentials, provider-side access logs, encryption/key policy, dual control, retention/expiry jobs, restore/destruction drills, and a contamination-response process. The local directory adapter must remain incapable of satisfying an official release gate.

### F-10 — Independent review lacks operational governance

Software correctly cannot self-certify genuine review. Define reviewer onboarding/offboarding, authenticated organization identity, conflict declarations, minimum quorum by release tier, evidence-retention policy, decision invalidation/supersession, emergency revocation, and periodic access review. Human approval records should refer to immutable evidence bundles, not mutable documentation paths.

### F-11 — Public and privileged APIs share one deployable boundary

Role checks are valuable, but the specification asks for a public read-only surface and a private maintainer control plane. Mounting both in one service means a routing/auth defect can expose mutation endpoints. Deploy separate ASGI applications or listeners: a public service that does not import or mount mutation routes, and a private service reachable only through authenticated operator infrastructure.

The public frontend still contains token/OIDC client behavior even though the visible layout is read-only. Remove it from the public bundle or move all operator functionality into a separately built and separately hosted application.

### F-12 — Documentation and evidence can contradict current code

The gap register includes both older text saying Harbor is not wired and newer text saying it is wired. It also mixes previously reported PostgreSQL success with sections that say PostgreSQL evidence is still absent. The top-level README still describes an earlier, much smaller proposal and `toolchain.json` says Node/pnpm are deferred despite an active web application.

Replace cumulative narrative status files with generated current-state summaries plus an append-only decision/evidence history. Every evidence JSON should contain the source commit/tree digest, generator version, command, timestamp, and input digests, and CI should fail if deterministic evidence is stale.

### F-13 — Deployment and software-supply-chain controls are incomplete

Container and workflow files exist, but production readiness is not demonstrated. Base images are tag-pinned rather than digest-pinned; runtime images install/build with broad source context; no deployed read-only filesystem/capability-drop/seccomp policy was observed; and the release SBOM action is not pinned to an immutable commit. Staging, backups/restore, secret rotation, live alert delivery, and a hardened Harbor profile remain external gaps.

Use multi-stage minimal runtime images, digest-pin bases and actions, generate/sign SBOM and provenance, scan containers/dependencies/secrets, run with read-only roots and dropped capabilities, and verify backup restoration and migration rollback in disposable environments.

### F-14 — Production configuration does not fail closed consistently

Local CORS defaults and development signing behavior are convenient, but a production process should refuse to start if explicit allowed origins, trusted OIDC issuer/audience/JWKS, durable signing identity, storage provider, or required isolation policy are absent. Configuration should be represented by a validated environment profile such as `local`, `ci`, `preview`, or `official`; an `official` profile must prohibit all development fallbacks.

### F-15 — The planned package boundary and implemented boundary differ

The specification proposes a dedicated `packages/aieb-harbor`; Harbor integration currently spans `aieb-runner` and API worker code. This is not inherently wrong, but the contract boundary should be explicit. Extract a dedicated adapter package only after the execution envelope and split-phase protocol stabilize; doing it earlier would create cosmetic churn without fixing identity continuity.

## 7. Proposed design decisions

These decisions should be recorded as ADRs and reflected in schemas, migrations, and acceptance tests.

### ADR-AUDIT-001 — Immutable release tiers and authorization

Every campaign and publication has an immutable stage: `development`, `preview`, or `official`. Promotion creates a new publication; it never mutates an old stage. `official` requires a separately signed authorization record referencing the exact release digest and all required gate evidence. Public APIs always return the stage and provenance.

### ADR-AUDIT-002 — Content-addressed task packages

An admitted revision points to a canonical package digest in immutable storage. Workers materialize only by digest into a fresh directory and independently verify it before launch. Slugs are display identifiers, never execution locators. The canonical digest covers file paths, modes, bytes, required metadata, evaluator/public-contract digests, and the environment identity.

### ADR-AUDIT-003 — Split engineering and private verification

Track A uses two explicit leases and two isolated environments. The engineering lease has no hidden material and returns only allowed candidate artifacts. After teardown, the verification lease receives the immutable candidate digest and private verifier capability. Each phase has its own deadline, usage, trace, recovery policy, and terminal state. No verifier result produced in the engineering environment is official.

### ADR-AUDIT-004 — Separate public data plane and private control plane

The public service mounts read-only publication/query routes only and can run from signed snapshots or a read replica. The private service owns mutations, worker coordination, admissions, reviews, and holdout access. They use different origins, credentials, network policies, and deployable artifacts.

### ADR-AUDIT-005 — Signed evidence envelopes and trust registry

Every trial and publication produces a canonical evidence envelope containing input identities, environment/backend versions, usage, trace/artifact digests, validity classification, and timestamps. Official envelopes are signed by a managed key whose key ID is published in a versioned trust registry with rotation and revocation semantics. Evidence is independently verifiable offline.

### ADR-AUDIT-006 — One authoritative verification graph

The project defines a machine-readable verification graph used by local development, CI, admission, and release. A release cannot use a narrower test command than pull requests. Database, Harbor, cloud-provider, and human gates are explicit required/blocked nodes rather than silent skips. Generated evidence records which graph version ran.

### ADR-AUDIT-007 — Evaluator-owned Track-B truth

Finding matches, reproduction results, patch correctness, root-cause accuracy, regression safety, duplicate grouping, and severity credit are produced by a versioned isolated evaluator—not accepted from the candidate payload. A private label-set digest is bound to one task revision and evaluator revision. Track-B releases and rankings remain separate from Track A.

### ADR-AUDIT-008 — Official execution profile

An `official` configuration profile has no local fallbacks. It requires digest-pinned images, approved hardened isolation, production holdout storage, durable keys, explicit CORS/OIDC settings, complete telemetry, and provider authorization. The process exits before accepting work if any requirement is missing.

## 8. Recommended delivery plan

### Phase 0 — Restore a trustworthy baseline

1. Fix backend test collection and update stale MVP-2/frontend tests.
2. Replace `unittest discover` with complete pytest execution everywhere.
3. Make TypeScript generation use the lockfile-installed local binary with no network dependency.
4. Add Python lint/type checks and run frontend tests in the authoritative command.
5. Reconcile README, toolchain metadata, gap register, and deterministic evidence.
6. Require a clean-checkout rerun of the complete verification graph.

**Exit criterion:** one documented local command and corresponding CI workflow pass from a clean checkout without silently skipped test families.

### Phase 1 — Close execution identity and protocol gaps

1. Implement content-addressed task package storage/materialization.
2. Persist and verify the complete Harbor execution envelope.
3. Correct phase-specific deadlines and timeout classification.
4. Adopt and implement the split-phase Harbor/verifier ADR.
5. Add PostgreSQL-backed crash, retry, lease-fencing, and identity-substitution tests.
6. Run a real disposable Harbor campaign and preserve independently verifiable evidence.

**Exit criterion:** an auditor can start from a frozen campaign digest and prove exactly which task, environment, model, candidate, verifier, usage, and result bytes participated.

### Phase 2 — Establish release provenance

1. Add immutable release stages and explicit evidence provenance.
2. Introduce official authorization and managed signing/trust records.
3. Prevent development holdouts, local isolation, ephemeral keys, or incomplete cohorts from reaching `official` publication.
4. Split public and private deployables.
5. Update the public site and exports to display stage, cohort eligibility, uncertainty, trial counts, costs, configuration, and limitations.

**Exit criterion:** a consumer can cryptographically and visually distinguish development, preview, and official results, and can verify the official release offline.

### Phase 3 — Build benchmark-quality content

1. Replace/deepen MVP-1 tasks until at least three genuinely distinct applications pass robust structural and semantic review.
2. Curate real private holdouts and execute ten-reset matrices through the official protocol.
3. Complete the Track-B evaluator, private labels, controls, and separate release cohort.
4. Record genuine independent human approvals with conflict and quorum rules.

**Exit criterion:** admitted tasks pass technical gates and independent semantic review; neither task count nor code-volume heuristics substitute for application realism.

### Phase 4 — Production hardening and operations

1. Deploy staging with real OIDC, PostgreSQL, object storage, secrets, metrics, alerts, and backups.
2. Harden and attest images; pin supply-chain dependencies and actions.
3. Exercise restore, key rotation, migration rollback, holdout expiry, worker crash, and Harbor outage drills.
4. Run an authorized capped campaign, publish it as preview, obtain independent acceptance, and only then prepare an official release.

## 9. Minimum evidence required before calling v2 complete

- All tests and generated-artifact checks pass from a clean checkout.
- PostgreSQL migrations pass upgrade-from-previous, fresh install, constraint, and rollback/recovery drills.
- A real Harbor campaign proves task/environment identity, isolation, teardown, retries, timeout behavior, and complete evidence persistence.
- At least three deep, genuinely distinct Track-A applications are admitted with required reset matrices.
- Production private holdouts are accessed only through scoped credentials and have independently reviewed overlap/isolation/retention evidence.
- Every admitted task and release has genuine independent human review; authors/requesters cannot approve their own subjects.
- Track-B has at least one privately labeled, admitted task and a real evaluator-owned end-to-end result; its release remains separate from Track A.
- Official releases use managed signing keys, a published trust registry, complete cohort rules, and immutable stage/authorization records.
- The public site exposes release stage, eligibility, trial counts, uncertainty, configuration, costs, validity, limitations, and supersession history.
- Staging/production operational evidence includes alert delivery, restore tests, secret/key rotation, and incident/revocation procedures.

## 10. Final recommendation

Do not expand the number of benchmark tasks or add ranking features until the identity, provenance, and verification-baseline issues are closed. The best next milestone is not “more tasks”; it is one small preview release whose entire chain—from admitted content-addressed task package through Harbor, clean verification, signed evidence, independent review, and public display—can be reproduced and audited.

After that vertical slice is trustworthy, deepen three application families and repeat the same proven pipeline. This approach will make the benchmark harder to game, easier to operate, and substantially more credible than a broader suite built on unverifiable execution or ambiguous release status.
