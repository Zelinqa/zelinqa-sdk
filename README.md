# @zelinqa/sdk

Official TypeScript client for the [Zelinqa Engine](https://docs.zelinqa.ai)
V1 public API (`https://api.zelinqa.ai`). A [Python client](https://github.com/Zelinqa/nbq-sdk/blob/main/python/README.md)
is also available as `zelinqa`.

Zelinqa Engine keeps the canonical state of a conversation server-side and answers
one question: given what this conversation already told you, what should be
asked next? You configure objectives and a question bank in Zelinqa Studio, publish
an immutable version, and the API selects the next question from the session
state.

The SDK follows the versioned V1 contract. Field names on the wire are
`snake_case`.

```bash
pnpm add @zelinqa/sdk
```

```ts
import { ZelinqaClient } from "@zelinqa/sdk";

const client = new ZelinqaClient({ apiKey: process.env.ZELINQA_API_KEY ?? "" });
const conversation = await client.startSession({ client_reference: "demo-001" });
let proposal = await conversation.next();
// Ask proposal.candidates[0].text, then pass the visitor's answer:
proposal = await conversation.answer({ userText: "We need to qualify inbound requests" });
```

The handle tracks the session and decision identifiers. See the
[TypeScript guide](https://github.com/Zelinqa/nbq-sdk/blob/main/typescript/README.md)
for choice answers, configuration, errors and retries.

## SDKs

| | Python | TypeScript |
|---|---|---|
| Package | `zelinqa` | `@zelinqa/sdk` |
| Version | 1.0.0 | 1.0.0 |
| Runtime | Python 3.11+ | Node ≥ 22 |
| Dependencies | `httpx`, `pydantic` v2 | none |
| Guide | [Python guide](https://github.com/Zelinqa/nbq-sdk/blob/main/python/README.md) | [TypeScript guide](https://github.com/Zelinqa/nbq-sdk/blob/main/typescript/README.md) |

```bash
uv add zelinqa          # or: pip install zelinqa
pnpm add @zelinqa/sdk   # or: npm install / yarn add
```

## Two clients, two keys

A Zelinqa key carries scopes, and Studio recommends one key per job. Both SDKs are
split the same way.

| Client | Scope | What it does |
|---|---|---|
| `ZelinqaClient` (+ `AsyncZelinqaClient` in Python) | `runtime` | sessions, next question, out-of-turn events, state, feedback |
| `ZelinqaConfigurationClient` (+ async) | `configuration:read` · `configuration:write` · `configuration:publish` | read the corpus, edit the draft, publish, follow the compilation, read the audit log |

Scope rules worth knowing:

- A `runtime` key cannot read configuration.
- **Reading a draft requires both `configuration:read` and
  `configuration:write`**. A read-only key receives `insufficient_scope`.
- Reading the audit log requires `configuration:publish`.
- The domain and tenant are derived from the API key; clients do not send them.

## Runtime flow

### Without technical identifiers

```python
from zelinqa import ZelinqaClient

with ZelinqaClient() as client:  # ZELINQA_API_KEY, runtime scope
    conversation = client.start_session(client_reference="demo-001")
    proposal = conversation.next()
    # Ask proposal.candidates[0].text, then pass the real reply:
    proposal = conversation.answer("We need to qualify inbound requests")
    conversation.submit_feedback(result="partial", label="demo_finished")
```

```ts
import { ZelinqaClient } from "@zelinqa/sdk";
const client = new ZelinqaClient({ apiKey: process.env.ZELINQA_API_KEY ?? "" });
const conversation = await client.startSession({ client_reference: "demo-001" });
let proposal = await conversation.next();
// Ask the question, then pass the real reply:
proposal = await conversation.answer({ userText: "We need to qualify inbound requests" });
await conversation.submitFeedback({ result: "partial", label: "demo_finished" });
```

The handle supplies session, version, decision and question IDs. Use
`candidate_rank` / `candidateRank` for the candidate actually asked (default 1).
`choice_labels` / `choiceLabels` maps exact displayed labels to choice IDs;
unknown, duplicate or ambiguous labels are rejected locally. Semi-open questions
respect `selection_mode`. `free_text` / `freeText` supplements a selected choice;
for an unlisted answer use `user_text` / `userText` instead.

Answer rules (validated locally by `answer()` before any network request):

- **Open question:** send the person's actual words in `user_text` / `userText`.
  Text is analyzed by the engine. An outcome alone is not an open answer.
- **Closed or semi-open question:** choices alone require no model call.
  For semi-open questions, `free_text` / `freeText` may supplement a selected
  choice. Additional text is analyzed by the engine.
- **No answer or refusal:** `outcome="asked_no_answer"` or `outcome="refused"`
  is accepted without text for every question type and requires no model call.

These no-model paths assume no other text is supplied for analysis and an
unambiguous question. Initial `next()` and context-only updates are unchanged.

Call mutations sequentially per handle. Persist `conversation.id` in your backend,
not the LLM prompt, to call `resume_session` / `resumeSession` after restart.
The helpers add no HTTP call to `/next` and keep no transcript cache. Free text
is not automatically marked complete, and conflicts are never silently replayed.


The session handle (`start_session` / `startSession`) tracks `state_version` for
you. Conflicts are never hidden.

### Python

```python
from zelinqa import ZelinqaClient

with ZelinqaClient() as client:                     # api_key from ZELINQA_API_KEY
    session = client.start_session(client_reference="crm-lead-8842", max_turns=15)

    decision = session.next()                   # first turn, nothing asked yet
    candidate = decision.candidates[0]

    # …your agent asks the question, possibly reworded, and gets an answer…
    decision = session.next(
        previous_turn={
            "assistant_text": candidate.text,
            "user_text": "Un canapé contemporain pour le salon, nous avons un chat.",
        }
    )

    # Data the CRM already knows: no need to ask for it.
    state = session.apply_events(client_updates={"data": [{"id": "annual_budget", "value": 2500}]})
    print(state.targets["annual_budget"].status)          # confirmed

    state = session.refresh()                             # GET, authoritative state
    session.submit_feedback(result="success", label="achat", metadata={"order_id": "SO-99120"})
```

### TypeScript

```ts
import { ZelinqaClient } from "@zelinqa/sdk";

const client = new ZelinqaClient({ apiKey: process.env.ZELINQA_RUNTIME_KEY ?? "" });
const session = await client.startSession({
  client_reference: "crm-lead-8842",
  max_turns: 15,
});

let decision = await session.next();
const candidate = decision.candidates[0];

if (candidate !== undefined) {
  decision = await session.next({
    previous_turn: {
      assistant_text: candidate.text,
      user_text: "Un canapé contemporain pour le salon, nous avons un chat.",
    },
  });
}

const state = await session.applyEvents({
  client_updates: { data: [{ id: "annual_budget", operation: "set", value: 2500 }] },
});
console.log(state.targets.annual_budget?.status);        // confirmed

await session.refresh();
await session.submitFeedback({
  result: "success",
  label: "achat",
  metadata: { order_id: "SO-99120" },
});
```

Both handles expose the session id, the tracked state version, the last full
state and the pending decision, plus `next`, `apply_events`/`applyEvents`,
`refresh` and `submit_feedback`/`submitFeedback`. `resume_session` /
`resumeSession` rebuilds a handle from an existing session — after a crash,
re-reading the session is enough, there is no resume token to keep. Call the
client methods directly (`create_session`, `next`, `apply_events`,
`get_session`, `submit_feedback`) when you persist `state_version` yourself.

Zelinqa never decides for you: `action == "ask"` means candidates are available,
ranked, rank 1 first; `action == "stop"` only happens when no identifiable
question is left. A reached turn limit or an achieved objective is reported in
`warnings` while the best question is still proposed. `degraded` /
`degraded_reasons` say the turn was understood in reduced mode — typically no
verbatim was sent.

## Configuration flow

Draft edits are atomic; nothing goes live until a publication has compiled.
Compilation runs outside the HTTP request, so `publish` returns `202` with a
compilation id.

```python
with ZelinqaConfigurationClient() as studio:
    draft = studio.get_configuration(state="draft")        # needs read + write
    print(draft.domain.name)                               # current Studio name
    applied = studio.apply_changes(changes, expected_draft_revision=draft.draft_revision)
    queued = studio.publish(expected_draft_revision=applied.draft_revision)
    status = studio.wait_for_compilation(queued.compilation_id, poll_interval=3, timeout=900)
```

```ts
const draft = await studio.getConfiguration({ state: "draft" });
console.log(draft.domain.name); // current Studio name, not objective.name
const applied = await studio.applyChanges({
  changes,
  expected_draft_revision: draft.draft_revision ?? undefined,
});
const queued = await studio.publish({ expected_draft_revision: applied.draft_revision });
const status = await studio.waitForCompilation(queued.compilation_id, {
  pollIntervalMs: 3_000,
  timeoutMs: 900_000,
});
```

`wait_for_compilation` / `waitForCompilation` returns the terminal status —
a failed compilation is an editorial outcome, so inspect `status.error` instead
of catching an exception. Only running out of time raises
`ZelinqaCompilationTimeoutError`. Other configuration methods: `list_questions` /
`listQuestions`, `iter_questions` / `iterateQuestions` (cursor pagination),
`export_questions_csv` / `exportQuestionsCsv`, `list_audit` / `listAudit`,
`get_compilation` / `getCompilation`.

## Errors

The SDK-specific error classes have the same names in both languages. Python SDK
exceptions derive from `ZelinqaError`; TypeScript may also throw native errors
for invalid local arguments, choice labels, or caller cancellation.
The envelope `code` decides the type first, then the HTTP status when the body
is not a V1 envelope.

| Class | When | Notable attributes |
|---|---|---|
| `ZelinqaConnectionError` | network failure or per-attempt timeout, after retries | |
| `ZelinqaCompilationTimeoutError` | the compilation wait ran out of time | compilation id, timeout |
| `ZelinqaAPIError` | base of every HTTP failure | status code, `code`, message, request id, details, retry after |
| `ZelinqaAuthenticationError` | `401` (no `Authorization` header) **or** `403` without an envelope | status code |
| `ZelinqaInsufficientScopeError` | `403` `insufficient_scope` — the service's dynamic check | required / granted scopes |
| `ZelinqaNotFoundError` | `404`, specialised as `ZelinqaUnknownSessionError`, `ZelinqaUnknownConfigurationError`, `ZelinqaUnknownCompilationError` | |
| `ZelinqaConflictError` | `409`, specialised as `ZelinqaStateVersionConflictError`, `ZelinqaIdempotencyKeyReusedError`, `ZelinqaCompilationInProgressError` | supplied / current state version · idempotency key · compilation id and status |
| `ZelinqaCompiledArtifactUnavailableError` | `410` `compiled_artifact_unavailable` | |
| `ZelinqaValidationError` | `422`, specialised as `ZelinqaInvalidPreviousTurnError`, `ZelinqaConstraintNoMatchError`, `ZelinqaInvalidChoiceError`, `ZelinqaConfigurationValidationError` | grouped `issues` |
| `ZelinqaRateLimitError` | `429`, retried automatically first | retry after |
| `ZelinqaIdempotencyContentionError` | `503` `idempotency_contention`, retried first | retry after |
| `ZelinqaServerError` | any other `5xx` | |

Gateway semantics matter here: a missing `Authorization` header is `401`
`{"message":"Unauthorized"}`, while an invalid, revoked or expired key **and** a
key missing a scope required for the route both return the
same opaque `403 {"message":"Forbidden"}`. The SDKs do not pretend to tell those
two apart — both become `ZelinqaAuthenticationError`; read the status code to
separate a missing header from a refused key. `ZelinqaInsufficientScopeError` is
reserved for the `403` that carries a real V1 envelope.

`str(error)` / `String(error)` reads `"<code or status>: <message>
(request_id=…)"`. Quote the request id when you contact Zelinqa support; it
never contains a key or conversation content.

## Retries, idempotency and timeouts

- Every mutation sends `Idempotency-Key`. Without one, the SDK generates a
  UUID v4 **once per logical call and reuses it across that call's retries**, so
  a retry can never double a turn, an outcome, a feedback or a publication.
  Same key + same body replays the original response; same key + different body
  raises `ZelinqaIdempotencyKeyReusedError`. Server-side records expire after 24 h.
- Retried, up to `max_retries` / `maxRetries` (default 2), with exponential
  backoff 0.5 s, 1 s, 2 s … capped at 10 s and jittered: connection errors,
  timeouts, `429`, `500`, `502`, `503`, `504`, and the `idempotency_contention`
  envelope. `GET` requests too.
- Never retried: `400`, `401`, `403`, `404`, `409`, `410`, `422`.
- `Retry-After` (seconds or HTTP date) and `details.retry_after_seconds` win
  over the backoff, clamped to 30 s.
- The timeout (default 30 s) applies **per attempt**, combined with any
  cancellation token you pass (`signal` in TypeScript).

## Environment variables

The Python SDK reads the environment; **the TypeScript SDK never does** —
`apiKey` is required and explicit.

| Variable | Used by | Default |
|---|---|---|
| `ZELINQA_API_KEY` | both Python clients | — |
| `ZELINQA_CONFIGURATION_API_KEY` | Python configuration client, before `ZELINQA_API_KEY` | — |
| `ZELINQA_BASE_URL` | both Python clients | `https://api.zelinqa.ai` |

## Security

**These are server-side clients.** A Zelinqa key is a bearer credential restricted
to its configured domain and scopes. Never
embed one in a browser, a mobile app or any client you do not control — proxy
Zelinqa through your own service instead.

- The key lives in the HTTP client and nowhere else. No exception, message,
  `repr`, `toString`, log line or returned value contains it.
- Use separate keys per scope, least privilege. A runtime key must not publish.
- `initial_history` is consumed once in memory to build the initial state; it is
  never persisted, never logged, never returned.
- Keep keys out of the repository: `.env*` and `*.local` are git-ignored.
- No selection score, semantic evidence, embedding, prompt or compiled matrix is
  ever exposed by the API or the SDKs.

## Repository

```text
openapi/      frozen V1 contract snapshot (source of truth, read-only)
python/       Python client, tests, live suite
typescript/   TypeScript client, generated types, tests, live suite
scripts/      version consistency and codegen utilities
```

## Development

### TypeScript

```bash
corepack enable
pnpm install --frozen-lockfile
pnpm generate:types     # regenerate typescript/src/generated from the contract
pnpm check              # lint + typecheck + tests + version consistency
pnpm build              # ESM, CommonJS and type declarations
```

### Python

```bash
uv sync --group dev
uv run ruff check python/src python/tests
uv run mypy python/src
uv run pytest           # live tests excluded by default
```

`pnpm check:versions` asserts that `pyproject.toml`,
`python/src/zelinqa/_version.py`, `package.json` and `typescript/src/version.ts`
agree. Run it before every release.

### Live tests

Both live suites are opt-in, hit real staging and are skipped by default. They
need `ZELINQA_LIVE=1` plus `ZELINQA_LIVE_RUNTIME_KEY`, `ZELINQA_LIVE_CONFIG_READ_KEY`,
`ZELINQA_LIVE_CONFIG_WRITE_KEY`, `ZELINQA_LIVE_CONFIG_PUBLISH_KEY`,
`ZELINQA_LIVE_CONFIG_MANAGE_KEY` (read + write + publish, for draft reads) and
`ZELINQA_LIVE_REVOKED_KEY`; `ZELINQA_LIVE_BASE_URL` defaults to
`https://api.zelinqa.ai`.

```bash
ZELINQA_LIVE=1 uv run pytest -m live python/tests/live
ZELINQA_LIVE=1 pnpm test:live
```

They upsert a throwaway corpus so re-runs work, log only request ids and error
codes, and space their model-backed calls to respect service limits. In CI they
run only from the manual `live-tests.yml` workflow (`workflow_dispatch`, secrets
from the `staging-live` environment), never on a pull request.

## Changelog

See the [changelog](https://github.com/Zelinqa/nbq-sdk/blob/main/CHANGELOG.md) for release history.

## License

Apache-2.0. See [LICENSE](https://github.com/Zelinqa/nbq-sdk/blob/main/LICENSE).
