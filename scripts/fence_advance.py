"""ENG-020 gap 4: the OPERATOR command that advances the system fence epoch after a database
restore - the only way to do so, and the exact command the restore runbook names.

Authentication: the caller must present the control-plane OPERATOR database role. The command
fail-closes without AIEB_DATABASE_URL (exactly like the API service), and performs every fence
mutation inside that role's session; no password or token is ever written anywhere. This is the
same operator boundary every existing control-plane action in this repository uses (the kill
switch, reconciliation, the drills) - there is no separate token infrastructure, and inventing
one would be new auth surface rather than honoring the real one.

Operational barrier (deciding-review finding 3): advancing the epoch is structurally blocked
unless the global KILL SWITCH is ACTIVE, so no NEW dispatch can be in flight while the fence
moves. In-flight work that is still connected is handled by the fence row's FOR SHARE /
FOR UPDATE serialization in `repository` (finding 1) - but the runbook still requires workers
and the reconciler to be stopped or network-isolated first, so the advance never races even a
single already-running worker's transaction. Verifies the barrier and re-checks it inside the
command right before mutating.

Usage:
    python scripts/fence_advance.py --check
    python scripts/fence_advance.py --reason "restore drill 2026-09-20" [--by-user <uuid>]

`--check` is read-only: prints the current fence epoch and the kill-switch state, exits non-zero
if the barrier is missing (so it doubles as a pre-flight gate). The mutating form refuses to run
unless the kill switch is active, prints the old/new epochs, and re-runs the same barrier check
inside the advance transaction.
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


def _check(session_factory) -> dict[str, object]:
    from aieb_api.worker import repository

    with session_factory() as session:
        epoch = repository.current_fence_epoch(session)
        kill_switch_active = repository.is_kill_switch_active(session)
    return {"fence_epoch": epoch, "kill_switch_active": kill_switch_active}


def _advance(session_factory, *, reason: str, by_user: uuid.UUID | None) -> dict[str, object]:
    from aieb_api.worker import repository

    with session_factory() as session:
        if not repository.is_kill_switch_active(session):
            print(
                "fence-advance REFUSED: the kill switch is not active. The restore runbook requires it "
                "to be set (no new dispatch) before the fence may advance.",
                file=sys.stderr,
            )
            return {"advanced": False, "refused": "kill switch not active"}
        before = repository.current_fence_epoch(session)
        after = repository.advance_fence_epoch(session, reason=reason, activated_by_user_id=by_user)
        # Re-check the barrier from inside the post-advance view so the printed state is the
        # state the operator actually acted on.
        still_active = repository.is_kill_switch_active(session)
    if not still_active:
        print("fence-advance REFUSED: the kill switch state changed mid-command; nothing was advanced.", file=sys.stderr)
        return {"advanced": False, "refused": "kill switch changed mid-command"}
    if after != before + 1:
        print(f"fence-advance FAILED: expected epoch {before} -> {before + 1}, observed {after}", file=sys.stderr)
        raise SystemExit(1)
    return {"advanced": True, "fence_epoch_before": before, "fence_epoch_after": after}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fence-advance",
        description="ENG-020 operator command: advance the system fence epoch after a database restore.",
    )
    parser.add_argument("--check", action="store_true", help="read-only: print fence epoch + kill-switch state, exit non-zero if the barrier is missing")
    parser.add_argument("--reason", help="audit reason for the advance (required to advance)")
    parser.add_argument("--by-user", type=uuid.UUID, default=None, help="operator user id recorded as the actor")
    args = parser.parse_args(argv)

    session_factory = _session_factory()
    state = _check(session_factory)
    print(f"fence epoch {state['fence_epoch']}; kill switch {'ACTIVE' if state['kill_switch_active'] else 'inactive'}")
    if args.check:
        return 0 if state["kill_switch_active"] else 3
    if not args.reason:
        parser.error("--reason is required (the advance is an audited operator action)")
    result = _advance(session_factory, reason=args.reason, by_user=args.by_user)
    if not result["advanced"]:
        return 3
    print(f"fence epoch advanced: {result['fence_epoch_before']} -> {result['fence_epoch_after']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())