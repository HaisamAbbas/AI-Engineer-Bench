"""ENG-014 migration compatibility: upgrade -> downgrade -> upgrade against real PostgreSQL.

Requires AIEB_DATABASE_URL; skipped (not faked against sqlite) when unset.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "services/api"
DATABASE_URL = os.environ.get("AIEB_DATABASE_URL")


def _alembic(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["AIEB_DATABASE_URL"] = DATABASE_URL or ""
    python = ROOT / ".venv/Scripts/python.exe"
    return subprocess.run(
        [str(python), "-m", "alembic", *args], cwd=API_DIR, env=env, capture_output=True, text=True,
    )


@unittest.skipUnless(DATABASE_URL, "AIEB_DATABASE_URL is not set; ENG-014 real-Postgres tests are blocked")
class MigrationCompatibilityTests(unittest.TestCase):
    def test_upgrade_downgrade_upgrade_round_trip(self) -> None:
        base = _alembic("downgrade", "base")
        self.assertEqual(base.returncode, 0, base.stderr)

        up_first = _alembic("upgrade", "head")
        self.assertEqual(up_first.returncode, 0, up_first.stderr)
        self.assertIn("upgrade", up_first.stderr.lower())

        down_one = _alembic("downgrade", "-1")
        self.assertEqual(down_one.returncode, 0, down_one.stderr)
        self.assertIn("downgrade", down_one.stderr.lower())

        up_again = _alembic("upgrade", "head")
        self.assertEqual(up_again.returncode, 0, up_again.stderr)

        current = _alembic("current")
        self.assertEqual(current.returncode, 0, current.stderr)
        self.assertIn("(head)", current.stdout)


if __name__ == "__main__":
    unittest.main()
