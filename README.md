<p align="center">
  <a href="https://zelinqa.ai">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Zelinqa/zelinqa-sdk/main/assets/readme/hero-dark.svg">
      <img src="https://raw.githubusercontent.com/Zelinqa/zelinqa-sdk/main/assets/readme/hero-light.svg" alt="Zelinqa SDK" width="100%">
    </picture>
  </a>
</p>

<p align="center">
  <a href="https://pypi.org/project/zelinqa/"><img src="https://img.shields.io/pypi/v/zelinqa?label=PyPI&color=6B5BD6" alt="PyPI version"></a>
  <a href="https://www.npmjs.com/package/@zelinqa/sdk"><img src="https://img.shields.io/npm/v/%40zelinqa%2Fsdk?label=npm&color=6B5BD6" alt="npm version"></a>
  <a href="https://github.com/Zelinqa/zelinqa-sdk/actions/workflows/python-ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Zelinqa/zelinqa-sdk/python-ci.yml?branch=main&label=Python%20CI" alt="Python CI"></a>
  <a href="https://github.com/Zelinqa/zelinqa-sdk/actions/workflows/typescript-ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/Zelinqa/zelinqa-sdk/typescript-ci.yml?branch=main&label=TypeScript%20CI" alt="TypeScript CI"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/node-22%2B-5FA04E?logo=node.js&logoColor=white" alt="Node.js 22+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="License Apache-2.0"></a>
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#your-first-conversation">First conversation</a> ·
  <a href="#what-you-can-do">What you can do</a> ·
  <a href="#documentation">Documentation</a> ·
  <a href="https://docs.zelinqa.ai">docs.zelinqa.ai</a>
</p>

---

**Zelinqa chooses the next question.** You describe once what a conversation must find out. Your application sends each reply from the person, and the engine returns the next useful question, or stops when the objective is met. Your assistant keeps the conversation and the wording; the SDK keeps track of session, decision, question and choice identifiers for you.

## Why

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Zelinqa/zelinqa-sdk/main/assets/readme/why-dark.svg">
  <img src="https://raw.githubusercontent.com/Zelinqa/zelinqa-sdk/main/assets/readme/why-light.svg" alt="Without Zelinqa, the model generates every question and decides with gaps. With Zelinqa, the engine runs the questioning phase from the published domain and the model decides once, with a complete typed state." width="100%">
</picture>

Any agent that has to ask before it decides, for an interview, a qualification, an orientation, a triage or a recommendation, faces the same problem: the model that decides is also the one improvising the questions. Zelinqa takes the questioning phase. The model keeps the conversation and the final decision, with a complete, typed state instead of a transcript.

## How it works

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Zelinqa/zelinqa-sdk/main/assets/readme/loop-dark.svg">
  <img src="https://raw.githubusercontent.com/Zelinqa/zelinqa-sdk/main/assets/readme/loop-light.svg" alt="Your application sends each reply; the Zelinqa engine returns the next question or a stop, with progress per dimension." width="100%">
</picture>

