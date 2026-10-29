/**
 * The agent loop.
 *
 * A bounded loop: the model is given the tools, its tool calls are validated and executed,
 * the results are fed back, and it is asked to answer. Two guards make it safe to run on a
 * free tier and safe to expose publicly:
 *
 *   - **A hard iteration cap.** A model that loops on tool calls would burn the daily neuron
 *     allowance in one request. The cap is a spend limit as much as a correctness one.
 *   - **A no-tool-call check.** If the model produces an answer without calling a single
 *     tool, the answer is discarded and replaced with a refusal. This is the enforcement of
 *     "the LLM does not answer from memory": the prompt asks it not to, and this makes it
 *     impossible, because a number the model recalls is indistinguishable from a number it
 *     invented and both are unacceptable here.
 */

import { PROMPT_VERSION, SYSTEM_PROMPT } from "./prompt";
import type { Citation } from "./data";
import { coverage } from "./data";
import { executeTool, toolSpecs, validateCall, type ToolName } from "./tools";

export interface Env {
  AI: Ai;
  MODEL: string;
  MODEL_COMPARISON: string;
  ALLOWED_MODELS: string;
  MAX_QUESTION_CHARS: string;
  MAX_TOOL_ITERATIONS: string;
  RATE_LIMIT_PER_MINUTE: string;
  RATE_LIMIT_PER_DAY: string;
}

/**
 * Restrict the caller-supplied model to an allowlist.
 *
 * The request body accepts a model override so the eval harness can compare models without a
 * redeploy. Left unbounded that is a hole: anyone could pass the most expensive model on the
 * account and spend the owner's quota. An unknown model falls back to the default rather than
 * erroring, because a wrong model choice should not be able to break the endpoint.
 */
