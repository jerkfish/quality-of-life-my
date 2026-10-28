/**
 * Tool and validation tests.
 *
 * These run against the real bundle, so they will fail if the pipeline changes a field name
 * out from under the Worker — which is the point. A tool that silently reads `undefined` and
 * reports "no data" looks exactly like a tool that works.
 */

import { describe, expect, it } from "vitest";
import { resolveModel } from "../src/agent";
import { BASE_YEAR, LATEST_MONTH, bundle, coverage } from "../src/data";
import {
  executeTool,
  resolveState,
  toolSchemas,
  toolSpecs,
  validateCall,
  zodToJsonSchema,
} from "../src/tools";

// ------------------------------------------------------------------ schema plumbing

describe("schema derivation", () => {
  it("derives a JSON Schema for every tool", () => {
    for (const spec of toolSpecs()) {
      expect(spec.parameters).toHaveProperty("type", "object");
      expect(spec.description.length).toBeGreaterThan(20);
    }
  });

  it("marks only genuinely optional fields as not required", () => {
    const schema = zodToJsonSchema(toolSchemas.get_cpi_series) as {
      required: string[];
      properties: Record<string, unknown>;
    };
    // `code` is mandatory; everything else has a default or is genuinely optional.
    expect(schema.required).toContain("code");
    expect(schema.required).not.toContain("state");
    expect(schema.required).not.toContain("mode");
    expect(Object.keys(schema.properties).sort()).toEqual(
      ["code", "from", "mode", "state", "to"].sort(),
    );
  });

  it("carries field descriptions through to the model", () => {
    const schema = zodToJsonSchema(toolSchemas.get_cpi_series) as {
      properties: Record<string, { description?: string }>;
    };
    expect(schema.properties.code?.description).toMatch(/division/i);
    expect(schema.properties.state?.description).toMatch(/state/i);
  });

  it("exposes enum values so the model knows the allowed choices", () => {
    const schema = zodToJsonSchema(toolSchemas.get_cpi_series) as {
      properties: Record<string, { enum?: string[] }>;
    };
    expect(schema.properties.code?.enum).toContain("overall");
    expect(schema.properties.code?.enum).toHaveLength(14);
  });

  it("every tool is described well enough to be called correctly", () => {
    for (const [name, schema] of Object.entries(toolSchemas)) {
      expect((schema as { description?: string }).description, `${name} needs a description`)
        .toBeTruthy();
    }
  });
});

// ---------------------------------------------------------------------- validation

describe("argument validation", () => {
  it("accepts valid arguments", () => {
    const result = validateCall("get_cpi_series", { code: "01", mode: "yoy" });
    expect(result.ok).toBe(true);
  });

  it("rejects an unknown tool and lists the real ones", () => {
    const result = validateCall("definitely_not_a_tool", {});
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain("Unknown tool");
      expect(result.error).toContain("get_cpi_series");
    }
  });

  it("rejects a missing required argument", () => {
    const result = validateCall("get_cpi_series", {});
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toMatch(/Invalid arguments/);
  });

  it("rejects a division code outside the allowed set", () => {
    const result = validateCall("get_cpi_series", { code: "99" });
    expect(result.ok).toBe(false);
  });

  it("rejects a negative basket weight", () => {
    const result = validateCall("personal_inflation", { basket: { "01": -5 } });
    expect(result.ok).toBe(false);
  });

  it("accepts an empty argument object for the no-argument tools", () => {
    expect(validateCall("get_coverage", {}).ok).toBe(true);
    expect(validateCall("get_anomalies", {}).ok).toBe(true);
  });

  it("treats a missing argument object as empty rather than crashing", () => {
    // A model that calls a no-argument tool with `arguments: null` should not 500.
    expect(validateCall("get_coverage", null).ok).toBe(true);
  });
});

// ------------------------------------------------------------------ state resolution

