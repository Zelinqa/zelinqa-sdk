"""Every documented error must surface as its own exception type.

The envelope ``code`` decides first; the HTTP status decides when the body is
not a V1 envelope — which is what the public gateway returns on 401 (missing
header) and on 403 (invalid, revoked, or missing a statically checked scope).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from spec_examples import response_example
from zelinqa import (
    ZelinqaAPIError,
    ZelinqaAuthenticationError,
    ZelinqaClient,
    ZelinqaCompilationInProgressError,
    ZelinqaCompiledArtifactUnavailableError,
    ZelinqaConfigurationClient,
    ZelinqaConfigurationValidationError,
    ZelinqaConflictError,
    ZelinqaConstraintNoMatchError,
    ZelinqaError,
    ZelinqaIdempotencyContentionError,
    ZelinqaIdempotencyKeyReusedError,
    ZelinqaInsufficientScopeError,
    ZelinqaInvalidChoiceError,
    ZelinqaInvalidPreviousTurnError,
    ZelinqaNotFoundError,
    ZelinqaRateLimitError,
    ZelinqaServerError,
    ZelinqaStateVersionConflictError,
    ZelinqaUnknownCompilationError,
    ZelinqaUnknownConfigurationError,
    ZelinqaUnknownSessionError,
    ZelinqaValidationError,
)
from zelinqa.errors import GATEWAY_FORBIDDEN_MESSAGE, api_error_from_response

API_KEY = "nbq_live_test"
BASE_URL = "https://api.example.test"


def failing_client(
    status: int, payload: Any = None, *, text: str | None = None, headers: Any = None
) -> ZelinqaClient:
    def handler(_: httpx.Request) -> httpx.Response:
        if text is not None:
            return httpx.Response(status, text=text, headers=headers)
        return httpx.Response(status, json=payload, headers=headers)

    return ZelinqaClient(
        API_KEY, base_url=BASE_URL, max_retries=0, transport=httpx.MockTransport(handler)
    )


def raised(status: int, payload: Any = None, **kwargs: Any) -> ZelinqaAPIError:
    with failing_client(status, payload, **kwargs) as client:
        with pytest.raises(ZelinqaAPIError) as captured:
            client.get_session("ses_01J8Z")
    return captured.value


# ----------------------------------------------------- envelope code mapping


@pytest.mark.parametrize(
    ("response_name", "label", "status", "expected"),
    [
        ("Unauthorized", None, 401, ZelinqaAuthenticationError),
        ("InsufficientScope", None, 403, ZelinqaInsufficientScopeError),
        ("Unauthorized", None, 403, ZelinqaAuthenticationError),
        ("IdempotencyConflict", None, 409, ZelinqaIdempotencyKeyReusedError),
        (
            "SessionMutationConflict",
            "state_version_conflict",
            409,
            ZelinqaStateVersionConflictError,
        ),
        (
            "SessionMutationConflict",
            "idempotency_key_reused",
            409,
            ZelinqaIdempotencyKeyReusedError,
        ),
        ("PublishConflict", "compilation_in_progress", 409, ZelinqaCompilationInProgressError),
        ("PublishConflict", "idempotency_key_reused", 409, ZelinqaIdempotencyKeyReusedError),
        ("UnknownSession", None, 404, ZelinqaUnknownSessionError),
        ("UnknownConfiguration", None, 404, ZelinqaUnknownConfigurationError),
        ("UnknownCompilation", None, 404, ZelinqaUnknownCompilationError),
        ("CompiledArtifactUnavailable", None, 410, ZelinqaCompiledArtifactUnavailableError),
        ("NextUnprocessable", "invalid_previous_turn", 422, ZelinqaInvalidPreviousTurnError),
        ("NextUnprocessable", "constraint_no_match", 422, ZelinqaConstraintNoMatchError),
        ("NextUnprocessable", "invalid_choice", 422, ZelinqaInvalidChoiceError),
        ("InvalidChoice", None, 422, ZelinqaInvalidChoiceError),
        (
            "ConfigurationValidationFailed",
            "validation_publication",
            422,
            ZelinqaConfigurationValidationError,
        ),
        (
            "ConfigurationValidationFailed",
            "revision_perimee",
            422,
            ZelinqaConfigurationValidationError,
        ),
        ("IdempotencyContention", None, 503, ZelinqaIdempotencyContentionError),
    ],
)
def test_documented_envelope_maps_to_its_exception(
    response_name: str, label: str | None, status: int, expected: type[ZelinqaAPIError]
) -> None:
    payload = response_example(response_name, label)
    error = raised(status, payload)

    assert type(error) is expected
    assert error.status_code == status
    assert error.code == payload["code"]
    assert error.message == payload["message"]
    assert error.request_id == payload["request_id"]
    assert error.details == payload.get("details", {})
    assert isinstance(error, ZelinqaError)


def test_every_error_code_of_the_catalogue_is_mapped() -> None:
    from zelinqa.errors import _CODE_ERRORS
    from zelinqa.models import ErrorCode

    documented = set(ErrorCode.__args__)  # type: ignore[attr-defined]
    assert documented == set(_CODE_ERRORS), (
        f"unmapped: {sorted(documented - set(_CODE_ERRORS))}, "
        f"unknown: {sorted(set(_CODE_ERRORS) - documented)}"
    )


# ------------------------------------------------------------ error families


def test_insufficient_scope_exposes_the_scopes() -> None:
    error = raised(403, response_example("InsufficientScope"))
    assert isinstance(error, ZelinqaInsufficientScopeError)
    assert error.required_scopes == ["configuration:publish"]
    assert error.granted_scopes == ["configuration:read", "configuration:write"]


def test_state_version_conflict_exposes_both_versions() -> None:
    error = raised(409, response_example("SessionMutationConflict", "state_version_conflict"))
    assert isinstance(error, ZelinqaStateVersionConflictError)
    assert error.supplied_state_version == 7
    assert error.current_state_version == 8
    assert isinstance(error, ZelinqaConflictError)


def test_idempotency_key_reused_exposes_the_key_name() -> None:
    error = raised(409, response_example("IdempotencyConflict"))
    assert isinstance(error, ZelinqaIdempotencyKeyReusedError)
    assert error.idempotency_key == "create-session-8842"


def test_compilation_in_progress_exposes_the_running_job() -> None:
    error = raised(409, response_example("PublishConflict", "compilation_in_progress"))
    assert isinstance(error, ZelinqaCompilationInProgressError)
    assert error.compilation_id == "cmp_01K2QF"
    assert error.status == "running"


def test_unknown_session_is_a_not_found() -> None:
    error = raised(404, response_example("UnknownSession"))
    assert isinstance(error, ZelinqaNotFoundError)
    assert error.details["session_id"] == "ses_inconnue"


def test_configuration_validation_exposes_parsed_issues() -> None:
    payload = response_example("ConfigurationValidationFailed", "validation_publication")
    error = raised(422, payload)
    assert isinstance(error, ZelinqaConfigurationValidationError)
    assert isinstance(error, ZelinqaValidationError)
    assert [issue.code for issue in error.issues] == [
        "success_information_without_active_question",
        "success_information_only_in_optional_dimension",
    ]
    assert error.issues[0].entity == "success_information"
    assert error.issues[0].entity_id == "delivery_window"


def test_configuration_validation_tolerates_an_unknown_issue_code() -> None:
    error = raised(
        422,
        {
            "code": "configuration_validation_failed",
            "message": "1 anomalie.",
            "request_id": "req_new",
            "details": {"issues": [{"code": "a_code_invented_tomorrow", "message": "Nope."}]},
        },
    )
    assert isinstance(error, ZelinqaConfigurationValidationError)
    assert error.issues[0].code == "a_code_invented_tomorrow"


def test_invalid_choice_carries_the_offending_value() -> None:
    error = raised(422, response_example("InvalidChoice"))
    assert isinstance(error, ZelinqaInvalidChoiceError)
    assert error.details["invalid_value"] == "dans_deux_ans"


# ------------------------------------------------- non envelope and statuses


def test_gateway_401_without_an_envelope() -> None:
    """A missing ``Authorization`` header: the gateway body is not an envelope."""

    error = raised(401, {"message": "Unauthorized"})
    assert type(error) is ZelinqaAuthenticationError
    assert error.code is None
    assert error.message == "Unauthorized"
    assert error.request_id is None


def test_gateway_403_without_an_envelope_is_an_authentication_error() -> None:
    """Invalid, revoked, expired, or missing a statically checked scope.

    The deployed authorizer answers the same opaque body in all four cases, so
    the SDK must not claim it knows which one applied.
    """

    error = raised(403, {"message": "Forbidden"})
    assert type(error) is ZelinqaAuthenticationError
    assert error.code is None
    assert error.details == {}
    assert error.message == GATEWAY_FORBIDDEN_MESSAGE
    assert "invalid, revoked, expired" in error.message


def test_gateway_403_with_an_empty_body() -> None:
    error = raised(403, text="")
    assert type(error) is ZelinqaAuthenticationError
    assert error.message == GATEWAY_FORBIDDEN_MESSAGE


def test_403_with_the_v1_envelope_stays_an_insufficient_scope() -> None:
    """Only the service-side dynamic check produces a V1 envelope on 403."""

    error = raised(403, response_example("InsufficientScope"))
    assert type(error) is ZelinqaInsufficientScopeError
    assert error.code == "insufficient_scope"


def test_non_json_body_still_produces_a_typed_error() -> None:
    error = raised(502, text="<html>Bad gateway</html>")
    assert isinstance(error, ZelinqaServerError)
    assert error.code is None
    assert error.status_code == 502


def test_empty_body_still_produces_a_typed_error() -> None:
    error = raised(500, text="")
    assert isinstance(error, ZelinqaServerError)
    assert "status 500" in error.message


def test_request_id_falls_back_to_the_header() -> None:
    error = raised(500, text="boom", headers={"X-Request-Id": "req_header"})
    assert error.request_id == "req_header"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ZelinqaAuthenticationError),
        (403, ZelinqaAuthenticationError),
        (404, ZelinqaNotFoundError),
        (409, ZelinqaConflictError),
        (410, ZelinqaCompiledArtifactUnavailableError),
        (422, ZelinqaValidationError),
        (429, ZelinqaRateLimitError),
        (500, ZelinqaServerError),
        (503, ZelinqaServerError),
        (504, ZelinqaServerError),
        (418, ZelinqaAPIError),
    ],
)
def test_status_only_mapping(status: int, expected: type[ZelinqaAPIError]) -> None:
    error = api_error_from_response(status, {"message": "no envelope here"}, {})
    assert type(error) is expected


def test_rate_limit_carries_retry_after() -> None:
    error = raised(429, {"message": "slow down"}, headers={"Retry-After": "3"})
    assert isinstance(error, ZelinqaRateLimitError)
    assert error.retry_after == 3.0


def test_idempotency_contention_uses_details_retry_after() -> None:
    payload = response_example("IdempotencyContention")
    error = raised(503, payload)
    assert isinstance(error, ZelinqaIdempotencyContentionError)
    assert error.retry_after == 1.0


def test_idempotency_contention_caps_details_retry_after() -> None:
    payload = {
        **response_example("IdempotencyContention"),
        "details": {"retry_after_seconds": 3600},
    }
    error = raised(503, payload)
    assert isinstance(error, ZelinqaIdempotencyContentionError)
    assert error.retry_after == 30.0


def test_idempotency_contention_is_retried_then_raised() -> None:
    payload = response_example("IdempotencyContention")
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(503, json=payload, headers={"Retry-After": "0"})

    with ZelinqaClient(
        API_KEY, base_url=BASE_URL, max_retries=2, transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(ZelinqaIdempotencyContentionError):
            client.create_session(idempotency_key="create-session-8842")

    assert len(attempts) == 3
    assert {attempt.headers["Idempotency-Key"] for attempt in attempts} == {"create-session-8842"}


# --------------------------------------------------------------- formatting


def test_str_shows_the_code_message_and_request_id() -> None:
    error = raised(404, response_example("UnknownSession"))
    assert str(error) == "unknown_session: La session demandée est inconnue. (request_id=req_9007)"


def test_str_falls_back_to_the_status_without_a_code() -> None:
    error = api_error_from_response(500, {"message": "boom"}, {})
    assert str(error) == "500: boom"


@pytest.mark.parametrize("status", [401, 403, 404, 409, 410, 422, 429, 500, 503])
def test_no_error_ever_leaks_the_api_key(status: int) -> None:
    error = raised(status, {"message": f"failure {status}", "code": None})
    for rendering in (str(error), repr(error), str(error.details), error.message):
        assert API_KEY not in rendering


def test_client_repr_never_leaks_the_api_key() -> None:
    runtime = ZelinqaClient(API_KEY, base_url=BASE_URL)
    management = ZelinqaConfigurationClient(API_KEY, base_url=BASE_URL)
    try:
        for rendering in (repr(runtime), str(runtime), repr(management), str(management)):
            assert API_KEY not in rendering
    finally:
        runtime.close()
        management.close()


async def test_async_client_surfaces_the_same_errors() -> None:
    from zelinqa import AsyncZelinqaClient

    payload = response_example("UnknownSession")
    transport = httpx.MockTransport(lambda _: httpx.Response(404, json=payload))
    async with AsyncZelinqaClient(
        API_KEY, base_url=BASE_URL, max_retries=0, transport=transport
    ) as c:
        with pytest.raises(ZelinqaUnknownSessionError) as captured:
            await c.get_session("ses_inconnue")
    assert captured.value.request_id == "req_9007"
