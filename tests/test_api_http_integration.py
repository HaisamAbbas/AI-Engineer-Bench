"""Real socket-level HTTP and CORS checks against the running ASGI service.

Requires AIEB_DATABASE_URL and an upgraded disposable PostgreSQL schema. This
complements route-level TestClient tests by exercising uvicorn, TCP, response
headers, and browser preflight behavior over actual HTTP.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT / "services/api/src", ROOT / "packages/aieb-core/src"):
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))

DATABASE_URL = os.environ.get("AIEB_DATABASE_URL")


@unittest.skipUnless(DATABASE_URL, "AIEB_DATABASE_URL is not set; real HTTP integration requires PostgreSQL")
class ApiHttpIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        from aieb_api import auth, db
        from aieb_api.app import create_app
        import uvicorn

        db.configure(DATABASE_URL)
        auth._configured = False
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            self.port = available.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(
            create_app(), host="127.0.0.1", port=self.port, log_level="error", access_log=False,
        ))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.port}"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=0.2)
                connection.request("GET", "/openapi.json")
                response = connection.getresponse()
                response.read()
            except OSError:
                connection.close()
                time.sleep(0.05)
                continue
            connection.close()
            if response.status == 200:
                break
        else:
            self.fail("uvicorn did not accept HTTP connections")

    def tearDown(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.assertFalse(self.thread.is_alive(), "uvicorn failed to shut down")

    def test_real_http_preflight_and_public_json_routes(self) -> None:
        request = Request(
            f"{self.base_url}/v1/methodology",
            method="OPTIONS",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        with urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get("access-control-allow-origin"), "http://localhost:5173")

        request = Request(f"{self.base_url}/v1/methodology", headers={"Origin": "http://localhost:5173"})
        with urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get("access-control-allow-origin"), "http://localhost:5173")
            self.assertTrue(response.headers.get("x-request-id"))
            self.assertIsInstance(json.loads(response.read()), list)

        blocked = Request(f"{self.base_url}/v1/methodology", headers={"Origin": "https://unlisted.invalid"})
        with urlopen(blocked, timeout=5) as response:
            self.assertNotEqual(response.headers.get("access-control-allow-origin"), "https://unlisted.invalid")


if __name__ == "__main__":
    unittest.main()
