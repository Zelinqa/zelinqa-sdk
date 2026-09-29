"""Runtime client: headers, paths, bodies, parsing and the session handle.

Every method is exercised twice, blocking and async, over an
``httpx.MockTransport``. Responses are the payloads documented in the frozen
OpenAPI snapshot.
"""

from __future__ import annotations

import copy
from typing import Any

import httpx
import pytest
from spec_examples import (
    ARRET_SANS_QUESTION,
    DECISION_APRES_MAX_TURNS,
    DECISION_NORMALE,
    SESSION_NEUVE,
    operation_example,
    response_example,
)
from zelinqa import (
    AsyncSession,
    AsyncZelinqaClient,
    ClientUpdates,
    ConversationSummary,
    NextResponse,
    PreviousTurn,
    SelectionOptions,
    Session,
    SessionStateResponse,
    SetDataUpdate,
    StructuredAnswer,
    ZelinqaClient,
    ZelinqaStateVersionConflictError,
)
from zelinqa._version import __version__

API_KEY = "nbq_live_test"
BASE_URL = "https://api.example.test"
FEEDBACK_ACCEPTED = operation_example("/v1/sessions/{session_id}/feedback", "post", "202")
STATE_VERSION_CONFLICT = response_example("SessionMutationConflict", "state_version_conflict")


class Recorder:
    """Collects the requests a client actually sent."""

    def __init__(self, *responses: httpx.Response) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def body(self, index: int = -1) -> Any:
        import json

        return json.loads(self.requests[index].content)


def json_response(status: int, payload: Any) -> httpx.Response:
    return httpx.Response(status, json=copy.deepcopy(payload))


def sync_client(recorder: Recorder, **kwargs: Any) -> ZelinqaClient:
    return ZelinqaClient(
        API_KEY,
        base_url=BASE_URL,
        max_retries=0,
        transport=httpx.MockTransport(recorder),
        **kwargs,
    )


def async_client(recorder: Recorder, **kwargs: Any) -> AsyncZelinqaClient:
    return AsyncZelinqaClient(
        API_KEY,
        base_url=BASE_URL,
        max_retries=0,
        transport=httpx.MockTransport(recorder),
        **kwargs,
    )


# ------------------------------------------------------------- construction


def test_repr_and_str_never_leak_the_key() -> None:
    recorder = Recorder(json_response(201, SESSION_NEUVE))
    with sync_client(recorder) as client:
        assert API_KEY not in repr(client)
        assert API_KEY not in str(client)


async def test_async_repr_never_leaks_the_key() -> None:
    recorder = Recorder(json_response(201, SESSION_NEUVE))
    async with async_client(recorder) as client:
        assert API_KEY not in repr(client)


def test_api_key_falls_back_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZELINQA_API_KEY", "nbq_live_from_env")
    monkeypatch.setenv("ZELINQA_BASE_URL", BASE_URL)
    recorder = Recorder(json_response(201, SESSION_NEUVE))
    with ZelinqaClient(max_retries=0, transport=httpx.MockTransport(recorder)) as client:
        client.create_session()
    assert recorder.last.headers["Authorization"] == "Bearer nbq_live_from_env"
    assert str(recorder.last.url).startswith(BASE_URL)


def test_missing_api_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZELINQA_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key is required"):
        ZelinqaClient()