1. **Configure once.** In [Zelinqa Studio](https://client.zelinqa.ai), describe a domain: the dimensions a conversation must cover, the questions available for each one, the information each question collects, and the objective. Publish it and create a runtime API key.
2. **Open a session and send replies.** The engine reads each reply, updates what is known and measures the progress of every dimension.
3. **Ask what comes back.** Each call returns the next question with its choices, or a stop with its reason. The question text comes from your published domain, in your words.

## Install

<table>
<tr>
<th align="left">Python 3.11+</th>
<th align="left">TypeScript · Node.js 22+</th>
</tr>
<tr>
<td>

```bash
pip install zelinqa
```

</td>
<td>

```bash
npm install @zelinqa/sdk
```

</td>
</tr>
<tr>
<td>Sync and async clients · typed responses</td>
<td>ESM and CommonJS · typed responses · no runtime dependencies</td>
</tr>
</table>

Create a runtime API key for your published domain in Studio and set it on your backend:

```bash
export ZELINQA_API_KEY="YOUR_RUNTIME_API_KEY"
```

Keep API keys out of browser code, mobile apps, logs and source control. A free [Developer edition](https://zelinqa.ai/en/pricing) account is enough to run every example below.

## Your first conversation

Both examples ask one question, send the person's reply and read the next decision. For choice questions, enter one of the displayed labels exactly.

**Python**

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

**TypeScript**

```ts
import { ZelinqaClient } from "@zelinqa/sdk";

const client = new ZelinqaClient({ apiKey: process.env.ZELINQA_API_KEY ?? "" });
const session = await client.startSession({ client_reference: "demo-001" });
let decision = await session.next();
const question = decision.candidates[0];

if (decision.action === "ask" && question !== undefined) {
  console.log(question.text);
  for (const choice of question.choices) console.log(`- ${choice.label}`);

  const reply = await readLine("> "); // your input method
  if (!reply.trim()) {
    decision = await session.answer({ outcome: "asked_no_answer" });
  } else if (question.choices.length > 0) {
    decision = await session.answer({ choiceLabels: [reply] });
  } else {
    decision = await session.answer({ userText: reply });
  }
  console.log(decision.action, decision.warnings);
}
```

A few rules the engine enforces:

- Each `answer()` records the reply **and** returns the next decision. Do not call `next()` again just to read that question.
- Open questions need the person's actual words. Choices alone need no model call; free text is analyzed by the engine.
- Report a real refusal with `outcome: "refused"` and an empty reply with `outcome: "asked_no_answer"`. Never invent an answer.
- Use a session sequentially and store `session.id` in your backend to resume after a restart.
- When the session reaches `max_turns`, the decision has `action: "stop"` and `stop_reason: "max_turns_reached"`; later calls return the same stop. Keep warnings visible and let your application decide when to finish.

## What you can do

<table>
<tr>
<td width="33%" valign="top"><strong>Run conversations</strong><br>Start a session, send replies, read the next question and the progress of every dimension.</td>
<td width="33%" valign="top"><strong>Resume after a restart</strong><br>Store the session id and pick the conversation up where it stopped, with the same handle.</td>
<td width="33%" valign="top"><strong>Add what you already know</strong><br>Pass CRM data as confirmed information or a context summary, without asking the person again.</td>
</tr>
<tr>
<td valign="top"><strong>Record outcomes</strong><br>Submit the business result at the end so every conversation can be reviewed against what it produced.</td>
<td valign="top"><strong>Manage configuration</strong><br>Read questions, edit drafts and publish with the configuration client and its dedicated scopes.</td>
<td valign="top"><strong>Stay in control</strong><br>Typed errors, timeouts, retries, idempotency keys, request cancellation and a custom <code>fetch</code> in TypeScript.</td>
</tr>
</table>

## Documentation

| Resource | Where |
|---|---|
| Getting started, concepts and API reference | [docs.zelinqa.ai](https://docs.zelinqa.ai) |
| Python guide: sessions, async, configuration, errors | [`python/docs/usage.md`](python/docs/usage.md) |
| TypeScript guide: sessions, configuration, errors, compiler setup | [`typescript/docs/usage.md`](typescript/docs/usage.md) |
| REST API reference | [docs.zelinqa.ai/en/integrations/rest-api](https://docs.zelinqa.ai/en/integrations/rest-api) |
| OpenAPI contract used to generate the types | [`openapi/`](openapi/) |
| Changelog | [`CHANGELOG.md`](CHANGELOG.md) |
| Security policy | [`SECURITY.md`](SECURITY.md) |

## Related

- [**zelinqa-mcp**](https://github.com/Zelinqa/zelinqa-mcp): the same engine as an MCP server for Claude Desktop, Claude Code, Cursor, Codex and other MCP hosts, with no code to write.
- [**Zelinqa Studio**](https://client.zelinqa.ai): where domains are configured, published and measured.

## Contributing

Issues and pull requests are welcome at [github.com/Zelinqa/zelinqa-sdk](https://github.com/Zelinqa/zelinqa-sdk/issues). Release prerequisites are described in [`PUBLISHING.md`](PUBLISHING.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
