"""Deterministic, scriptable provider adapter with zero network/credential
dependency. Used by every unit test and by the real Harbor Docker smoke test
(scripts/run_eng023_model_loop_spike.py) - it must never make a real network
call, so a run against this adapter costs nothing and needs no credentials."""

from __future__ import annotations

from dataclasses import dataclass, field

from aieb_runner.model_providers.base import (
    ProviderError,
    ProviderMessage,
    ProviderResponse,
)


_REGISTRY: dict[str, "FakeProviderAdapter"] = {}


@dataclass
class FakeProviderAdapter:
    """Replays a fixed queue of canned responses/exceptions, in order.

    Construct with `script`: a list where each element is either a
    `ProviderResponse` to return, or a `ProviderError` instance to raise, for
    the Nth call to `complete()`. Once the queue is exhausted, the last
    scripted entry repeats forever - this keeps a loop that retries past the
    scripted length from crashing the test with an IndexError, while still
    being fully deterministic.
    """

    script: list[ProviderResponse | ProviderError] = field(default_factory=list)
    declined_settings: tuple[str, ...] = ()
    call_count: int = field(default=0, init=False)
    seen_messages: list[tuple[ProviderMessage, ...]] = field(default_factory=list, init=False)

    def complete(
        self,
        messages: tuple[ProviderMessage, ...],
        tools: tuple[dict[str, object], ...],
        settings: dict[str, object],
    ) -> ProviderResponse:
        del tools, settings
        self.seen_messages.append(messages)
        index = min(self.call_count, len(self.script) - 1) if self.script else -1
        self.call_count += 1
        if index < 0:
            raise ProviderError("FakeProviderAdapter has no scripted response")
        entry = self.script[index]
        if isinstance(entry, ProviderError):
            raise entry
        return entry

    def unsupported_settings(self, settings: dict[str, object]) -> tuple[str, ...]:
        return tuple(key for key in settings if key in self.declined_settings)

    @classmethod
    def register(cls, key: str, adapter: "FakeProviderAdapter") -> None:
        """Register a scripted adapter in a process-wide registry, keyed by
        an opaque id. This is the seam `model_loop.ModelTrackReferenceLoop`
        uses when it is constructed by Harbor purely from
        `agent_import_path` (no way to pass a live Python object through
        that string) but a real, scripted run is still needed - e.g.
        scripts/run_eng023_model_loop_spike.py, which runs in the same host
        process as the agent (Harbor's installed-agent `run()` executes as
        an asyncio task on the host, dispatching into the sandboxed
        container only for `environment.exec()` calls - see
        `HarborBackend.launch()`), so a registry keyed by an env-var value
        set just before `backend.launch()` is visible to the agent
        instance Harbor constructs a moment later."""
        _REGISTRY[key] = adapter

    @classmethod
    def get_registered(cls, key: str) -> "FakeProviderAdapter | None":
        return _REGISTRY.get(key)
