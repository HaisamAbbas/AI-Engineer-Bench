# ENG-021: Reviewer Checklist

**For independent reviewers: This checklist is the source of truth for your
review. Do NOT rely on the automated scripts' output as approval — they only
produce evidence reports.**

Status: **All items PENDING** — independent review not yet performed.

## 1. Task Admission Review (§26 Lifecycle)

For each of the 12 tasks, verify:

- [ ] `task.yaml` is present and schema-valid
- [ ] `provenance.json` is present and complete
- [ ] `repo/` contains the base implementation
- [ ] `reference/backend.py` contains a working baseline
- [ ] `alternative/backend.py` contains a broken baseline
- [ ] `counterexamples/` contains at least 1 failing example per requirement
- [ ] `contracts/application-api.md` describes the contract
- [ ] `environment/` documents the runtime setup
- [ ] `dev_data/` contains fixture data
- [ ] `dev_tests/README.md` documents the dev tests
- [ ] All 8 requirements in `task.yaml` are mandatory
- [ ] `dependency_mode: fixture` is honored
- [ ] `entrypoint` is correct

## 2. Evaluator Safety Review

- [ ] Evaluator imports no candidate code (AST-verified via `scripts/evaluator_review.py`)
- [ ] HTTP-based evaluation (no direct candidate access)
- [ ] Evaluator runs in a separate process
- [ ] Closure digest matches `task.yaml` `evaluator_digest` field
- [ ] No leakage vectors (network, filesystem, environment)

## 3. Provenance & Contamination Review

- [ ] `provenance.json` fields are complete and consistent
- [ ] `dev_data/` tokens extracted and hashed
- [ ] No token overlap between `dev_data/` and holdout private examples
- [ ] Three-label discipline enforced (no `development` labeled for official use)
- [ ] No contamination between public dev data and holdout fixtures

## 4. Family Split & Diversity Review

- [ ] Confirm 3 base application types (knowledge-service, extraction-service, assistant-service)
- [ ] Confirm 4 family IDs per base type (12 total)
- [ ] Acknowledge spec §28 expects 6 projects; repo has 3 (exploratory intervals, not population-wide)
- [ ] Acknowledge very thin backends (2-4 lines of logic in shared HTTP harness)

## 5. Sandbox & Isolation Review (ENG-019)

- [ ] Deny-by-default egress proxy with token auth
- [ ] Cloud metadata endpoints unconditionally denied
- [ ] Docker socket refused (HarborBackend refuses hardened isolation)
- [ ] **BLOCKED**: VM-level isolation requires official VM provider (not yet created)

## 6. Campaign Readiness Review

- [ ] 180 trials (12 tasks × 3 entrants × 5 reps) are pre-specified
- [ ] Sampling plan is interleaved cyclic permutation
- [ ] MME = 0.10 is prespecified
- [ ] Cost reservation is assumption-based (not hard-enforced)
- [ ] Infrastructure-invalid policy (retain, not score) is documented
- [ ] Sample-size methodology is assumption-based (NOT pilot-derived)
- [ ] **CRITICAL**: 5 reps is NOT statistically powered (MDD ≈ 0.62 > MME 0.10)

## 7. Publication Safety (ENG-018)

- [ ] Publication table immutability is implemented
- [ ] Two-human approval is required (NOT automated)
- [ ] Redaction whitelist is in place
- [ ] Withdrawal/corrections append-only

## 8. Final Verdict

- [ ] All 12 tasks pass admission review
- [ ] ENG-001 (real smoke) is authorized and complete
- [ ] ENG-012 (real pilot) is authorized and complete
- [ ] ENG-019 (VM isolation) is complete
- [ ] ENG-020 (staging/prod deployment) is complete
- [ ] Python lint/type CI gate is implemented
- [ ] Two independent human reviewers have signed off

**If any item above is unchecked, the campaign and release remain BLOCKED.**
