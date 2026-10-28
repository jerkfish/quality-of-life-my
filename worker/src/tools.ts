/**
 * Typed tools over the prepared data.
 *
 * Design rules, and why:
 *
 * 1.  **One source of truth for each schema.** Parameters are declared once in zod and the
 *     JSON Schema handed to the model is *derived* from it. Hand-writing both is how they
 *     silently drift, and a drifted schema means the model calls a tool with an argument the
 *     executor does not know about.
 *
 * 2.  **Arguments are validated before execution, always.** The model is asked to produce
 *     structured arguments; whether it complies is not something to assume.
 *
 * 3.  **Invalid arguments are repaired where a repair is unambiguous, rejected otherwise.**
 *     "Selangor" for "Selangor" is a repair. "Mars" is a rejection that tells the model what
 *     the valid values are, so it can try again rather than guessing.
 *
 * 4.  **Every successful result carries its own citations.** A number the agent states must be
 *     traceable to a dataset and a date range, and the only reliable way to guarantee that is
 *     to make the citation part of the result rather than something the model is trusted to
 *     remember.
 */

import { z } from "zod";
import {
  BASE_YEAR,
  LATEST_MONTH,
  bundle,
  citationFor,
  coverage,
  cumulative,
  divisionLabel,
  findItems,
  groupLabel,
  periodIndex,
  stateInflation,
  yoy,
  type Citation,
} from "./data";

// --------------------------------------------------------------------- schemas

