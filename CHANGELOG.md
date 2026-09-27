# Changelog

## 1.0.0 — Unreleased

- Python `zelinqa`, TypeScript `@zelinqa/sdk`, `ZelinqaClient` and
  `ZelinqaConfigurationClient`; Python also provides async clients.
- V1 runtime sessions, next question, context/events, progress and feedback.
- `Session.answer` with text or choice labels; IDs stay in the integration layer.
- Open answers require the person's text before a request is sent. Explicit
  `asked_no_answer` and `refused` outcomes remain valid without text for all types.
  Choice-only answers remain supported for closed and semi-open questions.
- Current question metadata: editorial `source` and semi-open `selection_mode`.
- Configuration read/edit/publish, compilation polling, CSV export and audit.
- Typed errors, optimistic concurrency, retry/idempotency support.
- Python errors survive pickling: `ZelinqaAPIError` subclasses and
  `ZelinqaCompilationTimeoutError` cross process boundaries (`multiprocessing`,
  process pools, task queues) with their status, details and derived attributes.
- The 0.9 routes are no longer exposed by the SDKs. The `nbq` and
  `@zelinqa/nbq` 0.9 packages remain separate distributions.

Migration: replace `nbq` imports / `@zelinqa/nbq`, `NBQClient` class names and
`NBQ_*` SDK environment variables with the documented Zelinqa equivalents.
Existing API keys, REST paths and wire fields do not change. No automatic
redirect from old packages and no publication is implied by this entry.
