# ENG-001 compatibility report

Date: 2026-09-13  
Scope: disposable execution-foundation fixture only; no scientific benchmark task or paid evaluation

## Selection

| Component | Selected/tested value | Evidence |
| --- | --- | --- |
| Python | 3.12.10 | Installed interpreter, package install, unit checks, and Docker integration passed |
| uv | 0.11.27 | `uv lock` and locked environment sync passed |
| Harbor package | 0.22.0 | Exact dependency in `packages/aieb-runner/pyproject.toml`; wheel locked in `uv.lock` |
| Harbor source | tag `v0.22.0`; commit `4407eb5227a2ff4f0d3f16b2eb48849382fdf276`; tag object `41a50d62d7f35677cc34ba3a0c36f042a4fef68c` | Separate ignored checkout at `.cache/research/harbor-v0.22.0`; the annotated tag has no cryptographic signature |
| Harbor wheel | SHA-256 `4c4c6571b3d160ed0cb45b82918136751fb08e7b8596412723ac00dde12eeabb` | PyPI release metadata and `uv.lock` agree |
| Runner build backend | uv-build 0.8.4 | Exact build requirement in the runner package and lock |
| Docker | Linux server 29.7.2; Compose 5.5.1 | Local integration host; observed compatibility, not a minimum-version declaration |
| Fixture image | `python:3.12.10-slim-bookworm@sha256:da14110a04b750d409d6f63c24f999eca02c1e804e707e780e0d1f4c1d1f89f` | Digest-pinned and built during the integration |

Harbor remains behind the AIEB-owned protocol in `packages/aieb-runner/src/aieb_runner/backends/base.py`. Imports of Harbor types are confined to `backends/harbor/`. The source checkout is exploratory evidence only: it is ignored, is not a workspace member, and is not a fork or vendored dependency.

The installed 0.22.0 CLI's `harbor run --help` advertises `codex` as a supported installed-agent choice. The verification used Harbor's [v0.22.0 source](https://github.com/harbor-framework/harbor/tree/v0.22.0), [PyPI release metadata](https://pypi.org/project/harbor/0.22.0/), [getting-started guide](https://www.harborframework.com/docs/getting-started), [task contract](https://www.harborframework.com/docs/tasks), and [resource-control documentation](https://www.harborframework.com/docs/tasks/managing-resources). Local source inspection, not documentation alone, established the adapter calls and teardown sequence used by the fixture.

## Requirement results

| Requirement | Result | Evidence and limitation |
| --- | --- | --- |
| 1. Supported installed coding agent edits an editable repository with an application service | **NOT RUN / BLOCKED** | The deterministic `BaseInstalledAgent` fixture installed, edited the repository, created a file, and called the application sidecar. It proves Harbor's installed-agent contract only. It is fake and is not evidence that Codex or another real supported coding agent works. No credentialed/provider run was authorized. |
| 2. Deadline stops execution without writable contestant processes remaining | **PASS for deterministic fixture** | Harbor raised `AgentTimeoutError` at 8 seconds while a contestant-owned delayed writer was alive. Candidate collection occurred, the main environment was stopped before sidecar collection, the delayed file was absent, the verifier completed, the worker exited normally, and a post-run scoped Docker/process query was empty. |
| 3. Allowed newly created files are collected | **PASS** | The configured `/workspace/submission` artifact was collected with status `ok`; `new-file.txt` and an edited source copy were present. The application sidecar artifact was also collected with status `ok`. |
| 4. Files replay into a fresh environment and are externally checked | **PASS** | Harbor's `separate` verifier environment received the configured artifacts and produced `evidence.json` with `candidate_replayed`, `checked_outside_agent_environment`, and `late_write_absent` all true. It shared no contestant process or development volume. |
| 5a. CPU and memory controls | **PASS for declared/local enforcement** | Both task and verifier request 1 CPU and 256 MB; the adapter sets Harbor enforcement policy to `limit`. Harbor reports Docker CPU and memory limits supported. Kernel-level adversarial validation is deferred to ENG-019. |
| 5b. Multi-service application | **PASS** | The agent health-checked an `application` Compose sidecar and the sidecar supplied a collected state artifact. |
| 5c. Network controls | **PARTIAL** | The agent uses an allowlist containing only the application service, and the separate verifier uses `no-network`; both paths completed. Public-egress denial and cloud-metadata denial were not adversarially probed and remain ENG-019 work. |
| 5d. Teardown | **PASS for scoped local fixture** | No container matching the unique `eng001-*` trial scope remained after the successful run. Unrelated pre-existing containers were not touched. Orphan/crash reconciliation remains ENG-015/ENG-019 work. |
| 5e. Storage/GPU/VM isolation | **NOT RUN** | This spike tested Docker CPU and memory only. It does not claim storage enforcement, GPU controls, VM-grade isolation, host-metadata isolation, or production multi-tenant safety. |

## Entrant evidence coverage

The deterministic fixture produced agent name/version, timing boundaries, the timeout exception, artifact manifest, verifier result, and logs. `n_input_tokens`, `n_cache_tokens`, `n_output_tokens`, and `cost_usd` were all null. Lifecycle hooks and this adapter therefore must not be treated as a complete tool-call trace or billing ledger. Role-separated usage receipts and reconciliation remain ENG-008.

The local contract verifier emitted `reward = 1.0`; this is only a binary fixture assertion and is not a benchmark score. No scientific task, entrant comparison, or benchmark evaluation ran.

## Reproducible checks

Deterministic checks, with Docker Desktop running:

```powershell
uv sync --all-packages --locked
./dev.ps1 check
$env:AIEB_RUN_HARBOR_INTEGRATION='1'
.\.venv\Scripts\python.exe -m unittest tests.integration.test_eng001_harbor -v
```

The accepted integration completed one test in 57.757 seconds. The normalized record is `deterministic-run-summary.json`; it includes hashes of the disposable raw trial outputs. Raw run directories under `.cache/eng001-runs/` are intentionally ignored working data.

Exact real-agent smoke command (not run):

```powershell
.\.venv\Scripts\harbor.exe run -p tests\fixtures\eng001_harbor\task -a codex -m <AUTHORIZED_PINNED_MODEL> --job-name eng001-real-agent --jobs-dir .cache\eng001-real-agent --n-concurrent 1 --delete --yes
```

Run it only after explicitly authorizing the provider/model and confirming credentials and an existing spend cap. The fixture instruction should be replaced with a real-agent-specific smoke instruction before treating its result as acceptance evidence; the deterministic fixture's forced deadline behavior is contract-oriented.

## Iteration notes

Exploratory failures were retained as working data, not acceptance evidence: a 5-second healthcheck was too short for Windows Docker startup overhead; `no-network` routed the application sidecar behind the egress proxy and broke service-name access; and an early fake-agent shell command accidentally left an active host-side Compose exec at cancellation. The accepted fixture uses an application-only allowlist, a separate no-network verifier, a 30-second healthcheck allowance, and short completed exec calls before sleeping to the deadline. No chosen foundation was replaced to hide these adapter-level constraints.

Harbor's task checksum logged `fatal: bad revision 'HEAD'` because this repository has an unborn Git branch. The run continued and produced the recorded checksum. No commit was created because commits were not authorized.
