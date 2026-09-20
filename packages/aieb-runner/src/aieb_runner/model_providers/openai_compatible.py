"""A real adapter for a standard OpenAI-compatible `/chat/completions` HTTP
endpoint, implemented with stdlib `urllib.request` only.

Deliberately does not add `openai`/`litellm`/`anthropic`/`httpx`/`requests`
as a dependency of `aieb-runner` (those happen to be importable in this
shared dev venv but are not declared dependencies of this package) - this
mirrors the project's existing convention of hand-rolling a small stdlib
client rather than adding a dependency for a simple HTTP call (see the
ENG-020 Prometheus exporter).

Credential handling: the API key is read from an environment variable at
call time only. It is never written to any file and never interpolated into
any string handed to `environment.exec_as_agent()`/`exec_as_root()` - the
only place it is ever used is the `Authorization` header of an HTTP request
this adapter makes itself, on the host process running the loop, not inside
the sandboxed environment's shell.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from aieb_runner.model_providers.base import (
    AuthenticationError,
    InvalidRequestError,
    ProviderMessage,
    ProviderResponse,
    RateLimitError,
    ToolCall,
    TransportError,
    UsageInfo,
)

# Settings this adapter is known not to be able to honor at all, regardless
# of what the underlying provider supports - named explicitly so the loop
# can build a disclosed/degraded profile before the first request, per
# ENG-023's "no silent fallback" contract.
_KNOWN_UNSUPPORTED_SETTINGS: frozenset[str] = frozenset({"logprobs", "top_logprobs"})


@dataclass(frozen=True)
class OpenAICompatibleAdapter:
    """Speaks the standard `POST {base_url}/chat/completions` shape.

    `api_key_env_var` names the environment variable to read the credential
    from AT CALL TIME (never stored on the instance) - this is the exact
    mechanism the credential-protection tests verify: the key never appears
    in any file this adapter writes, and never in any command string.
    """

    base_url: str
    model: str
    api_key_env_var: str = "AIEB_MODEL_TRACK_API_KEY"
    timeout_sec: float = 60.0

    def unsupported_settings(self, settings: dict[str, object]) -> tuple[str, ...]:
        return tuple(key for key in settings if key in _KNOWN_UNSUPPORTED_SETTINGS)

    def _read_api_key(self) -> str:
        import os

        key = os.environ.get(self.api_key_env_var)
        if not key:
            raise AuthenticationError(
                f"credential env var {self.api_key_env_var!r} is not set; refusing to "
                "call a real provider without an explicit credential"
            )
        return key

    def complete(
        self,
        messages: tuple[ProviderMessage, ...],
        tools: tuple[dict[str, object], ...],
        settings: dict[str, object],
    ) -> ProviderResponse:
        api_key = self._read_api_key()
        honored_settings = {
            key: value
            for key, value in settings.items()
            if key not in _KNOWN_UNSUPPORTED_SETTINGS
        }
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [_message_to_wire(message) for message in messages],
            **honored_settings,
        }
        if tools:
            payload["tools"] = list(tools)

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url=f"{self.base_url.rstrip('/')}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                # The only place the credential is ever used: an HTTP header
                # on a request this adapter makes itself. It is never logged,
                # never written to a file, and never passed to any shell
                # command string.
                "Authorization": f"Bearer {api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
            raise _map_http_error(exc.code, detail) from exc
        except urllib.error.URLError as exc:
            raise TransportError(f"transport failure calling {self.base_url}: {exc}") from exc
        except TimeoutError as exc:
            raise TransportError(f"timed out calling {self.base_url}: {exc}") from exc

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise InvalidRequestError(f"provider returned unparseable JSON: {exc}") from exc

        return _wire_to_response(data, unsupported=tuple(
            key for key in settings if key in _KNOWN_UNSUPPORTED_SETTINGS
        ))


def _message_to_wire(message: ProviderMessage) -> dict[str, object]:
    wire: dict[str, object] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        wire["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        wire["tool_call_id"] = message.tool_call_id
    return wire


def _map_http_error(status: int, detail: str) -> Exception:
    if status in (401, 403):
        return AuthenticationError(f"provider rejected credential (HTTP {status}): {detail}")
    if status == 429:
        return RateLimitError(f"provider rate-limited request (HTTP 429): {detail}")
    if status == 400:
        return InvalidRequestError(f"provider rejected request as invalid (HTTP 400): {detail}")
    if status >= 500:
        return TransportError(f"provider server error (HTTP {status}): {detail}")
    return InvalidRequestError(f"unexpected provider HTTP status {status}: {detail}")


def _wire_to_response(data: dict[str, object], *, unsupported: tuple[str, ...]) -> ProviderResponse:
    choices = data.get("choices") or []
    if not choices:
        raise InvalidRequestError(f"provider response had no choices: {data!r}")
    choice = choices[0]
    message = choice.get("message") or {}
    tool_calls = tuple(
        ToolCall(
            id=str(raw.get("id", "")),
            name=str((raw.get("function") or {}).get("name", "")),
            arguments=_parse_arguments((raw.get("function") or {}).get("arguments")),
        )
        for raw in (message.get("tool_calls") or [])
    )
    usage = data.get("usage") or {}
    return ProviderResponse(
        content=str(message.get("content") or ""),
        tool_calls=tool_calls,
        reported_model=str(data.get("model", "")),
        finish_reason=str(choice.get("finish_reason", "unknown")),
        usage=UsageInfo(
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        ),
        unsupported_settings=unsupported,
    )


def _parse_arguments(raw: object) -> dict[str, object]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
