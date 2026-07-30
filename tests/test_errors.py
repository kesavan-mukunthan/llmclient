from __future__ import annotations

import builtins

import pytest

from llmclient.errors import (
    AuthError,
    ConfigError,
    LLMClientError,
    RateLimitError,
    TimeoutError,
    VendorError,
)


@pytest.mark.parametrize(
    "exc_type",
    [ConfigError, AuthError, RateLimitError, TimeoutError, VendorError],
)
def test_all_errors_subclass_llmclient_error(exc_type: type[Exception]) -> None:
    assert issubclass(exc_type, LLMClientError)


def test_llmclient_error_subclasses_exception() -> None:
    assert issubclass(LLMClientError, Exception)


def test_timeout_error_subclasses_builtin_timeout_error() -> None:
    assert issubclass(TimeoutError, builtins.TimeoutError)


def test_timeout_error_is_caught_by_builtin_except_clause() -> None:
    with pytest.raises(builtins.TimeoutError):
        raise TimeoutError("request timed out")


def test_vendor_error_carries_metadata() -> None:
    err = VendorError(
        "boom", vendor="anthropic", status=500, body_excerpt="short body"
    )
    assert err.vendor == "anthropic"
    assert err.status == 500
    assert err.body_excerpt == "short body"


def test_vendor_error_status_may_be_none() -> None:
    err = VendorError("boom", vendor="google", status=None, body_excerpt="")
    assert err.status is None


def test_vendor_error_truncates_body_excerpt_to_500_chars() -> None:
    long_body = "x" * 1000
    err = VendorError("boom", vendor="anthropic", status=429, body_excerpt=long_body)
    assert len(err.body_excerpt) == 500
    assert err.body_excerpt == "x" * 500


def test_vendor_error_body_excerpt_under_limit_is_unchanged() -> None:
    body = "y" * 100
    err = VendorError("boom", vendor="anthropic", status=400, body_excerpt=body)
    assert err.body_excerpt == body
