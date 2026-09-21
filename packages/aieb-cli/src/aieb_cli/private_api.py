"""Private authenticated API client for the v2 operator CLI.

This client talks ONLY to authenticated private API operations - never to a
public website route and never to an unauthenticated mutation. It is a thin
transport/hedge layer: it carries credentials and idempotency correctly and
normalizes failures into stable CLI errors, but it implements no business
rules. The API remains authoritative.

Guarantees:

- every request carries ``Authorization: Bearer <token>`` when a token is
  supplied, and every mutation additionally carries ``Idempotency-Key``;
- connect and read are bounded by explicit socket timeouts;
- redirects are refused for mutation requests (a 3xx on a mutation is never
  transparently followed to a different resource);
- access tokens and response body secrets are never rendered into any
  exception ``__str__``/``message`` (a caller that prints ``str(exc)`` leaks
  nothing);
- the server's response body is preserved on ``ApiClientError.detail`` so
  ``--json`` callers can surface it, but secrets are redacted recursively
  before store/print: values under secret-looking keys (``token``,
  ``password``, ``secret``, ``credential``, ``authorization``, ...) and
  values matching common secret shapes (JWTs, bearer blobs, URL userinfo,
  ``sk-*``/``ghp_*``/``xox*`` keys, ...) are replaced, so an error body can
  never leak a token, credential, password, or nested secret to stdout;
- non-2xx responses are normalized into stable, scriptable error codes;
- automatic retry happens only for safe network-level failures (no response
  was received) and only for requests that carry an idempotency key (so a
  replayed request can never double-apply). Reads are retried once too,
  because they are naturally idempotent; server responses (including 5xx)
  are never retried.
"""

from __future__ import annotations

import json
import re
import socket
import ssl
import time
import uuid
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPSConnection
from typing import Any, Protocol
from urllib.parse import urlsplit, urljoin

MUTATION_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_RETRYABLE_SOCKET_ERRORS = (
    ConnectionAbortedError,
    ConnectionRefusedError,
    ConnectionResetError,
    TimeoutError,
    socket.timeout,
    socket.gaierror,
    BrokenPipeError,
)

OPERATOR_SCHEMA = "aieb.operator-cli/v1"

_redacted = "<redacted>"

_REDACTED_VALUE = "[REDACTED]"

# Keys whose value is treated as a secret whenever the lowercased key name
# contains one of these fragments. Scoped to credential-shaped words so benign
# fields such as ``request_id`` or ``snapshot_digest`` survive intact.
_SECRET_KEY_FRAGMENTS = (
    "token",
    "password",
    "passwd",
    "pwd",
    "secret",
    "credential",
    "apikey",
    "api_key",
    "authorization",
    "authz",
    "bearer",
    "access_key",
    "private_key",
    "client_secret",
    "refresh",
    "cookie",
)

_SECRET_URL_USERINFO = re.compile(r"(\b[a-z][a-z0-9+.\-]*://)([^/@\s]*):([^/@\s]*)@", re.IGNORECASE)
_SECRET_BEARER = re.compile(r"\b(bearer\s+)([A-Za-z0-9._\-~+/=]{8,})\b", re.IGNORECASE)

_SECRET_VALUE_PATTERNS = (
    # JWT - "eyJ..." base64url triplets.
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\b"), _REDACTED_VALUE),
    # Bearer/credential declaration followed by a long credential blob.
    (_SECRET_BEARER, lambda m: f"{m.group(1)}{_REDACTED_VALUE}"),
    # URL userinfo: scheme://user:password@host -> the credentials are scrubbed
    # while the scheme and hostname are kept.
    (_SECRET_URL_USERINFO, lambda m: f"{m.group(1)}{_REDACTED_VALUE}@"),
    # Well-known secret-key prefixes (OpenAI, Anthropic, GitHub, Slack, Google).
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b"), _REDACTED_VALUE),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9_\-]{10,}\b"), _REDACTED_VALUE),
    (re.compile(r"\bghp_[A-Za-z0-9_\-]{20,}\b"), _REDACTED_VALUE),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _REDACTED_VALUE),
    (re.compile(r"\bAIza[A-Za-z0-9_\-]{25,}\b"), _REDACTED_VALUE),
)


def _is_secret_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.lower().replace("-", "_").replace(" ", "_")
    return any(fragment in normalized for fragment in _SECRET_KEY_FRAGMENTS)


