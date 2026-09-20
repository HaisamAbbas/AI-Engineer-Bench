"""ENG-020 gap 4: the OPERATOR command that advances the system fence epoch after a database
restore - the only way to do so, and the exact command the restore runbook names.

Authorization (honest statement, re-review round 2): there is NO dedicated least-privilege
operator database role, grants, or `current_user` validation yet. Authorization relies solely on
possession of a WRITE-CAPABLE database credential - whatever `AIEB_DATABASE_URL` authenticates
(the command fail-closes without it, exactly like the API service), and every fence mutation
happens inside that session. The command reports `current_user` so the database identity that
actually performed the advance is VISIBLE; `--by-user` is an OPTIONAL, INFORMATIONAL actor label
recorded on the audit row for human readability - it is user-asserted, NOT authenticated
attribution and must reference an existing `users` row (the FK enforces that), so it is never
taken as proof of who really ran the command. A dedicated operator role with real grants is
recorded as a disclosed gap in the runbook/evidence rather than invented late.

Operational barrier (deciding-review findings 3 + round 2): advancing the epoch REQUIRES the
global KILL SWITCH to be ACTIVE, and that check is ATOMIC with the mutation - one transaction
locks the kill_switch row FOR UPDATE, confirms it is active (raising otherwise, epoch untouched),
bumps the fence epoch under its own lock, and commits once. A concurrent deactivation either
completes first (the command then REFUSES with no epoch change) or waits until the advance has
fully committed. Every fenced lease/credential operation holds the fence row FOR SHARE for its
whole transaction (finding 1), so the advance serializes against even a single already-running
worker's transaction; the runbook STILL requires workers/reconciler to be stopped or
network-isolated first as defense-in-depth.

The command ALSO requires the barrier to be re-established ON THE RESTORED DATABASE after the
restore: a restore overwrites the live DB's kill_switch row with the backup's (usually inactive)
state, so a barrier set before restoring is LOST. The runbook therefore restores first, then
re-establishes the barrier on the restored DB, then runs this command.

Usage:
    python scripts/fence_advance.py --check
    python scripts/fence_advance.py --reason "restore drill 2026-09-20" [--by-user <uuid>]

`--check` is read-only: prints the current fence epoch, the kill-switch state, and the session's
`current_user`, and exits non-zero (3) if the barrier is missing - a pre-flight gate. The mutating
form refuses to run unless the kill switch is active and prints the old/new epochs plus the DB
identity that advanced them.

Rounds 2 + 3 summary: (a) the barrier check is ATOMIC with the epoch bump (one transaction),
(b) a command reports the (previous, new) transition read under the SAME locks - it never reads
the epoch up front or reports FAILED after its own mutation committed, and (c) `--check` performs
no writes at all (it reads the fence row directly rather than triggering the repository's
missing-row self-heal), so an operator pre-flight never mutates the database.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for src in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src"):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _session_factory():
    from aieb_api import db
    from aieb_api.db import DatabaseNotConfigured

    try:
        db.configure()
    except DatabaseNotConfigured as exc:
        print(f"fence-advance FAILED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    return db.session_factory()


def _current_user(session) -> str:
    from sqlalchemy import text

    return str(session.execute(text("select current_user")).scalar_one())


def _check(session_factory) -> dict[str, object]:
    from aieb_api import models as api_models
    from aieb_api.worker import repository

    with session_factory() as session:
        # Read the epoch DIRECTLY so `--check` performs no writes at all: the repository's
        # current_fence_epoch() self-heals a missing singleton row with an INSERT (rolled back on
        # close, but a write during a pre-flight check is still a write).
        fence = session.get(api_models.SystemFenceRow, 1)
        epoch = 0 if fence is None else fence.lease_fence_epoch
        kill_switch_active = repository.is_kill_switch_active(session)
        db_user = _current_user(session)
    return {"fence_epoch": epoch, "kill_switch_active": kill_switch_active, "db_user": db_user}


def _advance(session_factory, *, reason: str, by_user: uuid.UUID | None) -> dict[str, object]:
    from aieb_api.worker import repository

    with session_factory() as session:
        try:
            before, after = repository.advance_fence_epoch_with_barrier(
                session, reason=reason, activated_by_user_id=by_user,
            )
        except repository.KillSwitchBarrierError as exc:
            print(f"fence-advance REFUSED: {exc}", file=sys.stderr)
            return {"advanced": False, "refused": "kill switch not active"}
        except ValueError as exc:
            print(f"fence-advance REFUSED: {exc}", file=sys.stderr)
            return {"advanced": False, "refused": str(exc)}
        db_user = _current_user(session)
    return {"advanced": True, "fence_epoch_before": before, "fence_epoch_after": after, "db_user": db_user}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fence-advance",
        description="ENG-020 operator command: advance the system fence epoch after a database restore.",
    )
    parser.add_argument("--check", action="store_true", help="read-only: print fence epoch + kill-switch state + db user, exit non-zero if the barrier is missing")
    parser.add_argument("--reason", help="audit reason for the advance (required to advance)")
    parser.add_argument(
        "--by-user",
        type=uuid.UUID,
        default=None,
        help="optional INFORMATIONAL actor label recorded on the audit row (must reference an existing users row); "
             "NOT authentication - the DB identity used is reported as current_user",
    )
    args = parser.parse_args(argv)

    session_factory = _session_factory()
    state = _check(session_factory)
    print(
        f"fence epoch {state['fence_epoch']}; kill switch "
        f"{'ACTIVE' if state['kill_switch_active'] else 'inactive'}; db user {state['db_user']}"
    )
    if args.check:
        return 0 if state["kill_switch_active"] else 3
    if not args.reason:
        parser.error("--reason is required (the advance is an audited operator action)")
    result = _advance(session_factory, reason=args.reason, by_user=args.by_user)
    if not result["advanced"]:
        return 3
    print(
        f"fence epoch advanced: {result['fence_epoch_before']} -> {result['fence_epoch_after']} "
        f"(db user {result['db_user']})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())