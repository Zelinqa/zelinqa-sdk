# TypeScript SDK usage guide

Official TypeScript client for the [Zelinqa Engine](https://docs.zelinqa.ai) V1 public
API (`https://api.zelinqa.ai`). Zelinqa Engine decides the **next best question** to
ask in a conversation: you configure objectives and a question bank in Zelinqa Studio,
publish an immutable version, and the API picks the next question from the session
state it keeps for you.

Zero runtime dependencies, ESM + CommonJS, full type declarations generated from
the OpenAPI contract. Node ≥ 22. TypeScript consumers need ES2022 and DOM type
libraries for `fetch`, `Request`, `URL` and `AbortSignal` (or equivalent Node
fetch types).

## Install

Use `await session.answer({ userText: "the actual reply" })` after
`session.next()`. Choices use `{ choiceLabels: ["Exact label"] }`. The handle
resolves technical IDs. Persist the session ID in your backend to resume after
a restart; use one handle sequentially.

Open questions require the person's actual `userText`; the engine analyzes
that text. Closed and semi-open questions accept choices alone without a model
call. Semi-open choices may include `freeText`, which is analyzed. An explicit
`outcome: "asked_no_answer"` or `outcome: "refused"` needs no text or model call
for any type. These no-model paths assume no other text needs analysis.
`answer()` rejects an open answer without text locally, before any HTTP request.


```bash
pnpm add @zelinqa/sdk   # or npm install / yarn add
```

## Security — backend only

A Zelinqa key is a bearer credential scoped to one domain and its permissions. **Never ship it to a
browser, a mobile bundle or any client-side code**, and never log it. The SDK
keeps the key in a private field, writes it to exactly one place — the
`Authorization` header — and never puts it in an error, a message, a stack trace
or the output of `toString()`.

Use separate keys with least-privilege scopes: `runtime` for conversations,
`configuration:read` / `configuration:write` / `configuration:publish` for
management. Zelinqa Studio shows a raw key only once.

## Two clients

| Client | Scope | What it does |
|---|---|---|
| `ZelinqaClient` | `runtime` | create a session, get the next question, apply events, read the state, record the outcome |
| `ZelinqaConfigurationClient` | `configuration:*` | read the corpus, edit the draft, publish, follow the compilation, read the audit log |

```ts
import { ZelinqaClient, ZelinqaConfigurationClient } from "@zelinqa/sdk";

const runtime = new ZelinqaClient({ apiKey: process.env.ZELINQA_RUNTIME_KEY ?? "" });
const configuration = new ZelinqaConfigurationClient({
  apiKey: process.env.ZELINQA_CONFIGURATION_KEY ?? "",
});
```

Constructor options (both clients):

| Option | Default | Meaning |
|---|---|---|
| `apiKey` | — | required; the SDK never reads the environment for you |
| `baseUrl` | `https://api.zelinqa.ai` | absolute HTTP(S) origin, no credentials, query or fragment |
| `timeoutMs` | `30000` | per-attempt timeout |
| `maxRetries` | `2` | retries after the first attempt |
| `fetch` | global `fetch` | inject your own for tests or exotic runtimes |

Every method takes an optional `{ idempotencyKey?, signal? }` as its last
argument.

## Runtime: the conversation loop

### With the `Session` handle (recommended)

`startSession()` creates the session and returns a handle that remembers
`state_version` for you.

```ts
const session = await runtime.startSession({
  client_reference: "crm-lead-8842",
  max_turns: 15,
});

// First turn: no previous_turn yet.
let decision = await session.next();

while (decision.action === "ask") {
  const candidate = decision.candidates[0];
  if (candidate === undefined) break;

  // Your agent is free to rephrase the published wording.
  const assistantText = candidate.text;
  const userText = await askTheVisitor(assistantText);

  decision = await session.next({
    previous_turn: { assistant_text: assistantText, user_text: userText },
  });
}

// Structured answer to a choice question: deterministic, no LLM call.
await session.next({
  previous_turn: {
    question_id: "q_style",
    structured_answer: { choice_ids: ["choice_contemporain"] },
  },
});

// Data you already know, applied out of turn.
await session.applyEvents({
  client_updates: { data: [{ id: "annual_budget", operation: "set", value: 2500 }] },
});

// Context that never went through Zelinqa.
await session.applyEvents({
  context_update: { mode: "summary", text: "The visitor moves in March." },
});

const state = await session.refresh();
console.log(state.progress.objective.effective_status, session.stateVersion);

await session.submitFeedback({
  result: "success",
  label: "purchase",
  metadata: { order_id: "SO-99120" },
});
```

The handle exposes `id`, `stateVersion`, `state` (the last full `SessionState`),
and `pendingDecision`. `refresh()` re-reads the session — after a crash, that is
all you need: the pending decision comes back with its exact candidates, so no
resume token exists or is needed.

`session.answer({ userText: "..." })` resolves the pending question's technical
identifiers and requests the next decision. For a choice question, use
`session.answer({ choiceLabels: ["Exact displayed label"] })`. A semi-open
choice can add `freeText`. Unknown, duplicate or ambiguous labels are rejected
locally. The exported `answerTurn(pendingDecision, answer)` builds only the
`previous_turn` payload and makes no HTTP call; use it when calling `next()`
directly.

`resumeSession(sessionId)` builds a handle from an existing session.

### With explicit `state_version`

Every mutation carries the version you last read, as an optimistic lock. Use the
plain client when you persist the version yourself — in a queue message, a
workflow step or a database row.

```ts
const created = await runtime.createSession({ client_reference: "crm-lead-8842" });
let stateVersion = created.versions.state_version; // 0

const decision = await runtime.next("ses_01J8Z", {
  state_version: stateVersion,
  previous_turn: { user_text: "Around 2000 euros." },
});
stateVersion = decision.versions.state_version;

const state = await runtime.applyEvents("ses_01J8Z", {
  state_version: stateVersion,
  client_updates: { dimensions: [{ id: "so_delivery", operation: "exclude" }] },
});
stateVersion = state.versions.state_version;
```

If the session moved in the meantime, the API answers `state_version_conflict`
and the SDK raises `ZelinqaStateVersionConflictError`. **It is never swallowed and
never auto-refreshed** — you decide whether to re-read and replay:

```ts
import { ZelinqaStateVersionConflictError } from "@zelinqa/sdk";

try {
  await session.next({ previous_turn: { user_text } });
} catch (error) {
  if (error instanceof ZelinqaStateVersionConflictError) {
    console.warn("session moved", error.suppliedStateVersion, "→", error.currentStateVersion);
    await session.refresh();
  } else {
    throw error;
  }
}
```

### Soft stops

Zelinqa does not decide for you. When `max_turns` is reached, the objective is
already achieved, or normal eligibility is empty, `next` still returns the best
available question and says so in `warnings`
(`max_turns_reached`, `objective_achieved`, `eligibility_exhausted_fallback`,
`constraints_relaxed`). `action: "stop"` only happens when no identifiable
question is left; then `stop_reason` is set and `candidates` is empty.

`degraded` / `degraded_reasons` tell you when the turn was understood in reduced
mode — typically `missing_user_text` when you sent no verbatim.

## Configuration: edit, publish, compile

```ts
// Read the published corpus.
const published = await configuration.getConfiguration();

// Read the draft — needs configuration:read AND configuration:write.
const draft = await configuration.getConfiguration({ state: "draft" });

// One page, or every page.
const page = await configuration.listQuestions({ limit: 50, type: "single_choice" });
for await (const question of configuration.iterateQuestions({ active: true })) {
  console.log(question.id, question.text);
}

// Spreadsheet export includes selection_mode and source.
const csv = await configuration.exportQuestionsCsv({ dimension_id: "so_besoin" });

// Atomic, ordered draft edit. Pin the revision you read to avoid clobbering
// another editor's work.
const applied = await configuration.applyChanges({
  expected_draft_revision: draft.draft_revision ?? undefined,
  changes: [
    {
      entity: "question",
      operation: "create",
      question: {
        id: "q_delivery_window",
        text: "When would you like it delivered?",
        type: "single_choice",
        dimension_id: "so_livraison",
        active: true,
        choices: [
          { id: "choice_1m", label: "Within the month", maps_to_value: "dans_le_mois" },
          { id: "choice_3m", label: "Within three months", maps_to_value: "trois_mois" },
        ],
      },
    },
  ],
});

// Publishing is asynchronous: it queues a compilation and returns 202.
const queued = await configuration.publish({
  expected_draft_revision: applied.draft_revision,
});

// Wait for it. Returns the terminal status — including `failed`.
const terminal = await configuration.waitForCompilation(queued.compilation_id, {
  pollIntervalMs: 3_000,
  timeoutMs: 900_000,
});

if (terminal.status === "failed") {
  console.error(terminal.error?.code, terminal.error?.message);
} else {
  console.log("now active:", terminal.configuration_version);
}

// Who changed what (configuration:publish).
const audit = await configuration.listAudit({ limit: 50, resource_type: "question" });
```

`waitForCompilation` raises `ZelinqaCompilationTimeoutError` only when the budget runs
out: a failed compilation is an editorial outcome, not an SDK failure, so inspect
`status.error` yourself. Pass a `signal` to cancel the wait.

`applyChanges` and `publish` report blocking problems as
`ZelinqaConfigurationValidationError`, with every issue grouped in `error.issues`
(`code`, `message`, `entity`, `entity_id`, `change_index`) so a UI can show them
all at once. Display `message`; never assume you know every `code`.

## Errors

SDK HTTP, connection and compilation errors derive from `ZelinqaError`.
Invalid local arguments, choice labels and caller cancellation can instead
raise native JavaScript errors.

| Class | When |
|---|---|
| `ZelinqaConnectionError` | network failure or per-attempt timeout, after retries |
| `ZelinqaCompilationTimeoutError` | `waitForCompilation` budget spent |
| `ZelinqaAPIError` | base of every HTTP failure: `statusCode`, `code`, `message`, `requestId`, `details`, `retryAfter` |
| `ZelinqaAuthenticationError` | `401` (no `Authorization` header) **or** `403` without a V1 envelope: key invalid, revoked, expired, or missing the scope the gateway requires for the route |
| `ZelinqaInsufficientScopeError` | `403` with `insufficient_scope`: the service's dynamic check — today only `?state=draft` without `configuration:write`. Carries `requiredScopes` / `grantedScopes` |
| `ZelinqaNotFoundError` | `404`; specialised as `ZelinqaUnknownSessionError`, `ZelinqaUnknownConfigurationError`, `ZelinqaUnknownCompilationError` |
| `ZelinqaConflictError` | `409`; specialised as `ZelinqaStateVersionConflictError` (`suppliedStateVersion`, `currentStateVersion`), `ZelinqaIdempotencyKeyReusedError`, `ZelinqaCompilationInProgressError` (`compilationId`, `compilationStatus`) |
| `ZelinqaCompiledArtifactUnavailableError` | `410`: the artifact pinned by the session is unreachable |
| `ZelinqaValidationError` | `422`; specialised as `ZelinqaInvalidPreviousTurnError`, `ZelinqaConstraintNoMatchError`, `ZelinqaInvalidChoiceError`, `ZelinqaConfigurationValidationError` (`issues`) |
| `ZelinqaRateLimitError` | `429`; retried automatically first |
| `ZelinqaIdempotencyContentionError` | `503 idempotency_contention`; retried automatically, raised only once retries are exhausted |
| `ZelinqaServerError` | any other `5xx` |

The gateway cannot tell an invalid key from an under-scoped one — both are a bare
`403 {"message":"Forbidden"}` — so both become `ZelinqaAuthenticationError`. Read
`statusCode` to distinguish a missing header (`401`) from a refused key (`403`).

`String(error)` renders as `"<code or status>: <message> (request_id=…)"`. Quote
`error.requestId` when you open a support ticket.

## Retries, timeouts and idempotency

- Every mutation sends `Idempotency-Key`. If you do not supply one, the SDK
  generates a UUID v4 **once per logical call and reuses it across all retries**,
  so a retry can never double a turn, an outcome, a feedback or a publication.
  Same key + same body replays the original response; same key + different body
  is `ZelinqaIdempotencyKeyReusedError`. Server-side records are purged after 24 h.
- Retried, up to `maxRetries`: connection errors, per-attempt timeouts, `429`,
  `500`, `502`, `503`, `504`. `GET` requests too.
- Never retried: `400`, `401`, `403`, `404`, `409`, `410`, `422`.
- Backoff: 0.5 s, 1 s, 2 s … capped at 10 s, with jitter. `Retry-After` (seconds
  or HTTP-date) and `details.retry_after_seconds` win, capped at 30 s.
- `timeoutMs` applies **per attempt**, via an `AbortController` combined with the
  `signal` you pass.

```ts
const controller = new AbortController();
setTimeout(() => controller.abort(), 5_000);

await runtime.next("ses_01J8Z", { state_version: 4 }, {
  idempotencyKey: "next-ses01J8Z-turn-4", // 8–128 characters
  signal: controller.signal,
});
```

## Custom `fetch`

Any Fetch-compatible function works — useful for tests, tracing, proxies or a
runtime whose global `fetch` you would rather not use.

```ts
const client = new ZelinqaClient({
  apiKey: process.env.ZELINQA_RUNTIME_KEY ?? "",
  fetch: async (input, init) => {
    const started = Date.now();
    const response = await fetch(input, init);
    // Log the route and the status — never the headers.
    console.log(new URL(String(input)).pathname, response.status, Date.now() - started);
    return response;
  },
});
```

## Types

All wire types are generated from `openapi/nbq-v1.openapi.yaml` and re-exported
under friendly names: `SessionState`, `NextResponse`, `Candidate`,
`Configuration`, `ConfiguredQuestion`, `CompilationStatus`,
`ConfigurationChange`, and so on. Field names are snake_case, exactly as on the
wire. Regenerate with `pnpm generate:types`.

## License

Apache-2.0. See [LICENSE](https://github.com/Zelinqa/zelinqa-sdk/blob/main/LICENSE).