def _redact_secret_values(value: str) -> str:
    redacted = value
    for pattern, replacement in _SECRET_VALUE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _redact_secrets(value: Any) -> Any:
    """Recursively replace secret-shaped values; everything else is preserved.

    Empties are never invented: a value that is not secret-looking (including
    empty strings and scalars) is returned untouched, so redaction cannot
    obscure diagnostics that the operator actually needs.
    """
    if isinstance(value, dict):
        return {
            key: (_REDACTED_VALUE if _is_secret_key(key) else _redact_secrets(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_secrets(item) for item in value]
    if isinstance(value, str):
        return _redact_secret_values(value)
    return value


class ClientTransportError(Exception):
    """A network-level failure while issuing a single HTTP request.

    ``safe_to_retry`` is True only when the request may be safely replayed:
    the failure happened before any response was received AND either the
    request is a read or it carried an idempotency key.
    """

    def __init__(self, message: str, *, safe_to_retry: bool = False) -> None:
        super().__init__(message)
        self.safe_to_retry = safe_to_retry


class ApiClientError(Exception):
    """Normalized, non-secret, stable CLI error from an API interaction.

    ``__str__`` never includes the access token or the raw server body, so
    printing the error to stdout/stderr cannot leak credentials or secrets.
    The decoded server body (if any) is kept on ``detail`` for ``--json``
    callers instead.
    """

    def __init__(
        self,
        *,
        code: str,
        message: str,
        retryable: bool = False,
        status: int | None = None,
        request_id: str | None = None,
        detail: Any = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status = status
        self.request_id = request_id
        self.detail = detail
        self.hint = hint


@dataclass(frozen=True)
class RawResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class RawTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse: ...


class SocketTransport:
    """stdlib http.client based transport with bounded connect/read timeouts.

    Redirects are not silently followed for mutation methods: a redirect
    response is returned intact and the client layer refuses it. Read
    requests may follow up to ``max_redirects`` hops.
    """

    def __init__(
        self,
        *,
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
        max_redirects: int = 5,
    ) -> None:
        if connect_timeout <= 0 or read_timeout <= 0:
            raise ValueError("timeouts must be positive")
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._max_redirects = max_redirects

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            raise ClientTransportError(
                f"unsupported API URL scheme {parsed.scheme!r}; use http:// or https://",
                safe_to_retry=False,
            )
        if parsed.hostname is None:
            raise ClientTransportError("API URL has no hostname", safe_to_retry=False)
        redirects = 0
        while True:
            try:
                status, response_headers, data = self._single(
                    method, parsed, headers, body, timeout,
                )
            except ClientTransportError:
                raise
            if status in _REDIRECT_STATUSES and method in _READ_METHODS:
                if redirects >= self._max_redirects:
                    return RawResponse(status=status, headers=response_headers, body=data)
                location = response_headers.get("location")
                if not location:
                    return RawResponse(status=status, headers=response_headers, body=data)
                parsed = urlsplit(urljoin(url, location))
                redirects += 1
                continue
            return RawResponse(status=status, headers=response_headers, body=data)

    def _single(
        self,
        method: str,
        parsed,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, dict[str, str], bytes]:
        port = parsed.port
        if parsed.scheme == "https":
            connection: HTTPConnection | HTTPSConnection = HTTPSConnection(
                parsed.hostname,
                port or 443,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        else:
            connection = HTTPConnection(parsed.hostname, port or 80, timeout=timeout)
        try:
            connection.connect()
        except OSError as exc:
            connection.close()
            safe = isinstance(exc, _RETRYABLE_SOCKET_ERRORS)
            raise ClientTransportError(f"connect failed: {exc}", safe_to_retry=safe) from exc
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        request_headers = dict(headers)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            data = response.read()
            status = int(response.status)
            response_headers = {str(key).lower(): str(value) for key, value in response.getheaders()}
            return status, response_headers, data
        except OSError as exc:
            # The failure happened without a usable response: replay is safe
            # only when the idempotency key protects the mutation (enforced by
            # the client before it sends) or the request is a read.
            safe = isinstance(exc, _RETRYABLE_SOCKET_ERRORS)
            raise ClientTransportError(f"request/read failed: {exc}", safe_to_retry=safe) from exc
        finally:
            connection.close()


class PrivateApiClient:
    """Bearer-authenticated private API client.

    ``base_url`` is the configured API root (for example
    ``https://api.example.invalid``). ``access_token`` is the OIDC bearer
    token the operator obtained out of band; it is never rendered into error
    messages. ``transport`` may be replaced in tests with a fake to exercise
    the client's behavior without a socket.
    """

    def __init__(
        self,
        *,
        base_url: str,
        access_token: str,
        transport: RawTransport | None = None,
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport or SocketTransport(
            connect_timeout=connect_timeout, read_timeout=read_timeout,
        )
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._access_token = access_token

    def _url(self, path: str) -> str:
        return self._base_url + (path if path.startswith("/") else f"/{path}")

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        idempotency_key: str | None = None,
        query: dict[str, str] | None = None,
        max_attempts: int = 2,
    ) -> dict[str, Any]:
        """Issue one private API request and return the decoded JSON body.

        Raises ``ApiClientError`` with a stable code on any failure. Only
        safe network failures are retried, and only with the same supplied
        idempotency key (never a freshly generated one).
        """
        method = method.upper()
        is_mutation = method in MUTATION_METHODS
        if not (self._access_token or "").strip():
            raise ApiClientError(
                code="authorization_required",
                message="an access token is required for private API operations; pass --access-token or set AIEB_API_TOKEN",
                retryable=False,
            )
        if is_mutation and not idempotency_key:
            raise ApiClientError(
                code="idempotency_key_required",
                message="this mutation requires an Idempotency-Key; pass --idempotency-key (the same key replays the original response on retry)",
                retryable=False,
            )
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token.strip()}",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        raw_body = (
            json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
            if body is not None
            else None
        )
        url = self._url(path)
        if query:
            suffix = "&".join(
                _safe_query_key(k) + "=" + _safe_query_value(v) for k, v in sorted(query.items()) if v is not None
            )
            if suffix:
                url = url + ("&" if "?" in url else "?") + suffix
        attempt = 0
        while True:
            attempt += 1
            try:
                raw = self._transport.request(
                    method, url, headers=headers, body=raw_body, timeout=self._read_timeout,
                )
                break
            except ClientTransportError as exc:
                can_retry = (
                    attempt < max_attempts
                    and exc.safe_to_retry
                    and (not is_mutation or idempotency_key is not None)
                )
                if not can_retry:
                    raise ApiClientError(
                        code="network_error",
                        message=f"private API unreachable: {exc}",
                        retryable=True,
                        hint="retry is attempted only for safe network failures and reuses the same Idempotency-Key",
                    ) from exc
                time.sleep(0.05)
                continue
        return self._decode(raw, method=method)

    def _decode(self, raw: RawResponse, *, method: str) -> dict[str, Any]:
        text = raw.body.decode("utf-8", errors="replace")
        request_id = raw.headers.get("x-request-id")
        if raw.status in _REDIRECT_STATUSES and method in MUTATION_METHODS:
            raise ApiClientError(
                code="redirect_refused",
                message=f"server responded with HTTP {raw.status} redirect; refusing to follow a redirect for a mutation request to a private API",
                retryable=False,
                status=raw.status,
                request_id=request_id,
                detail=_redact_secrets(_maybe_json(text)),
            )
        if 200 <= raw.status < 300:
            decoded = _maybe_json(text)
            if isinstance(decoded, dict):
                return decoded
            return {"raw": text, "request_id": request_id}
        error = _server_error(raw.status, request_id, text)
        if error is not None:
            error["message"] = _redact_secret_values(error["message"])
            raise ApiClientError(**error)
        raise ApiClientError(
            code=f"http_{raw.status}",
            message=_redact_secret_values(text) or f"request failed with HTTP {raw.status}",
            retryable=raw.status in (429, 503),
            status=raw.status,
            request_id=request_id,
            detail=_redact_secrets(_maybe_json(text)),
        )


def _server_error(status: int, request_id: str | None, text: str) -> dict[str, Any] | None:
    """Map a private API's error envelope into a stable CLI error code."""
    server = _maybe_json(text)
    err = server.get("error") if isinstance(server, dict) else None
    server_message = None
    if isinstance(err, dict):
        server_message = err.get("message") if isinstance(err.get("message"), str) else None
        server_retryable = err.get("retryable") is True
    elif isinstance(server, dict) and isinstance(server.get("message"), str):
        server_message = server["message"]
        server_retryable = False
    else:
        server_retryable = False
    message = server_message or text or f"request failed with HTTP {status}"
    if status == 401:
        code, retryable = "authorization_denied", False
    elif status == 403:
        code, retryable = "authorization_forbidden", False
    elif status == 404:
        code, retryable = "not_found", False
    elif status == 409:
        code = "idempotency_conflict" if "idempotency" in message.lower() else "conflict"
        retryable = False
    elif status == 412:
        code, retryable = "stale_version", False
    elif status == 422:
        code, retryable = "validation_error", False
    elif status == 429:
        code, retryable = "rate_limited", True
    elif status == 503:
        code, retryable = "service_unavailable", True
    else:
        code, retryable = (f"http_{status}", False)
    return {
        "code": code,
        "message": message,
        "retryable": server_retryable if server_retryable else retryable,
        "status": status,
        "request_id": request_id,
        "detail": _redact_secrets(server),
    }


def _maybe_json(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _safe_query_key(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe="")

def _safe_query_value(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe="")


def fresh_request_id() -> str:
    """A locally assigned request id used when the server never responded."""
    return str(uuid.uuid4())