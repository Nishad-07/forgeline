"""LLM error taxonomy used by the router to decide between retry, repair, failover and pause."""

from __future__ import annotations

import re

import httpx

TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
FATAL_STATUS = {400, 401, 403, 404}


class ProviderError(Exception):
    """The provider could not produce a response at all."""

    def __init__(self, provider: str, message: str, *, transient: bool, status: int | None = None) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.transient = transient  # transient -> retry with backoff; otherwise fail over immediately
        self.status = status


class StructuredOutputError(Exception):
    """The provider answered, but the answer never matched the required schema (after repairs)."""

    def __init__(self, provider: str, schema: str, attempts: int, last_error: str) -> None:
        super().__init__(f"{provider}: output did not match {schema} after {attempts} attempt(s): {last_error}")
        self.provider = provider
        self.schema = schema
        self.attempts = attempts
        self.last_error = last_error


class AllProvidersUnavailable(Exception):
    """Every configured provider failed. The workflow should pause and be resumable later."""

    def __init__(self, errors: list[Exception]) -> None:
        super().__init__("all LLM providers failed: " + " | ".join(str(e)[:200] for e in errors))
        self.errors = errors


def _status_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int):
        return value
    match = re.search(r"\b([45]\d\d)\b", str(exc))
    return int(match.group(1)) if match else None


def classify(provider: str, exc: BaseException) -> ProviderError:
    """Map an arbitrary SDK/network exception onto ProviderError(transient=...)."""
    if isinstance(exc, ProviderError):
        return exc
    message = f"{type(exc).__name__}: {str(exc)[:300]}"
    if isinstance(exc, (ConnectionError, TimeoutError, httpx.TransportError)):
        return ProviderError(provider, message, transient=True)
    status = _status_of(exc)
    if status in FATAL_STATUS:
        return ProviderError(provider, message, transient=False, status=status)
    if status in TRANSIENT_STATUS:
        return ProviderError(provider, message, transient=True, status=status)
    # Unknown failures: retry once, then fail over.
    return ProviderError(provider, message, transient=True, status=status)
