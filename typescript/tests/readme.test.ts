import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { runInNewContext } from "node:vm";
import ts from "typescript";
import { afterEach, describe, expect, it, vi } from "vitest";

import { type NextResponse, ZelinqaClient } from "../src/index.js";
import { at, jsonResponse, parseBody, recordFetch, sharedExample } from "./helpers.js";

const readmes = [
  new URL("../../README.md", import.meta.url),
  new URL("../README.md", import.meta.url),
];

function example(url: URL): string {
  const source = readFileSync(url, "utf8");
  const blocks = [...source.matchAll(/```ts\n([\s\S]*?)\n```/g)];
  expect(blocks).toHaveLength(1);
  const code = at(blocks, 0)[1];
  if (!code) throw new Error("Missing README example");
  return code;
}

function decision(type: "open" | "single_choice"): NextResponse {
  const response = structuredClone(sharedExample("DecisionNormale")) as unknown as NextResponse;
  const question = at(response.candidates, 0);
  response.candidates = [question];
  question.type = type;
  if (type === "open") {
    delete question.selection_mode;
  } else {
    question.selection_mode = "single";
  }
  question.choices = type === "open" ? [] : [{ choice_id: "choice_email", label: "Email" }];
  return response;
}

async function runExample(url: URL, reply: string, next: NextResponse) {
  const recorder = recordFetch([
    () => jsonResponse(sharedExample("SessionNeuve"), { status: 201 }),
    () => jsonResponse(next),
    () => jsonResponse(sharedExample("DecisionNormale")),
  ]);
  vi.stubGlobal("fetch", recorder.fetch);
  const input = { question: vi.fn(async () => reply), close: vi.fn() };
  const createInterface = vi.fn(() => input);
  const log = vi.fn();
  const source = example(url);
  expect(source).toContain('import { ZelinqaClient } from "@zelinqa/sdk";');
  expect([...source.matchAll(/^import .+;$/gm)]).toHaveLength(3);
  const executable = source.replace(/^import .+;\n/gm, "");
  const result: unknown = runInNewContext(`(async () => { ${executable}\n})()`, {
    ZelinqaClient,
    createInterface,
    stdin: {},
    stdout: {},
    console: { log },
    process: { env: { ZELINQA_API_KEY: "readme-test-key" } },
  });
  await result;
  return { recorder, input, createInterface, log };
}

afterEach(() => vi.unstubAllGlobals());

it("keeps the npm and TypeScript quickstarts identical", () => {
  expect(readFileSync(at(readmes, 0), "utf8")).toBe(readFileSync(at(readmes, 1), "utf8"));
});

describe.each(readmes)("README example: %s", (url) => {
  it("typechecks the documented code with the SDK's public exports", () => {
    const configPath = fileURLToPath(new URL("../tsconfig.json", import.meta.url));
    const config = ts.readConfigFile(configPath, ts.sys.readFile);
    expect(config.error).toBeUndefined();
    const parsed = ts.parseJsonConfigFileContent(
      config.config,
      ts.sys,
      fileURLToPath(new URL("../", import.meta.url)),
    );
    const path = fileURLToPath(new URL("./readme-example.ts", import.meta.url));
    const options = {
      ...parsed.options,
      paths: { "@zelinqa/sdk": [fileURLToPath(new URL("../src/index.ts", import.meta.url))] },
    };
    const host = ts.createCompilerHost(options);
    const originalSource = host.getSourceFile.bind(host);
    host.getSourceFile = (file, languageVersion, onError, shouldCreateNewSourceFile) =>
      file === path
        ? ts.createSourceFile(file, example(url), languageVersion, true)
        : originalSource(file, languageVersion, onError, shouldCreateNewSourceFile);
    const program = ts.createProgram([path], options, host);
    expect(
      ts
        .getPreEmitDiagnostics(program)
        .map((d) => ts.flattenDiagnosticMessageText(d.messageText, "\n")),
    ).toEqual([]);
  });

  it("submits real text with the pending identifiers and current state version", async () => {
    const next = decision("open");
    const { recorder, input, log } = await runExample(url, "We need to qualify requests", next);
    expect(recorder.requests).toHaveLength(3);
    expect(parseBody(at(recorder.requests, 2))).toMatchObject({
      state_version: next.versions.state_version,
      previous_turn: {
        decision_id: next.decision_id,
        question_id: at(next.candidates, 0).question_id,
        user_text: "We need to qualify requests",
      },
    });
    expect(input.close).toHaveBeenCalledOnce();
    expect(log).toHaveBeenLastCalledWith("ask", sharedExample("DecisionNormale").warnings);
  });

  it("resolves the displayed choice label without an extra request", async () => {
    const { recorder, input } = await runExample(url, "Email", decision("single_choice"));
    expect(recorder.requests).toHaveLength(3);
    expect(parseBody(at(recorder.requests, 2))).toMatchObject({
      previous_turn: { structured_answer: { choice_ids: ["choice_email"] } },
    });
    expect(input.close).toHaveBeenCalledOnce();
  });

  it("reports an empty reply as asked_no_answer", async () => {
    const { recorder, input } = await runExample(url, " \t", decision("open"));
    expect(recorder.requests).toHaveLength(3);
    expect(parseBody(at(recorder.requests, 2))).toMatchObject({
      previous_turn: { outcome: "asked_no_answer" },
    });
    expect(input.close).toHaveBeenCalledOnce();
  });

  it("does not prompt or submit an answer when the engine stops", async () => {
    const next = sharedExample("ArretSansQuestion") as unknown as NextResponse;
    const { recorder, createInterface } = await runExample(url, "unused", next);
    expect(recorder.requests).toHaveLength(2);
    expect(createInterface).not.toHaveBeenCalled();
  });

  it("rejects an unknown choice locally", async () => {
    const next = decision("single_choice");
    await expect(runExample(url, "Unknown", next)).rejects.toThrow(
      "Unknown or ambiguous choice label",
    );
  });
});
