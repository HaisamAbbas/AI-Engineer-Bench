from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


STATE = Path("/tmp/application-state.json")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        requests = 0
        if STATE.exists():
            requests = json.loads(STATE.read_text(encoding="utf-8"))["requests"]
        STATE.write_text(json.dumps({"requests": requests + 1}), encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ready")

    def log_message(self, format: str, *args: object) -> None:
        return


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
