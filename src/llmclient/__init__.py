"""llmclient: a vendor-agnostic LLM client."""

from llmclient.errors import (
    AuthError,
    ConfigError,
    LLMClientError,
    RateLimitError,
    TimeoutError,
    VendorError,
)

__all__ = [
    "AuthError",
    "ConfigError",
    "LLMClientError",
    "RateLimitError",
    "TimeoutError",
    "VendorError",
]
