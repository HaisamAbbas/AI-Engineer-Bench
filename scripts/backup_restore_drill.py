"""ENG-020 backup/restore drill (spec section 40: "Restoring a database does not resume
abandoned trials blindly; reconcile leases, revoke stale credentials and quarantine orphan
allocations.") Measuring restore duration is not the acceptance criterion here - this script
proves the three specific post-restore behaviors the spec names, through a REAL pg_dump/
pg_restore cycle against two separate disposable PostgreSQL databases (not a same-connection
simulation):

1. A lease abandoned before the backup is NOT silently treated as resumable after restore -
   it is still 'leased', at its pre-restore generation, until reconciliation acts on it.
2. Reconciliation, run against the RESTORED database, correctly recognizes and quarantines
   (reconciles/replaces) the orphaned lease - the same mechanism already covered by
   tests/test_worker_leasing.py's orphan-teardown tests, now proven to survive an actual
   restore, not merely a live connection.
3. The pre-restore worker's identity (its lease generation) is fenced out the first and only
   time a finalize is attempted with it - never accepted even transiently before
   reconciliation runs (the existing lease-fencing mechanism is the concrete form "revoke
   stale credentials" takes here). A live pre-restore credential rides along in the backup
   and is asserted to be DEAD at first use after the fence advance, with
   attempt_credential_status() AGREING (deciding-review finding 2) rather than reporting a
   "valid" status no actual verification would honor.
4. The fence advance itself is atomic against in-flight fenced transactions (deciding-review
   finding 1): every fenced operation shares the fence row for its whole transaction, so a
   pre-restore worker cannot read epoch 0 and land an epoch-0 mutation after the advance
   committed. Covered continuously by the two-session regression in
   tests/test_worker_leasing.py::test_fence_advance_is_atomic_against_in_flight_fenced_operations.
5. The operator CONTROLS are exercised through the real command
   `scripts/fence_advance.py` - NOT a direct repository call (re-review round 2): `--check`
   pre-flight in both barrier states, kill-switch REFUSAL with no epoch change, and the
   mutating advance under a re-established barrier - plus the runbook-bug reproduction that a
   restore OVERWRITES the live DB's kill-switch row with the backup's (inactive) state, so
   the barrier must be re-established ON THE RESTORED DATABASE after the restore, and the
   atomicity of that barrier with the epoch bump (ordering A: deactivation first -> refusal,
   no change; ordering B: deactivation blocks until the advance commits - covered by the
   two-session regressions in tests/test_worker_leasing.py).

The drill's pre-restore lease is DELIBERATELY still WITHIN its lease window when the backup is
taken (lease_expiry set to the future, not backdated): that is exactly the pre-reconciliation
fencing hole the audit ledgered as open gap 4 - a restored snapshot that still shows a lease as
live lets a stale worker act (heartbeat, finalize, issue credentials) before the reconciler's
next poll, and if that worker keeps heartbeating, its lease never expires so reconciliation would
never fence it at all. Gap 4 closes the hole with a monotonic system fence epoch the operator
advances AFTER restore: every lease/credential stamped with an older epoch fails every fenced
operation at FIRST touch, before any reconciliation, and the reconciler's sweep treats
stale-epoch leases as orphaned on its next poll even while their expiry is still in the future.
This script now performs that operator step after restore and asserts the stale worker is fenced
BEFORE reconciliation runs - the procedure requirement the older drill only wrote down as a
comment without a mechanism behind it.

Restore duration is reported as a LOCAL PROXY measurement only, disclosed as such - it is not
a production RPO/RTO measurement (spec section 40's targets: metadata RPO <=15 minutes, RTO
<=4 hours, "tested before official launch", which requires real staging/production
infrastructure this environment does not have).

Requires: AIEB_DATABASE_URL pointing at a disposable Postgres server (host/port/credentials
reused; this script creates and drops its OWN two database names on that server) and `pg_dump`/
`pg_restore` on PATH.
"""
from __future__ import annotations

import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
for src in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src", ROOT / "packages/aieb-runner/src"):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

import os

SOURCE_DB = "aieb_restore_drill_source"
RESTORED_DB = "aieb_restore_drill_restored"

# Real CI (Ubuntu with postgresql-client installed, as this repo's other workflows already
# assume for `pg_isready`-style checks) runs pg_dump/pg_restore/createdb/dropdb directly on
# PATH. This local dev environment's disposable Postgres runs in a Docker container with no
# client tools on the host - set this env var to route the same commands through
# `docker exec` into that container instead, without changing what is actually being tested.
_DOCKER_CONTAINER = os.environ.get("AIEB_PG_DOCKER_CONTAINER")


