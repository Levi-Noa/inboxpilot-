"""Retry helpers for transient API and LLM failures."""

from __future__ import annotations

import os
import time
from typing import Callable, TypeVar

T = TypeVar("T")

MAX_RETRIES = max(1, int(os.getenv("AGENT_MAX_RETRIES", "3")))
BASE_DELAY_SECONDS = max(0.1, float(os.getenv("AGENT_RETRY_BASE_DELAY", "0.75")))

_RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}


def is_retryable_exception(exc: Exception) -> bool:
    """Return True for transient failures that are safe to retry."""
    status_code = getattr(getattr(exc, "resp", None), "status", None)
    if isinstance(status_code, int):
        return status_code in _RETRYABLE_HTTP_STATUS

    # Network style transient errors from request/transport layers
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    message = str(exc).lower()
    return "timeout" in message or "temporarily unavailable" in message or "rate limit" in message


def with_retries(operation: Callable[[], T], is_retryable: Callable[[Exception], bool] = is_retryable_exception) -> T:
    """Execute operation with exponential backoff for retryable exceptions."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return operation()
        except Exception as exc:
            if attempt >= MAX_RETRIES or not is_retryable(exc):
                raise
            backoff = BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            time.sleep(backoff)
