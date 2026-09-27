# `zelinqa` — Python SDK for the Zelinqa Engine API V1

Official Python client for [Zelinqa Engine](https://docs.zelinqa.ai), the Zelinqa
conversational qualification platform. Zelinqa keeps the canonical state of a
conversation server-side and answers one question: given what this conversation
already told you, what should be asked next?

The SDK follows `openapi/nbq-v1.openapi.yaml`, the frozen V1 contract. Field
names on the wire are `snake_case`, exactly as in the contract.

- Python 3.11+
- Sync and async clients, `httpx` transport, `pydantic` v2 models
- Fully typed (`py.typed`), strict `mypy`

```bash
uv add zelinqa          # or: pip install zelinqa
```

## Two clients, two keys

Use `session.answer("the actual reply")` after `session.next()` to avoid copying
technical IDs. Choices use `session.answer(choice_labels=["Exact label"])`.

Open questions require the person's actual `user_text`; the engine analyzes
that text. Closed and semi-open questions accept choices alone without a model
call. Semi-open choices may include `free_text`, which is analyzed. An explicit
`outcome="asked_no_answer"` or `outcome="refused"` needs no text or model call
for any type. These no-model paths assume no other text needs analysis.
`answer()` rejects an open answer without text locally, before any HTTP request.
Async handles offer the same methods with `await`. Persist the session ID in
your backend to resume after a restart; use one handle sequentially.


A Zelinqa key carries scopes. Studio recommends one key per job, so the SDK is
split the same way.

| Client | Scope | What it does |
|---|---|---|
| `ZelinqaClient` / `AsyncZelinqaClient` | `runtime` | drives conversations: sessions, next question, events, feedback |
| `ZelinqaConfigurationClient` / `AsyncZelinqaConfigurationClient` | `configuration:read`, `configuration:write`, `configuration:publish` | reads the question bank, edits the draft, publishes it |

```python
from zelinqa import ZelinqaClient, ZelinqaConfigurationClient

runtime = ZelinqaClient("YOUR_RUNTIME_KEY")             # scope runtime
studio = ZelinqaConfigurationClient("YOUR_CONFIG_KEY")  # management scopes
```

### Environment variables

| Variable | Used by | Default |
|---|---|---|
| `ZELINQA_API_KEY` | both clients | — |
| `ZELINQA_CONFIGURATION_API_KEY` | configuration client, before `ZELINQA_API_KEY` | — |
| `ZELINQA_BASE_URL` | both clients | `https://api.zelinqa.ai` |

```python
with ZelinqaClient() as client:          # api_key from ZELINQA_API_KEY
    ...
```

Both clients are context managers and expose `close()` (async: `aclose()`).
Their `repr()` and `str()` never contain the key.

### Constructor options

```python
ZelinqaClient(
    api_key=None,        # falls back to the environment
    base_url=None,       # absolute http(s), no credentials, no query
    timeout=30.0,        # seconds, per attempt
    max_retries=2,
    transport=None,      # an httpx transport, for tests
)
```

## Runtime: one conversation

### With a session handle (recommended)

`start_session()` returns a `Session` that remembers `state_version` for you.

```python
from zelinqa import ZelinqaClient

with ZelinqaClient() as client:
    session = client.start_session(client_reference="crm-lead-8842", max_turns=15)

    decision = session.next()                       # first turn, nothing asked yet
    candidate = decision.candidates[0]
    print(candidate.question_id, candidate.text, candidate.type)

    # …your agent asks the question, possibly reworded, and gets an answer…

    decision = session.next(
        previous_turn={
            "assistant_text": candidate.text,
            "user_text": "Un canapé contemporain pour le salon, nous avons un chat.",
        }
    )
    print(decision.progress.objective.effective_status, decision.turns_remaining)

    # A choice question: answer with the ids, no LLM call on that path.
    choice = decision.candidates[0]
    if choice.choices:
        decision = session.next(
            previous_turn={
                "question_id": choice.question_id,
                "structured_answer": {"choice_ids": [choice.choices[0].choice_id]},
            }
        )

    # Something the CRM already knows: no need to ask for it.
    state = session.apply_events(client_updates={"data": [{"id": "annual_budget", "value": 2500}]})
    print(state.targets["annual_budget"].status)     # confirmed

    # Messages exchanged outside Zelinqa.
    session.apply_events(
        context_update={"mode": "summary", "text": "Le visiteur emménage en mars."}
    )

    session.submit_feedback(result="success", label="achat", metadata={"order_id": "SO-99120"})
```

`Session` exposes `id`, `state_version`, `state`, `pending_decision`, and
`next()`, `answer()`, `apply_events()`, `refresh()`, `submit_feedback()`.

`answer(user_text)` submits an open answer to the pending decision. For a choice
question, pass `choice_labels=["Exact displayed label"]` instead. The handle
resolves the decision, question and choice IDs; it rejects unknown or ambiguous
labels locally. For a semi-open choice with an additional explanation, pass
`free_text` alongside `choice_labels`. It then requests the next decision.
The exported `answer_turn(pending_decision, user_text="...")` builds only the
`previous_turn` payload and makes no HTTP call.

After a crash, read the session back — there is no resume token to keep:

```python
session = client.resume_session("ses_01J8Z")
assert session.pending_decision is not None          # the exact candidates, rehydrated
decision = session.next(previous_turn={"user_text": "…"})
```

A conflict is never hidden. If another worker moved the session, `next()` raises
`ZelinqaStateVersionConflictError` and the handle is left untouched: you decide
whether to replay, refresh, or give up.

```python
from zelinqa import ZelinqaStateVersionConflictError

try:
    session.next(previous_turn={"user_text": "…"})
except ZelinqaStateVersionConflictError as error:
    print(error.supplied_state_version, error.current_state_version)
    session.refresh()                                # explicit, on your terms
```

### With explicit `state_version`

If you keep the state yourself — a queue worker, a Lambda, a state machine —
call the client directly and pass the version you read.

```python
state = client.create_session(client_reference="crm-lead-8842")
version = state.versions.state_version               # 0

decision = client.next("ses_01J8Z", state_version=version)
version = decision.versions.state_version

state = client.apply_events(
    "ses_01J8Z",
    state_version=version,
    client_updates={"dimensions": [{"id": "so_livraison", "operation": "exclude"}]},
)
state = client.get_session("ses_01J8Z")
client.submit_feedback("ses_01J8Z", result="partial")
```

### Async

The async client has the same surface; `iter_questions` becomes an async
iterator.

```python
from zelinqa import AsyncZelinqaClient

async with AsyncZelinqaClient() as client:
    session = await client.start_session()
    decision = await session.next()
```

### Reading a decision

`next()` returns a `NextResponse`. Zelinqa never decides for you:

- `action == "ask"` → `decision_id` is set, `candidates` is non-empty, ranked, rank 1 first.
- `action == "stop"` → no identifiable question is left; `stop_reason` says why.
- `warnings` reports conditions that would justify stopping (`max_turns_reached`,
  `objective_achieved`, `eligibility_exhausted_fallback`, `constraints_relaxed`)
  while still proposing the best question available. The decision to stop is yours.
- `degraded` / `degraded_reasons` say the turn was understood in reduced mode —
  typically because no `user_text` was provided.

No selection score, semantic evidence, embedding or prompt is ever exposed.

### Constraining one call

```python
decision = session.next(
    selection={
        "candidate_count": 2,
        "allowed_question_types": ["single_choice", "multiple_choice"],
        "dimensions": {"ids": ["so_besoin"], "mode": "restrict"},
    }
)
```

## Configuration: read, edit, publish

```python
from zelinqa import ZelinqaConfigurationClient

with ZelinqaConfigurationClient() as studio:
    published = studio.get_configuration()                     # configuration:read
    draft = studio.get_configuration(state="draft")            # needs read + write

    page = studio.list_questions(dimension_id="so_besoin", type="single_choice", limit=50)
    for question in studio.iter_questions(active=True):        # follows the cursor
        print(question.id, question.text)

    csv = studio.export_questions_csv()                        # raw text/csv
    events = studio.list_audit(limit=20)                       # configuration:publish
```

`SuccessInformation` carries the collected value's JSON Schema. The contract
calls that field `schema`, which collides with a `pydantic` attribute, so the
Python attribute is `json_schema` and the wire name stays `schema`.

```python
print(published.success_informations[0].json_schema)           # {"type": "number", "minimum": 0}
```

### Editing the draft, then publishing

Changes are applied atomically: either all of them land, or none does. Nothing
goes live until a publication has compiled.

```python
from zelinqa import ZelinqaConfigurationClient

with ZelinqaConfigurationClient() as studio:
    draft = studio.get_configuration(state="draft")

    applied = studio.apply_changes(
        [
            {
                "entity": "question",
                "operation": "create",
                "question": {
                    "id": "q_delivery_window",
                    "text": "À quelle période souhaitez-vous être livré ?",
                    "type": "single_choice",
                    "dimension_id": "so_livraison",
                    "active": True,
                    "choices": [
                        {"id": "choice_1m", "label": "Dans le mois", "maps_to_value": "dans_le_mois"},
                        {"id": "choice_3m", "label": "Dans les trois mois", "maps_to_value": "trois_mois"},
                    ],
                },
            },
            {
                "entity": "success_information",
                "operation": "create",
                "success_information": {
                    "id": "delivery_window",
                    "label": "Fenêtre de livraison souhaitée",
                    "primary_question_id": "q_delivery_window",
                    "schema": {"type": "string", "enum": ["dans_le_mois", "trois_mois"]},
                },
            },
        ],
        expected_draft_revision=draft.draft_revision,          # refuse to overwrite someone else
    )
    print(applied.applied, applied.draft_revision)

    queued = studio.publish(expected_draft_revision=applied.draft_revision)
    print(queued.compilation_id, queued.status)                # cmp_… queued

    status = studio.wait_for_compilation(queued.compilation_id, poll_interval=3, timeout=900)
    if status.status == "succeeded":
        print("now active:", status.configuration_version)
    else:
        print("failed:", status.error.code, status.error.message)
```

Compilation runs outside the HTTP request: a large corpus needs several LLM
batches. `publish()` returns as soon as the job is accepted, and the previous
compiled version stays readable so sessions already using it can finish.

`wait_for_compilation()` returns the terminal status — a failure is a normal
outcome, so inspect `status.error` rather than catching an exception. Only
running out of time raises `ZelinqaCompilationTimeoutError`. Typed models can be
passed instead of dicts:

```python
from zelinqa import QuestionChange, QuestionPatch

studio.apply_changes([QuestionChange(operation="update", question=QuestionPatch(id="q_style", active=False))])
```

## Errors

Every exception derives from `ZelinqaError`. The business envelope `code` decides
the type first; the HTTP status decides when the body is not a V1 envelope.

| Raised | Status / code | Notable attributes |
|---|---|---|
| `ZelinqaAuthenticationError` | `401` (no `Authorization` header), or `403` with no envelope — invalid, revoked, expired, or missing the scope the gateway checks for that route | `status_code` |
| `ZelinqaInsufficientScopeError` | `403` `insufficient_scope` — the service-side check, today only `?state=draft` | `required_scopes`, `granted_scopes` |
| `ZelinqaUnknownSessionError` | `404` `unknown_session` | `details["session_id"]` |
| `ZelinqaUnknownConfigurationError` | `404` `unknown_configuration` | |
| `ZelinqaUnknownCompilationError` | `404` `unknown_compilation` | |
| `ZelinqaStateVersionConflictError` | `409` `state_version_conflict` | `supplied_state_version`, `current_state_version` |
| `ZelinqaIdempotencyKeyReusedError` | `409` `idempotency_key_reused` | `idempotency_key` |
| `ZelinqaCompilationInProgressError` | `409` `compilation_in_progress` | `compilation_id`, `status` |
| `ZelinqaCompiledArtifactUnavailableError` | `410` `compiled_artifact_unavailable` | |
| `ZelinqaInvalidPreviousTurnError` | `422` `invalid_previous_turn` | |
| `ZelinqaConstraintNoMatchError` | `422` `constraint_no_match` | |
| `ZelinqaInvalidChoiceError` | `422` `invalid_choice` | |
| `ZelinqaConfigurationValidationError` | `422` `configuration_validation_failed` | `issues: list[ConfigurationIssue]` |
| `ZelinqaRateLimitError` | `429` | `retry_after` |
| `ZelinqaIdempotencyContentionError` | `503` `idempotency_contention` | retried first, raised only when retries run out |
| `ZelinqaServerError` | other `5xx` | |
| `ZelinqaAPIError` | anything else | base of all of the above |
| `ZelinqaConnectionError` | network failure or timeout, after every retry | |
| `ZelinqaCompilationTimeoutError` | `wait_for_compilation` ran out of time | `compilation_id`, `timeout` |

`ZelinqaAPIError` always carries `status_code`, `code`, `message`, `request_id`,
`details` and `retry_after`. `str(error)` reads
`"<code or status>: <message> (request_id=…)"` — quote the `request_id` when
you contact Zelinqa support.

Note that a `403` from the gateway is deliberately not split further: the
gateway answers the same opaque body for a revoked key and for a key missing a
route's scope, so the SDK does not pretend to know which it was.

```python
from zelinqa import ZelinqaAPIError, ZelinqaConfigurationValidationError

try:
    studio.publish()
except ZelinqaConfigurationValidationError as error:
    for issue in error.issues:
        print(issue.code, issue.entity, issue.entity_id, issue.message)
except ZelinqaAPIError as error:
    print(error.status_code, error.code, error.request_id)
```

`ConfigurationIssue.code` is a plain string on purpose: the contract does not
freeze that catalogue. Display `message`, and never assume you know every code.

## Retries and idempotency

Every mutation sends an `Idempotency-Key`. If you do not supply one, the SDK
generates a UUID v4 **once per logical call** and reuses it across that call's
retries — a retry can therefore never double a turn, an outcome, a feedback or
a publication. Supply your own when you want the replay to be idempotent across
processes too:

```python
session.next(previous_turn={"user_text": "…"}, idempotency_key=f"next-{session.id}-turn-4")
```

Same key and same body replays the original response. Same key with a different
body raises `ZelinqaIdempotencyKeyReusedError`.

Retried, up to `max_retries` (default 2), with exponential backoff (0.5 s, 1 s,
2 s… capped at 10 s, jittered): connection errors and timeouts, `429`, `500`,
`502`, `503`, `504`, and the `idempotency_contention` envelope. `Retry-After`
(seconds or HTTP date) and `details.retry_after_seconds` win over the backoff,
clamped to 30 s. `GET` requests are retried the same way. `400`, `401`, `403`,
`404`, `409`, `410` and `422` are never retried. `timeout` applies per attempt,
so the worst case is roughly `timeout × (max_retries + 1)` plus the backoff.

## Security

**These clients belong in a backend.** A Zelinqa key is restricted to its
configured domain and scopes. Never ship one to a browser,
a mobile app or any client you do not control; proxy Zelinqa through your own
service instead.

- The key lives in the `httpx` client and nowhere else: no exception, message,
  `repr`, log line or returned value contains it.
- Use separate keys per scope. A runtime key must not be able to publish.
- The tenant and domain are derived from the API key, not supplied by the client.
- `initial_history` is consumed once in memory to build the initial state. It
  is never persisted, never logged, never returned.
- Keep keys out of the repository. `.env*` and `*.local` are git-ignored.

## Development

```bash
uv sync --group dev
uv run ruff check python/src python/tests
uv run ruff format --check python/src python/tests
uv run mypy python/src
uv run pytest                     # live tests excluded by default
```

The live suite runs against real staging keys and is opt-in:

```bash
ZELINQA_LIVE=1 uv run pytest -m live python/tests/live
```

It needs `ZELINQA_LIVE_RUNTIME_KEY`, `ZELINQA_LIVE_CONFIG_READ_KEY`,
`ZELINQA_LIVE_CONFIG_WRITE_KEY`, `ZELINQA_LIVE_CONFIG_PUBLISH_KEY`,
`ZELINQA_LIVE_CONFIG_MANAGE_KEY` and `ZELINQA_LIVE_REVOKED_KEY`, and optionally
`ZELINQA_LIVE_BASE_URL`. In CI it only runs from the manual `live-tests` workflow,
with secrets from the `staging-live` environment.

## Changelog

See the [changelog](https://github.com/Zelinqa/zelinqa-sdk/blob/main/CHANGELOG.md) for release history.

## License

Apache-2.0. See [LICENSE](https://github.com/Zelinqa/zelinqa-sdk/blob/main/LICENSE).