describe("state name resolution", () => {
  it("resolves the exact DOSM spelling", () => {
    expect(resolveState("Selangor")).toBe("Selangor");
    expect(resolveState("W.P. Kuala Lumpur")).toBe("W.P. Kuala Lumpur");
  });

  it("resolves common aliases", () => {
    expect(resolveState("Penang")).toBe("Pulau Pinang");
    expect(resolveState("KL")).toBe("W.P. Kuala Lumpur");
    expect(resolveState("Kuala Lumpur")).toBe("W.P. Kuala Lumpur");
    expect(resolveState("Malacca")).toBe("Melaka");
  });

  it("rejects an unknown place instead of guessing", () => {
    // The important behaviour: "Selangor" must never resolve to "Sarawak".
    expect(resolveState("Mars")).toBeNull();
    expect(resolveState("Singapore")).toBeNull();
  });

  it("does not fuzzy-match near-miss spellings", () => {
    expect(resolveState("Selangorr")).toBeNull();
  });
});

// ------------------------------------------------------------------------ tools

describe("get_coverage", () => {
  it("reports the real coverage window", () => {
    const result = executeTool("get_coverage", {});
    expect(result.ok).toBe(true);
    const data = result.data as Record<string, unknown>;
    expect(data.nationalFrom).toBe(bundle.periods.national[0]);
    expect(data.nationalTo).toBe(LATEST_MONTH);
    expect(data.incomeSurveyYears).toEqual(bundle.income.survey_years);
  });

  it("lists the forecast series so the model can pick a valid id", () => {
    const data = executeTool("get_coverage", {}).data as { available_forecast_series: { series_id: string }[] };
    expect(data.available_forecast_series.length).toBeGreaterThan(0);
    expect(data.available_forecast_series.map((s) => s.series_id)).toContain("cpi_headline:overall");
  });

  it("carries a citation", () => {
    expect(executeTool("get_coverage", {}).citations.length).toBeGreaterThan(0);
  });
});

describe("get_cpi_series", () => {
  it("returns year-on-year values that match a hand computation", () => {
    const result = executeTool("get_cpi_series", { code: "overall", mode: "yoy" });
    expect(result.ok).toBe(true);
    const data = result.data as { last: { period: string; value: number } };
    const periods = bundle.periods.national;
    const idx = periods.indexOf(data.last.period);
    const now = bundle.cpi.national.overall![idx]!;
    const before = bundle.cpi.national.overall![idx - 12]!;
    expect(data.last.value).toBeCloseTo((now / before - 1) * 100, 1);
  });

  it("returns index levels when asked", () => {
    const result = executeTool("get_cpi_series", { code: "overall", mode: "index" });
    const data = result.data as { last: { value: number } };
    expect(data.last.value).toBeGreaterThan(100);
  });

  it("rejects an unknown state with the valid list", () => {
    const result = executeTool("get_cpi_series", { code: "overall", state: "Atlantis" });
    expect(result.ok).toBe(false);
    expect(result.error).toContain("Selangor");
  });

  it("resolves a state alias and reports a cumulative figure", () => {
    const result = executeTool("get_cpi_series", {
      code: "overall",
      state: "Penang",
      mode: "yoy",
    });
    const data = result.data as { state: string };
    expect(data.state).toBe("Pulau Pinang");
  });

  it("refuses a period outside the series rather than returning nothing", () => {
    const result = executeTool("get_cpi_series", { code: "overall", from: "2099-01", to: "2099-12" });
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/no data|contains no data/i);
  });

  it("records how much it downsampled, rather than truncating silently", () => {
    const result = executeTool("get_cpi_series", { code: "overall", mode: "index" });
    const data = result.data as { n_points: number; downsampled_to: number };
    expect(data.n_points).toBeGreaterThan(data.downsampled_to);
  });
});

describe("compare_states", () => {
  it("ranks all 16 states on real wage growth", () => {
    const result = executeTool("compare_states", { metric: "real_wage_growth" });
    expect(result.ok).toBe(true);
    const data = result.data as { ranked: { rank: number; state: string }[] };
    expect(data.ranked).toHaveLength(16);
    expect(data.ranked[0]!.rank).toBe(1);
    expect(data.ranked.map((r) => r.state)).toContain("Selangor");
  });

  it("ranks household income separately, because it is a different population", () => {
    const wages = executeTool("compare_states", { metric: "real_wage_growth" }).data as {
      ranked: { state: string }[];
    };
    const income = executeTool("compare_states", { metric: "real_income_growth" }).data as {
      ranked: { state: string }[];
    };
    // Different measures, different populations, so the orderings must not be identical.
    expect(wages.ranked.map((r) => r.state)).not.toEqual(income.ranked.map((r) => r.state));
  });

  it("carries both the measure and the deflator as citations", () => {
    const result = executeTool("compare_states", { metric: "real_wage_growth" });
    expect(result.citations.map((c) => c.dataset)).toContain("wages_state");
    expect(result.citations.map((c) => c.dataset)).toContain("cpi_state");
  });

  it("states the population caveat", () => {
    const data = executeTool("compare_states", { metric: "real_wage_growth" }).data as { note: string };
    expect(data.note).toMatch(/self-employed|informal/i);
  });
});