export function resolveModel(requested: string | undefined, env: Env): string {
  if (!requested) return env.MODEL;
  const allowed = (env.ALLOWED_MODELS ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  return allowed.includes(requested) ? requested : env.MODEL;
}

interface Message {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  name?: string;
}

interface ToolCall {
  name: string;
  arguments?: unknown;
}

interface ModelResponse {
  response?: string;
  tool_calls?: ToolCall[];
  usage?: unknown;
}

export interface TraceStep {
  type: "tool_call" | "tool_result" | "rejected_call" | "model_error";
  name?: string;
  arguments?: unknown;
  ok?: boolean;
  error?: string;
  ms?: number;
}

export interface AgentAnswer {
  answer: string;
  citations: Citation[];
  refused: boolean;
  refusal_reason?: string;
  /** Short, non-sensitive diagnostic string when the model call itself failed. */
  error_detail?: string;
  model: string;
  prompt_version: string;
  steps: TraceStep[];
  iterations: number;
  usage?: unknown;
}

/** Wrap tool output so the model can tell where the data starts and ends. */
function wrapToolResult(name: string, payload: unknown): string {
  // The delimiters are part of the injection defence: they make the boundary between "data"
  // and "conversation" unambiguous even if a value inside happens to look like text.
  return [
    `<<<TOOL_RESULT name="${name}">>>`,
    JSON.stringify(payload),
    "<<<END_TOOL_RESULT>>>",
  ].join("\n");
}

const REFUSAL_NO_TOOL =
  "I can't answer that from the data I have. I only answer questions about Malaysian " +
  "consumer prices, wages and household income, and I have to look the figures up rather " +
  "than recall them — so if I did not retrieve anything, I have nothing to tell you.";

export interface RunOptions {
  env: Env;
  question: string;
  modelOverride?: string;
  maxIterations?: number;
}

export async function runAgent({ env, question, modelOverride, maxIterations }: RunOptions): Promise<AgentAnswer> {
  const model = resolveModel(modelOverride, env);
  const cap = Math.min(
    maxIterations ?? Number(env.MAX_TOOL_ITERATIONS ?? 6),
    Number(env.MAX_TOOL_ITERATIONS ?? 6),
  );

  const messages: Message[] = [
    { role: "system", content: SYSTEM_PROMPT },
    {
      role: "system",
      content: `Data coverage summary (authoritative; do not contradict it):\n${JSON.stringify(
        coverage(),
      )}`,
    },
    { role: "user", content: question },
  ];

  const steps: TraceStep[] = [];
  const citations: Citation[] = [];
  const seenCitations = new Set<string>();
  // Cheap models sometimes repeat a call verbatim. Re-executing costs tokens and adds nothing,
  // so the first result is replayed. It also stops a confused model burning the tool budget.
  const completedCalls = new Map<string, string>();
  let iterations = 0;
  let usage: unknown;
  let lastText = "";

  while (iterations < cap) {
    iterations += 1;

    let response: ModelResponse;
    try {
      response = (await env.AI.run(model as keyof AiModels, {
        messages,
        tools: toolSpecs(),
        max_tokens: 700,
        temperature: 0.1,
      })) as unknown as ModelResponse;
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      steps.push({ type: "model_error", error: message });
      // Log it, and surface a short machine-readable code. A generic "unavailable" makes a
      // quota exhaustion, a removed model and a malformed request look identical, which cost
      // real debugging time more than once.
      console.error(JSON.stringify({ event: "model_error", model, error: message }));
      const hint = /quota|neuron|limit|429|capacity/i.test(message)
        ? "The free daily model allowance may be exhausted; it resets at 00:00 UTC."
        : /no such model|not found|deprecated|removed/i.test(message)
          ? "The configured model is not available on this account."
          : "This is a server-side model error, not a problem with your question.";
      return {
        answer:
          "The language model is unavailable right now, so I can't answer. " +
          hint +
          " The charts on this site are built from the same data and do not depend on the model.",
        citations: [],
        refused: true,
        refusal_reason: `model error: ${message}`,
        error_detail: message.slice(0, 300),
        model,
        prompt_version: PROMPT_VERSION,
        steps,
        iterations,
      };
    }

    usage = response.usage ?? usage;
    lastText = response.response ?? "";
    const calls = response.tool_calls ?? [];

    if (calls.length === 0) {
      // No tool call means no retrieved evidence. See the module comment.
      if (citations.length === 0) {
        steps.push({ type: "model_error", error: "answered without calling any tool" });
        return {
          answer: REFUSAL_NO_TOOL,
          citations: [],
          refused: true,
          refusal_reason: "answered without retrieving data",
          model,
          prompt_version: PROMPT_VERSION,
          steps,
          iterations,
        };
      }
      return {
        answer: lastText.trim() || REFUSAL_NO_TOOL,
        citations,
        refused: false,
        model,
        prompt_version: PROMPT_VERSION,
        steps,
        iterations,
        usage,
      };
    }

    // Record the assistant turn that requested the tools.
    //
    // This must carry the tool calls, not the assistant's prose. The first version pushed
    // `lastText` (usually an empty string), which left the following `tool` messages
    // referencing nothing: the model saw a pile of unexplained JSON and could not tell which
    // result answered which request. The symptom was an agent that called the right tool,
    // received the right data, and then refused to answer — which reads as a model failure
    // and is actually a message-format bug.
    messages.push({
      role: "assistant",
      content: JSON.stringify({
        response: lastText || null,
        tool_calls: calls.map((c) => ({ name: c.name, arguments: c.arguments })),
      }),
    });

    for (const call of calls) {
      const started = Date.now();
      const validated = validateCall(call.name, call.arguments);

      if (!validated.ok) {
        steps.push({ type: "rejected_call", name: call.name, error: validated.error });
        messages.push({
          role: "tool",
          name: call.name,
          content: wrapToolResult(call.name, { ok: false, error: validated.error }),
        });
        continue;
      }

      const callKey = `${validated.name}:${JSON.stringify(validated.args)}`;
      const replayed = completedCalls.get(callKey);
      if (replayed !== undefined) {
        steps.push({ type: "rejected_call", name: validated.name, error: "duplicate call replayed" });
        messages.push({ role: "tool", name: validated.name, content: wrapToolResult(validated.name, JSON.parse(replayed)) });
        continue;
      }

      steps.push({
        type: "tool_call",
        name: validated.name,
        arguments: validated.args,
      });

      const result = executeTool(validated.name as ToolName, validated.args);
      steps.push({
        type: "tool_result",
        name: validated.name,
        ok: result.ok,
        error: result.ok ? undefined : result.error,
        ms: Date.now() - started,
      });

      for (const citation of result.citations) {
        const key = `${citation.dataset}|${citation.range ?? ""}|${citation.note ?? ""}`;
        if (!seenCitations.has(key)) {
          seenCitations.add(key);
          citations.push(citation);
        }
      }

      const payloadJson = JSON.stringify(result.ok ? result.data : { error: result.error });
      completedCalls.set(callKey, payloadJson);
      messages.push({ role: "tool", name: validated.name, content: wrapToolResult(validated.name, JSON.parse(payloadJson)) });
    }
  }

  // Hit the iteration cap. Anything already retrieved is still usable, so answer with what
  // we have rather than returning nothing — but say plainly that the search was cut short.
  if (citations.length > 0) {
    return {
      answer:
        (lastText.trim() ? `${lastText.trim()}\n\n` : "") +
        "I stopped after the maximum number of data lookups for one question, so this answer " +
        "may be incomplete.",
      citations,
      refused: false,
      model,
      prompt_version: PROMPT_VERSION,
      steps,
      iterations,
      usage,
    };
  }

  return {
    answer:
      "I couldn't find that in the data within the number of lookups allowed for one " +
      "question. Try asking about a specific category, state or period.",
    citations: [],
    refused: true,
    refusal_reason: "tool budget exhausted without a usable result",
    model,
    prompt_version: PROMPT_VERSION,
    steps,
    iterations,
    usage,
  };
}
