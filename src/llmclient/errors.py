"""Exception hierarchy for llmclient."""

from __future__ import annotations

import builtins

_BODY_EXCERPT_LIMIT = 500


class LLMClientError(Exception):
    """Base class for all errors raised by llmclient."""


class ConfigError(LLMClientError):
    """Raised when configuration is missing or invalid."""


class AuthError(LLMClientError):
    """Raised when a vendor rejects credentials."""


class RateLimitError(LLMClientError):
    """Raised when a vendor reports the caller is being rate limited."""


class TimeoutError(LLMClientError, builtins.TimeoutError):
    """Raised when a request to a vendor times out.

    Subclasses `builtins.TimeoutError` so callers using the standard
    `except TimeoutError` idiom still catch this.
    """


class VendorError(LLMClientError):
    """Raised for vendor-reported failures not covered by a more specific error."""

    def __init__(self, message: str, *, vendor: str, status: int | None, body_excerpt: str) -> None:
        super().__init__(message)
        self.vendor = vendor
        self.status = status
        self.body_excerpt = body_excerpt[:_BODY_EXCERPT_LIMIT]
