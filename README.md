# Zelinqa TypeScript SDK

The official TypeScript client for Zelinqa. Configure a domain in
[Zelinqa Studio](https://client.zelinqa.ai), publish it, and let the API select
the next useful question for each conversation.

Node.js 22+ · ESM and CommonJS · Typed responses · No runtime dependencies

## Install

```bash
npm install @zelinqa/sdk
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

```ts
import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";
import { ZelinqaClient } from "@zelinqa/sdk";

const client = new ZelinqaClient({ apiKey: process.env.ZELINQA_API_KEY ?? "" });
const session = await client.startSession({ client_reference: "demo-001" });
let decision = await session.next();
const question = decision.candidates[0];

if (decision.action === "ask" && question !== undefined) {
  console.log(question.text);
  for (const choice of question.choices) {
    console.log(`- ${choice.label}`);
  }

  const input = createInterface({ input: stdin, output: stdout });
  try {
    const reply = await input.question("> ");
    if (!reply.trim()) {
      decision = await session.answer({ outcome: "asked_no_answer" });
    } else if (question.choices.length > 0) {
      decision = await session.answer({ choiceLabels: [reply] });
    } else {
      decision = await session.answer({ userText: reply });
    }
    console.log(decision.action, decision.warnings);
  } finally {
    input.close();
  }
}
```

The session handle supplies session, decision, question and choice IDs for you.
Each `answer()` records the reply and returns the next decision; do not call
`next()` again just to retrieve that question. Use a session sequentially and
store `session.id` in your backend if you need to resume after a restart.

Open questions need the person's actual words. Choices alone need no model
call; free text is analyzed by the engine. Report a real refusal with
`outcome: "refused"`, or an empty reply with `outcome: "asked_no_answer"`.
Keep warnings visible and let your application decide when to finish.

## What you can do

- Run conversations, resume sessions, add context and record business feedback.
- Read questions, edit drafts and publish with `ZelinqaConfigurationClient`
  and the corresponding configuration scopes.
- Handle typed errors, configure timeouts and retries, and cancel requests.

## Learn more

- [Documentation and API reference](https://docs.zelinqa.ai)
- [TypeScript guide: choices, configuration, errors and compiler setup](https://github.com/Zelinqa/zelinqa-sdk/blob/main/typescript/docs/usage.md)
- [Python SDK](https://github.com/Zelinqa/zelinqa-sdk/blob/main/python/README.md) — `pip install zelinqa`
- [Changelog](https://github.com/Zelinqa/zelinqa-sdk/blob/main/CHANGELOG.md)
- [Report an issue](https://github.com/Zelinqa/zelinqa-sdk/issues)

## License

Apache-2.0. See [LICENSE](https://github.com/Zelinqa/zelinqa-sdk/blob/main/LICENSE).
