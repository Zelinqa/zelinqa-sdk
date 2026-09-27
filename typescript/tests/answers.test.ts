import { describe, expect, it } from "vitest";
import {
  type AnswerInput,
  answerTurn,
  type PendingDecisionView,
  type SessionStateResponse,
  ZelinqaClient,
} from "../src/index.js";
import { sharedExample } from "./helpers.js";

const pending: PendingDecisionView = {
  decision_id: "decision-hidden",
  candidates: [
    {
      rank: 1,
      question_id: "question-hidden",
      text: "Channels?",
      type: "semi_open",
      selection_mode: "multiple",
      target_ids: [],
      choices: [
        { choice_id: "a", label: "Email" },
        { choice_id: "b", label: "Phone" },
      ],
    },
  ],
};

describe("business answers", () => {
  function pendingType(type: PendingDecisionView["candidates"][number]["type"]) {
    const p = structuredClone(pending);
    const candidate = p.candidates[0];
    if (!candidate) throw new Error("Missing test candidate");
    candidate.type = type;
    if (type === "open") {
      candidate.choices = [];
      delete candidate.selection_mode;
    }
    return p;
  }

  it.each<AnswerInput>([
    {},
    { outcome: "asked_answered" },
    { userText: "" },
    { userText: " \t\n" },
    { assistantText: "What do you need?" },
  ])("requires actual text for an open answer", (input) => {
    expect(() => answerTurn(pendingType("open"), input)).toThrow("open question requires userText");
  });

  it.each(["open", "single_choice", "multiple_choice", "semi_open"] as const)(
    "allows explicit unanswered outcomes without text for %s",
    (type) => {
      for (const outcome of ["asked_no_answer", "refused"] as const) {
        const turn = answerTurn(pendingType(type), { outcome });
        expect(turn.outcome).toBe(outcome);
        expect(turn.user_text).toBeUndefined();
        expect(turn.structured_answer).toBeUndefined();
      }
    },
  );

  it.each<[PendingDecisionView["candidates"][number]["type"], AnswerInput]>([
    ["open", { userText: "We need to qualify requests" }],
    ["single_choice", { choiceLabels: ["Email"] }],
    ["multiple_choice", { choiceLabels: ["Email", "Phone"] }],
    ["semi_open", { choiceLabels: ["Email"] }],
    ["semi_open", { choiceLabels: ["Email"], freeText: "Also mail" }],
    ["semi_open", { userText: "A different channel" }],
    ["single_choice", { choiceLabels: ["Email"], userText: "Email is easiest" }],
  ])("preserves valid %s answer forms", (type, input) => {
    expect(answerTurn(pendingType(type), input).user_text).toBe(input.userText);
  });

  it("uses the type of the selected candidate", () => {
    const mixed = pendingType("open");
    const second = pending.candidates[0];
    if (!second) throw new Error("Missing test candidate");
    mixed.candidates.push({ ...second, rank: 2 });
    expect(
      answerTurn(mixed, { candidateRank: 2, choiceLabels: ["Email"] }).structured_answer,
    ).toEqual({ choice_ids: ["a"] });
  });

  it("rejects before network without losing the pending decision", async () => {
    const state = structuredClone(sharedExample("SessionNeuve")) as unknown as SessionStateResponse;
    state.pending_decision = pendingType("open");
    state.versions.state_version = 7;
    const requests: (string | undefined)[] = [];
    const client = new ZelinqaClient({
      apiKey: "test",
      fetch: async (_url, init) => {
        requests.push(init?.body?.toString());
        return Response.json(requests.length === 1 ? state : sharedExample("DecisionNormale"));
      },
    });
    const session = await client.resumeSession(state.session_id);
    await expect(session.answer({ outcome: "asked_answered" })).rejects.toThrow(
      "requires userText",
    );
    expect(requests).toHaveLength(1);
    expect(session.stateVersion).toBe(7);
    await session.answer({ outcome: "refused" });
    expect(requests).toHaveLength(2);
    expect(JSON.parse(requests[1] ?? "null")).toMatchObject({
      state_version: 7,
      previous_turn: { outcome: "refused" },
    });
  });
  it("maps exact labels, multi + other, and pending decision", () => {
    expect(
      answerTurn(pending, { choiceLabels: ["Email", "Phone"], freeText: "Mail" }),
    ).toMatchObject({
      decision_id: "decision-hidden",
      question_id: "question-hidden",
      structured_answer: { choice_ids: ["a", "b"], free_text: "Mail" },
    });
  });
  it.each([
    { choiceLabels: ["Unknown"] },
    { choiceLabels: ["Email", "Email"] },
    { candidateRank: 9, userText: "Hello" },
    {},
    { userText: "" },
    { choiceLabels: [] },
    { freeText: "Other" },
  ])("rejects invalid business input", (input) => {
    expect(() => answerTurn(pending, input)).toThrow();
  });
  it("rejects missing pending, single-mode multi-selection and ambiguous labels", () => {
    expect(() => answerTurn(null, { userText: "Hi" })).toThrow();
    const p = structuredClone(pending);
    if (!p.candidates[0]) throw new Error("fixture");
    p.candidates[0].selection_mode = "single";
    expect(() => answerTurn(p, { choiceLabels: ["Email", "Phone"] })).toThrow();
    p.candidates[0].choices.push({ choice_id: "c", label: "Email" });
    expect(() => answerTurn(p, { choiceLabels: ["Email"] })).toThrow();
  });
  it("adds no HTTP roundtrip and infers no success from free text", async () => {
    const bodies: Record<string, unknown>[] = [];
    const client = new ZelinqaClient({
      apiKey: "test",
      fetch: async (_url, init) => {
        bodies.push(JSON.parse(String(init?.body)));
        return Response.json(
          sharedExample(bodies.length === 1 ? "SessionNeuve" : "DecisionNormale"),
        );
      },
    });
    const session = await client.startSession();
    const decision = await session.next();
    await session.answer({ userText: "I am considering it" });
    expect(bodies).toHaveLength(3);
    expect(bodies[2]).toMatchObject({
      state_version: decision.versions.state_version,
      previous_turn: { decision_id: decision.decision_id, user_text: "I am considering it" },
    });
    expect(bodies[2]?.previous_turn).not.toHaveProperty("outcome");
  });
});