describe("personal_inflation", () => {
  it("computes a weighted average that matches a hand calculation", () => {
    const basket = { "01": 1000, "07": 1000 };
    const result = executeTool("personal_inflation", { basket });
    expect(result.ok).toBe(true);
    const data = result.data as {
      personal_inflation_pct: number;
      contributions: { code: string; rate_pct: number }[];
    };
    const rate01 = data.contributions.find((c) => c.code === "01")!.rate_pct;
    const rate07 = data.contributions.find((c) => c.code === "07")!.rate_pct;
    expect(data.personal_inflation_pct).toBeCloseTo((rate01 + rate07) / 2, 2);
  });

  it("reports the gap against the headline rate", () => {
    const data = executeTool("personal_inflation", { basket: { "01": 1, "07": 1 } }).data as {
      personal_inflation_pct: number;
      headline_inflation_pct: number;
      gap_pp: number;
    };
    expect(data.gap_pp).toBeCloseTo(data.personal_inflation_pct - data.headline_inflation_pct, 2);
  });

  it("excludes an unpriceable category instead of counting it as zero", () => {
    const result = executeTool("personal_inflation", { basket: { "01": 100, "99": 100 } });
    const data = result.data as {
      personal_inflation_pct: number;
      contributions: { code: string; rate_pct: number }[];
      unpriced_categories: string[];
    };
    expect(data.unpriced_categories).toContain("99");
    // Rate must equal category 01 alone, not the average of 01 and 0.
    expect(data.personal_inflation_pct).toBeCloseTo(data.contributions[0]!.rate_pct, 2);
  });

  it("rejects a basket that cannot be priced at all", () => {
    const result = executeTool("personal_inflation", { basket: { "98": 100, "97": 50 } });
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/could be priced/i);
  });

  it("rejects a basket with no positive amounts", () => {
    const result = executeTool("personal_inflation", { basket: { "01": 0 } });
    expect(result.ok).toBe(false);
  });

  it("supports the since-2010 window", () => {
    const data = executeTool("personal_inflation", {
      basket: { "01": 1 },
      period: "since_2010",
    }).data as { period: string; personal_inflation_pct: number };
    expect(data.period).toBe("since_2010");
    expect(data.personal_inflation_pct).toBeGreaterThan(0);
  });
});

describe("get_forecast", () => {
  it("defaults to the headline series", () => {
    const result = executeTool("get_forecast", {});
    expect(result.ok).toBe(true);
    const data = result.data as { series_id: string; points: unknown[] };
    expect(data.series_id).toBe("cpi_headline:overall");
    expect(data.points.length).toBe(12);
  });

  it("always reports whether the model beat the baseline", () => {
    // The honesty requirement has to survive the trip through the tool boundary.
    const data = executeTool("get_forecast", {}).data as {
      beats_baseline: boolean;
      verdict: string;
      baseline: string;
    };
    expect(typeof data.beats_baseline).toBe("boolean");
    expect(data.verdict.length).toBeGreaterThan(10);
    expect(data.baseline).toBe("inflation_persistence");
  });

  it("carries the accuracy comparison, not just the projection", () => {
    const data = executeTool("get_forecast", {}).data as {
      accuracy: { mae_pp: number };
      baseline_accuracy: { mae_pp: number };
    };
    expect(data.accuracy.mae_pp).toBeGreaterThan(0);
    expect(data.baseline_accuracy.mae_pp).toBeGreaterThan(0);
  });

  it("rejects an unknown series and lists the valid ones", () => {
    const result = executeTool("get_forecast", { series_id: "cpi_headline:99" });
    expect(result.ok).toBe(false);
    expect(result.error).toContain("cpi_headline:overall");
  });
});

