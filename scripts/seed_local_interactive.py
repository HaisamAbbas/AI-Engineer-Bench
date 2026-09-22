"""Seed a small local fixture and mint dev-only auth tokens for interactive use.

Reuses the exact same seeding helpers as scripts/check_admin_browser.py
(tests/test_api_service.py's ApiServiceTests fixture methods), but does NOT
launch or tear down any servers - it only seeds data and writes tokens, so
the API/web servers can be started separately and left running.

Usage: AIEB_DATABASE_URL=... python scripts/seed_local_interactive.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    if not os.environ.get("AIEB_DATABASE_URL"):
        raise SystemExit("Set AIEB_DATABASE_URL to a disposable PostgreSQL database")
    os.environ["AIEB_ENV"] = "test"
    os.environ.setdefault("AIEB_TEST_SHARED_SECRET", "local-interactive-dev-secret-not-for-prod")

    import jwt
    from aieb_api import db, models
    from sqlalchemy import func, select

    from tests import test_api_service as fixtures

    db.configure(os.environ["AIEB_DATABASE_URL"])
    with db.session_factory()() as session:
        already_seeded = session.scalar(select(func.count()).select_from(models.CampaignRow)) or \
            session.scalar(select(func.count()).select_from(models.TaskRevisionRow))

    seed = fixtures.ApiServiceTests()
    if not already_seeded:
        seed._seed_task()
        seed._seed_entrant()
    fixtures._grant_roles("local-viewer", ("reviewer",))
    fixtures._grant_roles("local-operator", ("operator",))
    fixtures._grant_roles("local-admin", ("administrator",))

    tokens = {
        subject: jwt.encode(
            {"sub": subject, "iss": "test", "exp": int(time.time()) + 3600 * 8},
            os.environ["AIEB_TEST_SHARED_SECRET"],
            algorithm="HS256",
        )
        for subject in ("local-viewer", "local-operator", "local-admin")
    }
    out_dir = ROOT / ".cache" / "local-interactive"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tokens.json").write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    print(json.dumps(tokens, indent=2))


if __name__ == "__main__":
    main()
