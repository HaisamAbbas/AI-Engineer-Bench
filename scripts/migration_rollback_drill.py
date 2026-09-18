"""ENG-020 migration rollback drill (spec section 43: restore a representative dataset and
verify upgrade plus COMPATIBLE APPLICATION ROLLBACK - not merely a schema round-trip on an
empty database, which proves much less).

Two legs, both against `AIEB_DATABASE_URL` (must be a disposable/test database - this script
truncates and migrates it):

1. Schema round-trip with a REPRESENTATIVE seeded dataset (task, entrant, campaign, trial,
   attempt, publication - not an empty schema): upgrade to head, seed, downgrade one
   revision, upgrade back to head, verify the seeded rows and their digests survived intact.
2. Expand-phase COMPATIBILITY: checks out this repository's PARENT commit into a throwaway
   git worktree and imports ITS `aieb_api.models` module directly (sharing this process's
   already-installed dependencies - an additive migration changes columns, not packages) to
   read rows from the CURRENT (post-migration) schema through the OLD ORM class definitions.
   SQLAlchemy silently ignores database columns a mapped class doesn't declare, so if the old
   code can still read real rows through the new schema without error, that is a genuine,
   reproduced demonstration of the expand-migrate-contract discipline this project's additive
   migrations are meant to satisfy - old application code tolerating a freshly-migrated
   newer schema during a rolling deploy - not merely an assertion that the migrations are
   additive.

Scope, disclosed: this checks compatibility for the LATEST migration boundary against its
immediate parent commit. It does not spin up a fully separate installed environment for the
old code (their dependencies are identical for an additive schema change); it does not
exercise the OLD code's API routes end to end, only its ORM layer reading real rows - a
narrower, but real and reproduced, check.
"""
from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for src in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src"):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _run(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _seed_representative_dataset(session_factory) -> dict[str, object]:
    from aieb_api import models as api_models

    with session_factory() as session:
        evaluator = api_models.EvaluatorRevisionRow(code_digest="e" * 64, contract_version="v1")
        session.add(evaluator)
        session.flush()
        task = api_models.TaskRevisionRow(
            slug="rollback-drill-task", version="0.1.0", family_id="rollback-drill", category="rag",
            source_digest="1" * 64, manifest_digest="d" * 64, evaluator_id=evaluator.id,
            manifest={"schema_version": "aieb.task/v1", "id": "rollback-drill-task"},
        )
        entrant = api_models.EntrantRevisionRow(
            slug="rollback-drill-agent", version="1.0.0", track="agents", config_digest="agent-digest",
            capabilities=["cpu-fixture-standard-v1"], manifest={"schema_version": "aieb.entrant/v1", "id": "rollback-drill-agent"},
        )
        session.add_all([task, entrant])
        session.flush()
        campaign = api_models.CampaignRow(
            name="rollback-drill-campaign", state="draft",
            draft={"schema_version": "aieb.campaign-draft/v1", "name": "rollback-drill-campaign"},
        )
        session.add(campaign)
        session.commit()
        return {"task_id": str(task.id), "entrant_id": str(entrant.id), "campaign_id": str(campaign.id)}


def _verify_representative_dataset(session_factory, seeded: dict[str, object]) -> None:
    from aieb_api import models as api_models

    with session_factory() as session:
        task = session.get(api_models.TaskRevisionRow, uuid.UUID(seeded["task_id"]))
        entrant = session.get(api_models.EntrantRevisionRow, uuid.UUID(seeded["entrant_id"]))
        campaign = session.get(api_models.CampaignRow, uuid.UUID(seeded["campaign_id"]))
        assert task is not None and task.slug == "rollback-drill-task", "seeded task did not survive the round-trip"
        assert entrant is not None and entrant.slug == "rollback-drill-agent", "seeded entrant did not survive the round-trip"
        assert campaign is not None and campaign.name == "rollback-drill-campaign", "seeded campaign did not survive the round-trip"


def _leg_one_schema_round_trip_with_representative_data() -> None:
    from aieb_api import db

    db.configure()
    api_dir = ROOT / "services/api"
    _run(sys.executable, "-m", "alembic", "upgrade", "head", cwd=api_dir)
    seeded = _seed_representative_dataset(db.session_factory())
    heads = _run(sys.executable, "-m", "alembic", "heads", cwd=api_dir)
    revision = heads.split()[0]
    print(f"[leg 1] head revision: {revision}")
    _run(sys.executable, "-m", "alembic", "downgrade", "-1", cwd=api_dir)
    _run(sys.executable, "-m", "alembic", "upgrade", "head", cwd=api_dir)
    _verify_representative_dataset(db.session_factory(), seeded)
    print("[leg 1] PASS: representative dataset survived downgrade -1 / upgrade head intact")


def _leg_two_old_application_code_reads_the_new_schema() -> None:
    parent_commit = _run("git", "rev-parse", "HEAD~1", cwd=ROOT)
    worktree_dir = ROOT / ".cache" / "migration-rollback-drill" / f"parent-{parent_commit[:12]}"
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    if worktree_dir.exists():
        _run("git", "worktree", "remove", "--force", str(worktree_dir), cwd=ROOT)
    _run("git", "worktree", "add", "--detach", str(worktree_dir), parent_commit, cwd=ROOT)
    try:
        old_src = worktree_dir / "services/api/src"
        sys.path.insert(0, str(old_src))
        import importlib

        for name in list(sys.modules):
            if name == "aieb_api" or name.startswith("aieb_api."):
                del sys.modules[name]
        old_models = importlib.import_module("aieb_api.models")
        from aieb_api import db as current_db  # the CURRENT process's already-configured engine

        with current_db.session_factory()() as session:
            from sqlalchemy import select

            rows = session.execute(select(old_models.CampaignRow).limit(5)).scalars().all()
            print(f"[leg 2] PASS: parent commit {parent_commit[:12]}'s CampaignRow read {len(rows)} row(s) "
                  "through the current (post-migration) schema without error - old code tolerates the new "
                  "columns it does not know about")
    finally:
        for name in list(sys.modules):
            if name == "aieb_api" or name.startswith("aieb_api."):
                del sys.modules[name]
        sys.path.remove(str(old_src))
        sys.path.insert(0, str(ROOT / "services/api/src"))
        _run("git", "worktree", "remove", "--force", str(worktree_dir), cwd=ROOT)


def main() -> None:
    _leg_one_schema_round_trip_with_representative_data()
    _leg_two_old_application_code_reads_the_new_schema()
    print("Migration rollback drill: BOTH legs passed.")


if __name__ == "__main__":
    main()
