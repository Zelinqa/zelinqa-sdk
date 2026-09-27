"""Execute the published quickstart with the real SDK and synthetic HTTP replies."""

import copy
import re
from pathlib import Path

import pytest
import zelinqa
from spec_examples import ARRET_SANS_QUESTION, DECISION_NORMALE, SESSION_NEUVE
from test_runtime_client import Recorder, json_response, sync_client

README = Path(__file__).resolve().parents[1] / "README.md"


def quickstart():
    examples = re.findall(r"```python\n(.*?)\n```", README.read_text(), re.DOTALL)
    assert len(examples) == 1, "Every executable README example must be tested"
    return compile(examples[0], str(README), "exec")


def decision_for(kind):
    decision = copy.deepcopy(DECISION_NORMALE)
    candidate = decision["candidates"][0]
    candidate["type"] = kind
    candidate["selection_mode"] = None if kind == "open" else "single"
    if kind == "multiple_choice":
        candidate["selection_mode"] = "multiple"
    candidate["choices"] = (
        [] if kind == "open" else [{"choice_id": "choice-email", "label": "Email"}]
    )
    return decision


@pytest.mark.parametrize(
    "kind,reply",
    [
        ("open", "I need to qualify inbound requests."),
        ("single_choice", "Email"),
        ("multiple_choice", "Email"),
        ("semi_open", "Email"),
        ("open", ""),
        ("open", "   "),
        ("single_choice", ""),
    ],
)
def test_quickstart_submits_the_real_reply(monkeypatch, kind, reply):
    decision = decision_for(kind)
    recorder = Recorder(
        json_response(201, SESSION_NEUVE),
        json_response(200, decision),
        json_response(200, ARRET_SANS_QUESTION),
    )
    monkeypatch.setattr(zelinqa, "ZelinqaClient", lambda: sync_client(recorder))
    monkeypatch.setattr("builtins.input", lambda prompt: reply)

    exec(quickstart(), {})

    assert len(recorder.requests) == 3
    assert recorder.body(0)["client_reference"] == "demo-001"
    body = recorder.body()
    assert body["state_version"] == decision["versions"]["state_version"]
    previous = body["previous_turn"]
    assert previous["decision_id"] == decision["decision_id"]
    assert previous["question_id"] == decision["candidates"][0]["question_id"]
    if not reply.strip():
        assert previous["outcome"] == "asked_no_answer"
        assert previous.get("user_text") is None
        assert previous.get("structured_answer") is None
    elif kind == "open":
        assert previous["user_text"] == reply
        assert previous.get("structured_answer") is None
    else:
        assert previous["structured_answer"]["choice_ids"] == ["choice-email"]
        assert previous.get("user_text") is None


def test_quickstart_does_not_ask_after_stop(monkeypatch):
    recorder = Recorder(json_response(201, SESSION_NEUVE), json_response(200, ARRET_SANS_QUESTION))
    monkeypatch.setattr(zelinqa, "ZelinqaClient", lambda: sync_client(recorder))
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("No question is pending"))

    exec(quickstart(), {})

    assert len(recorder.requests) == 2


def test_quickstart_rejects_an_unknown_label_before_sending(monkeypatch):
    recorder = Recorder(
        json_response(201, SESSION_NEUVE), json_response(200, decision_for("single_choice"))
    )
    monkeypatch.setattr(zelinqa, "ZelinqaClient", lambda: sync_client(recorder))
    monkeypatch.setattr("builtins.input", lambda prompt: "Not a displayed choice")

    with pytest.raises(ValueError, match="Unknown or ambiguous choice label"):
        exec(quickstart(), {})

    assert len(recorder.requests) == 2
