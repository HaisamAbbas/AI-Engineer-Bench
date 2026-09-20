"""Provider-adapter layer for the ENG-023 fixed reference model-track loop.

This package is deliberately small and dependency-free: `base.py` defines the
adapter contract and error-attribution hierarchy, `fake.py` is the fully
deterministic double used by every test and by the Harbor Docker smoke test,
and `openai_compatible.py` is the one real network-calling adapter (stdlib
`urllib.request` only - no `openai`/`litellm`/`anthropic`/`httpx`/`requests`
dependency is added to this package).
"""

from aieb_runner.model_providers.base import (
    AuthenticationError,
    InvalidRequestError,
    ProviderAdapter,
    ProviderError,
    ProviderMessage,
    ProviderResponse,
    RateLimitError,
    ToolCall,
    TransportError,
    UnsupportedSettings,
)
from aieb_runner.model_providers.fake import FakeProviderAdapter
from aieb_runner.model_providers.openai_compatible import OpenAICompatibleAdapter

__all__ = [
    "AuthenticationError",
    "FakeProviderAdapter",
    "InvalidRequestError",
    "OpenAICompatibleAdapter",
    "ProviderAdapter",
    "ProviderError",
    "ProviderMessage",
    "ProviderResponse",
    "RateLimitError",
    "ToolCall",
    "TransportError",
    "UnsupportedSettings",
]