def _base_url() -> tuple[str, str, str, int, str]:
    raw = os.environ["AIEB_DATABASE_URL"]
    parsed = urlparse(raw.replace("postgresql+psycopg://", "postgresql://"))
    return raw, parsed.username or "postgres", parsed.hostname or "localhost", parsed.port or 5432, parsed.password or ""


def _pg_tool(tool: str, args: list[str], *, password: str, capture_to: Path | None = None) -> None:
    env = {**os.environ, "PGPASSWORD": password}
    if _DOCKER_CONTAINER:
        command = ["docker", "exec", "-e", f"PGPASSWORD={password}", _DOCKER_CONTAINER, tool, *args]
        if capture_to is not None:
            with open(capture_to, "wb") as handle:
                subprocess.run(command, check=True, stdout=handle, stderr=subprocess.PIPE)
        else:
            subprocess.run(command, check=True, capture_output=True, text=True)
    else:
        command = [tool, *args]
        if capture_to is not None:
            with open(capture_to, "wb") as handle:
                subprocess.run(command, check=True, stdout=handle, stderr=subprocess.PIPE, env=env)
        else:
            subprocess.run(command, check=True, capture_output=True, text=True, env=env)


def _url_for(database: str, *, user: str, host: str, port: int, password: str) -> str:
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"


