# Zelinqa Python SDK

The official Python client for Zelinqa. Configure a domain in
[Zelinqa Studio](https://client.zelinqa.ai), publish it, and let the API select
the next useful question for each conversation.

Python 3.11+ · Sync and async clients · Typed responses

## Install

```bash
pip install zelinqa
```

## Set your API key

Create a runtime API key for your published domain in Studio and set it on
your backend:

```bash
export ZELINQA_API_KEY="YOUR_RUNTIME_API_KEY"
```

Keep API keys out of browser code, mobile apps, logs and source control.

## Get a question and send the reply

This small console example asks one question and submits the person's reply.
For choice questions, enter one of the displayed labels exactly.

```python
from zelinqa import ZelinqaClient

with ZelinqaClient() as client:
    session = client.start_session(client_reference="demo-001")
    decision = session.next()

    if decision.action == "ask":
        question = decision.candidates[0]
        print(question.text)
        for choice in question.choices:
            print(f"- {choice.label}")

        reply = input("> ")
        if not reply.strip():
            decision = session.answer(outcome="asked_no_answer")
        elif question.choices:
            decision = session.answer(choice_labels=[reply])
        else:
            decision = session.answer(reply)

        print(decision.action)  # "ask" for another question, or "stop"
```

The session handle supplies session, decision, question and choice IDs for you.
Each `answer()` records the reply and returns the next decision; do not call
`next()` again just to retrieve that question. Use a session sequentially and
store `session.id` in your backend if you need to resume after a restart.

Open questions need the person's actual words. Choices alone need no model
call; free text is analyzed by the engine. Report a real refusal with
`outcome="refused"`, or an empty reply with `outcome="asked_no_answer"`.
Keep warnings visible and let your application decide when to finish.

## What you can do

- Run conversations, resume sessions, add context and record business feedback.
- Use `AsyncZelinqaClient` with `await` for asynchronous applications.
- Read questions, edit drafts and publish with `ZelinqaConfigurationClient`
  and the corresponding configuration scopes.
- Handle typed errors, configure timeouts and retries, and provide idempotency keys.

## Learn more

- [Documentation and API reference](https://docs.zelinqa.ai)
- [Python guide: choices, async, configuration and errors](https://github.com/Zelinqa/zelinqa-sdk/blob/main/python/docs/usage.md)
- [Changelog](https://github.com/Zelinqa/zelinqa-sdk/blob/main/CHANGELOG.md)
- [Report an issue](https://github.com/Zelinqa/zelinqa-sdk/issues)

## License

Apache-2.0. See [LICENSE](https://github.com/Zelinqa/zelinqa-sdk/blob/main/LICENSE).
