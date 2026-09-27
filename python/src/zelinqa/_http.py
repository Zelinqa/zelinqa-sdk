"""Shared transport for the Zelinqa clients.

Everything that decides *what* to send and *whether to try again* lives here as
pure functions. Only :class:`SyncExecutor` and :class:`AsyncExecutor` touch the
network, so the sync and the async client share one policy instead of two
copies of it.

The API key is held by the ``httpx`` client alone. It never reaches a log line,
an exception, a ``repr`` or a returned value.
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import quote, urlsplit

import httpx

from ._version import __version__
from .errors import (
    MAX_RETRY_AFTER_SECONDS,
    ZelinqaAPIError,
    ZelinqaConnectionError,
    api_error_from_response,
    parse_retry_after,
)

DEFAULT_BASE_URL = "https://api.zelinqa.ai"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 2
BASE_URL_ENV = "ZELINQA_BASE_URL"
API_KEY_ENV = "ZELINQA_API_KEY"
CONFIGURATION_API_KEY_ENV = "ZELINQA_CONFIGURATION_API_KEY"

#: First backoff delay. Doubles per attempt, up to :data:`BACKOFF_CAP_SECONDS`.
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_CAP_SECONDS = 10.0

#: Statuses that are worth another attempt with the same idempotency key.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
#: Envelope codes that are transient by contract.
RETRYABLE_ERROR_CODES = frozenset({"idempotency_contention"})

JSON_ACCEPT = "application/json"
CSV_ACCEPT = "text/csv"


def resolve_api_key(api_key: str | None, *, env_names: tuple[str, ...]) -> str:
    """Return the key to use, falling back on the environment in order."""

    if api_key is not None:
        if not api_key.strip():
            raise ValueError("api_key must not be empty")
        return api_key
    for name in env_names:
        value = os.environ.get(name)
        if value and value.strip():
            return value
    joined = " or ".join(env_names)
    raise ValueError(f"an API key is required: pass api_key= or set {joined}")


def resolve_base_url(base_url: str | None) -> str:
    """Return the validated base URL, from the argument, the env, or the default."""

    candidate = base_url or os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL
    parsed = urlsplit(candidate)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        raise ValueError("base_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("base_url must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain a query string or fragment")
    return candidate.rstrip("/")


def validate_timeout(timeout: float) -> float:
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    return timeout


def validate_max_retries(max_retries: int) -> int:
    if max_retries < 0:
        raise ValueError("max_retries must be zero or greater")
    return max_retries


def client_headers(api_key: str) -> dict[str, str]:
    """Headers sent on every request of a client."""

    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": JSON_ACCEPT,
        "User-Agent": f"zelinqa-python/{__version__}",
    }


def new_idempotency_key() -> str:
    """Generate one idempotency key for one logical call."""

    return str(uuid.uuid4())


def resolve_idempotency_key(idempotency_key: str | None) -> str:
    """Validate a caller-supplied key, or mint one for this logical call.

    The result is reused across every retry of that call: regenerating it would
    turn a retry into a second effect.
    """

    if idempotency_key is None:
        return new_idempotency_key()
    if not idempotency_key.strip():
        raise ValueError("idempotency_key must not be empty")
    if not 8 <= len(idempotency_key) <= 128:
        raise ValueError("idempotency_key must be between 8 and 128 characters")
    return idempotency_key


def path_segment(value: str) -> str:
    """Escape one path segment so an identifier can never traverse the path."""

    if not value or not value.strip():
        raise ValueError("path identifiers must not be empty")
    return quote(value, safe="")


def query_params(params: Mapping[str, Any]) -> dict[str, str]:
    """Drop the unset filters and serialise the rest the way the API reads them."""

    encoded: dict[str, str] = {}
    for name, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            encoded[name] = "true" if value else "false"
        else:
            encoded[name] = str(value)
    return encoded


@dataclass(frozen=True)
class Request:
    """One logical call: the same object feeds every attempt."""

    method: Literal["GET", "POST"]
    path: str
    params: dict[str, str] = field(default_factory=dict)
    json_body: dict[str, Any] | None = None
    idempotency_key: str | None = None
    accept: str = JSON_ACCEPT

    def headers(self) -> dict[str, str]:
        headers = {"Accept": self.accept}
        if self.idempotency_key is not None:
            headers["Idempotency-Key"] = self.idempotency_key
        if self.json_body is not None:
            headers["Content-Type"] = JSON_ACCEPT
        return headers


@dataclass(frozen=True)
class Attempt:
    """What one attempt produced, reduced to what the retry policy needs."""

    status_code: int
    payload: Any
    text: str
    code: str | None
    headers: Mapping[str, str]

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300


def read_attempt(response: httpx.Response) -> Attempt:
    """Decode a response once, so the body is never read twice."""

    payload: Any = None
    text = ""
    if response.headers.get("content-type", "").startswith("text/"):
        text = response.text
    else:
        try:
            payload = response.json()
        except ValueError:
            text = response.text
    code: str | None = None
    if isinstance(payload, Mapping):
        raw = payload.get("code")
        if isinstance(raw, str) and raw:
            code = raw
    return Attempt(
        status_code=response.status_code,
        payload=payload,
        text=text,
        code=code,
        headers=response.headers,
    )


def backoff_delay(attempt_index: int, *, jitter: float = 1.0) -> float:
    """Exponential backoff 0.5s, 1s, 2s… capped, multiplied by a jitter factor."""

    base = min(BACKOFF_BASE_SECONDS * (2.0**attempt_index), BACKOFF_CAP_SECONDS)
    return base * jitter


def _random_jitter() -> float:
    return random.uniform(0.7, 1.0)


def retry_delay(
    attempt: Attempt | None,
    *,
    attempt_index: int,
    max_retries: int,
    jitter: float = 1.0,
) -> float | None:
    """Delay before the next attempt, or ``None`` when the call must stop.

    ``attempt`` is ``None`` for a connection error or a timeout. A response the
    server marked retryable wins its ``Retry-After``, then
    ``details.retry_after_seconds``, then the exponential backoff.
    """

    if attempt_index >= max_retries:
        return None
    if attempt is None:
        return backoff_delay(attempt_index, jitter=jitter)
    if attempt.is_success:
        return None
    retryable = (
        attempt.status_code in RETRYABLE_STATUS_CODES
        or (attempt.code or "") in RETRYABLE_ERROR_CODES
    )
    if not retryable:
        return None
    explicit = parse_retry_after(attempt.headers.get("retry-after"))
    if explicit is None:
        explicit = _details_retry_after(attempt.payload)
    if explicit is not None:
        return explicit
    return backoff_delay(attempt_index, jitter=jitter)


def _details_retry_after(payload: Any) -> float | None:
    if not isinstance(payload, Mapping):
        return None
    details = payload.get("details")
    if not isinstance(details, Mapping):
        return None
    value = details.get("retry_after_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return float(min(max(value, 0.0), MAX_RETRY_AFTER_SECONDS))


def error_for(attempt: Attempt) -> ZelinqaAPIError:
    """Build the typed error of a failed attempt."""

    payload = attempt.payload
    if payload is None and attempt.text:
        payload = {"message": attempt.text[:500]}
    return api_error_from_response(attempt.status_code, payload, attempt.headers)


def json_payload(attempt: Attempt) -> dict[str, Any]:
    """Return the decoded JSON object of a successful attempt."""

    if not isinstance(attempt.payload, Mapping):
        raise api_error_from_response(
            attempt.status_code,
            {"message": "the Zelinqa API returned a body that is not a JSON object"},
            attempt.headers,
        )
    return dict(attempt.payload)


def text_payload(attempt: Attempt) -> str:
    """Return the raw text body of a successful attempt."""

    return attempt.text


class _Executor:
    """State shared by the sync and the async executor."""

    def __init__(self, *, max_retries: int) -> None:
        self._max_retries = max_retries

    def _next_delay(self, attempt: Attempt | None, attempt_index: int) -> float | None:
        return retry_delay(
            attempt,
            attempt_index=attempt_index,
            max_retries=self._max_retries,
            jitter=_random_jitter(),
        )


class SyncExecutor(_Executor):
    """Blocking attempt loop. Holds the only reference to the API key."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float,
        max_retries: int,
        transport: httpx.BaseTransport | None,
    ) -> None:
        super().__init__(max_retries=max_retries)
        self._http = httpx.Client(
            base_url=base_url,
            headers=client_headers(api_key),
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def send(self, request: Request) -> Attempt:
        attempt: Attempt | None = None
        last_error: Exception | None = None
        for attempt_index in range(self._max_retries + 1):
            attempt = None
            try:
                response = self._http.request(
                    request.method,
                    request.path,
                    params=request.params or None,
                    json=request.json_body,
                    headers=request.headers(),
                )
            except httpx.TransportError as exc:
                last_error = exc
            else:
                attempt = read_attempt(response)
            delay = self._next_delay(attempt, attempt_index)
            if delay is None:
                break
            time.sleep(delay)
        if attempt is None:
            raise ZelinqaConnectionError("unable to reach the Zelinqa API") from last_error
        if not attempt.is_success:
            raise error_for(attempt)
        return attempt


class AsyncExecutor(_Executor):
    """Async attempt loop. Holds the only reference to the API key."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout: float,
        max_retries: int,
        transport: httpx.AsyncBaseTransport | None,
    ) -> None:
        super().__init__(max_retries=max_retries)
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers=client_headers(api_key),
            timeout=timeout,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def send(self, request: Request) -> Attempt:
        attempt: Attempt | None = None
        last_error: Exception | None = None
        for attempt_index in range(self._max_retries + 1):
            attempt = None
            try:
                response = await self._http.request(
                    request.method,
                    request.path,
                    params=request.params or None,
                    json=request.json_body,
                    headers=request.headers(),
                )
            except httpx.TransportError as exc:
                last_error = exc
            else:
                attempt = read_attempt(response)
            delay = self._next_delay(attempt, attempt_index)
            if delay is None:
                break
            await asyncio.sleep(delay)
        if attempt is None:
            raise ZelinqaConnectionError("unable to reach the Zelinqa API") from last_error
        if not attempt.is_success:
            raise error_for(attempt)
        return attempt