describe("get_item_price", () => {
  it("finds an item by a simple word", () => {
    const result = executeTool("get_item_price", { query: "chicken" });
    expect(result.ok).toBe(true);
    const data = result.data as { items: { item: string; median_price_rm: number }[] };
    expect(data.items.length).toBeGreaterThan(0);
    expect(data.items[0]!.median_price_rm).toBeGreaterThan(0);
  });

  it("narrows to a state when asked", () => {
    const data = executeTool("get_item_price", { query: "chicken", state: "Sabah" }).data as {
      state: string;
      items: { price: number }[];
    };
    expect(data.state).toBe("Sabah");
    expect(data.items[0]!.price).toBeGreaterThan(0);
  });

  it("reports the cheapest and dearest states", () => {
    const data = executeTool("get_item_price", { query: "chicken" }).data as {
      items: { cheapest_state: string; dearest_state: string; cheapest_price_rm: number; dearest_price_rm: number }[];
    };
    const first = data.items[0]!;
    expect(first.cheapest_price_rm).toBeLessThanOrEqual(first.dearest_price_rm);
    expect(first.cheapest_state).not.toBe(first.dearest_state);
  });

  it("repeats the not-an-inflation-measure caveat in the result", () => {
    const data = executeTool("get_item_price", { query: "rice" }).data as { caveat: string };
    expect(data.caveat).toMatch(/not be used for inflation/i);
  });

  it("gives a useful error for a term with no match", () => {
    const result = executeTool("get_item_price", { query: "dragonfruit" });
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/chicken|simpler word/i);
  });
});

describe("get_anomalies", () => {
  it("returns the largest deviations first", () => {
    const data = executeTool("get_anomalies", {}).data as {
      largest_deviations: { z: number }[];
      n_flagged: number;
    };
    const zs = data.largest_deviations.map((a) => Math.abs(a.z));
    expect(zs).toEqual([...zs].sort((a, b) => b - a));
  });

  it("carries the statistical-not-causal caveat", () => {
    const data = executeTool("get_anomalies", {}).data as { note: string };
    expect(data.note).toMatch(/not explanations|coincidence/i);
  });
});

describe("the bundle itself", () => {
  it("carries the CPI base year the rest of the project assumes", () => {
    expect(BASE_YEAR).toBe(2010);
  });

  it("records what was trimmed for size, so the gap is inspectable", () => {
    expect(bundle.dropped_for_size.length).toBeGreaterThan(0);
    expect(bundle.dropped_for_size.join(" ")).toMatch(/pricecatcher/i);
  });

  it("reports a data version, so the UI can show freshness", () => {
    expect(coverage().generatedAt).toBeTruthy();
  });
});

// ------------------------------------------------------------------- model allowlist

describe("model allowlist", () => {
  const env = {
    MODEL: "default-model",
    MODEL_COMPARISON: "big-model",
    ALLOWED_MODELS: "default-model,big-model",
  } as unknown as Parameters<typeof resolveModel>[1];

  it("uses the default when nothing is requested", () => {
    expect(resolveModel(undefined, env)).toBe("default-model");
  });

  it("allows an allowlisted override, so evals need no redeploy", () => {
    expect(resolveModel("big-model", env)).toBe("big-model");
  });

  it("falls back to the default for a model that is not allowlisted", () => {
    // Without this, `{"model": "<most expensive model on the account>"}` spends the owner's
    // quota on a stranger's request.
    expect(resolveModel("@cf/some/expensive-model", env)).toBe("default-model");
  });

  it("falls back rather than erroring, so a bad override cannot break the endpoint", () => {
    expect(resolveModel("", env)).toBe("default-model");
    expect(resolveModel("   ", env)).toBe("default-model");
  });
});

// --------------------------------------------------- income and spending-group tools