def main() -> None:
    _raw, user, host, port, password = _base_url()
    # Inside the Docker container (docker-exec mode) the tools connect to Postgres's own
    # local socket; only the direct/real-CI mode needs an explicit host/port.
    conn_args = [] if _DOCKER_CONTAINER else ["-h", host, "-p", str(port)]
    admin_env = {**os.environ, "PGPASSWORD": password}

    for name in (SOURCE_DB, RESTORED_DB):
        try:
            _pg_tool("dropdb", [*conn_args, "-U", user, "--if-exists", name], password=password)
        except subprocess.CalledProcessError:
            pass
    _pg_tool("createdb", [*conn_args, "-U", user, SOURCE_DB], password=password)

    source_url = _url_for(SOURCE_DB, user=user, host=host, port=port, password=password)
    os.environ["AIEB_DATABASE_URL"] = source_url
    from aieb_api import db, models as api_models
    from aieb_api.worker import repository
    from sqlalchemy import select, update

    db.configure(source_url)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "services/api",
                    check=True, capture_output=True, text=True, env={**os.environ, "AIEB_DATABASE_URL": source_url})

    # Seed an abandoned, expired-lease work item exactly as the reconciler tests do.
    with db.session_factory()() as session:
        evaluator = api_models.EvaluatorRevisionRow(code_digest="e" * 64, contract_version="v1")
        session.add(evaluator)
        session.flush()
        task = api_models.TaskRevisionRow(
            slug="restore-drill-task", version="0.1.0", family_id="restore-drill", category="rag",
            source_digest="1" * 64, manifest_digest="d" * 64, evaluator_id=evaluator.id,
            manifest={"schema_version": "aieb.task/v1", "id": "restore-drill-task"},
        )
        entrant = api_models.EntrantRevisionRow(
            slug="restore-drill-agent", version="1.0.0", track="agents", config_digest="agent-digest",
            capabilities=["cpu-fixture-standard-v1"], manifest={"schema_version": "aieb.entrant/v1", "id": "restore-drill-agent"},
        )
        session.add_all([task, entrant])
        session.flush()
        campaign = api_models.CampaignRow(
            name="restore-drill-campaign", state="running",
            draft={"schema_version": "aieb.campaign-draft/v1", "name": "restore-drill-campaign"},
        )
        session.add(campaign)
        session.flush()
        trial = api_models.TrialRow(
            campaign_id=campaign.id, task_revision_id=task.id, entrant_revision_id=entrant.id,
            repetition=1, cell_digest="c" * 64,
        )
        session.add(trial)
        session.flush()
        attempt = api_models.AttemptRow(trial_id=trial.id, number=1, phase="engineering")
        session.add(attempt)
        session.flush()
        work_item = api_models.WorkItemRow(attempt_id=attempt.id, type="engineering", state="ready")
        session.add(work_item)
        session.commit()
        work_item_id = work_item.id

    with db.session_factory()() as session:
        leased = repository.claim_work_item(session, worker_id="pre-backup-worker")
    assert leased is not None
    stale_generation = leased.generation
    # Deliberately set the lease to expire in the FUTURE (30 minutes out), NOT backdated: this
    # is the exact gap-4 hole. An expired lease is fenced by the pre-existing reconciler sweep;
    # a STILL-renewable restored lease is the case nothing fenced until the epoch advance, and
    # a stale worker heartbeating it would keep it renewable forever.
    with db.session_factory()() as session:
        session.execute(
            update(api_models.WorkItemRow).where(api_models.WorkItemRow.id == leased.work_item_id).values(
                lease_expiry=datetime.now(timezone.utc) + timedelta(minutes=30)
            )
        )
        session.commit()
    # A live pre-restore credential rides along in the backup too: the restored snapshot must not
    # keep it usable for the rest of its TTL. It is valid at backup time (asserted), then must be
    # dead at first use after the fence advance (asserted in assertion 2).
    with db.session_factory()() as session:
        pre_restore = repository.issue_attempt_credential(
            session, attempt_id=leased.attempt_id, actor_role="candidate",
            work_item_id=leased.work_item_id, worker_id="pre-backup-worker", lease_generation=stale_generation,
        )
        assert repository.attempt_credential_status(
            session, attempt_id=leased.attempt_id, actor_role="candidate",
        ).valid, "the pre-restore credential must be valid BEFORE the backup"
    print(f"seeded an in-window leased item (work_item={leased.work_item_id}, generation={stale_generation}) plus a live pre-restore credential before backup")

    dump_path = ROOT / ".cache" / "restore-drill" / "backup.dump"
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    # -F c (custom format) to STDOUT, captured to a local file - works identically whether
    # pg_dump runs directly or via `docker exec` (its stdout is relayed either way).
    _pg_tool("pg_dump", [*conn_args, "-U", user, "-F", "c", SOURCE_DB], password=password, capture_to=dump_path)
    print(f"backed up {SOURCE_DB} to {dump_path}")

    # The runbook-bug reproduction the re-review flagged: the operator sets the barrier on the
    # LIVE (pre-restore) DB before restoring, but the backup was taken earlier with the kill
    # switch INACTIVE - so restoring it OVERWRITES the live DB's kill_switch row with the backup's
    # inactive state and the barrier is LOST. Set the live-DB barrier now, then prove the restored
    # DB does NOT inherit it (assertion 1) and that the runbook must re-establish it there.
    with db.session_factory()() as session:
        repository.activate_kill_switch(
            session, activated_by_user_id=None,
            reason="backup/restore drill: pre-restore live-DB barrier, expected to be lost by the restore",
        )
    print(f"set the live-DB kill switch before restoring - the restore must NOT carry it (backup had it inactive)")

    _pg_tool("createdb", [*conn_args, "-U", user, RESTORED_DB], password=password)
    restore_start = time.monotonic()
    if _DOCKER_CONTAINER:
        with open(dump_path, "rb") as handle:
            subprocess.run(
                ["docker", "exec", "-i", "-e", f"PGPASSWORD={password}", _DOCKER_CONTAINER,
                 "pg_restore", "-U", user, "-d", RESTORED_DB],
                check=True, stdin=handle, capture_output=True,
            )
    else:
        with open(dump_path, "rb") as handle:
            subprocess.run(
                ["pg_restore", "-h", host, "-p", str(port), "-U", user, "-d", RESTORED_DB],
                check=True, stdin=handle, capture_output=True, env=admin_env,
            )
    restore_seconds = time.monotonic() - restore_start
    print(f"restored into {RESTORED_DB} in {restore_seconds:.2f}s "
          "(LOCAL PROXY measurement only - not a production RPO/RTO figure)")

    restored_url = _url_for(RESTORED_DB, user=user, host=host, port=port, password=password)
    db.configure(restored_url)

    # Assertion 1: not silently resumed.
    with db.session_factory()() as session:
        row = session.get(api_models.WorkItemRow, work_item_id)
        assert row.state == "leased", f"expected the abandoned lease to survive restore as 'leased', got {row.state!r}"
        assert row.generation == stale_generation, "generation must be unchanged immediately after restore"
        assert row.lease_expiry > datetime.now(timezone.utc), "the restored lease is still WITHIN its window - the gap-4 case"
        assert repository.is_kill_switch_active(session) is False, (
            "the restored DB must have the BACKUP's kill-switch state (inactive), not the live DB's "
            "pre-restore barrier - the restore overwrote the barrier, so it must be re-established AFTER the restore"
        )
        fence_before = repository.current_fence_epoch(session)
    assert fence_before == 0, "the fence epoch itself must survive restore unchanged"
    print(f"[assertion 1] PASS: the lease is still 'leased' at generation {stale_generation}, expiry in the future, fence epoch {fence_before}; the restore LOST the pre-restore live-DB kill-switch barrier (backup was inactive), so the runbook must re-establish it on the restored database")

    # THE OPERATOR POST-RESTORE STEP (gap 4) - now driven through the REAL operator command
    # `scripts/fence_advance.py`, exactly as the restore runbook names it (re-review round 2:
    # the drill previously called repository.advance_fence_epoch() directly, which is why the
    # command's own defects escaped its assertions).
    fence_advance = [sys.executable, str(ROOT / "scripts/fence_advance.py")]
    adv_env = {**os.environ, "AIEB_DATABASE_URL": restored_url}

    # --check while the barrier is MISSING on the restored DB: must fail the pre-flight (exit 3).
    check_missing = subprocess.run([*fence_advance, "--check"], env=adv_env, capture_output=True, text=True)
    assert check_missing.returncode == 3, f"--check must exit 3 while the barrier is missing (got {check_missing.returncode}): {check_missing.stdout}\n{check_missing.stderr}"
    assert "kill switch inactive" in check_missing.stdout, f"--check must report the inactive barrier: {check_missing.stdout}"
    assert "db user " in check_missing.stdout, "--check must report the database identity (current_user)"
    print("[operator step 1] PASS: fence_advance.py --check pre-flight fails (exit 3) while the restored DB has no barrier, and reports the db user")

    # The mutating form must REFUSE outright while the barrier is missing - and change NOTHING.
    refused = subprocess.run([*fence_advance, "--reason", "drill: must be refused", "--by-user", str(uuid.uuid4())], env=adv_env, capture_output=True, text=True)
    assert refused.returncode == 3, f"the advance must be refused while the barrier is missing (got {refused.returncode}): {refused.stdout}\n{refused.stderr}"
    assert "REFUSED" in refused.stdout + refused.stderr
    with db.session_factory()() as session:
        assert repository.current_fence_epoch(session) == fence_before, "a REFUSED advance must leave the epoch UNCHANGED (atomic refusal, no mutation)"
    print(f"[operator step 2] PASS: fence_advance.py refuses to advance while the barrier is missing - exit 3, epoch unchanged at {fence_before}")

    # Re-establish the barrier ON THE RESTORED DATABASE (finding: the restore lost the live-DB
    # barrier; the runbook re-sets it here), then verify it via --check (exit 0) and advance.
    with db.session_factory()() as session:
        repository.activate_kill_switch(
            session, activated_by_user_id=None,
            reason="backup/restore drill: barrier re-established on the restored database",
        )
    check_present = subprocess.run([*fence_advance, "--check"], env=adv_env, capture_output=True, text=True)
    assert check_present.returncode == 0, f"--check must pass once the barrier is re-established (got {check_present.returncode}): {check_present.stdout}\n{check_present.stderr}"
    assert "kill switch ACTIVE" in check_present.stdout, check_present.stdout
    print("[operator step 3] PASS: barrier re-established ON the restored DB; --check pre-flight now passes (exit 0, kill switch ACTIVE)")

    advanced_run = subprocess.run(
        [*fence_advance, "--reason", "backup/restore drill: post-restore fence advance"],
        env=adv_env, capture_output=True, text=True,
    )
    assert advanced_run.returncode == 0, f"the advance under the barrier must succeed (got {advanced_run.returncode}): {advanced_run.stdout}\n{advanced_run.stderr}"
    assert f"fence epoch advanced: {fence_before} -> {fence_before + 1}" in advanced_run.stdout, advanced_run.stdout
    with db.session_factory()() as session:
        advanced_to = repository.current_fence_epoch(session)
    assert advanced_to == fence_before + 1
    print(f"[operator step 4] PASS: fence_advance.py advanced the system fence epoch {fence_before} -> {advanced_to} on the restored database (db user reported)")

    # Assertion 2 (pretend the PRE-RESTORE worker came back and tried to act - BEFORE any
    # reconciliation runs, and while its lease expiry is still in the future). Every fenced
    # touch must fail at first use: heartbeat, a candidate-role credential issuance, and a
    # finalize. The credential path is the concrete form "revoke stale credentials" takes: a
    # pre-restore token (or the ability to mint one) is dead the moment the epoch advances,
    # and the STATUS read agrees (deciding-review finding 2) - never a "valid" status that no
    # live verification would honor.
    with db.session_factory()() as session:
        assert repository.heartbeat(
            session, work_item_id=work_item_id, worker_id="pre-backup-worker", generation=stale_generation,
            lease_seconds=60,
        ) is False, "a pre-restore worker must be fenced out from its very first heartbeat, before reconciliation"
    with db.session_factory()() as session:
        try:
            repository.issue_attempt_credential(
                session, attempt_id=leased.attempt_id, actor_role="candidate",
                work_item_id=work_item_id, worker_id="pre-backup-worker", lease_generation=stale_generation,
            )
        except repository.LeaseFenceError:
            pass
        else:
            raise AssertionError("a pre-restore worker must not be able to issue a credential before reconciliation")
    with db.session_factory()() as session:
        assert repository.verify_attempt_credential(
            session, attempt_id=leased.attempt_id, actor_role="candidate", token=pre_restore.token,
        ) is False, "a pre-restore token must be dead at first use once the fence epoch advanced"
        stale_status = repository.attempt_credential_status(session, attempt_id=leased.attempt_id, actor_role="candidate")
        assert stale_status.valid is False, "status must report a stale-epoch credential as invalid, NOT a valid one that verify would refuse"
        assert stale_status.lease_epoch == 0, "the pre-restore credential row itself is unchanged - only its validity vs the current epoch flipped"
    with db.session_factory()() as session:
        assert repository.finalize(
            session, work_item_id=work_item_id, worker_id="pre-backup-worker", generation=stale_generation,
            attempt_id=leased.attempt_id, terminal_status="pass", done=True,
        ) is False, "a pre-restore worker must not be able to commit results before reconciliation"
    print("[assertion 2] PASS: pre-restore worker fenced from heartbeat/issue/finalize at FIRST touch, pre-restore token dead, status agrees - all before any reconciliation")

    # Assertion 3 (spec section 40's "reconcile leases ... quarantine orphan allocations"):
    # reconciliation against the RESTORED database recognizes and quarantines the orphaned
    # lease - here via the STALE-EPOCH sweep (its expiry is still in the future, so only the
    # epoch condition could have claimed it) - bumping its generation, proving the SAME
    # mechanism that already protects a live database also protects state that came through
    # backup/restore.
    from aieb_api.worker.reconciler import reconcile_once

    work_root = ROOT / ".cache" / "restore-drill" / "work-root"
    work_root.mkdir(parents=True, exist_ok=True)
    summary = reconcile_once(db.session_factory(), work_root)
    assert summary.replaced == 1, f"expected reconciliation to replace the one orphaned lease, got {summary}"
    print(f"[assertion 3] PASS: reconciliation against the restored database quarantined the orphaned lease via the stale-epoch sweep ({summary})")

    # Assertion 4 (the "revoke stale credentials" equivalent): the pre-restore worker's identity
    # (its lease generation) was NEVER valid against the post-restore state - it is fenced out
    # the first and only time it is tried, not merely eventually.
    with db.session_factory()() as session:
        stale_retry_finalized = repository.finalize(
            session, work_item_id=work_item_id, worker_id="pre-backup-worker", generation=stale_generation,
            attempt_id=leased.attempt_id, terminal_status="pass", done=True,
        )
    assert stale_retry_finalized is False, "a stale pre-restore generation must be fenced out after reconciliation, not accepted"
    print("[assertion 4] PASS: the stale pre-restore worker/generation is fenced out and cannot commit results")

    # Resume (the runbook's final step): clear the barrier on the restored DB and confirm it is
    # really off, then prove a FRESH worker can claim and heartbeat under the advanced epoch.
    with db.session_factory()() as session:
        repository.deactivate_kill_switch(session)
    with db.session_factory()() as session:
        assert repository.is_kill_switch_active(session) is False, "resume requires the kill switch to be OFF"
    print("[resume] PASS: barrier cleared on the restored database (kill switch OFF)")

    # Assertion 5 (the advance must not break the system): a FRESH worker can still claim the
    # replacement work item, heartbeat it, and is NOT itself fenced.
    with db.session_factory()() as session:
        fresh = repository.claim_work_item(session, worker_id="post-restore-worker")
        assert fresh is not None, "a fresh worker must still be able to claim work after the fence advance"
        assert fresh.lease_epoch == advanced_to, f"a fresh claim must be stamped with the CURRENT fence epoch ({advanced_to}), got {fresh.lease_epoch}"
        assert repository.heartbeat(
            session, work_item_id=fresh.work_item_id, worker_id="post-restore-worker", generation=fresh.generation,
            lease_seconds=60,
        ) is True, "a fresh post-restore worker must be able to heartbeat"
    print("[assertion 5] PASS: a fresh post-restore worker claims and heartbeats under the advanced fence epoch")

    print("Backup/restore drill: ALL FIVE assertions passed (operator controls exercised through scripts/fence_advance.py).")


if __name__ == "__main__":
    main()
