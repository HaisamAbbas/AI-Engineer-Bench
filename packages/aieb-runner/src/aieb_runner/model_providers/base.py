"""Provider-adapter contract shared by every model-track provider.

Every adapter, real or fake, speaks the same small vocabulary:
`ProviderMessage` in, `ProviderResponse` out, and a typed `ProviderError`
hierarchy so a failure can always be attributed to exactly one cause
(transport, rate limit, authentication, invalid request). The loop
(`aieb_runner.model_loop`) treats retryable and non-retryable errors
differently and records which subclass actually occurred - this is what
`ProviderError.retryable` and the distinct subclasses exist for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ToolCall:
    """One tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class ProviderMessage:
    """One turn in the conversation sent to/received from a provider.

    `role` is one of "system", "user", "assistant", or "tool". A "tool" role
    message is a tool RESULT being fed back to the model and carries
    `tool_call_id` identifying which call it answers.
    """

    role: str
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class UsageInfo:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class ProviderResponse:
    """One model turn, plus enough identity/coverage information that the
    loop never has to guess what actually happened.

    `reported_model`: the model identity the PROVIDER says it used, read
    from the response body - never assumed to equal what was requested.
    `unsupported_settings`: any requested setting this adapter/provider could
    not honor, named explicitly. A non-empty tuple here is exactly the
    "disclosed profile" signal ENG-023's no-silent-fallback contract requires
    - the loop must never treat this response as a full match when it is
    non-empty.
    """

    content: str
    tool_calls: tuple[ToolCall, ...]
    reported_model: str
    finish_reason: str
    usage: UsageInfo = field(default_factory=UsageInfo)
    unsupported_settings: tuple[str, ...] = ()


class ProviderError(Exception):
    """Base of the provider error-attribution hierarchy.

    `retryable` classifies the failure for the loop's bounded-retry policy.
    Every real failure must raise one of the named subclasses below, never
    the bare base class, so the recorded outcome always names a specific
    cause (this is what the "provider error attribution" tests assert).
    """

    retryable: bool = False

    def __init__(self, message: str, *, retry_after_sec: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_sec = retry_after_sec


class TransportError(ProviderError):
    """Network/transport failure (DNS, connection refused, timeout, 5xx). Retryable."""

    retryable = True


class RateLimitError(ProviderError):
    """The provider rate-limited the request. Retryable; may carry a
    `retry_after_sec` hint parsed from the provider's response."""

    retryable = True


class AuthenticationError(ProviderError):
    """Credential rejected (401/403 or equivalent). Not retryable: retrying
    with the same credential will fail identically."""

    retryable = False


class InvalidRequestError(ProviderError):
    """Malformed/invalid request (400 or equivalent). Not retryable: the
    request itself is wrong, not the transport or the credential."""

    retryable = False


class ProviderAdapter(Protocol):
    """What the model loop needs from any provider, real or fake."""

    def complete(
        self,
        messages: tuple[ProviderMessage, ...],
        tools: tuple[dict[str, object], ...],
        settings: dict[str, object],
    ) -> ProviderResponse:
        """Run one completion turn. Raises a `ProviderError` subclass on failure."""
        ...

    def unsupported_settings(self, settings: dict[str, object]) -> tuple[str, ...]:
        """Which of `settings`' keys this adapter cannot honor, named
        explicitly, computed without making a network call. Used by the loop
        to build a disclosed/degraded profile up front, before the first
        request - not just reactively from a response."""
        ...


class UnsupportedSettings(RuntimeError):
    """Raised by an adapter constructor when a REQUIRED setting cannot be
    honored at all (as opposed to a soft/advisory one recorded via
    `unsupported_settings`)."""
