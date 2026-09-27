import copy

import pytest
from spec_examples import DECISION_NORMALE, SESSION_NEUVE
from test_runtime_client import Recorder, async_client, json_response, sync_client
from zelinqa import Candidate, PendingDecisionView, answer_turn
from zelinqa.configuration import _changes_body
from zelinqa.models import ConfiguredQuestion


def pending(kind="semi_open", mode="multiple"):
    return PendingDecisionView(
        decision_id="decision-secret",
        candidates=[
            Candidate(
                rank=1,
                question_id="question-secret",
                text="Quels canaux ?",
                type=kind,
                selection_mode=mode,
                target_ids=["target-secret"],
                choices=[
                    {"choice_id": "choice-a", "label": "Téléphone"},
                    {"choice_id": "choice-b", "label": "Email"},
                ],
            )
        ],
    )


def test_maps_choice_labels_and_pending_ids():
    turn = answer_turn(pending(), choice_labels=["Téléphone", "Email"], free_text="Et courrier")
    assert turn.decision_id == "decision-secret"
    assert turn.question_id == "question-secret"
    assert turn.structured_answer.choice_ids == ["choice-a", "choice-b"]
    assert turn.outcome is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"choice_labels": ["Unknown"]},
        {"choice_labels": ["Email", "Email"]},
        {"candidate_rank": 2, "user_text": "Hi"},
        {"choice_labels": []},
        {"free_text": "Other"},
        {},
        {"user_text": ""},
    ],
)
def test_invalid_answer_is_rejected_locally(kwargs):
    with pytest.raises(ValueError):
        answer_turn(pending(), **kwargs)


def test_no_pending_decision():
    with pytest.raises(ValueError, match="pending"):
        answer_turn(None, user_text="Hello")


@pytest.mark.parametrize("kind,mode", [("single_choice", "single"), ("semi_open", "single")])
def test_respects_single_choice(kind, mode):
    with pytest.raises(ValueError, match="only one"):
        answer_turn(pending(kind, mode), choice_labels=["Email", "Téléphone"])


def test_ambiguous_label_and_open_question():
    p = pending()
    p.candidates[0].choices[1].label = "Téléphone"
    with pytest.raises(ValueError, match="ambiguous"):
        answer_turn(p, choice_labels=["Téléphone"])
    with pytest.raises(ValueError, match="Open"):
        answer_turn(pending("open", None), choice_labels=["Email"])


def test_sync_session_answers_with_one_request_and_no_implicit_completion():
    recorder = Recorder(json_response(201, SESSION_NEUVE), json_response(200, DECISION_NORMALE))
    with sync_client(recorder) as client:
        session = client.start_session()
        decision = session.next()
        before = len(recorder.requests)
        session.answer("Je souhaite comprendre mes options")
        assert len(recorder.requests) == before + 1
        body = recorder.body()
        assert body["state_version"] == decision.versions.state_version
        assert body["previous_turn"]["decision_id"] == decision.decision_id
        assert body["previous_turn"]["question_id"] == decision.candidates[0].question_id
        assert "outcome" not in body["previous_turn"]


async def test_async_resume_answer_and_failure_preserve_pending_state():
    state = copy.deepcopy(SESSION_NEUVE)
    state["pending_decision"] = pending().model_dump()
    state["versions"]["state_version"] = 7
    recorder = Recorder(json_response(200, state), json_response(200, DECISION_NORMALE))
    async with async_client(recorder) as client:
        session = await client.resume_session(state["session_id"])
        with pytest.raises(ValueError):
            await session.answer(choice_labels=["Unknown"])
        assert session.state_version == 7
        assert len(recorder.requests) == 1
        await session.answer(choice_labels=["Email"])
        assert recorder.body()["state_version"] == 7
        assert recorder.body()["previous_turn"]["structured_answer"]["choice_ids"] == ["choice-b"]


