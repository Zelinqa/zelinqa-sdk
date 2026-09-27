"""Retry policy: what is retried, how long the SDK waits, and with which key.

The delays are asserted, not slept: ``time.sleep`` and ``asyncio.sleep`` are
captured, and the jitter factor is pinned to 1.0 so the backoff is exact.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from wsgiref.handlers import format_date_time

import httpx
import pytest
from spec_examples import SESSION_NEUVE, response_example
from zelinqa import (
    AsyncZelinqaClient,
    ZelinqaAPIError,
    ZelinqaAuthenticationError,
    ZelinqaConnectionError,
    ZelinqaRateLimitError,
    ZelinqaServerError,
)
from zelinqa import _http as http_module
from zelinqa import errors as errors_module

API_KEY = "nbq_live_test"
BASE_URL = "https://api.example.test"


@pytest.fixture(autouse=True)
def no_jitter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the jitter so a backoff delay is a single expected number."""

    monkeypatch.setattr(http_module, "_random_jitter", lambda: 1.0)


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Capture the blocking and the async sleeps without waiting."""

    delays: list[float] = []

    def fake_sleep(delay: float) -> None:
        delays.append(delay)

    async def fake_async_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(http_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(http_module.asyncio, "sleep", fake_async_sleep)
    return delays


class Handler:
    """Replays a scripted sequence of responses or raised transport errors."""

    def __init__(self, *steps: httpx.Response | Exception) -> None:
        self._steps = list(steps)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self._steps.pop(0) if len(self._steps) > 1 else self._steps[0]
        if isinstance(step, Exception):
            raise step
        return step

    @property
    def keys(self) -> set[str]:
        return {request.headers["Idempotency-Key"] for request in self.requests}


def ok(payload: Any = SESSION_NEUVE, status: int = 201) -> httpx.Response:
    return httpx.Response(status, json=payload)


def call_create(handler: Handler, *, max_retries: int = 2, **kwargs: Any) -> Any:
    from zelinqa import ZelinqaClient

    with ZelinqaClient(
        API_KEY,
        base_url=BASE_URL,
        max_retries=max_retries,
        transport=httpx.MockTransport(handler),
    ) as instance:
        return instance.create_session(**kwargs)


def call_get(handler: Handler, *, max_retries: int = 2) -> Any:
    from zelinqa import ZelinqaClient

    with ZelinqaClient(
        API_KEY,
        base_url=BASE_URL,
        max_retries=max_retries,
        transport=httpx.MockTransport(handler),
    ) as instance:
        return instance.get_session("ses_01J8Z")


# ------------------------------------------------------------------- backoff


def test_backoff_doubles_and_is_capped() -> None:
    delays = [http_module.backoff_delay(index) for index in range(7)]
    assert delays == [0.5, 1.0, 2.0, 4.0, 8.0, 10.0, 10.0]


def test_jitter_only_shortens_the_nominal_delay() -> None:
    assert http_module.backoff_delay(1, jitter=0.7) == 0.7
    assert http_module.backoff_delay(1, jitter=1.0) == 1.0


def test_retryable_5xx_uses_the_exponential_backoff(slept: list[float]) -> None:
    handler = Handler(httpx.Response(503, json={"message": "later"}), ok())
    state = call_create(handler, max_retries=2, idempotency_key="create-session-8842")

    assert state.session_id == "ses_01J8Z"
    assert slept == [0.5]
    assert len(handler.requests) == 2
    assert handler.keys == {"create-session-8842"}


def test_backoff_grows_across_attempts(slept: list[float]) -> None:
    handler = Handler(
        httpx.Response(500, json={"message": "boom"}),
        httpx.Response(502, json={"message": "boom"}),
        ok(),
    )
    call_create(handler, max_retries=3)
    assert slept == [0.5, 1.0]
    assert len(handler.requests) == 3


# -------------------------------------------------------------- Retry-After


def test_retry_after_in_seconds_wins_over_the_backoff(slept: list[float]) -> None:
    handler = Handler(
        httpx.Response(429, json={"message": "slow"}, headers={"Retry-After": "2"}), ok()
    )
    call_create(handler)
    assert slept == [2.0]


def test_retry_after_as_an_http_date(slept: list[float]) -> None:
    when = format_date_time((datetime.now(tz=UTC) + timedelta(seconds=5)).timestamp())
    handler = Handler(
        httpx.Response(503, json={"message": "later"}, headers={"Retry-After": when}), ok()
    )
    call_create(handler)
    assert len(slept) == 1
    assert 3.0 <= slept[0] <= 5.0


def test_retry_after_is_capped(slept: list[float]) -> None:
    handler = Handler(
        httpx.Response(429, json={"message": "slow"}, headers={"Retry-After": "600"}), ok()
    )
    call_create(handler)
    assert slept == [errors_module.MAX_RETRY_AFTER_SECONDS]


def test_retry_after_in_the_past_is_clamped_to_zero(slept: list[float]) -> None:
    when = format_date_time((datetime.now(tz=UTC) - timedelta(seconds=60)).timestamp())
    handler = Handler(
        httpx.Response(503, json={"message": "later"}, headers={"Retry-After": when}), ok()
    )
    call_create(handler)
    assert slept == [0.0]


def test_unparseable_retry_after_falls_back_to_the_backoff(slept: list[float]) -> None:
    handler = Handler(
        httpx.Response(503, json={"message": "later"}, headers={"Retry-After": "soon"}), ok()
    )
    call_create(handler)
    assert slept == [0.5]


def test_details_retry_after_seconds_is_honoured(slept: list[float]) -> None:
    handler = Handler(httpx.Response(503, json=response_example("IdempotencyContention")), ok())
    call_create(handler)
    assert slept == [1.0]


def test_details_retry_after_seconds_is_capped(slept: list[float]) -> None:
    payload = {
        **response_example("IdempotencyContention"),
        "details": {"retry_after_seconds": 3600},
    }
    handler = Handler(httpx.Response(503, json=payload), ok())
    call_create(handler)
    assert slept == [errors_module.MAX_RETRY_AFTER_SECONDS]


def test_idempotency_contention_is_retried_on_its_code(slept: list[float]) -> None:
    payload = {**response_example("IdempotencyContention"), "details": {}}
    handler = Handler(httpx.Response(503, json=payload), ok())
    state = call_create(handler, idempotency_key="create-session-8842")
    assert state.session_id == "ses_01J8Z"
    assert slept == [0.5]


# ------------------------------------------------------------- no retry 4xx


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 410, 422])
def test_client_errors_are_never_retried(status: int, slept: list[float]) -> None:
    handler = Handler(httpx.Response(status, json={"message": "nope"}))
    with pytest.raises(ZelinqaAPIError) as captured:
        call_create(handler, max_retries=3)

    assert len(handler.requests) == 1
    assert slept == []
    assert captured.value.status_code == status


def test_authentication_error_is_immediate(slept: list[float]) -> None:
    handler = Handler(httpx.Response(401, json={"message": "Unauthorized"}))
    with pytest.raises(ZelinqaAuthenticationError):
        call_create(handler, max_retries=5)
    assert len(handler.requests) == 1
    assert slept == []


# ------------------------------------------------------- connection failures


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("too slow"),
        httpx.ConnectTimeout("too slow"),
        httpx.RemoteProtocolError("truncated"),
    ],
)
def test_transport_errors_are_retried_then_wrapped(error: Exception, slept: list[float]) -> None:
    handler = Handler(error)
    with pytest.raises(ZelinqaConnectionError) as captured:
        call_create(handler, max_retries=2)

    assert len(handler.requests) == 3
    assert slept == [0.5, 1.0]
    assert isinstance(captured.value.__cause__, type(error))
    assert API_KEY not in str(captured.value)


def test_a_transport_error_can_recover(slept: list[float]) -> None:
    handler = Handler(httpx.ConnectError("refused"), ok())
    state = call_create(handler, idempotency_key="create-session-8842")
    assert state.session_id == "ses_01J8Z"
    assert handler.keys == {"create-session-8842"}
    assert slept == [0.5]


# --------------------------------------------------------- retry accounting


@pytest.mark.parametrize(("max_retries", "expected"), [(0, 1), (1, 2), (3, 4)])
def test_max_retries_is_respected(max_retries: int, expected: int, slept: list[float]) -> None:
    handler = Handler(httpx.Response(500, json={"message": "boom"}))
    with pytest.raises(ZelinqaServerError):
        call_create(handler, max_retries=max_retries)

    assert len(handler.requests) == expected
    assert len(slept) == max_retries


def test_the_generated_idempotency_key_is_stable_across_retries(slept: list[float]) -> None:
    handler = Handler(
        httpx.Response(503, json={"message": "later"}),
        httpx.Response(503, json={"message": "later"}),
        ok(),
    )
    call_create(handler, max_retries=2)

    assert len(handler.requests) == 3
    assert len(handler.keys) == 1


def test_two_logical_calls_use_two_generated_keys() -> None:
    from zelinqa import ZelinqaClient

    handler = Handler(ok())
    with ZelinqaClient(
        API_KEY, base_url=BASE_URL, max_retries=0, transport=httpx.MockTransport(handler)
    ) as instance:
        instance.create_session()
        instance.create_session()
    assert len(handler.keys) == 2


def test_get_requests_are_retried_too(slept: list[float]) -> None:
    handler = Handler(httpx.Response(504, json={"message": "gateway"}), ok(SESSION_NEUVE, 200))
    state = call_get(handler, max_retries=1)

    assert state.session_id == "ses_01J8Z"
    assert len(handler.requests) == 2
    assert slept == [0.5]
    assert "Idempotency-Key" not in handler.requests[0].headers


def test_rate_limit_raises_once_the_budget_is_spent(slept: list[float]) -> None:
    handler = Handler(httpx.Response(429, json={"message": "slow"}, headers={"Retry-After": "1"}))
    with pytest.raises(ZelinqaRateLimitError) as captured:
        call_create(handler, max_retries=1)

    assert len(handler.requests) == 2
    assert slept == [1.0]
    assert captured.value.retry_after == 1.0


def test_idempotency_key_bounds_are_validated() -> None:
    handler = Handler(ok())
    with pytest.raises(ValueError, match="between 8 and 128"):
        call_create(handler, idempotency_key="short")
    with pytest.raises(ValueError, match="must not be empty"):
        call_create(handler, idempotency_key="   ")


# ------------------------------------------------------------------- policy


@pytest.mark.parametrize("status", sorted(http_module.RETRYABLE_STATUS_CODES))
def test_retry_delay_accepts_every_retryable_status(status: int) -> None:
    attempt = http_module.Attempt(
        status_code=status, payload={}, text="", code=None, headers=httpx.Headers()
    )
    assert http_module.retry_delay(attempt, attempt_index=0, max_retries=2) == 0.5


def test_retry_delay_stops_on_the_last_attempt() -> None:
    attempt = http_module.Attempt(
        status_code=500, payload={}, text="", code=None, headers=httpx.Headers()
    )
    assert http_module.retry_delay(attempt, attempt_index=2, max_retries=2) is None


def test_retry_delay_stops_on_a_success() -> None:
    attempt = http_module.Attempt(
        status_code=200, payload={}, text="", code=None, headers=httpx.Headers()
    )
    assert http_module.retry_delay(attempt, attempt_index=0, max_retries=2) is None


# -------------------------------------------------------------------- async


async def test_async_client_retries_with_the_same_policy(slept: list[float]) -> None:
    handler = Handler(httpx.Response(503, json={"message": "later"}), ok())
    async with AsyncZelinqaClient(
        API_KEY, base_url=BASE_URL, max_retries=2, transport=httpx.MockTransport(handler)
    ) as instance:
        state = await instance.create_session(idempotency_key="create-session-8842")

    assert state.session_id == "ses_01J8Z"
    assert slept == [0.5]
    assert handler.keys == {"create-session-8842"}


async def test_async_client_caps_details_retry_after(slept: list[float]) -> None:
    handler = Handler(
        httpx.Response(503, json={"message": "later", "details": {"retry_after_seconds": 3600}}),
        ok(),
    )
    async with AsyncZelinqaClient(
        API_KEY, base_url=BASE_URL, max_retries=1, transport=httpx.MockTransport(handler)
    ) as instance:
        state = await instance.create_session()

    assert state.session_id == "ses_01J8Z"
    assert slept == [30.0]


def test_nonfinite_retry_after_falls_back_to_backoff() -> None:
    attempt = http_module.Attempt(
        status_code=503,
        payload={"details": {"retry_after_seconds": float("nan")}},
        text="",
        code=None,
        headers=httpx.Headers({"Retry-After": "nan"}),
    )
    assert http_module.retry_delay(attempt, attempt_index=0, max_retries=1) == 0.5


async def test_async_transport_errors_are_wrapped(slept: list[float]) -> None:
    handler = Handler(httpx.ConnectError("refused"))
    async with AsyncZelinqaClient(
        API_KEY, base_url=BASE_URL, max_retries=1, transport=httpx.MockTransport(handler)
    ) as instance:
        with pytest.raises(ZelinqaConnectionError):
            await instance.create_session()

    assert len(handler.requests) == 2
    assert slept == [0.5]


async def test_async_client_errors_are_not_retried(slept: list[float]) -> None:
    handler = Handler(httpx.Response(422, json={"message": "nope"}))
    async with AsyncZelinqaClient(
        API_KEY, base_url=BASE_URL, max_retries=3, transport=httpx.MockTransport(handler)
    ) as instance:
        with pytest.raises(Exception, match="nope"):
            await instance.create_session()

    assert len(handler.requests) == 1
    assert slept == []