const divisionEnum = z
  .enum(["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "13", "overall"])
  .describe("CPI division code. 'overall' is headline inflation.");

const yearField = z
  .number()
  .int()
  .min(1970)
  .max(2100)
  .describe("Calendar year, e.g. 2010.");

export const toolSchemas = {
  get_coverage: z
    .object({})
    .describe(
      "Report exactly what data is available, which years and months it covers, and when each " +
        "source was last updated. Call this first whenever a question mentions a period or " +
        "place you are not certain is covered.",
    ),

  get_cpi_series: z
    .object({
      code: divisionEnum,
      state: z
        .string()
        .optional()
        .describe(
          "Optional Malaysian state or federal territory, e.g. 'Selangor'. Omit for the " +
            "national series. State-level history begins in 2010.",
        ),
      from: z.string().optional().describe("Start period as YYYY-MM. Defaults to the series start."),
      to: z.string().optional().describe("End period as YYYY-MM. Defaults to the latest month."),
      mode: z
        .enum(["index", "yoy", "cumulative"])
        .optional()
        .describe(
          "index = CPI level (2010 = 100); yoy = percent change from a year earlier; " +
            "cumulative = percent change since 2010. Defaults to yoy.",
        ),
    })
    .describe("Consumer price index or inflation for one category, nationally or in one state."),

  get_income_series: z
    .object({
      measure: z
        .enum(["wage", "household_income"])
        .describe(
          "wage = median monthly wage of employees who are Malaysian citizens; " +
            "household_income = median monthly household income from the HIES survey.",
        ),
      state: z
        .string()
        .optional()
        .describe("Optional state; omit for the national series."),
      real: z
        .boolean()
        .optional()
        .describe("True for CPI-deflated (2010 ringgit), false for nominal. Defaults to real."),
    })
    .describe(
      "National or state income and wage levels over time, in ringgit, nominal or " +
        "CPI-deflated. Use this for questions about how much people earn or how it has grown. " +
        "These are different populations: wages cover employees, household income covers " +
        "households including the self-employed.",
    ),

  get_spending_group: z
    .object({
      group: z
        .enum([
          "001", "011", "012", "021", "023", "031", "032", "041", "043", "044", "045",
          "051", "052", "053", "054", "055", "061", "062", "063", "071", "072", "073",
          "081", "082", "083", "091", "092", "093", "094", "095", "101", "102", "103",
          "104", "105", "111", "112", "121", "122", "123", "124", "125", "126", "127",
          "131", "132", "133",
        ])
        .describe(
          "3-digit COICOP group. Notable ones: 001 = food away from home (eating out), " +
            "011 = food at home (groceries), 041 = actual rentals for housing, " +
            "072 = fuels and lubricants for transport.",
        ),
      query: z
        .string()
        .optional()
        .describe(
          "Instead of a code, describe what you want in words, e.g. 'eating out' or " +
            "'rent'. The closest matching groups are returned.",
        ),
      mode: z
        .enum(["cumulative", "yoy"])
        .optional()
        .describe("cumulative = percent change since 2010 (default); yoy = latest 12 months."),
    })
    .describe(
      "Inflation for a fine-grained spending category. This is the only way to separate " +
        "eating out from buying groceries, which have moved very differently. Prefer this " +
        "over get_cpi_series when the question is about a specific kind of spending.",
    ),

  compare_states: z
    .object({
      metric: z
        .enum(["inflation", "real_wage_growth", "real_income_growth", "nominal_wage_growth"])
        .describe(
          "inflation = cumulative CPI change; real_wage_growth = CPI-deflated wage change " +
            "(employees, citizens); real_income_growth = CPI-deflated household income change.",
        ),
    })
    .describe(
      "Rank all 16 states and federal territories by one metric. Use this for any question " +
        "about which state did best or worst, or where the gap is widest.",
    ),

  personal_inflation: z
    .object({
      basket: z
        .record(z.string(), z.number().nonnegative())
        .describe(
          "Map of CPI division code to monthly spending in ringgit, e.g. {\"01\": 900, " +
            "\"07\": 500}. Weights are normalised, so only proportions matter.",
        ),
      period: z
        .enum(["latest_12_months", "since_2010"])
        .optional()
        .describe("Which window to compare over. Defaults to the latest 12 months."),
    })
    .describe(
      "Compute a personalised inflation rate from a household's own spending split, and " +
        "compare it with the headline rate. Use when someone describes their own spending.",
    ),

  get_forecast: z
    .object({
      series_id: z
        .string()
        .optional()
        .describe(
          "Which series to forecast, e.g. 'cpi_headline:overall' or 'cpi_headline:01'. " +
            "Omit for the headline. Call get_coverage to see the available ids.",
        ),
    })
    .describe(
      "The 12-month CPI projection, with its 80% interval, backtest accuracy, and whether the " +
        "model actually beat a naive baseline. Report the verdict honestly.",
    ),

  get_anomalies: z
    .object({})
    .describe(
      "Months whose inflation departed unusually from trend, and the inflation regimes " +
        "detected in the series. Statistical flags, not explanations.",
    ),

  get_item_price: z
    .object({
      query: z.string().min(2).describe("Item name to search for, e.g. 'chicken' or 'rice'."),
      state: z
        .string()
        .optional()
        .describe("Optional state to report the price in. Omit to compare across all states."),
    })
    .describe(
      "Observed retail prices for a specific item, from PriceCatcher. This is a price " +
        "surveillance dataset and is explicitly NOT an inflation measure.",
    ),
} as const;

export type ToolName = keyof typeof toolSchemas;

// ------------------------------------------------------- zod -> JSON Schema

/**
 * A minimal zod-to-JSON-Schema converter, covering only the constructs used above.
 *
 * Deliberately not a dependency. `zod-to-json-schema` is a fine package, but this project
 * uses five node types, and a hand-rolled converter that errors loudly on anything it does
 * not understand is safer than a general one that quietly produces something the model
 * cannot use. `describeSchema` below is what catches the unsupported case.
 */
export function zodToJsonSchema(schema: z.ZodTypeAny): Record<string, unknown> {
  if (schema instanceof z.ZodObject) {
    const shape = schema.shape as Record<string, z.ZodTypeAny>;
    const properties: Record<string, unknown> = {};
    const required: string[] = [];
    for (const [key, value] of Object.entries(shape)) {
      properties[key] = zodToJsonSchema(value);
      if (!value.isOptional()) required.push(key);
    }
    return {
      type: "object",
      properties,
      required,
      additionalProperties: false,
      ...(schema.description ? { description: schema.description } : {}),
    };
  }
  if (schema instanceof z.ZodOptional) {
    // Carry the *wrapper's* description through, not just the inner type's. `z.string()
    // .optional().describe("...")` puts the description on the ZodOptional, so a plain
    // unwrap silently drops the field documentation — and the field docs are how the model
    // knows that `state` expects "Selangor" and not "Malaysia".
    const inner = zodToJsonSchema(schema.unwrap()) as Record<string, unknown>;
    const wrapperDescription = schema.description;
    if (wrapperDescription) inner.description = wrapperDescription;
    return inner;
  }
  if (schema instanceof z.ZodRecord) {
    return { type: "object", additionalProperties: zodToJsonSchema(schema.valueSchema) };
  }
  if (schema instanceof z.ZodString) {
    return { type: "string", ...(schema.description ? { description: schema.description } : {}) };
  }
  if (schema instanceof z.ZodNumber) {
    const out: Record<string, unknown> = { type: "number" };
    if (schema.description) out.description = schema.description;
    return out;
  }
  if (schema instanceof z.ZodEnum) {
    return {
      type: "string",
      enum: [...schema.options],
      ...(schema.description ? { description: schema.description } : {}),
    };
  }
  return { type: "string" };
}

/** The tool list in the shape Workers AI expects. */
export function toolSpecs() {
  return (Object.keys(toolSchemas) as ToolName[]).map((name) => ({
    name,
    description: (toolSchemas[name] as z.ZodTypeAny).description ?? name,
    parameters: zodToJsonSchema(toolSchemas[name] as z.ZodTypeAny),
  }));
}

// --------------------------------------------------------------- execution

export interface ToolResult {
  ok: boolean;
  data?: unknown;
  error?: string;
  citations: Citation[];
}

const STATE_ALIASES: Record<string, string> = {
  penang: "Pulau Pinang",
  "pulau pinang": "Pulau Pinang",
  kl: "W.P. Kuala Lumpur",
  "kuala lumpur": "W.P. Kuala Lumpur",
  kualalumpur: "W.P. Kuala Lumpur",
  labuan: "W.P. Labuan",
  putrajaya: "W.P. Putrajaya",
  "negeri sembilan": "Negeri Sembilan",
  n9: "Negeri Sembilan",
  johor: "Johor",
  kedah: "Kedah",
  kelantan: "Kelantan",
  melaka: "Melaka",
  malacca: "Melaka",
  pahang: "Pahang",
  perak: "Perak",
  perlis: "Perlis",
  sabah: "Sabah",
  sarawak: "Sarawak",
  selangor: "Selangor",
  terengganu: "Terengganu",
};

/**
 * Resolve a state name the model produced to one DOSM actually publishes.
 *
 * Returns the canonical name, or null. Note it does *not* fuzzy-match "Selangor" to
 * "Sarawak" — an unresolved state is a rejection the model can recover from, whereas a wrong
 * state silently answers a different question.
 */
export function resolveState(input: string): string | null {
  const key = input.trim().toLowerCase().replace(/^w\.p\.\s*/, "");
  if (STATE_ALIASES[key]) return STATE_ALIASES[key]!;
  const direct = bundle.states.find((s) => s.toLowerCase() === input.trim().toLowerCase());
  return direct ?? null;
}

function normalizeDivision(input: string): string {
  const trimmed = input.trim();
  if (trimmed === "overall") return "overall";
  const two = trimmed.padStart(2, "0");
  return /^\d{2}$/.test(two) ? two : trimmed;
}

/**
 * Resolve a requested window to array indices.
 *
 * `findIndex` returns -1 when nothing matches, and the two ends need opposite treatment:
 * a `from` past the end of the series means an empty window (clamp to the end), while a `to`
 * past the end means "through the end" (clamp to the end). Calling `Math.max(0, -1)` on the
 * start — the obvious thing, and what this did first — silently turns "2099-01 onwards" into
 * "the entire series", which is a wrong answer that looks like a right one.
 */
function resolveWindow(from: string | undefined, to: string | undefined, periods: string[]) {
  let startIdx = 0;
  if (from) {
    const i = periods.findIndex((p) => p >= from);
    startIdx = i === -1 ? periods.length : i;
  }
  let endIdx = periods.length;
  if (to) {
    const i = periods.findIndex((p) => p > to);
    endIdx = i === -1 ? periods.length : i;
  }
  return { startIdx, endIdx, periods };
}

/**
 * The mean index level across the base year's twelve months.
 *
 * Computed once per call rather than being a constant, because the CPI is published as an
 * index rather than a rebased series and a partial base year would silently shift every
 * cumulative figure.
 */
function baseIndexFor(code: string): number | null {
  const series = bundle.cpi.national[code];
  if (!series) return null;
  const values = bundle.periods.national
    .map((p, i) => ({ p, v: series[i] }))
    .filter(({ p, v }) => p.startsWith(String(BASE_YEAR)) && typeof v === "number")
    .map(({ v }) => v as number);
  if (values.length === 0) return null;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

const seriesWindow = (from?: string, to?: string) =>
  resolveWindow(from, to, bundle.periods.national);

// ------------------------------------------------------------ tool bodies

function getCoverage(): ToolResult {
  const c = coverage();
  return {
    ok: true,
    data: {
      ...c,
      note:
        "State-level CPI begins 2010. Household income is survey data and exists only for the " +
        "years listed, with gaps. Wage data covers employees who are Malaysian citizens, not " +
        "the self-employed or informal sector.",
      available_forecast_series: (bundle.forecast?.series ?? []).map((s) => ({
        series_id: s.series_id,
        label: s.label,
        beats_baseline: s.beats_baseline,
      })),
      available_divisions: Object.entries(bundle.divisions).map(([code, d]) => ({
        code,
        name: d.en,
      })),
    },
    citations: [citationFor("cpi_headline", undefined, "Coverage report")],
  };
}

function getCpiSeries(args: z.infer<typeof toolSchemas.get_cpi_series>): ToolResult {
  const code = normalizeDivision(args.code);
  const mode = args.mode ?? "yoy";

  if (args.state) {
    const state = resolveState(args.state);
    if (!state) {
      return {
        ok: false,
        error: `Unknown state ${JSON.stringify(args.state)}. Valid values: ${bundle.states.join(", ")}.`,
        citations: [],
      };
    }

    if (mode === "cumulative" || mode === "index") {
      // State history is held as per-state overall monthly arrays; divisions are annual
      // snapshots. Be explicit about which is available rather than approximating.
      if (code !== "overall") {
        const snapshot = bundle.cpi.state_by_division[state]?.[code];
        if (!snapshot) {
          return { ok: false, error: `No ${code} data for ${state}.`, citations: [] };
        }
        return {
          ok: true,
          data: {
            scope: "state",
            state,
            code,
            label: divisionLabel(code),
            mode: "annual_index",
            years: snapshot,
            note:
              "State-level figures are annual means of the monthly index, per state and " +
              "division. Monthly state x division arrays are not bundled in the Worker.",
          },
          citations: [citationFor("cpi_state", `${BASE_YEAR}-present`, `${state}, ${divisionLabel(code)}`)],
        };
      }
      const values = bundle.cpi.state_overall[state] ?? [];
      const snapshot = bundle.cpi.state_by_division[state]?.overall ?? {};
      return {
        ok: true,
        data: {
          scope: "state",
          state,
          code: "overall",
          label: "All items (headline)",
          mode: "annual_index",
          years: snapshot,
          first_year: Object.keys(snapshot)[0],
          last_year: Object.keys(snapshot).at(-1),
          months_available: values.length,
        },
        citations: [citationFor("cpi_state", `${BASE_YEAR}-present`, `${state}, headline CPI`)],
      };
    }

    const from = args.from;
    const to = args.to;
    const snapshot = bundle.cpi.state_by_division[state]?.overall ?? {};
    const years = Object.keys(snapshot).map(Number).sort((a, b) => a - b);
    const fromYear = from ? Number(from.slice(0, 4)) : years[0];
    const toYear = to ? Number(to.slice(0, 4)) : years.at(-1);
    if (!fromYear || !toYear) {
      return { ok: false, error: "State inflation range is not available.", citations: [] };
    }
    const value = stateInflation(state, fromYear, toYear);
    if (value === null) {
      return {
        ok: false,
        error: `No state inflation for ${state} between ${fromYear} and ${toYear}. Coverage is ${years[0]}-${years.at(-1)}.`,
        citations: [],
      };
    }
    return {
      ok: true,
      data: {
        scope: "state",
        state,
        code: "overall",
        mode: "cumulative",
        from_year: fromYear,
        to_year: toYear,
        cumulative_inflation_pct: Number(value.toFixed(2)),
      },
      citations: [citationFor("cpi_state", `${fromYear}-${toYear}`, `${state} headline CPI`)],
    };
  }

  const { startIdx, endIdx, periods } = seriesWindow(args.from, args.to);
  if (endIdx - startIdx < 1) {
    return { ok: false, error: "The requested period contains no data.", citations: [] };
  }

  const out: { period: string; value: number }[] = [];
  for (let i = startIdx; i < endIdx; i++) {
    if (mode === "index") {
      const series = bundle.cpi.national[code];
      const v = series?.[i];
      if (typeof v === "number") out.push({ period: periods[i]!, value: Number(v.toFixed(3)) });
    } else if (mode === "yoy") {
      const v = yoy(code, i);
      if (v !== null) out.push({ period: periods[i]!, value: Number(v.toFixed(2)) });
    } else {
      // Cumulative means "since the base year", as the schema says. The first version rebased
      // to the start of the *window*, which defaults to 1980 — so a question about "since
      // 2010" silently got a figure measured from 1980, and the tool contradicted its own
      // description. The base index is computed once, over the base year's full twelve months.
      const series = bundle.cpi.national[code];
      const v = series?.[i];
      const base = baseIndexFor(code);
      if (typeof base === "number" && typeof v === "number" && base !== 0) {
        out.push({ period: periods[i]!, value: Number(((v / base - 1) * 100).toFixed(2)) });
      }
    }
  }

  if (out.length === 0) {
    return {
      ok: false,
      error: `No ${mode} data for division ${code} in that window. The national series runs ${periods[0]}-${periods.at(-1)}.`,
      citations: [],
    };
  }

  // Long series are truncated for the model's benefit; the summary keeps the endpoints so
  // nothing load-bearing is lost. Truncation is stated rather than silent.
  const maxPoints = 60;
  const step = Math.max(1, Math.ceil(out.length / maxPoints));
  const sampled = out.filter((_, i) => i % step === 0 || i === out.length - 1);

  return {
    ok: true,
    data: {
      scope: "national",
      code,
      label: divisionLabel(code),
      mode,
      ...(mode === "cumulative"
        ? { basis: `latest value vs the ${BASE_YEAR} monthly mean` }
        : {}),
      first: out[0],
      last: out.at(-1),
      latest_value: out.at(-1)?.value ?? null,
      n_points: out.length,
      downsampled_to: sampled.length,
      step,
      series: sampled,
    },
    citations: [
      citationFor("cpi_headline", `${out[0]!.period} to ${out.at(-1)!.period}`, divisionLabel(code)),
    ],
  };
}

function getIncomeSeries(args: z.infer<typeof toolSchemas.get_income_series>): ToolResult {
  const real = args.real ?? true;
  const wantWage = args.measure === "wage";
  const basis = real ? `real, ${BASE_YEAR} ringgit` : "nominal ringgit";
  const unit = wantWage ? "RM per month, per employee" : "RM per month, per household";

  if (args.state) {
    const state = resolveState(args.state);
    if (!state) {
      return {
        ok: false,
        error: `Unknown state ${JSON.stringify(args.state)}. Valid values: ${bundle.states.join(", ")}.`,
        citations: [],
      };
    }
    // Branch rather than unifying: the two sources genuinely have different shapes, and
    // papering over that with a union type is how a reader ends up with the wrong column.
    const points: { year: number; value: number | null }[] = wantWage
      ? bundle.wages.by_state
          .filter((r) => r.state === state)
          .sort((a, b) => a.year - b.year)
          .map((r) => ({ year: r.year, value: real ? r.real_median : r.wages_median }))
      : bundle.income.by_state
          .filter((r) => r.state === state)
          .sort((a, b) => a.year - b.year)
          .map((r) => ({ year: r.year, value: r.income_median }));

    if (points.length === 0) {
      return {
        ok: false,
        error: wantWage
          ? `No wage records for ${state}.`
          : `No household income records for ${state}. HIES is survey data covering ${bundle.income.survey_years.join(", ")}.`,
        citations: [],
      };
    }
    const first = points[0]!;
    const last = points.at(-1)!;
    return {
      ok: true,
      data: {
        measure: args.measure,
        state,
        basis,
        unit,
        first_year: first.year,
        last_year: last.year,
        first_value: first.value,
        last_value: last.value,
        series: points,
      },
      citations: [citationFor(wantWage ? "wages_state" : "hh_income_state", `${first.year}-${last.year}`, args.measure)],
    };
  }

  if (wantWage) {
    const rows = bundle.wages.national.slice().sort((a, b) => a.year - b.year);
    if (rows.length === 0) return { ok: false, error: "No national wage series in this build.", citations: [] };
    const first = rows[0]!;
    const last = rows.at(-1)!;
    const startValue = real ? first.real_median : first.wages_median;
    const endValue = real ? last.real_median : last.wages_median;
    return {
      ok: true,
      data: {
        measure: "wage",
        scope: "national",
        basis,
        unit,
        population: "employees who are Malaysian citizens",
        note: "Excludes the self-employed and the informal sector.",
        first_year: first.year,
        last_year: last.year,
        first_value: startValue,
        last_value: endValue,
        growth_pct:
          startValue && endValue ? Number((((endValue / startValue) - 1) * 100).toFixed(2)) : null,
        series: rows.map((r) => ({ year: r.year, value: real ? r.real_median : r.wages_median })),
      },
      citations: [citationFor("wages_state", `${first.year}-${last.year}`, "National median wage")],
    };
  }

  const rows = bundle.income.national.slice().sort((a, b) => a.year - b.year);
  if (rows.length === 0) return { ok: false, error: "No national household income series in this build.", citations: [] };
  const first = rows[0]!;
  const last = rows.at(-1)!;
  const startValue = first.income_median;
  const endValue = last.income_median;
  // CPI-deflated properly: ratio of growth factors, not a subtraction -- and only when real
  // terms were actually asked for. The first version applied the deflator unconditionally, so
  // `real: false` returned the real figure and the two were indistinguishable.
  const deflator = real && first.cpi && last.cpi ? last.cpi / first.cpi : 1;
  return {
    ok: true,
    data: {
      measure: "household_income",
      scope: "national",
      basis,
      unit,
      population: "all Malaysian households (HIES survey)",
      note: `Survey years only, with gaps: ${bundle.income.survey_years.join(", ")}.`,
      first_year: first.year,
      last_year: last.year,
      first_value: startValue,
      last_value: endValue,
      growth_pct:
        startValue && endValue && deflator
          ? Number((((endValue / startValue) / deflator - 1) * 100).toFixed(2))
          : null,
      series: rows.map((r) => ({ year: r.year, value: r.income_median })),
    },
    citations: [citationFor("hh_income_state", `${first.year}-${last.year}`, "National median household income")],
  };
}

/**
 * Plain-language names for the groups people actually ask about.
 *
 * Naive substring matching is not good enough here, and the failure is instructive: "eating
 * out" scored a hit against **"Outpatient care services"** because "outpatient" contains
 * "out", and "food at home" tied with "Food away from home" because both contain the words.
 * A tie broken by array order silently answers a different question.
 */
const GROUP_ALIASES: Record<string, string> = {
  "eating out": "001",
  restaurant: "001",
  restaurants: "001",
  dining: "001",
  takeaway: "001",
  groceries: "011",
  grocery: "011",
  supermarket: "011",
  "food at home": "011",
  "cooking at home": "011",
  rent: "041",
  rental: "041",
  housing: "041",
  petrol: "072",
  fuel: "072",
  gasoline: "072",
  electricity: "045",
  water: "044",
  tobacco: "023",
  alcohol: "021",
  clothing: "031",
  shoes: "032",
  healthcare: "061",
  "medical care": "061",
  education: "101",
  transport: "071",
  "public transport": "073",
  communication: "081",
  internet: "081",
};

function matchGroups(query: string): { c: string; score: number }[] {
  const lowered = query.toLowerCase().trim();

  // An exact alias wins outright — it is a deliberate mapping, not a guess.
  for (const [phrase, code] of Object.entries(GROUP_ALIASES)) {
    if (lowered === phrase || lowered.includes(phrase)) {
      return [{ c: code, score: 100 }];
    }
  }

  const terms = lowered.split(/[^a-z]+/).filter((t) => t.length > 2);
  return Object.entries(bundle.groups.names)
    .map(([c, name]) => {
      const haystack = name.en.toLowerCase();
      // Whole-word matching only. "out" must not match "outpatient".
      const hits = terms.filter((t) => new RegExp(`\b${t}\b`).test(haystack)).length;
      return { c, score: hits };
    })
    .filter((entry) => entry.score > 0 && entry.score === terms.length)
    // Among equally-matching names, prefer the shortest: "Food at home" over
    // "Food away from home" when both match every term.
    .sort((a, b) => {
      if (b.score !== a.score) return b.score - a.score;
      const an = bundle.groups.names[a.c]?.en.length ?? 999;
      const bn = bundle.groups.names[b.c]?.en.length ?? 999;
      return an - bn;
    });
}

function getSpendingGroup(args: z.infer<typeof toolSchemas.get_spending_group>): ToolResult {
  const mode = args.mode ?? "cumulative";
  const periods = bundle.groups.periods;
  let code = args.group;

  if (args.query && !args.group) {
    const scored = matchGroups(args.query);
    if (scored.length === 0) {
      return {
        ok: false,
        error: `No spending group matches ${JSON.stringify(args.query)}. Groups include: food away from home, food at home, actual rentals for housing, fuels and lubricants for transport.`,
        citations: [],
      };
    }
    code = scored[0]!.c as z.infer<typeof toolSchemas.get_spending_group>["group"];
  }

  const series = bundle.groups.series[code];
  if (!series) {
    return { ok: false, error: `No series for group ${code}.`, citations: [] };
  }

  const annualMeans = (year: string) => {
    const values = periods
      .map((p, i) => ({ p, v: series[i] }))
      .filter(({ p, v }) => p.startsWith(year) && typeof v === "number")
      .map(({ v }) => v as number);
    return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
  };

  if (mode === "yoy") {
    const n = periods.length;
    const now = series[n - 1];
    const before = series[n - 13];
    if (typeof now !== "number" || typeof before !== "number" || before === 0) {
      return { ok: false, error: `No year-on-year data for group ${code}.`, citations: [] };
    }
    return {
      ok: true,
      data: {
        group: code,
        label: groupLabel(code),
        mode: "yoy",
        period: periods[n - 1],
        value_pct: Number(((now / before - 1) * 100).toFixed(2)),
      },
      citations: [citationFor("cpi_3d", periods[n - 1] ?? undefined, groupLabel(code))],
    };
  }

  // Same definition as get_cpi_series: the latest observed month against the base year's
  // monthly mean. Two tools answering "since 2010" differently is worse than either choice.
  const base = annualMeans(String(BASE_YEAR));
  const end = series[series.length - 1];
  const lastPeriod = periods.at(-1) ?? "";
  if (base === null || typeof end !== "number" || base === 0) {
    return { ok: false, error: `Cannot compute cumulative change for group ${code}.`, citations: [] };
  }
  return {
    ok: true,
    data: {
      group: code,
      label: groupLabel(code),
      mode: "cumulative",
      basis: `latest month (${lastPeriod}) vs the ${BASE_YEAR} monthly mean`,
      value_pct: Number(((end / base - 1) * 100).toFixed(2)),
    },
    citations: [citationFor("cpi_3d", `${BASE_YEAR}-${lastPeriod}`, groupLabel(code))],
  };
}

function compareStates(args: z.infer<typeof toolSchemas.compare_states>): ToolResult {
  const metric = args.metric;
  if (metric === "real_income_growth") {
    const rows = bundle.regional.income_ranking
      .slice()
      .sort((a, b) => b.real_income_growth_pct - a.real_income_growth_pct);
    return {
      ok: true,
      data: {
        metric,
        note:
          "Household income, CPI-deflated. Survey data, so the endpoints are survey years " +
          "rather than consecutive years.",
        period: bundle.regional.periods.income,
        ranked: rows.map((r, i) => ({
          rank: i + 1,
          state: r.state,
          real_growth_pct: r.real_income_growth_pct,
          nominal_growth_pct: r.nominal_income_growth_pct,
          inflation_pct: r.cumulative_inflation_pct,
        })),
      },
      citations: [citationFor("hh_income_state", undefined, "Household median income, real terms")],
    };
  }

  const rows = bundle.regional.wages_ranking
    .slice()
    .sort((a, b) => b.real_wage_growth_pct - a.real_wage_growth_pct);
  return {
    ok: true,
    data: {
      metric,
      note: "Employees who are Malaysian citizens. Excludes the self-employed and informal sector.",
      period: bundle.regional.periods.wages,
      ranked: rows.map((r, i) => ({
        rank: i + 1,
        state: r.state,
        real_growth_pct: r.real_wage_growth_pct,
        nominal_growth_pct: r.nominal_wage_growth_pct,
        inflation_pct: r.cumulative_inflation_pct,
      })),
    },
    citations: [
      citationFor("wages_state", undefined, "Median monthly wage, real terms"),
      citationFor("cpi_state", undefined, "State CPI used as the deflator"),
    ],
  };
}

function personalInflation(args: z.infer<typeof toolSchemas.personal_inflation>): ToolResult {
  const period = args.period ?? "latest_12_months";
  const latestIdx = bundle.periods.national.length - 1;

  const rates: Record<string, number> = {};
  for (const code of Object.keys(bundle.divisions)) {
    const v = period === "latest_12_months" ? yoy(code, latestIdx) : cumulative(code);
    if (v !== null) rates[code] = v;
  }

  const entries = Object.entries(args.basket).map(([raw, weight]) => [
    normalizeDivision(raw),
    weight,
  ]) as [string, number][];
  const positive = entries.filter(([, w]) => w > 0);

  if (positive.length === 0) {
    return { ok: false, error: "The basket contains no positive amounts.", citations: [] };
  }

  const missing = positive.filter(([code]) => !(code in rates)).map(([code]) => code);
  const priced = positive.filter(([code]) => code in rates);
  const pricedTotal = priced.reduce((a, [, w]) => a + w, 0);
  const total = positive.reduce((a, [, w]) => a + w, 0);

  if (pricedTotal === 0) {
    return {
      ok: false,
      error: `None of the basket's categories could be priced. Unknown codes: ${missing.join(", ")}. Valid codes: ${Object.keys(bundle.divisions).join(", ")}.`,
      citations: [],
    };
  }

  const contributions = priced
    .map(([code, weight]) => {
      const share = weight / pricedTotal;
      return {
        code,
        name: divisionLabel(code),
        share: Number(share.toFixed(4)),
        rate_pct: Number(rates[code]!.toFixed(2)),
        contribution_pp: Number((share * rates[code]!).toFixed(3)),
      };
    })
    .sort((a, b) => b.contribution_pp - a.contribution_pp);

  const personal = contributions.reduce((a, c) => a + c.contribution_pp, 0);
  const headline = rates.overall ?? null;

  return {
    ok: true,
    data: {
      period,
      personal_inflation_pct: Number(personal.toFixed(2)),
      headline_inflation_pct: headline === null ? null : Number(headline.toFixed(2)),
      gap_pp: headline === null ? null : Number((personal - headline).toFixed(2)),
      contributions,
      unpriced_categories: missing,
      priced_share_of_basket: Number((pricedTotal / total).toFixed(3)),
      note:
        missing.length > 0
          ? "Categories with no price data were excluded from the normalisation, not counted as zero."
          : undefined,
    },
    citations: [
      citationFor("cpi_headline", period === "since_2010" ? `${BASE_YEAR}-${LATEST_MONTH}` : "latest 12 months", "Category CPI used for weighting"),
    ],
  };
}

function getForecast(args: z.infer<typeof toolSchemas.get_forecast>): ToolResult {
  const forecast = bundle.forecast;
  if (!forecast) {
    return { ok: false, error: "No forecast is available in this build.", citations: [] };
  }
  const id = args.series_id ?? "cpi_headline:overall";
  const series = forecast.series.find((s) => s.series_id === id);
  if (!series) {
    return {
      ok: false,
      error: `Unknown series ${JSON.stringify(id)}. Available: ${forecast.series.map((s) => s.series_id).join(", ")}.`,
      citations: [],
    };
  }
  const best = series.scores.slice().sort((a, b) => a.mae_pp - b.mae_pp)[0];
  const baseline = series.scores.find((s) => s.model === forecast.meta.baseline);

  return {
    ok: true,
    data: {
      series_id: series.series_id,
      label: series.label,
      method: series.method,
      baseline: forecast.meta.baseline,
      horizon_months: series.horizon,
      last_observed_period: series.last_observed_period,
      beats_baseline: series.beats_baseline,
      verdict: series.verdict,
      accuracy: best
        ? { model: best.model, mae_pp: best.mae_pp, mape_pct: best.mape_pct, n_backtests: best.n_observations }
        : null,
      baseline_accuracy: baseline ? { mae_pp: baseline.mae_pp, mape_pct: baseline.mape_pct } : null,
      points: series.points.map((p) => ({
        period: p.period,
        implied_inflation_pct: p.implied_yoy,
        lo80: p.implied_yoy_lo80,
        hi80: p.implied_yoy_hi80,
      })),
    },
    citations: [
      citationFor("cpi_headline", `${series.last_observed_period} + ${series.horizon} months`, `Forecast, method ${series.method}`),
    ],
  };
}

function getAnomalies(): ToolResult {
  const a = bundle.anomalies;
  if (!a) return { ok: false, error: "No anomaly analysis is available in this build.", citations: [] };
  const worst = a.anomalies
    .slice()
    .sort((x, y) => Math.abs(y.z) - Math.abs(x.z))
    .slice(0, 12);
  return {
    ok: true,
    data: {
      threshold_z: a.threshold_z,
      n_flagged: a.anomalies.length,
      largest_deviations: worst,
      regimes: a.regimes,
      corroborating_context: a.context,
      note:
        "These are statistical flags from an STL decomposition, not explanations. Where other " +
        "series moved at the same time, that is coincidence in time and is not evidence of cause.",
    },
    citations: [citationFor("cpi_headline", "1980-present", "STL residual analysis")],
  };
}

function getItemPrice(args: z.infer<typeof toolSchemas.get_item_price>): ToolResult {
  const pc = bundle.pricecatcher;
  if (!pc) return { ok: false, error: "No item price data is available in this build.", citations: [] };

  const matches = findItems(args.query);
  if (matches.length === 0) {
    return {
      ok: false,
      error: `No item matching ${JSON.stringify(args.query)}. PriceCatcher covers around ${pc.n_items} grocery items; try a simpler word such as "chicken", "rice" or "oil".`,
      citations: [],
    };
  }

  if (args.state) {
    const state = resolveState(args.state);
    if (!state) {
      return { ok: false, error: `Unknown state ${JSON.stringify(args.state)}.`, citations: [] };
    }
    const priced = matches
      .map((m) => ({ item: m.item, unit: m.unit, price: m.prices[state] ?? null }))
      .filter((m) => typeof m.price === "number");
    if (priced.length === 0) {
      return {
        ok: false,
        error: `No price recorded for ${matches.map((m) => m.item).join(", ")} in ${state} for ${pc.month}.`,
        citations: [],
      };
    }
    return {
      ok: true,
      data: { month: pc.month, state, items: priced },
      citations: [citationFor("pricecatcher", pc.month ?? undefined, `PriceCatcher, ${state}`)],
    };
  }

  const items = matches.map((m) => {
    const prices = Object.entries(m.prices).filter(([, v]) => typeof v === "number") as [string, number][];
    prices.sort((a, b) => a[1] - b[1]);
    const values = prices.map(([, v]) => v);
    const median = values.length ? values[Math.floor(values.length / 2)]! : null;
    return {
      item: m.item,
      unit: m.unit,
      median_price_rm: median === null ? null : Number(median.toFixed(2)),
      cheapest_state: prices[0]?.[0] ?? null,
      cheapest_price_rm: prices[0]?.[1] ?? null,
      dearest_state: prices.at(-1)?.[0] ?? null,
      dearest_price_rm: prices.at(-1)?.[1] ?? null,
      n_states: values.length,
    };
  });

  return {
    ok: true,
    data: {
      month: pc.month,
      items,
      caveat:
        "PriceCatcher is a price-surveillance dataset. DOSM states it should not be used for " +
        "inflation analysis; use the CPI for that. Units differ per item, so prices are not " +
        "comparable across items.",
    },
    citations: [citationFor("pricecatcher", pc.month ?? undefined, "PriceCatcher item prices")],
  };
}

// ---------------------------------------------------------------- dispatch

export type ValidatedCall =
  | { ok: true; name: ToolName; args: Record<string, unknown> }
  | { ok: false; name: string; error: string };

/** Validate raw arguments from the model against the tool's schema. */
export function validateCall(name: string, rawArgs: unknown): ValidatedCall {
  if (!(name in toolSchemas)) {
    return {
      ok: false,
      name,
      error: `Unknown tool ${JSON.stringify(name)}. Available tools: ${Object.keys(toolSchemas).join(", ")}.`,
    };
  }
  const schema = toolSchemas[name as ToolName] as z.ZodTypeAny;
  const parsed = schema.safeParse(rawArgs ?? {});
  if (!parsed.success) {
    const issues = parsed.error.issues
      .map((i) => `${i.path.join(".") || "(root)"}: ${i.message}`)
      .join("; ");
    return { ok: false, name, error: `Invalid arguments for ${name}. ${issues}` };
  }
  return { ok: true, name: name as ToolName, args: parsed.data as Record<string, unknown> };
}

/** Execute a validated tool call. Never throws: a failure is a result the model can read. */
export function executeTool(name: ToolName, args: Record<string, unknown>): ToolResult {
  try {
    switch (name) {
      case "get_coverage":
        return getCoverage();
      case "get_cpi_series":
        return getCpiSeries(args as z.infer<typeof toolSchemas.get_cpi_series>);
      case "get_income_series":
        return getIncomeSeries(args as z.infer<typeof toolSchemas.get_income_series>);
      case "get_spending_group":
        return getSpendingGroup(args as z.infer<typeof toolSchemas.get_spending_group>);
      case "compare_states":
        return compareStates(args as z.infer<typeof toolSchemas.compare_states>);
      case "personal_inflation":
        return personalInflation(args as z.infer<typeof toolSchemas.personal_inflation>);
      case "get_forecast":
        return getForecast(args as z.infer<typeof toolSchemas.get_forecast>);
      case "get_anomalies":
        return getAnomalies();
      case "get_item_price":
        return getItemPrice(args as z.infer<typeof toolSchemas.get_item_price>);
      default:
        return { ok: false, error: `Tool ${name} is declared but not implemented.`, citations: [] };
    }
  } catch (error) {
    // A tool that throws must not take down the request. The model gets the failure text and
    // can decide whether to retry with different arguments or give up gracefully.
    return {
      ok: false,
      error: `Tool ${name} failed: ${error instanceof Error ? error.message : String(error)}`,
      citations: [],
    };
  }
}
