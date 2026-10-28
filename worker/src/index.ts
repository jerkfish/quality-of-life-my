/**
 * Worker entry point.
 *
 * Routes:
 *   GET  /health           liveness plus the data version it is holding
 *   GET  /api/coverage     what data exists, for the UI's "what can I ask" panel
 *   POST /api/ask          { question } -> { answer, citations, refused }
 *
 * Abuse protection is layered, because each layer alone is insufficient:
 *   1. input length cap  — bounds the cost of any single request
 *   2. per-IP rate limit — bounds the number of requests an address can make
 *   3. tool-call budget  — bounds how much work one question can cause
 *   4. origin allowlist  — bounds who can call it from a browser at all
 *
 * Layer 3 lives in the agent; layers 1, 2 and 4 are here.
 */

import { runAgent, type Env } from "./agent";
import { coverage } from "./data";
import { PROMPT_VERSION, SYSTEM_PROMPT } from "./prompt";

export type { Env };

interface AskBody {
  question?: unknown;
  model?: unknown;
}

/**
 * Per-isolate sliding-window limiter.
 *
 * Honest limitation: this counter lives in one isolate's memory, so a request routed to a
 * different isolate starts from zero, and an isolate recycling resets it. That makes it a
 * speed bump rather than a hard quota — it stops a script hammering the endpoint from one
 * place, which is the realistic threat for a portfolio project, and it does not stop a
 * distributed one.
 *
 * The durable versions are Cloudflare's Rate Limiting binding (platform-managed, per-colo) or
 * a D1 table keyed by IP. Both are a config change away; neither is worth the complexity for
 * an endpoint whose worst case is exhausting its own free tier.
 */
const hits = new Map<string, number[]>();

function rateLimited(key: string, perMinute: number, perDay: number): boolean {
  const now = Date.now();
  const windowStart = now - 86_400_000;
  const recent = (hits.get(key) ?? []).filter((t) => t > windowStart);
  if (recent.length >= perDay) return true;
  const lastMinute = recent.filter((t) => t > now - 60_000);
  if (lastMinute.length >= perMinute) return true;
  recent.push(now);
  hits.set(key, recent);
  // Opportunistic cleanup so the map cannot grow without bound in a long-lived isolate.
  if (hits.size > 5_000) {
    for (const [k, v] of hits) {
      if (v.every((t) => t <= windowStart)) hits.delete(k);
    }
  }
  return false;
}

function corsHeaders(origin: string | null, allow: string[]): Record<string, string> {
  const permitted = origin && (allow.includes("*") || allow.includes(origin));
  return {
    "Access-Control-Allow-Origin": permitted ? origin! : allow[0] ?? "",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
    "Access-Control-Allow-Headers": "content-type",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
  };
}

function json(body: unknown, status: number, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...headers },
  });
}

/** The client IP, which Cloudflare always sets on an edge request. */
function clientKey(request: Request): string {
  return (
    request.headers.get("CF-Connecting-IP") ??
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ??
    "unknown"
  );
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const origin = request.headers.get("Origin");
    // `ALLOWED_ORIGINS` is optional; without it we allow any origin so a local preview works.
    const allow = (env as unknown as { ALLOWED_ORIGINS?: string }).ALLOWED_ORIGINS?.split(",") ?? ["*"];
    const cors = corsHeaders(origin, allow);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }

    if (url.pathname === "/health") {
      const c = coverage();
      return json(
        {
          status: "ok",
          data_version: c.generatedAt,
          cpi_through: c.nationalTo,
          model: env.MODEL,
          prompt_version: PROMPT_VERSION,
        },
        200,
        cors,
      );
    }

    if (url.pathname === "/api/coverage" && request.method === "GET") {
      return json({ coverage: coverage(), system_prompt: SYSTEM_PROMPT }, 200, cors);
    }

    if (url.pathname === "/api/ask") {
      if (request.method !== "POST") {
        return json({ error: "Use POST." }, 405, cors);
      }

      const key = clientKey(request);
      if (rateLimited(key, Number(env.RATE_LIMIT_PER_MINUTE ?? 8), Number(env.RATE_LIMIT_PER_DAY ?? 120))) {
        return json(
          {
            error:
              "Too many questions from this address. Please wait a moment and try again — the " +
              "site runs on a free tier with a daily allowance.",
          },
          429,
          { ...cors, "retry-after": "60" },
        );
      }

      let body: AskBody;
      try {
        body = (await request.json()) as AskBody;
      } catch {
        return json({ error: "Request body must be JSON." }, 400, cors);
      }

      const question = typeof body.question === "string" ? body.question.trim() : "";
      const maxChars = Number(env.MAX_QUESTION_CHARS ?? 500);

      if (question.length === 0) {
        return json({ error: "Ask a question." }, 400, cors);
      }
      if (question.length > maxChars) {
        return json(
          { error: `Questions are limited to ${maxChars} characters. Yours was ${question.length}.` },
          413,
          cors,
        );
      }

      const started = Date.now();
      const result = await runAgent({
        env,
        question,
        // Validated against ALLOWED_MODELS inside runAgent; an unknown model falls back
        // to the default rather than being run.
        modelOverride: typeof body.model === "string" ? body.model : undefined,
      });

      // Structured log. Deliberately excludes the question text: this is a public endpoint and
      // the question is user-supplied content we have no reason to retain.
      console.log(
        JSON.stringify({
          event: "ask",
          model: result.model,
          prompt_version: result.prompt_version,
          refused: result.refused,
          refusal_reason: result.refusal_reason ?? null,
          iterations: result.iterations,
          citations: result.citations.length,
          tools: result.steps.filter((s) => s.type === "tool_call").map((s) => s.name),
          ms: Date.now() - started,
        }),
      );

      return json(
        {
          answer: result.answer,
          citations: result.citations,
          refused: result.refused,
          model: result.model,
          prompt_version: result.prompt_version,
          // The trace is returned so a reader can see exactly which lookups produced the
          // answer. It is the difference between "trust me" and "here is the working".
          trace: result.steps,
        },
        200,
        cors,
      );
    }

    return json({ error: "Not found.", routes: ["/health", "/api/coverage", "/api/ask"] }, 404, cors);
  },
};
