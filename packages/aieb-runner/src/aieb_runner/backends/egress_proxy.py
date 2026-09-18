"""A real, working deny-by-default egress guard (ADR-12 / spec section 37, SE-02).

This is deliberately application-layer enforcement: it works by injecting
`HTTP_PROXY`/`HTTPS_PROXY` environment variables into the isolated process or
container, and refusing (with a logged denial) any CONNECT or plain-HTTP
request whose target host is not on the policy's allowlist or is one of the
denied cloud-metadata hosts. A sufficiently hostile binary that ignores those
environment variables and opens its own raw socket bypasses this - that
limitation is exactly why "Harbor public-egress/metadata adversarial
validation" and "Official VM provider" stay separately listed as deferred in
DECISIONS.md's Open decisions table. This module closes what IS honestly
closeable today: a normal HTTP client (the overwhelming majority of
candidate/agent code) honoring standard proxy environment variables is denied
and logged when it targets an unauthorized host or a cloud-metadata endpoint.
"""

from __future__ import annotations

import logging
import selectors
import socket
import threading
from dataclasses import dataclass

from .base import IsolationPolicy

logger = logging.getLogger("aieb_runner.egress_proxy")


@dataclass(frozen=True)
class EgressDenial:
    host: str
    port: int
    reason: str


class EgressGuardProxy:
    """A minimal CONNECT/plain-HTTP forward proxy enforcing `IsolationPolicy`.

    Not a general-purpose proxy: it only understands enough of HTTP CONNECT
    (for HTTPS) and absolute-URI plain HTTP requests to read the target host,
    check it against the policy, and either forward bytes verbatim
    (untouched, so TLS still terminates end-to-end at the real destination)
    or refuse with a 403 and a logged `EgressDenial`.
    """

    def __init__(self, policy: IsolationPolicy, *, bind_host: str = "127.0.0.1", advertised_host: str | None = None) -> None:
        """`bind_host` is the interface this process listens on. `advertised_host` is what a
        launched process/container is told to reach it as - these differ for a container (which
        must dial the host via `host.docker.internal` or the bridge gateway, not `127.0.0.1`)."""
        self._policy = policy
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((bind_host, 0))
        self._server.listen(64)
        self._server.settimeout(0.5)
        self.host = advertised_host or bind_host
        self.port: int = self._server.getsockname()[1]
        self.denials: list[EgressDenial] = []
        self._denials_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="egress-guard-proxy", daemon=True)
        self._thread.start()

    def env_vars(self) -> dict[str, str]:
        proxy_url = f"http://{self.host}:{self.port}"
        return {
            "HTTP_PROXY": proxy_url,
            "HTTPS_PROXY": proxy_url,
            "http_proxy": proxy_url,
            "https_proxy": proxy_url,
            # Force every outbound host through the guard; nothing bypasses it via NO_PROXY.
            "NO_PROXY": "",
            "no_proxy": "",
        }

    def _is_allowed(self, target_host: str) -> tuple[bool, str]:
        bare_host = target_host.split(":", 1)[0].lower()
        for denied in self._policy.denied_metadata_hosts:
            if bare_host == denied.lower():
                return False, "cloud-metadata endpoint is always denied"
        if not self._policy.egress_allowlist:
            return False, "deny-by-default: egress allowlist is empty"
        allowed = any(bare_host == entry.lower() for entry in self._policy.egress_allowlist)
        return allowed, "" if allowed else "host not on egress allowlist"

    def _record_denial(self, host: str, port: int, reason: str) -> None:
        denial = EgressDenial(host=host, port=port, reason=reason)
        with self._denials_lock:
            self.denials.append(denial)
        logger.warning("egress denied host=%s port=%s reason=%s", host, port, reason)

    def _serve(self) -> None:
        sel = selectors.DefaultSelector()
        while not self._stop.is_set():
            try:
                conn, _addr = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()
        sel.close()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(5.0)
        try:
            request = b""
            while b"\r\n\r\n" not in request and len(request) < 8192:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                request += chunk
            head_line = request.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
            parts = head_line.split(" ")
            if len(parts) < 2:
                conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                return
            method, target = parts[0], parts[1]

            if method.upper() == "CONNECT":
                host, _, port_str = target.partition(":")
                port = int(port_str or "443")
            else:
                # Absolute-URI plain HTTP: GET http://host[:port]/path HTTP/1.1
                without_scheme = target.split("://", 1)[-1]
                authority = without_scheme.split("/", 1)[0]
                host, _, port_str = authority.partition(":")
                port = int(port_str or "80")

            allowed, reason = self._is_allowed(host)
            if not allowed:
                self._record_denial(host, port, reason)
                conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\n" + reason.encode())
                return

            try:
                upstream = socket.create_connection((host, port), timeout=5.0)
            except OSError as exc:
                conn.sendall(f"HTTP/1.1 502 Bad Gateway\r\n\r\n{exc}".encode())
                return

            if method.upper() == "CONNECT":
                conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            else:
                upstream.sendall(request)
            self._pump(conn, upstream)
        except (OSError, ValueError, UnicodeDecodeError):
            pass
        finally:
            conn.close()

    @staticmethod
    def _pump(a: socket.socket, b: socket.socket) -> None:
        sel = selectors.DefaultSelector()
        sel.register(a, selectors.EVENT_READ)
        sel.register(b, selectors.EVENT_READ)
        try:
            while True:
                for key, _ in sel.select(timeout=30):
                    src, dst = (a, b) if key.fileobj is a else (b, a)
                    try:
                        data = src.recv(65536)
                    except OSError:
                        return
                    if not data:
                        return
                    try:
                        dst.sendall(data)
                    except OSError:
                        return
        finally:
            sel.close()
            b.close()

    def close(self) -> None:
        self._stop.set()
        try:
            self._server.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)

    def __enter__(self) -> "EgressGuardProxy":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
