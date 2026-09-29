# Changelog

## 1.0.1 — Unreleased

- Python: `StopReason` accepts `max_turns_reached`. When a session reaches
  `max_turns`, `next()` returns `action: "stop"` with
  `stop_reason: "max_turns_reached"`, an empty `candidates` list and no
  `decision_id`; the session status becomes `stopped` and later calls return the
  same stop. Version 1.0.0 rejected this response with a validation error.
- `max_turns_reached` is a stop reason, no longer a `SelectionWarning` value.
- Python: enumerations emitted only by the server are open: their alias is
  `Literal[<documented values>] | str`, so a value added by a later API release
  is returned as a plain string instead of failing validation. This covers
  `StopReason`, `SelectionWarning`, `DegradedReason`, `SessionStatus`,
  `ProgressStatus`, `DimensionEffectiveStatus`, `DimensionOverrideValue`,
  `TargetKind`, `TargetStatus`, `OutcomeSource`, `ConfigurationState`,
  `CompilationStatusValue`, `CompilationErrorCode`, `ConfigurationIssueEntity`,
  `ErrorCode` and the audit enumerations. Enumerations a client sends, and
  `NextAction`, stay closed.
- TypeScript: types regenerated from the updated contract. `StopReason`
  includes `max_turns_reached`; `SelectionWarning` no longer does. No runtime
  change.
- Guides and READMEs describe the stop at `max_turns`.

## 1.0.0 — 2026-09-27

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
redirect from old packages.