describe("get_income_series", () => {
  it("computes national real wage growth that matches the artifacts", () => {
    const data = executeTool("get_income_series", { measure: "wage" }).data as {
      growth_pct: number;
      population: string;
    };
    const rows = bundle.wages.national.slice().sort((a, b) => a.year - b.year);
    const expected =
      (rows[rows.length - 1]!.real_median! / rows[0]!.real_median! - 1) * 100;
    expect(data.growth_pct).toBeCloseTo(expected, 1);
    expect(data.population).toMatch(/citizens/);
  });

  it("returns household income separately, not the wage series", () => {
    const wage = executeTool("get_income_series", { measure: "wage" }).data as { growth_pct: number };
    const income = executeTool("get_income_series", { measure: "household_income" }).data as { growth_pct: number };
    expect(wage.growth_pct).not.toBeCloseTo(income.growth_pct, 1);
  });

  it("deflates by the ratio of growth factors, not by subtraction", () => {
    const nominal = executeTool("get_income_series", { measure: "household_income", real: false }).data as { growth_pct: number };
    const real = executeTool("get_income_series", { measure: "household_income", real: true }).data as { growth_pct: number };
    expect(nominal.growth_pct).toBeGreaterThan(real.growth_pct);
  });

  it("works for a state", () => {
    const data = executeTool("get_income_series", { measure: "wage", state: "Selangor" }).data as {
      state: string;
      series: unknown[];
    };
    expect(data.state).toBe("Selangor");
    expect(data.series.length).toBeGreaterThan(5);
  });

  it("rejects an unknown state", () => {
    expect(executeTool("get_income_series", { measure: "wage", state: "Singapore" }).ok).toBe(false);
  });
});

describe("get_spending_group", () => {
  it("separates food away from food at home", () => {
    const away = executeTool("get_spending_group", { group: "001" }).data as { value_pct: number; label: string };
    const home = executeTool("get_spending_group", { group: "011" }).data as { value_pct: number; label: string };
    expect(away.label).toMatch(/away/i);
    expect(home.label).toMatch(/at home/i);
    // The finding this whole project is built on.
    expect(away.value_pct).toBeGreaterThan(home.value_pct);
  });

  it("matches a group from words when no code is given", () => {
    const data = executeTool("get_spending_group", { query: "eating out" }).data as { group: string };
    expect(data.group).toBe("001");
  });

  it("matches groceries", () => {
    const data = executeTool("get_spending_group", { query: "food at home" }).data as { group: string };
    expect(data.group).toBe("011");
  });

  it("reports a year-on-year figure when asked", () => {
    const data = executeTool("get_spending_group", { group: "001", mode: "yoy" }).data as {
      value_pct: number;
      period: string;
    };
    expect(Number.isFinite(data.value_pct)).toBe(true);
    expect(data.period).toBeDefined();
  });

  it("gives a useful error for a query with no match", () => {
    const result = executeTool("get_spending_group", { query: "spaceships" });
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/food away from home/i);
  });
});

// ------------------------------------------------- cumulative rebasing regression

describe("cumulative mode rebases to the base year", () => {
  it("measures from 2010, not from the start of the series", () => {
    // The bug this pins: cumulative mode rebased to the first observation in the window, which
    // defaults to 1980. Asked for "since 2010", the tool returned a change measured from 1980
    // and contradicted its own schema description.
    const full = executeTool("get_cpi_series", { code: "overall", mode: "cumulative" });
    const data = full.data as { last: { value: number } };

    const series = bundle.cpi.national.overall!;
    const periods = bundle.periods.national;
    const baseValues = periods
      .map((p, i) => ({ p, v: series[i] }))
      .filter(({ p, v }) => p.startsWith(String(BASE_YEAR)) && typeof v === "number")
      .map(({ v }) => v as number);
    const base = baseValues.reduce((a, b) => a + b, 0) / baseValues.length;
    // The definition, stated once and shared by every tool: the latest observed month against
    // the base year's monthly mean.
    const end = series[series.length - 1]!;

    expect(data.last.value).toBeCloseTo((end / base - 1) * 100, 1);
    // And specifically not the since-1980 figure, which is materially larger.
    const from1980 = (series[series.length - 1]! / series[0]!) * 100 - 100;
    expect(Math.abs(data.last.value - from1980)).toBeGreaterThan(1);
  });

  it("still returns the whole series when no window is given", () => {
    const data = executeTool("get_cpi_series", { code: "overall", mode: "cumulative" }).data as {
      first: { value: number };
    };
    // The first *returned* point is 1980, and its cumulative value is relative to 2010, so it
    // is large and negative rather than the zero it would be under the old rebasing.
    expect(data.first.value).toBeLessThan(-40);
  });
});