def test_configuration_metadata_and_explicit_null_are_preserved():
    question = {
        "id": "q",
        "text": "Question",
        "type": "open",
        "source": "llm_generated",
        "selection_mode": None,
        "choices": [],
        "dimension_id": "so",
        "active": True,
    }
    assert ConfiguredQuestion.model_validate(question).source == "llm_generated"
    wire = _changes_body(
        [
            {
                "entity": "question",
                "operation": "update",
                "question": {"id": "q", "source": "llm_generated", "selection_mode": None},
            }
        ],
        expected_draft_revision=1,
    )
    assert wire["changes"][0]["question"] == {
        "id": "q",
        "source": "llm_generated",
        "selection_mode": None,
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"outcome": "asked_answered"},
        {"outcome": "skipped"},
        {"user_text": ""},
        {"user_text": " \t\n"},
        {"assistant_text": "What do you need?"},
    ],
)
def test_open_answer_requires_actual_text(kwargs):
    with pytest.raises(ValueError, match="open question requires user_text"):
        answer_turn(pending("open", None), **kwargs)


@pytest.mark.parametrize("kind", ["open", "single_choice", "multiple_choice", "semi_open"])
@pytest.mark.parametrize("outcome", ["asked_no_answer", "refused"])
def test_explicit_no_answer_allowed_without_text(kind, outcome):
    turn = answer_turn(pending(kind), outcome=outcome)
    assert turn.outcome == outcome
    assert turn.user_text is None
    assert turn.structured_answer is None


@pytest.mark.parametrize(
    "kind,kwargs",
    [
        ("open", {"user_text": "I need to qualify inbound requests"}),
        ("single_choice", {"choice_labels": ["Email"]}),
        ("multiple_choice", {"choice_labels": ["Email", "Téléphone"]}),
        ("semi_open", {"choice_labels": ["Email"]}),
        ("semi_open", {"choice_labels": ["Email"], "free_text": "Also by mail"}),
        ("semi_open", {"user_text": "A different channel"}),
        ("single_choice", {"choice_labels": ["Email"], "user_text": "Email is easiest"}),
    ],
)
def test_valid_answer_forms_are_preserved(kind, kwargs):
    turn = answer_turn(pending(kind), **kwargs)
    assert turn.question_id == "question-secret"
    assert turn.user_text == kwargs.get("user_text")


def test_selected_candidate_type_is_used():
    mixed = pending("open", None)
    closed = pending("single_choice", "single").candidates[0]
    closed.rank = 2
    mixed.candidates.append(closed)
    assert answer_turn(mixed, candidate_rank=2, choice_labels=["Email"]).structured_answer


def test_sync_open_answer_rejected_before_network_and_state_unchanged():
    state = copy.deepcopy(SESSION_NEUVE)
    state["pending_decision"] = pending("open", None).model_dump()
    state["versions"]["state_version"] = 7
    recorder = Recorder(json_response(200, state), json_response(200, DECISION_NORMALE))
    with sync_client(recorder) as client:
        session = client.resume_session(state["session_id"])
        with pytest.raises(ValueError, match="requires user_text"):
            session.answer(outcome="asked_answered")
        assert len(recorder.requests) == 1
        assert session.state_version == 7
        session.answer(outcome="refused")
        assert len(recorder.requests) == 2
        assert recorder.body()["previous_turn"]["outcome"] == "refused"


async def test_async_open_answer_rejected_before_network_and_state_unchanged():
    state = copy.deepcopy(SESSION_NEUVE)
    state["pending_decision"] = pending("open", None).model_dump()
    state["versions"]["state_version"] = 7
    recorder = Recorder(json_response(200, state), json_response(200, DECISION_NORMALE))
    async with async_client(recorder) as client:
        session = await client.resume_session(state["session_id"])
        with pytest.raises(ValueError, match="requires user_text"):
            await session.answer(outcome="asked_answered")
        assert len(recorder.requests) == 1
        assert session.state_version == 7
        await session.answer(outcome="asked_no_answer")
        assert len(recorder.requests) == 2
        assert recorder.body()["previous_turn"]["outcome"] == "asked_no_answer"