@pytest.mark.parametrize(
    ("base_url", "match"),
    [
        ("https://user:pass@example.test", "credentials"),
        ("ftp://example.test", "absolute HTTP"),
        ("https://example.test/?a=1", "query string"),
        ("/relative", "absolute HTTP"),
    ],
)
def test_base_url_is_validated(base_url: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        ZelinqaClient(API_KEY, base_url=base_url)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [({"timeout": 0}, "timeout"), ({"max_retries": -1}, "max_retries")],
)
def test_settings_are_validated(kwargs: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        ZelinqaClient(API_KEY, **kwargs)


# ------------------------------------------------------------ create_session


def test_create_session_sends_headers_path_and_body() -> None:
    recorder = Recorder(json_response(201, SESSION_NEUVE))
    with sync_client(recorder) as client:
        state = client.create_session(
            client_reference="crm-lead-8842",
            max_turns=10,
            initial_history=[{"role": "user", "text": "Un canapé contemporain."}],
            idempotency_key="create-session-8842",
        )

    request = recorder.last
    assert request.method == "POST"
    assert request.url.path == "/v1/sessions"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["Idempotency-Key"] == "create-session-8842"
    assert request.headers["User-Agent"] == f"zelinqa-python/{__version__}"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["Content-Type"] == "application/json"
    assert recorder.body() == {
        "client_reference": "crm-lead-8842",
        "max_turns": 10,
        "initial_history": [{"role": "user", "text": "Un canapé contemporain."}],
    }
    assert state.session_id == "ses_01J8Z"
    assert state.versions.state_version == 0
    assert state.status == "active"
    assert state.pending_decision is None
    assert state.request_id == "req_1001"


def test_create_session_omits_unset_fields_and_generates_a_key() -> None:
    recorder = Recorder(json_response(201, SESSION_NEUVE))
    with sync_client(recorder) as client:
        client.create_session()

    assert recorder.body() == {}
    assert len(recorder.last.headers["Idempotency-Key"]) >= 8


async def test_async_create_session() -> None:
    recorder = Recorder(json_response(201, SESSION_NEUVE))
    async with async_client(recorder) as client:
        state = await client.create_session(client_reference="crm-lead-8842")
    assert state.session_id == "ses_01J8Z"
    assert recorder.last.url.path == "/v1/sessions"
    assert recorder.body() == {"client_reference": "crm-lead-8842"}


# --------------------------------------------------------------------- next


def test_next_sends_state_version_and_parses_candidates() -> None:
    recorder = Recorder(json_response(200, DECISION_NORMALE))
    with sync_client(recorder) as client:
        decision = client.next(
            "ses_01J8Z",
            state_version=3,
            previous_turn=PreviousTurn(
                assistant_text="Et côté budget ?",
                user_text="Autour de 2 000 euros.",
            ),
            idempotency_key="next-ses01J8Z-turn-4",
        )

    assert recorder.last.url.path == "/v1/sessions/ses_01J8Z/next"
    assert recorder.body() == {
        "state_version": 3,
        "previous_turn": {
            "assistant_text": "Et côté budget ?",
            "user_text": "Autour de 2 000 euros.",
        },
    }
    assert decision.action == "ask"
    assert decision.decision_id == "dec_7f2a"
    assert [candidate.question_id for candidate in decision.candidates] == ["q_budget", "q_delai"]
    assert decision.candidates[1].choices[0].choice_id == "choice_1m"
    assert decision.versions.state_version == 4
    assert decision.warnings == []


def test_next_accepts_plain_mappings_and_typed_models() -> None:
    recorder = Recorder(json_response(200, DECISION_NORMALE))
    with sync_client(recorder) as client:
        client.next(
            "ses_01J8Z",
            state_version=6,
            previous_turn={"structured_answer": {"choice_ids": ["choice_contemporain"]}},
            context_update=ConversationSummary(text="Le visiteur emménage en mars."),
            client_updates=ClientUpdates(data=[SetDataUpdate(id="sdk_budget", value=2500)]),
            selection=SelectionOptions(candidate_count=2, allowed_question_types=["single_choice"]),
        )

    assert recorder.body() == {
        "state_version": 6,
        "previous_turn": {"structured_answer": {"choice_ids": ["choice_contemporain"]}},
        "context_update": {"mode": "summary", "text": "Le visiteur emménage en mars."},
        "client_updates": {"data": [{"id": "sdk_budget", "operation": "set", "value": 2500}]},
        "selection": {"candidate_count": 2, "allowed_question_types": ["single_choice"]},
    }


def test_next_parses_a_hard_stop() -> None:
    recorder = Recorder(json_response(200, ARRET_SANS_QUESTION))
    with sync_client(recorder) as client:
        decision = client.next("ses_01J8Z", state_version=24)

    assert decision.action == "stop"
    assert decision.decision_id is None
    assert decision.stop_reason == "no_question_available"
    assert decision.candidates == []
    assert decision.warnings == ["objective_achieved"]


def test_next_parses_the_max_turns_stop() -> None:
    recorder = Recorder(json_response(200, DECISION_APRES_MAX_TURNS))
    with sync_client(recorder) as client:
        decision = client.next(
            "ses_01J8Z",
            state_version=20,
            previous_turn={"user_text": "Plutôt dans les trois mois."},
        )

    assert decision.action == "stop"
    assert decision.stop_reason == "max_turns_reached"
    assert decision.decision_id is None
    assert decision.candidates == []
    assert decision.turn_count == 10
    assert decision.turns_remaining == 0
    assert decision.warnings == []


def test_next_rejects_an_invalid_body_before_sending() -> None:
    recorder = Recorder(json_response(200, DECISION_NORMALE))
    with sync_client(recorder) as client:
        with pytest.raises(ValueError, match="state_version"):
            client.next("ses_01J8Z", state_version=-1)
        with pytest.raises(ValueError, match="previous_turn"):
            client.next("ses_01J8Z", state_version=1, previous_turn={})
    assert recorder.requests == []


def test_session_id_is_escaped_in_the_path() -> None:
    recorder = Recorder(json_response(200, DECISION_NORMALE))
    with sync_client(recorder) as client:
        client.next("ses/../secret", state_version=0)
    assert recorder.last.url.raw_path == b"/v1/sessions/ses%2F..%2Fsecret/next"


async def test_async_next() -> None:
    recorder = Recorder(json_response(200, DECISION_NORMALE))
    async with async_client(recorder) as client:
        decision = await client.next("ses_01J8Z", state_version=3)
    assert decision.decision_id == "dec_7f2a"
    assert recorder.last.url.path == "/v1/sessions/ses_01J8Z/next"


# ------------------------------------------------------------- apply_events


def test_apply_events_sends_client_updates() -> None:
    recorder = Recorder(json_response(200, SESSION_NEUVE))
    with sync_client(recorder) as client:
        client.apply_events(
            "ses_01J8Z",
            state_version=2,
            client_updates={"dimensions": [{"id": "so_livraison", "operation": "exclude"}]},
        )

    assert recorder.last.url.path == "/v1/sessions/ses_01J8Z/events"
    assert recorder.body() == {
        "state_version": 2,
        "client_updates": {"dimensions": [{"id": "so_livraison", "operation": "exclude"}]},
    }
    assert recorder.last.headers["Idempotency-Key"]


def test_apply_events_requires_a_payload() -> None:
    recorder = Recorder(json_response(200, SESSION_NEUVE))
    with sync_client(recorder) as client:
        with pytest.raises(ValueError, match="context_update or client_updates"):
            client.apply_events("ses_01J8Z", state_version=2)
    assert recorder.requests == []


async def test_async_apply_events() -> None:
    recorder = Recorder(json_response(200, SESSION_NEUVE))
    async with async_client(recorder) as client:
        state = await client.apply_events(
            "ses_01J8Z",
            state_version=3,
            context_update={"mode": "messages", "messages": [{"role": "user", "text": "Bonjour"}]},
        )
    assert state.session_id == "ses_01J8Z"
    assert recorder.body()["context_update"]["mode"] == "messages"


# -------------------------------------------------------------- get_session


def test_get_session_is_a_plain_get() -> None:
    recorder = Recorder(json_response(200, SESSION_NEUVE))
    with sync_client(recorder) as client:
        state = client.get_session("ses_01J8Z")

    assert recorder.last.method == "GET"
    assert recorder.last.url.path == "/v1/sessions/ses_01J8Z"
    assert "Idempotency-Key" not in recorder.last.headers
    assert "Content-Type" not in recorder.last.headers
    assert state.targets == {}


def test_get_session_parses_a_pending_decision() -> None:
    payload = copy.deepcopy(SESSION_NEUVE)
    payload["pending_decision"] = {
        "decision_id": "dec_7f2a",
        "candidates": DECISION_NORMALE["candidates"],
    }
    payload["targets"] = {
        "annual_budget": {"kind": "data", "status": "confirmed", "value": 2500, "coverage": 1}
    }
    recorder = Recorder(json_response(200, payload))
    with sync_client(recorder) as client:
        state = client.get_session("ses_01J8Z")

    assert state.pending_decision is not None
    assert state.pending_decision.decision_id == "dec_7f2a"
    assert len(state.pending_decision.candidates) == 2
    assert state.targets["annual_budget"].status == "confirmed"
    assert state.targets["annual_budget"].value == 2500


def test_response_parsing_ignores_unknown_fields() -> None:
    payload = copy.deepcopy(SESSION_NEUVE)
    payload["a_field_from_a_future_version"] = {"anything": True}
    recorder = Recorder(json_response(200, payload))
    with sync_client(recorder) as client:
        assert client.get_session("ses_01J8Z").session_id == "ses_01J8Z"


def test_server_enumerations_accept_values_from_a_later_release() -> None:
    payload = copy.deepcopy(ARRET_SANS_QUESTION)
    payload["stop_reason"] = "a_future_stop_reason"
    payload["warnings"] = ["objective_achieved", "a_future_warning"]
    payload["degraded"] = True
    payload["degraded_reasons"] = ["a_future_degraded_reason"]
    payload["progress"]["objective"]["computed_status"] = "a_future_status"
    recorder = Recorder(json_response(200, payload))
    with sync_client(recorder) as client:
        decision = client.next("ses_01J8Z", state_version=24)

    assert decision.stop_reason == "a_future_stop_reason"
    assert decision.warnings == ["objective_achieved", "a_future_warning"]
    assert decision.degraded_reasons == ["a_future_degraded_reason"]
    assert decision.progress.objective.computed_status == "a_future_status"


def test_session_status_accepts_a_value_from_a_later_release() -> None:
    payload = copy.deepcopy(SESSION_NEUVE)
    payload["status"] = "a_future_status"
    assert SessionStateResponse.model_validate(payload).status == "a_future_status"


def test_next_action_stays_closed() -> None:
    payload = copy.deepcopy(ARRET_SANS_QUESTION)
    payload["action"] = "a_future_action"
    with pytest.raises(ValueError, match="action"):
        NextResponse.model_validate(payload)


async def test_async_get_session() -> None:
    recorder = Recorder(json_response(200, SESSION_NEUVE))
    async with async_client(recorder) as client:
        state = await client.get_session("ses_01J8Z")
    assert state.max_turns == 15


# ----------------------------------------------------------- submit_feedback


def test_submit_feedback_sends_result_label_and_metadata() -> None:
    recorder = Recorder(json_response(202, FEEDBACK_ACCEPTED))
    with sync_client(recorder) as client:
        feedback = client.submit_feedback(
            "ses_01J8Z",
            result="success",
            label="achat",
            metadata={"order_id": "SO-99120"},
            idempotency_key="feedback-ses01J8Z",
        )

    assert recorder.last.url.path == "/v1/sessions/ses_01J8Z/feedback"
    assert recorder.body() == {
        "result": "success",
        "label": "achat",
        "metadata": {"order_id": "SO-99120"},
    }
    assert feedback.feedback_id == "fbk_02K1"
    assert feedback.recorded_at.year == 2026


def test_submit_feedback_omits_unset_fields() -> None:
    recorder = Recorder(json_response(202, FEEDBACK_ACCEPTED))
    with sync_client(recorder) as client:
        client.submit_feedback("ses_01J8Z", result="failure")
    assert recorder.body() == {"result": "failure"}


async def test_async_submit_feedback() -> None:
    recorder = Recorder(json_response(202, FEEDBACK_ACCEPTED))
    async with async_client(recorder) as client:
        feedback = await client.submit_feedback("ses_01J8Z", result="partial")
    assert feedback.session_id == "ses_01J8Z"


# ------------------------------------------------------------ session handle


def test_start_session_returns_a_handle_tracking_state_version() -> None:
    recorder = Recorder(json_response(201, SESSION_NEUVE), json_response(200, DECISION_NORMALE))
    with sync_client(recorder) as client:
        session = client.start_session(client_reference="crm-lead-8842")
        assert isinstance(session, Session)
        assert session.id == "ses_01J8Z"
        assert session.state_version == 0
        assert session.state is not None
        assert session.pending_decision is None

        decision = session.next(previous_turn={"user_text": "Un canapé contemporain."})

    assert recorder.body(1)["state_version"] == 0
    assert session.state_version == 4
    assert decision.decision_id == "dec_7f2a"
    assert session.pending_decision is not None
    assert session.pending_decision.decision_id == "dec_7f2a"
    assert [c.question_id for c in session.pending_decision.candidates] == ["q_budget", "q_delai"]
    assert API_KEY not in repr(session)


def test_handle_forwards_an_explicit_state_version() -> None:
    recorder = Recorder(json_response(201, SESSION_NEUVE), json_response(200, DECISION_NORMALE))
    with sync_client(recorder) as client:
        session = client.start_session()
        session.next(state_version=2)
    assert recorder.body(1)["state_version"] == 2


def test_handle_clears_the_pending_decision_on_a_stop() -> None:
    recorder = Recorder(json_response(201, SESSION_NEUVE), json_response(200, ARRET_SANS_QUESTION))
    with sync_client(recorder) as client:
        session = client.start_session()
        session.next()
    assert session.pending_decision is None
    assert session.state_version == 25


def test_handle_keeps_returning_the_max_turns_stop() -> None:
    recorder = Recorder(
        json_response(201, SESSION_NEUVE),
        json_response(200, DECISION_APRES_MAX_TURNS),
    )
    with sync_client(recorder) as client:
        session = client.start_session()
        first = session.next(previous_turn={"user_text": "Plutôt dans les trois mois."})
        again = session.next()

    for decision in (first, again):
        assert decision.action == "stop"
        assert decision.stop_reason == "max_turns_reached"
    assert session.pending_decision is None
    assert session.state_version == 21
    assert "previous_turn" not in recorder.body(2)


async def test_async_handle_stops_at_max_turns() -> None:
    recorder = Recorder(
        json_response(201, SESSION_NEUVE),
        json_response(200, DECISION_APRES_MAX_TURNS),
    )
    async with async_client(recorder) as client:
        session = await client.start_session()
        decision = await session.next(previous_turn={"user_text": "Plutôt dans les trois mois."})

    assert decision.stop_reason == "max_turns_reached"
    assert session.pending_decision is None


def test_handle_tracks_events_and_refresh() -> None:
    advanced = copy.deepcopy(SESSION_NEUVE)
    advanced["versions"] = {**SESSION_NEUVE["versions"], "state_version": 7}
    refreshed = copy.deepcopy(SESSION_NEUVE)
    refreshed["versions"] = {**SESSION_NEUVE["versions"], "state_version": 9}

    recorder = Recorder(
        json_response(201, SESSION_NEUVE),
        json_response(200, advanced),
        json_response(200, refreshed),
    )
    with sync_client(recorder) as client:
        session = client.start_session()
        session.apply_events(client_updates={"data": [{"id": "sdk_budget", "value": 2500}]})
        assert session.state_version == 7
        session.refresh()

    assert session.state_version == 9
    assert recorder.requests[-1].method == "GET"


def test_handle_never_hides_a_state_version_conflict() -> None:
    recorder = Recorder(
        json_response(201, SESSION_NEUVE),
        json_response(409, STATE_VERSION_CONFLICT),
    )
    with sync_client(recorder) as client:
        session = client.start_session()
        with pytest.raises(ZelinqaStateVersionConflictError) as captured:
            session.next()

    assert captured.value.supplied_state_version == 7
    assert captured.value.current_state_version == 8
    # The handle is not silently resynchronised: the caller decides.
    assert session.state_version == 0
    assert len(recorder.requests) == 2


def test_resume_session_reads_the_state() -> None:
    payload = copy.deepcopy(SESSION_NEUVE)
    payload["versions"] = {**SESSION_NEUVE["versions"], "state_version": 12}
    payload["pending_decision"] = {
        "decision_id": "dec_7f2a",
        "candidates": DECISION_NORMALE["candidates"],
    }
    recorder = Recorder(json_response(200, payload))
    with sync_client(recorder) as client:
        session = client.resume_session("ses_01J8Z")

    assert recorder.last.method == "GET"
    assert session.state_version == 12
    assert session.pending_decision is not None


def test_handle_submits_feedback() -> None:
    recorder = Recorder(json_response(201, SESSION_NEUVE), json_response(202, FEEDBACK_ACCEPTED))
    with sync_client(recorder) as client:
        session = client.start_session()
        feedback = session.submit_feedback(result="success", label="achat")
    assert feedback.feedback_id == "fbk_02K1"
    assert recorder.last.url.path == "/v1/sessions/ses_01J8Z/feedback"


async def test_async_session_handle() -> None:
    recorder = Recorder(
        json_response(201, SESSION_NEUVE),
        json_response(200, DECISION_NORMALE),
        json_response(200, SESSION_NEUVE),
        json_response(202, FEEDBACK_ACCEPTED),
    )
    async with async_client(recorder) as client:
        session = await client.start_session(client_reference="crm-lead-8842")
        assert isinstance(session, AsyncSession)
        assert session.state_version == 0
        await session.next(previous_turn={"user_text": "Un canapé contemporain."})
        assert session.state_version == 4
        assert session.pending_decision is not None
        await session.refresh()
        assert session.state_version == 0
        feedback = await session.submit_feedback(result="success")

    assert feedback.feedback_id == "fbk_02K1"
    assert API_KEY not in repr(session)


async def test_async_resume_session_and_conflict() -> None:
    recorder = Recorder(
        json_response(200, SESSION_NEUVE),
        json_response(409, STATE_VERSION_CONFLICT),
    )
    async with async_client(recorder) as client:
        session = await client.resume_session("ses_01J8Z")
        with pytest.raises(ZelinqaStateVersionConflictError):
            await session.apply_events(client_updates={"objective": {"operation": "clear"}})
    assert session.state_version == 0


# --------------------------------------------------------- model validation


def test_structured_answer_rejects_duplicate_choices() -> None:
    with pytest.raises(ValueError, match="unique"):
        StructuredAnswer(choice_ids=["a", "a"])


def test_client_updates_requires_at_least_one_field() -> None:
    with pytest.raises(ValueError, match="at least one field"):
        ClientUpdates()


def test_request_models_reject_unknown_fields() -> None:
    with pytest.raises(ValueError, match="typo"):
        PreviousTurn.model_validate({"user_text": "hello", "typo": 1})
