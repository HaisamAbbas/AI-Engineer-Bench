"""Small stdlib HTTP application; candidate code is exercised only over HTTP."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .backend import Backend


class Handler(BaseHTTPRequestHandler):
    backend = Backend()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: int, body: object) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _body(self) -> dict[str, object]:
        size = int(self.headers.get("Content-Length", "0"))
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(HTTPStatus.OK, {"status": "ready"})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        try:
            body = self._body()
            if self.path == "/documents":
                status, response = self.backend.put(str(body["id"]), _integer(body["version"]), str(body["text"]), body.get("metadata") if isinstance(body.get("metadata"), dict) else None)
                self._json(status, response)
            elif self.path == "/search":
                self._json(HTTPStatus.OK, {"hits": self.backend.search(str(body["query"]), _integer(body.get("top_k", 10)), body.get("metadata") if isinstance(body.get("metadata"), dict) else None)})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def do_DELETE(self) -> None:
        try:
            prefix = "/documents/"
            if not self.path.startswith(prefix):
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            body = self._body()
            status, response = self.backend.delete(self.path[len(prefix):], _integer(body["version"]))
            self._json(status, response)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("version and top_k must be integers")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
