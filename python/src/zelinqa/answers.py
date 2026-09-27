"""Resolve a business answer against the pending decision, without an HTTP call."""

from collections.abc import Sequence

from .models import PendingDecisionView, PreviousTurn, QuestionOutcome, StructuredAnswer


def answer_turn(
    pending: PendingDecisionView | None,
    *,
    user_text: str | None = None,
    candidate_rank: int = 1,
    choice_labels: Sequence[str] | None = None,
    free_text: str | None = None,
    outcome: QuestionOutcome | None = None,
    assistant_text: str | None = None,
) -> PreviousTurn:
    if pending is None:
        raise ValueError("No pending question. Call next() first.")
    candidates = [c for c in pending.candidates if c.rank == candidate_rank]
    if len(candidates) != 1:
        raise ValueError("candidate_rank must identify one pending candidate")
    candidate = candidates[0]
    structured = None
    if choice_labels is not None or free_text is not None:
        if candidate.type == "open":
            raise ValueError("Open questions accept user_text, not choices")
        labels = list(choice_labels or [])
        if len(set(labels)) != len(labels):
            raise ValueError("Duplicate choice labels")
        ids = []
        for label in labels:
            matches = [c.choice_id for c in candidate.choices if c.label == label]
            if len(matches) != 1:
                raise ValueError("Unknown or ambiguous choice label; use exact displayed labels")
            ids.append(matches[0])
        if len(ids) > 1 and (
            candidate.type == "single_choice"
            or (candidate.type == "semi_open" and candidate.selection_mode != "multiple")
        ):
            raise ValueError("This question accepts only one choice")
        if free_text is not None and candidate.type != "semi_open":
            raise ValueError("Free text is only allowed for semi-open choices")
        if not ids:
            raise ValueError(
                "Select at least one choice; for an unlisted answer, send user_text instead"
            )
        structured = StructuredAnswer(choice_ids=ids, free_text=free_text)
    if (
        candidate.type == "open"
        and outcome not in ("asked_no_answer", "refused")
        and not (user_text and user_text.strip())
    ):
        raise ValueError(
            "An open question requires user_text unless outcome is asked_no_answer or refused"
        )
    if user_text is None and structured is None and outcome is None:
        raise ValueError("Provide an answer, choices, or an explicit outcome")
    return PreviousTurn(
        decision_id=pending.decision_id,
        question_id=candidate.question_id,
        assistant_text=assistant_text or candidate.text,
        user_text=user_text,
        structured_answer=structured,
        outcome=outcome,
    )
