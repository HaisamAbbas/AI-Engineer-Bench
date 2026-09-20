"""ENG-020 gap 5: the OPERATOR CLI for global kill-switch control.

This is the operator-facing command surface for the global kill switch -
the dedicated executable the runbooks name (see
docs/implementation/evidence/ENG-020/runbooks.md: "Activate the kill switch"
calls `repository.activate_kill_switch(...)` directly; this CLI wraps that
repository function with an executable command so the runbook's operator
barrier steps are a single command, not a code edit).

Authorization (honest statement, mirrors scripts/fence_advance.py): there is
NO dedicated least-privilege operator database role, grants, or
`current_user` validation yet. Authorization relies solely on possession of
a WRITE-CAPABLE database credential - whatever `AIEB_DATABASE_URL`
authenticates. The command reports `current_user` so the identity that
actually performed the action is visible. `--by-user` is accepted only by
`activate`, where it is persisted in the activation row after the supplied
user ID is checked. It is an operator-provided audit label, NOT authenticated
attribution. Deactivation has no actor field in the current control record,
so it does not pretend to accept or audit that label. A dedicated operator
role with real grants is a disclosed gap in the runbook, not claimed
capability.

Usage:
    python scripts/kill_switch.py --check
    python scripts/kill_switch.py activate --reason "provider outage" [--by-user <uuid>]
    python scripts/kill_switch.py deactivate

`--check` is read-only: prints the current kill-switch state and the
session's `current_user`, exits 0 if the switch is off (safe to resume
dispatch) or 3 if it is active (dispatch is blocked). The mutating forms
refuse to advance the state without the required arguments and print the
before/after state plus the DB identity that performed the action.

Atomic activation (mirrors scripts/fence_advance.py round-3 fix): the
repository `activate_kill_switch` locks the singleton row FOR UPDATE and
raises ValueError if already active - the check and the transition are
ONE transaction, so concurrent CLI invocations are serialized by PostgreSQL
rather than by application-level timing. Deactivation is similarly atomic:
the repository function locks FOR UPDATE and returns False (no-op) if
already inactive.
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
        print(f"kill-switch FAILED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    return db.session_factory()


def _current_user(session) -> str:
    from sqlalchemy import text

    return str(session.execute(text("select current_user")).scalar_one())


def _validate_by_user(session, by_user: uuid.UUID | None) -> None:
    """Fail fast with a clean message if --by-user doesn't reference a real user,
    rather than letting the FK constraint surface as an unhandled IntegrityError."""
    if by_user is None:
        return
    from aieb_api.models import User

    if session.get(User, by_user) is None:
        print(f"kill-switch REFUSED: --by-user {by_user} does not reference an existing users row", file=sys.stderr)
        raise SystemExit(2)


def _check(session_factory) -> dict[str, object]:
    from aieb_api import models as api_models

    with session_factory() as session:
        row = session.get(api_models.KillSwitchRow, 1)
        db_user = _current_user(session)
    return {
        # This direct read keeps --check write-free. A missing migration-seeded
        # singleton is an unknown safety state, never proof that dispatch is safe.
        "active": None if row is None else row.active,
        "kill_switch_missing": row is None,
        "reason": row.reason if row is not None else None,
        "db_user": db_user,
    }


def _activate(session_factory, *, reason: str, by_user: uuid.UUID | None) -> int:
    from aieb_api.worker import repository

    if not reason or not reason.strip():
        print("kill-switch REFUSED: --reason must be a non-empty string", file=sys.stderr)
        return 2

    with session_factory() as session:
        _validate_by_user(session, by_user)
        # activate_kill_switch locks the singleton row FOR UPDATE and raises
        # ValueError if already active - the check and transition are atomic
        # under that lock, so concurrent CLI invocations are serialized by
        # PostgreSQL rather than by application-level timing.
        try:
            teardown_requested = repository.activate_kill_switch(
                session, activated_by_user_id=by_user, reason=reason.strip(),
            )
        except ValueError as exc:
            db_user = _current_user(session)
            print(f"kill-switch REFUSED: {exc}; db user {db_user}", file=sys.stderr)
            session.rollback()
            return 3
        except RuntimeError as exc:
            print(f"kill-switch FAILED: {exc}", file=sys.stderr)
            session.rollback()
            return 2
        db_user = _current_user(session)
    print(
        f"kill switch activated (db user {db_user}); "
        f"campaigns teardown requested: {teardown_requested}"
    )
    return 0


def _deactivate(session_factory, *, by_user: uuid.UUID | None) -> int:
    from aieb_api.worker import repository

    with session_factory() as session:
        _validate_by_user(session, by_user)
        # deactivate_kill_switch locks the singleton row FOR UPDATE and
        # handles the "already inactive" case atomically (returns False),
        # so there is no separate unlocked pre-check.
        try:
            repository.deactivate_kill_switch(session)
        except RuntimeError as exc:
            print(f"kill-switch FAILED: {exc}", file=sys.stderr)
            session.rollback()
            return 2
        db_user = _current_user(session)
    print(f"kill switch deactivated (db user {db_user})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kill-switch",
        description="ENG-020 operator command: control the global kill switch.",
    )
    parser.add_argument("--check", action="store_true", help="read-only: print kill-switch state + db user; exit 3 if active, 0 if inactive")
    sub = parser.add_subparsers(dest="command")

    activate_parser = sub.add_parser("activate", help="activate the global kill switch (stops all new dispatch)")
    activate_parser.add_argument("--reason", required=True, help="audit reason for activating the kill switch")
    activate_parser.add_argument(
        "--by-user", type=uuid.UUID, default=None,
        help="optional operator-provided activation label (must reference an existing users row; NOT authentication)",
    )
    sub.add_parser("deactivate", help="deactivate the global kill switch (resumes new dispatch; does NOT resume cancelled campaigns)")

    args = parser.parse_args(argv)

    session_factory = _session_factory()

    if args.check:
        state = _check(session_factory)
        if state["kill_switch_missing"]:
            print(f"kill switch UNKNOWN (singleton row missing); db user {state['db_user']}")
            return 3
        reason_label = f"; reason: {state['reason']}" if state["active"] and state["reason"] else ""
        print(
            f"kill switch {'ACTIVE' if state['active'] else 'inactive'}{reason_label}; "
            f"db user {state['db_user']}"
        )
        return 0 if not state["active"] else 3

    if args.command == "activate":
        return _activate(session_factory, reason=args.reason, by_user=args.by_user)

    if args.command == "deactivate":
        return _deactivate(session_factory, by_user=None)

    parser.error("use --check, or 'activate'/'deactivate'")


if __name__ == "__main__":
    sys.exit(main())
