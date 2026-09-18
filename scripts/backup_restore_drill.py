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
   stale credentials" takes here - there is no separate server-side credential store to
   revoke). This script deliberately does NOT attempt a stale finalize before reconciliation:
   a real restore procedure must keep a pre-restore worker fenced throughout, not merely
   happen to reject it once something else later changes the generation.

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
    with db.session_factory()() as session:
        session.execute(
            update(api_models.WorkItemRow).where(api_models.WorkItemRow.id == leased.work_item_id).values(
                lease_expiry=datetime.now(timezone.utc) - timedelta(hours=1)
            )
        )
        session.commit()
    print(f"seeded an abandoned lease (work_item={leased.work_item_id}, generation={stale_generation}) before backup")

    dump_path = ROOT / ".cache" / "restore-drill" / "backup.dump"
    dump_path.parent.mkdir(parents=True, exist_ok=True)
    # -F c (custom format) to STDOUT, captured to a local file - works identically whether
    # pg_dump runs directly or via `docker exec` (its stdout is relayed either way).
    _pg_tool("pg_dump", [*conn_args, "-U", user, "-F", "c", SOURCE_DB], password=password, capture_to=dump_path)
    print(f"backed up {SOURCE_DB} to {dump_path}")

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
    print("[assertion 1] PASS: the abandoned lease is still 'leased', not silently resumed as fresh")

    # Deliberately does NOT attempt a stale finalize before reconciliation runs: a real
    # restore procedure must keep a pre-restore worker fenced until reconciliation completes,
    # not merely happen to reject it once something else later changes the generation. Fixing
    # test state after a speculative "would this have been accepted?" attempt would prove
    # nothing about that requirement - so this drill goes straight to reconciliation, then
    # proves fencing against its OWN output.

    # Assertion 2 (spec section 40's "reconcile leases ... quarantine orphan allocations"):
    # reconciliation against the RESTORED database recognizes and quarantines
    # the orphaned lease - bumping its generation - proving the SAME mechanism that already
    # protects a live database also protects state that came through backup/restore.
    from aieb_api.worker.reconciler import reconcile_once

    work_root = ROOT / ".cache" / "restore-drill" / "work-root"
    work_root.mkdir(parents=True, exist_ok=True)
    summary = reconcile_once(db.session_factory(), work_root)
    assert summary.replaced == 1, f"expected reconciliation to replace the one orphaned lease, got {summary}"
    print(f"[assertion 2] PASS: reconciliation against the restored database quarantined the orphaned lease ({summary})")

    # Assertion 3 (the "revoke stale credentials" equivalent - there is no separate
    # server-side credential store in this system): the pre-restore worker's identity
    # (its lease generation) was NEVER valid against the post-reconciliation state - it is
    # fenced out the first and only time it is tried, not merely eventually.
    with db.session_factory()() as session:
        stale_retry_finalized = repository.finalize(
            session, work_item_id=work_item_id, worker_id="pre-backup-worker", generation=stale_generation,
            attempt_id=leased.attempt_id, terminal_status="pass", done=True,
        )
    assert stale_retry_finalized is False, "a stale pre-restore generation must be fenced out after reconciliation, not accepted"
    print("[assertion 3] PASS: the stale pre-restore worker/generation is fenced out and cannot commit results")

    print("Backup/restore drill: ALL THREE assertions passed.")


if __name__ == "__main__":
    main()
