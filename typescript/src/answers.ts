import type { PendingDecisionView, PreviousTurn } from "./types.js";

/** Business-level answer; identifiers are resolved from the pending candidate. */
export interface AnswerInput {
  readonly userText?: string;
  readonly candidateRank?: number;
  readonly choiceLabels?: readonly string[];
  readonly freeText?: string;
  readonly outcome?: PreviousTurn["outcome"];
  readonly assistantText?: string;
}

export function answerTurn(pending: PendingDecisionView | null, answer: AnswerInput): PreviousTurn {
  if (!pending) throw new Error("No pending question. Call next() first.");
  const matches = pending.candidates.filter((c) => c.rank === (answer.candidateRank ?? 1));
  const candidate = matches[0];
  if (matches.length !== 1 || !candidate) throw new Error("Unknown candidate rank");
  let structured: PreviousTurn["structured_answer"];
  if (answer.choiceLabels !== undefined || answer.freeText !== undefined) {
    if (candidate.type === "open") throw new Error("Open questions accept userText, not choices");
    const labels = answer.choiceLabels ?? [];
    if (new Set(labels).size !== labels.length) throw new Error("Duplicate choice labels");
    const ids = labels.map((label) => {
      const choices = candidate.choices.filter((c) => c.label === label);
      if (choices.length !== 1 || !choices[0]) throw new Error("Unknown or ambiguous choice label");
      return choices[0].choice_id;
    });
    if (
      ids.length > 1 &&
      (candidate.type === "single_choice" ||
        (candidate.type === "semi_open" && candidate.selection_mode !== "multiple"))
    ) {
      throw new Error("This question accepts only one choice");
    }
    if (answer.freeText !== undefined && candidate.type !== "semi_open") {
      throw new Error("Free text is only allowed for semi-open choices");
    }
    if (!ids.length)
      throw new Error("Select at least one choice; for an unlisted answer send userText");
    structured = {
      choice_ids: ids,
      ...(answer.freeText !== undefined ? { free_text: answer.freeText } : {}),
    };
  }
  if (
    candidate.type === "open" &&
    answer.outcome !== "asked_no_answer" &&
    answer.outcome !== "refused" &&
    !answer.userText?.trim()
  ) {
    throw new Error(
      "An open question requires userText unless outcome is asked_no_answer or refused",
    );
  }
  if (answer.userText === undefined && !structured && answer.outcome === undefined) {
    throw new Error("Provide an answer, choices, or an explicit outcome");
  }
  if (answer.userText !== undefined && !answer.userText.trim()) throw new Error("Empty answer");
  return {
    decision_id: pending.decision_id,
    question_id: candidate.question_id,
    assistant_text: answer.assistantText ?? candidate.text,
    ...(answer.userText !== undefined ? { user_text: answer.userText } : {}),
    ...(answer.outcome !== undefined ? { outcome: answer.outcome } : {}),
    ...(structured ? { structured_answer: structured } : {}),
  };
}
