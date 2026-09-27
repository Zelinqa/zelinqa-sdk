"""Runtime clients for the five Zelinqa session routes.

A key with the ``runtime`` scope drives a conversation: create a session, ask
for the next question, report what happened, read the state back, declare the
outcome. Nothing here reads or writes configuration.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any

import httpx

from ._http import (
    API_KEY_ENV,
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    AsyncExecutor,
    Request,
    SyncExecutor,
    json_payload,
    path_segment,
    resolve_api_key,
    resolve_base_url,
    resolve_idempotency_key,
    validate_max_retries,
    validate_timeout,
)
from .answers import answer_turn
from .models import (
    ClientUpdates,
    ContextUpdate,
    FeedbackRequest,
    FeedbackResponse,
    FeedbackResult,
    InitialHistoryItem,
    MetadataValue,
    NextRequest,
    NextResponse,
    PendingDecisionView,
    PreviousTurn,
    QuestionOutcome,
    SelectionOptions,
    SessionCreateRequest,
    SessionEventsRequest,
    SessionStateResponse,
)

__all__ = ["AsyncSession", "AsyncZelinqaClient", "Session", "ZelinqaClient"]

_SESSIONS = "/v1/sessions"

# Sub-objects accept either the typed model or a plain mapping. Both are
# validated by pydantic before anything is sent.
PreviousTurnInput = PreviousTurn | Mapping[str, Any]
ContextUpdateInput = ContextUpdate | Mapping[str, Any]
ClientUpdatesInput = ClientUpdates | Mapping[str, Any]
SelectionInput = SelectionOptions | Mapping[str, Any]
InitialHistoryInput = Sequence[InitialHistoryItem | Mapping[str, Any]]


def _create_body(
    *,
    client_reference: str | None,
    max_turns: int | None,
    initial_history: InitialHistoryInput | None,
) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    if client_reference is not None:
        raw["client_reference"] = client_reference
    if max_turns is not None:
        raw["max_turns"] = max_turns
    if initial_history is not None:
        raw["initial_history"] = list(initial_history)
    return _dump(SessionCreateRequest.model_validate(raw))


def _next_body(
    *,
    state_version: int,
    previous_turn: PreviousTurnInput | None,
    context_update: ContextUpdateInput | None,
    client_updates: ClientUpdatesInput | None,
    selection: SelectionInput | None,
) -> dict[str, Any]:
    raw: dict[str, Any] = {"state_version": state_version}
    if previous_turn is not None:
        raw["previous_turn"] = previous_turn
    if context_update is not None:
        raw["context_update"] = context_update
    if client_updates is not None:
        raw["client_updates"] = client_updates
    if selection is not None:
        raw["selection"] = selection
    return _dump(NextRequest.model_validate(raw))


def _events_body(
    *,
    state_version: int,
    context_update: ContextUpdateInput | None,
    client_updates: ClientUpdatesInput | None,
) -> dict[str, Any]:
    raw: dict[str, Any] = {"state_version": state_version}
    if context_update is not None:
        raw["context_update"] = context_update
    if client_updates is not None:
        raw["client_updates"] = client_updates
    return _dump(SessionEventsRequest.model_validate(raw))


def _feedback_body(
    *,
    result: FeedbackResult,
    label: str | None,
    metadata: Mapping[str, MetadataValue] | None,
) -> dict[str, Any]:
    raw: dict[str, Any] = {"result": result}
    if label is not None:
        raw["label"] = label
    if metadata is not None:
        raw["metadata"] = dict(metadata)
    return _dump(FeedbackRequest.model_validate(raw))


def _dump(
    model: SessionCreateRequest | NextRequest | SessionEventsRequest | FeedbackRequest,
) -> dict[str, Any]:
    """Serialise a request body: JSON types, aliases, and no unset field."""

    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def _session_path(session_id: str, suffix: str = "") -> str:
    return f"{_SESSIONS}/{path_segment(session_id)}{suffix}"


class ZelinqaClient:
    """Blocking client for the Zelinqa runtime routes.

    ``api_key`` falls back to the ``ZELINQA_API_KEY`` environment variable and
    ``base_url`` to ``ZELINQA_BASE_URL``, then ``https://api.zelinqa.ai``.
    ``transport`` exists for tests: pass an ``httpx.MockTransport``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = resolve_base_url(base_url)
        self._executor = SyncExecutor(
            api_key=resolve_api_key(api_key, env_names=(API_KEY_ENV,)),
            base_url=self._base_url,
            timeout=validate_timeout(timeout),
            max_retries=validate_max_retries(max_retries),
            transport=transport,
        )

    def __repr__(self) -> str:
        return f"ZelinqaClient(base_url={self._base_url!r})"

    def __enter__(self) -> ZelinqaClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release the underlying connection pool."""

        self._executor.close()

    # -------------------------------------------------------------- routes

    def create_session(
        self,
        *,
        client_reference: str | None = None,
        max_turns: int | None = None,
        initial_history: InitialHistoryInput | None = None,
        idempotency_key: str | None = None,
    ) -> SessionStateResponse:
        """Create a session pinned to the configuration published right now."""

        attempt = self._executor.send(
            Request(
                method="POST",
                path=_SESSIONS,
                json_body=_create_body(
                    client_reference=client_reference,
                    max_turns=max_turns,
                    initial_history=initial_history,
                ),
                idempotency_key=resolve_idempotency_key(idempotency_key),
            )
        )
        return SessionStateResponse.model_validate(json_payload(attempt))

    def next(
        self,
        session_id: str,
        *,
        state_version: int,
        previous_turn: PreviousTurnInput | None = None,
        context_update: ContextUpdateInput | None = None,
        client_updates: ClientUpdatesInput | None = None,
        selection: SelectionInput | None = None,
        idempotency_key: str | None = None,
    ) -> NextResponse:
        """Understand the previous turn, then propose the next questions."""

        attempt = self._executor.send(
            Request(
                method="POST",
                path=_session_path(session_id, "/next"),
                json_body=_next_body(
                    state_version=state_version,
                    previous_turn=previous_turn,
                    context_update=context_update,
                    client_updates=client_updates,
                    selection=selection,
                ),
                idempotency_key=resolve_idempotency_key(idempotency_key),
            )
        )
        return NextResponse.model_validate(json_payload(attempt))

    def apply_events(
        self,
        session_id: str,
        *,
        state_version: int,
        context_update: ContextUpdateInput | None = None,
        client_updates: ClientUpdatesInput | None = None,
        idempotency_key: str | None = None,
    ) -> SessionStateResponse:
        """Apply a context or a client update without selecting a question."""

        attempt = self._executor.send(
            Request(
                method="POST",
                path=_session_path(session_id, "/events"),
                json_body=_events_body(
                    state_version=state_version,
                    context_update=context_update,
                    client_updates=client_updates,
                ),
                idempotency_key=resolve_idempotency_key(idempotency_key),
            )
        )
        return SessionStateResponse.model_validate(json_payload(attempt))

    def get_session(self, session_id: str) -> SessionStateResponse:
        """Read the full resumable state of a session."""

        attempt = self._executor.send(Request(method="GET", path=_session_path(session_id)))
        return SessionStateResponse.model_validate(json_payload(attempt))

    def submit_feedback(
        self,
        session_id: str,
        *,
        result: FeedbackResult,
        label: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        idempotency_key: str | None = None,
    ) -> FeedbackResponse:
        """Declare what the conversation actually produced."""

        attempt = self._executor.send(
            Request(
                method="POST",
                path=_session_path(session_id, "/feedback"),
                json_body=_feedback_body(result=result, label=label, metadata=metadata),
                idempotency_key=resolve_idempotency_key(idempotency_key),
            )
        )
        return FeedbackResponse.model_validate(json_payload(attempt))

    # ------------------------------------------------------------- handles

    def start_session(
        self,
        *,
        client_reference: str | None = None,
        max_turns: int | None = None,
        initial_history: InitialHistoryInput | None = None,
        idempotency_key: str | None = None,
    ) -> Session:
        """Create a session and return a handle that tracks ``state_version``."""

        return Session(
            self,
            self.create_session(
                client_reference=client_reference,
                max_turns=max_turns,
                initial_history=initial_history,
                idempotency_key=idempotency_key,
            ),
        )

    def resume_session(self, session_id: str) -> Session:
        """Read a session and return a handle that tracks ``state_version``."""

        return Session(self, self.get_session(session_id))


class Session:
    """Handle over one session that remembers ``state_version``.

    Every response updates the tracked version. A ``state_version_conflict`` is
    raised as :class:`~zelinqa.errors.ZelinqaStateVersionConflictError` and the handle
    is left untouched: refreshing on the caller's behalf would hide the fact
    that somebody else moved the session. Call :meth:`refresh` to resynchronise.
    """

    def __init__(self, client: ZelinqaClient, state: SessionStateResponse) -> None:
        self._client = client
        self._state: SessionStateResponse | None = state
        self._state_version = state.versions.state_version
        self._pending_decision = state.pending_decision
        self._id = state.session_id

    def __repr__(self) -> str:
        return f"Session(id={self._id!r}, state_version={self._state_version!r})"

    @property
    def id(self) -> str:
        """Opaque session identifier assigned by Zelinqa."""

        return self._id

    @property
    def state_version(self) -> int:
        """Version to send on the next mutation of this session."""

        return self._state_version

    @property
    def state(self) -> SessionStateResponse | None:
        """Last full state received. ``next()`` does not refresh it."""

        return self._state

    @property
    def pending_decision(self) -> PendingDecisionView | None:
        """Decision proposed and not resolved yet."""

        return self._pending_decision

    def _adopt_state(self, state: SessionStateResponse) -> SessionStateResponse:
        self._state = state
        self._state_version = state.versions.state_version
        self._pending_decision = state.pending_decision
        return state

    def _adopt_next(self, response: NextResponse) -> NextResponse:
        self._state_version = response.versions.state_version
        if response.action == "ask" and response.decision_id is not None:
            self._pending_decision = PendingDecisionView(
                decision_id=response.decision_id, candidates=response.candidates
            )
        else:
            self._pending_decision = None
        return response

    def next(
        self,
        *,
        state_version: int | None = None,
        previous_turn: PreviousTurnInput | None = None,
        context_update: ContextUpdateInput | None = None,
        client_updates: ClientUpdatesInput | None = None,
        selection: SelectionInput | None = None,
        idempotency_key: str | None = None,
    ) -> NextResponse:
        """Call ``/next`` with the tracked version unless one is given."""

        return self._adopt_next(
            self._client.next(
                self._id,
                state_version=self._state_version if state_version is None else state_version,
                previous_turn=previous_turn,
                context_update=context_update,
                client_updates=client_updates,
                selection=selection,
                idempotency_key=idempotency_key,
            )
        )

    def answer(
        self,
        user_text: str | None = None,
        *,
        candidate_rank: int = 1,
        choice_labels: Sequence[str] | None = None,
        free_text: str | None = None,
        outcome: QuestionOutcome | None = None,
        assistant_text: str | None = None,
        idempotency_key: str | None = None,
    ) -> NextResponse:
        """Answer a pending candidate using text/labels, never technical identifiers.

        Reports the candidate actually asked (rank 1 by default). Does not mark
        free-text answers complete: the API assesses them unless outcome is explicit.
        Like all session mutations, call sequentially; conflicts are not replayed.
        """
        turn = answer_turn(
            self._pending_decision,
            user_text=user_text,
            candidate_rank=candidate_rank,
            choice_labels=choice_labels,
            free_text=free_text,
            outcome=outcome,
            assistant_text=assistant_text,
        )
        return self.next(previous_turn=turn, idempotency_key=idempotency_key)

    def apply_events(
        self,
        *,
        state_version: int | None = None,
        context_update: ContextUpdateInput | None = None,
        client_updates: ClientUpdatesInput | None = None,
        idempotency_key: str | None = None,
    ) -> SessionStateResponse:
        """Call ``/events`` with the tracked version unless one is given."""

        return self._adopt_state(
            self._client.apply_events(
                self._id,
                state_version=self._state_version if state_version is None else state_version,
                context_update=context_update,
                client_updates=client_updates,
                idempotency_key=idempotency_key,
            )
        )

    def refresh(self) -> SessionStateResponse:
        """Re-read the session and resynchronise the tracked version."""

        return self._adopt_state(self._client.get_session(self._id))

    def submit_feedback(
        self,
        *,
        result: FeedbackResult,
        label: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        idempotency_key: str | None = None,
    ) -> FeedbackResponse:
        """Declare the outcome of this session."""

        return self._client.submit_feedback(
            self._id,
            result=result,
            label=label,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )


class AsyncZelinqaClient:
    """Async client for the Zelinqa runtime routes. Same surface as :class:`ZelinqaClient`."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = resolve_base_url(base_url)
        self._executor = AsyncExecutor(
            api_key=resolve_api_key(api_key, env_names=(API_KEY_ENV,)),
            base_url=self._base_url,
            timeout=validate_timeout(timeout),
            max_retries=validate_max_retries(max_retries),
            transport=transport,
        )

    def __repr__(self) -> str:
        return f"AsyncZelinqaClient(base_url={self._base_url!r})"

    async def __aenter__(self) -> AsyncZelinqaClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Release the underlying connection pool."""

        await self._executor.aclose()

    #: Alias of :meth:`aclose`, for symmetry with the blocking client.
    close = aclose

    # -------------------------------------------------------------- routes

    async def create_session(
        self,
        *,
        client_reference: str | None = None,
        max_turns: int | None = None,
        initial_history: InitialHistoryInput | None = None,
        idempotency_key: str | None = None,
    ) -> SessionStateResponse:
        """Create a session pinned to the configuration published right now."""

        attempt = await self._executor.send(
            Request(
                method="POST",
                path=_SESSIONS,
                json_body=_create_body(
                    client_reference=client_reference,
                    max_turns=max_turns,
                    initial_history=initial_history,
                ),
                idempotency_key=resolve_idempotency_key(idempotency_key),
            )
        )
        return SessionStateResponse.model_validate(json_payload(attempt))

    async def next(
        self,
        session_id: str,
        *,
        state_version: int,
        previous_turn: PreviousTurnInput | None = None,
        context_update: ContextUpdateInput | None = None,
        client_updates: ClientUpdatesInput | None = None,
        selection: SelectionInput | None = None,
        idempotency_key: str | None = None,
    ) -> NextResponse:
        """Understand the previous turn, then propose the next questions."""

        attempt = await self._executor.send(
            Request(
                method="POST",
                path=_session_path(session_id, "/next"),
                json_body=_next_body(
                    state_version=state_version,
                    previous_turn=previous_turn,
                    context_update=context_update,
                    client_updates=client_updates,
                    selection=selection,
                ),
                idempotency_key=resolve_idempotency_key(idempotency_key),
            )
        )
        return NextResponse.model_validate(json_payload(attempt))

    async def apply_events(
        self,
        session_id: str,
        *,
        state_version: int,
        context_update: ContextUpdateInput | None = None,
        client_updates: ClientUpdatesInput | None = None,
        idempotency_key: str | None = None,
    ) -> SessionStateResponse:
        """Apply a context or a client update without selecting a question."""

        attempt = await self._executor.send(
            Request(
                method="POST",
                path=_session_path(session_id, "/events"),
                json_body=_events_body(
                    state_version=state_version,
                    context_update=context_update,
                    client_updates=client_updates,
                ),
                idempotency_key=resolve_idempotency_key(idempotency_key),
            )
        )
        return SessionStateResponse.model_validate(json_payload(attempt))

    async def get_session(self, session_id: str) -> SessionStateResponse:
        """Read the full resumable state of a session."""

        attempt = await self._executor.send(Request(method="GET", path=_session_path(session_id)))
        return SessionStateResponse.model_validate(json_payload(attempt))

    async def submit_feedback(
        self,
        session_id: str,
        *,
        result: FeedbackResult,
        label: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        idempotency_key: str | None = None,
    ) -> FeedbackResponse:
        """Declare what the conversation actually produced."""

        attempt = await self._executor.send(
            Request(
                method="POST",
                path=_session_path(session_id, "/feedback"),
                json_body=_feedback_body(result=result, label=label, metadata=metadata),
                idempotency_key=resolve_idempotency_key(idempotency_key),
            )
        )
        return FeedbackResponse.model_validate(json_payload(attempt))

    # ------------------------------------------------------------- handles

    async def start_session(
        self,
        *,
        client_reference: str | None = None,
        max_turns: int | None = None,
        initial_history: InitialHistoryInput | None = None,
        idempotency_key: str | None = None,
    ) -> AsyncSession:
        """Create a session and return a handle that tracks ``state_version``."""

        return AsyncSession(
            self,
            await self.create_session(
                client_reference=client_reference,
                max_turns=max_turns,
                initial_history=initial_history,
                idempotency_key=idempotency_key,
            ),
        )

    async def resume_session(self, session_id: str) -> AsyncSession:
        """Read a session and return a handle that tracks ``state_version``."""

        return AsyncSession(self, await self.get_session(session_id))


class AsyncSession:
    """Async handle over one session. Same semantics as :class:`Session`."""

    def __init__(self, client: AsyncZelinqaClient, state: SessionStateResponse) -> None:
        self._client = client
        self._state: SessionStateResponse | None = state
        self._state_version = state.versions.state_version
        self._pending_decision = state.pending_decision
        self._id = state.session_id

    def __repr__(self) -> str:
        return f"AsyncSession(id={self._id!r}, state_version={self._state_version!r})"

    @property
    def id(self) -> str:
        """Opaque session identifier assigned by Zelinqa."""

        return self._id

    @property
    def state_version(self) -> int:
        """Version to send on the next mutation of this session."""

        return self._state_version

    @property
    def state(self) -> SessionStateResponse | None:
        """Last full state received. ``next()`` does not refresh it."""

        return self._state

    @property
    def pending_decision(self) -> PendingDecisionView | None:
        """Decision proposed and not resolved yet."""

        return self._pending_decision

    def _adopt_state(self, state: SessionStateResponse) -> SessionStateResponse:
        self._state = state
        self._state_version = state.versions.state_version
        self._pending_decision = state.pending_decision
        return state

    def _adopt_next(self, response: NextResponse) -> NextResponse:
        self._state_version = response.versions.state_version
        if response.action == "ask" and response.decision_id is not None:
            self._pending_decision = PendingDecisionView(
                decision_id=response.decision_id, candidates=response.candidates
            )
        else:
            self._pending_decision = None
        return response

    async def next(
        self,
        *,
        state_version: int | None = None,
        previous_turn: PreviousTurnInput | None = None,
        context_update: ContextUpdateInput | None = None,
        client_updates: ClientUpdatesInput | None = None,
        selection: SelectionInput | None = None,
        idempotency_key: str | None = None,
    ) -> NextResponse:
        """Call ``/next`` with the tracked version unless one is given."""

        return self._adopt_next(
            await self._client.next(
                self._id,
                state_version=self._state_version if state_version is None else state_version,
                previous_turn=previous_turn,
                context_update=context_update,
                client_updates=client_updates,
                selection=selection,
                idempotency_key=idempotency_key,
            )
        )

    async def answer(
        self,
        user_text: str | None = None,
        *,
        candidate_rank: int = 1,
        choice_labels: Sequence[str] | None = None,
        free_text: str | None = None,
        outcome: QuestionOutcome | None = None,
        assistant_text: str | None = None,
        idempotency_key: str | None = None,
    ) -> NextResponse:
        """Async equivalent of Session.answer; await each mutation sequentially."""
        turn = answer_turn(
            self._pending_decision,
            user_text=user_text,
            candidate_rank=candidate_rank,
            choice_labels=choice_labels,
            free_text=free_text,
            outcome=outcome,
            assistant_text=assistant_text,
        )
        return await self.next(previous_turn=turn, idempotency_key=idempotency_key)

    async def apply_events(
        self,
        *,
        state_version: int | None = None,
        context_update: ContextUpdateInput | None = None,
        client_updates: ClientUpdatesInput | None = None,
        idempotency_key: str | None = None,
    ) -> SessionStateResponse:
        """Call ``/events`` with the tracked version unless one is given."""

        return self._adopt_state(
            await self._client.apply_events(
                self._id,
                state_version=self._state_version if state_version is None else state_version,
                context_update=context_update,
                client_updates=client_updates,
                idempotency_key=idempotency_key,
            )
        )

    async def refresh(self) -> SessionStateResponse:
        """Re-read the session and resynchronise the tracked version."""

        return self._adopt_state(await self._client.get_session(self._id))

    async def submit_feedback(
        self,
        *,
        result: FeedbackResult,
        label: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        idempotency_key: str | None = None,
    ) -> FeedbackResponse:
        """Declare the outcome of this session."""

        return await self._client.submit_feedback(
            self._id,
            result=result,
            label=label,
            metadata=metadata,
            idempotency_key=idempotency_key,
        )
