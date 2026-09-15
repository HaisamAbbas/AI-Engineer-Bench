"""The public website (apps/web) is a browser SPA on its own origin; without
CORS, a browser's own preflight blocks every request before it reaches a
route at all - curl/TestClient calls that don't send an Origin header never
exercise this, which is exactly why the gap went unnoticed. These tests
send a real Origin header and check the response the browser would actually
see, matching what a real preflight/actual request enforces. No database is
needed: OPTIONS preflight requests are answered by CORSMiddleware itself,
before any route's session dependency ever runs.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for src in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src"):
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

import unittest

os.environ.setdefault("AIEB_ENV", "test")
os.environ.setdefault("AIEB_TEST_SHARED_SECRET", "test-only-shared-secret-not-a-real-credential")

from fastapi.testclient import TestClient  # noqa: E402

from aieb_api.app import create_app  # noqa: E402

DATABASE_URL = os.environ.get("AIEB_DATABASE_URL")


class CorsTests(unittest.TestCase):
    """Preflight (OPTIONS) needs no database - CORSMiddleware answers it
    before any route's session dependency runs - but a real GET does, so
    that one test is skipped without a real test Postgres instance, same as
    every other route-level test in this project."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_app()

    def setUp(self) -> None:
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))

    def test_local_website_origin_receives_cors_headers_on_preflight(self) -> None:
        response = self.client.options(
            "/v1/releases", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("access-control-allow-origin"), "http://localhost:5173")

    @unittest.skipUnless(DATABASE_URL, "AIEB_DATABASE_URL is not set; the actual (non-preflight) request needs a real database")
    def test_local_website_origin_receives_cors_headers_on_the_actual_request(self) -> None:
        response = self.client.get("/v1/releases", headers={"Origin": "http://localhost:5173"})
        self.assertEqual(response.headers.get("access-control-allow-origin"), "http://localhost:5173")

    def test_an_unlisted_origin_is_not_granted_cors_access(self) -> None:
        """CORS must be an explicit allowlist, not a blanket allow-everything -
        a request from a page the operator never intended to authorize must
        not receive an Access-Control-Allow-Origin header for it."""
        response = self.client.options(
            "/v1/releases", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"}
        )
        self.assertIsNone(response.headers.get("access-control-allow-origin"))


if __name__ == "__main__":
    unittest.main()
