/**
 * The data bundle, and the lookups the tools are built on.
 *
 * The bundle is compiled into the Worker (see `pipeline/src/colcheck/bundle.py` for why).
 * It is generated from the same artifacts the site renders, so the agent and the charts can
 * never disagree about a number — which is the failure mode that would do the most damage to
 * a project whose whole pitch is that its figures are traceable.
 *
 * Nothing here reads the network. Every answer the agent gives is computed from this object.
 */

// Plain JSON import: the bundler resolves it at build time, so the bundle is compiled
// into the Worker with no runtime fetch and no import-attribute syntax to trip over.
import raw from "./generated/data.json";

export interface Citation {
  dataset: string;
  source: string;
  asOf: string | null;
  range?: string;
  note?: string;
}

export interface DivisionName {
  en: string;
  bm: string;
}

interface AsOfEntry {
  as_of: string | null;
  last_updated: string | null;
  tier: string;
}

interface StateDivisionSnapshot {
  [divisionCode: string]: Record<string, number>;
}

export interface Bundle {
  generated_at: string;
  pipeline_version: string;
  source: string;
  caveats: { wages: string[]; income: string[]; pricecatcher: string };
  as_of: Record<string, AsOfEntry>;
  periods: { national: string[]; state: string[] };
  states: string[];
  base_year: number;
  divisions: Record<string, DivisionName>;
  cpi: {
    national: Record<string, (number | null)[]>;
    state_overall: Record<string, (number | null)[]>;
    state_by_division: Record<string, StateDivisionSnapshot>;
  };
  groups: { periods: string[]; series: Record<string, (number | null)[]>; names: Record<string, DivisionName> };
  wages: {
    national: { year: number; wages_median: number | null; real_median: number | null; cpi: number | null }[];
    by_state: { state: string; year: number; wages_median: number | null; real_median: number | null; cpi: number | null }[];
    summary: { state: string; real_growth_pct: number; nominal_growth_pct: number; cumulative_inflation_pct: number }[];
    meta: { period: { start_year: number; end_year: number } | null; population: string | null; as_of: string | null };
  };
  income: {
    national: { year: number; income_median: number | null; cpi: number | null }[];
    by_state: { state: string; year: number; income_median: number | null; cpi: number | null }[];
    summary: { state: string; real_growth_pct: number; nominal_growth_pct: number; cumulative_inflation_pct: number }[];
    survey_years: number[];
    meta: { period: { start_year: number; end_year: number } | null; as_of: string | null };
  };
  regional: {
    wages_ranking: {
      state: string;
      cumulative_inflation_pct: number;
      nominal_wage_growth_pct: number;
      real_wage_growth_pct: number;
    }[];
    income_ranking: {
      state: string;
      cumulative_inflation_pct: number;
      nominal_income_growth_pct: number;
      real_income_growth_pct: number;
    }[];
    spreads: Record<string, unknown>;
    periods: Record<string, { start_year: number; end_year: number } | null>;
  };
  forecast?: {
    meta: { baseline: string; scored_on: string; as_of: string | null };
    series: {
      series_id: string;
      label: string;
      method: string;
      horizon: number;
      last_observed_period: string;
      last_observed_index: number;
      beats_baseline: boolean;
      verdict: string;
      scores: { model: string; mae_pp: number; mape_pct: number; rmse_pp: number; n_observations: number }[];
      points: {
        period: string;
        point: number;
        implied_yoy: number | null;
        implied_yoy_lo80: number | null;
        implied_yoy_hi80: number | null;
      }[];
    }[];
  };
  anomalies?: {
    threshold_z: number | null;
    anomalies: { period: string; z: number; direction: string; yoy_at_period: number | null }[];
    regimes: { start_period: string; end_period: string; months: number; mean_yoy: number; min_yoy: number; max_yoy: number }[];
    context: Record<string, number | string>;
  };
  pricecatcher?: {
    month: string | null;
    n_items: number;
    items: {
      code: number;
      item: string;
      unit: string | null;
      group: string | null;
      category: string | null;
      prices: Record<string, number | null>;
    }[];
  };
  dropped_for_size: string[];
}

export const bundle = raw as unknown as Bundle;

/** Base year, as asserted by the pipeline. Every real-terms figure depends on it. */
export const BASE_YEAR = bundle.base_year;

/** The latest month present in the national CPI series. */
export const LATEST_MONTH = bundle.periods.national[bundle.periods.national.length - 1] ?? "";

/** Earliest and latest period covered anywhere, for the coverage tool and refusals. */
export function coverage() {
  const nat = bundle.periods.national;
  return {
    nationalFrom: nat[0] ?? null,
    nationalTo: nat[nat.length - 1] ?? null,
    stateFrom: bundle.periods.state[0] ?? null,
    stateTo: bundle.periods.state[bundle.periods.state.length - 1] ?? null,
    wagesFrom: bundle.wages.meta.period?.start_year ?? null,
    wagesTo: bundle.wages.meta.period?.end_year ?? null,
    incomeFrom: bundle.income.meta.period?.start_year ?? null,
    incomeTo: bundle.income.meta.period?.end_year ?? null,
    incomeSurveyYears: bundle.income.survey_years,
    itemMonth: bundle.pricecatcher?.month ?? null,
    forecastsTo:
      bundle.forecast?.series[0]?.points[bundle.forecast.series[0].points.length - 1]?.period ?? null,
    generatedAt: bundle.generated_at,
    asOf: bundle.as_of,
  };
}

export function divisionLabel(code: string): string {
  return bundle.divisions[code]?.en ?? code;
}

export function groupLabel(code: string): string {
  return bundle.groups.names[code]?.en ?? code;
}

/** Index value for a national division at a period index, or null if absent. */
export function nationalIndex(code: string, periodIndex: number): number | null {
  const series = bundle.cpi.national[code];
  if (!series) return null;
  const value = series[periodIndex];
  return typeof value === "number" ? value : null;
}

export function periodIndex(period: string): number {
  return bundle.periods.national.indexOf(period);
}

/**
 * Year-on-year inflation for a national division between two periods.
 *
 * Computed from index levels rather than reading any published percentage, because
 * compounding published percentages is where this kind of tool quietly goes wrong.
 */
export function yoy(code: string, toIndex: number): number | null {
  const now = nationalIndex(code, toIndex);
  const before = nationalIndex(code, toIndex - 12);
  if (now === null || before === null || before === 0) return null;
  return (now / before - 1) * 100;
}

/** Cumulative inflation for a national division from the base year to the latest period. */
export function cumulative(code: string): number | null {
  const series = bundle.cpi.national[code];
  if (!series) return null;
  const baseIdx = bundle.periods.national.findIndex((p) => p.startsWith(String(BASE_YEAR)));
  const baseWindow = bundle.periods.national
    .map((p, i) => ({ p, i }))
    .filter(({ p }) => p.startsWith(String(BASE_YEAR)))
    .map(({ i }) => series[i])
    .filter((v): v is number => typeof v === "number");
  if (baseWindow.length === 0 || baseIdx < 0) return null;
  const base = baseWindow.reduce((a, b) => a + b, 0) / baseWindow.length;

  const latestYear = (bundle.periods.national.at(-1) ?? "").slice(0, 4);
  const latestWindow = bundle.periods.national
    .map((p, i) => ({ p, i }))
    .filter(({ p }) => p.startsWith(latestYear))
    .map(({ i }) => series[i])
    .filter((v): v is number => typeof v === "number");
  if (latestWindow.length === 0 || base === 0) return null;
  const end = latestWindow.reduce((a, b) => a + b, 0) / latestWindow.length;
  return (end / base - 1) * 100;
}

/** State CPI inflation over a window, from the state's annual means. */
export function stateInflation(state: string, fromYear: number, toYear: number): number | null {
  const snapshot = bundle.cpi.state_by_division[state]?.overall;
  if (!snapshot) return null;
  const a = snapshot[String(fromYear)];
  const b = snapshot[String(toYear)];
  if (typeof a !== "number" || typeof b !== "number" || a === 0) return null;
  return (b / a - 1) * 100;
}

/** Find items by name, case-insensitively, with simple token matching. */
export function findItems(query: string, limit = 8) {
  const items = bundle.pricecatcher?.items ?? [];
  const terms = query
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((t) => t.length > 2);
  if (terms.length === 0) return [];
  const scored = items
    .map((item) => {
      const haystack = `${item.item} ${item.category ?? ""} ${item.group ?? ""}`.toLowerCase();
      const hits = terms.filter((t) => haystack.includes(t)).length;
      return { item, hits };
    })
    .filter((s) => s.hits > 0)
    .sort((a, b) => b.hits - a.hits);
  return scored.slice(0, limit).map((s) => s.item);
}

/** A citation for a dataset, using its real as-of date rather than a hard-coded one. */
export function citationFor(dataset: string, range?: string, note?: string): Citation {
  const entry = bundle.as_of[dataset];
  return {
    dataset,
    source: bundle.source,
    asOf: entry?.as_of ?? null,
    ...(range ? { range } : {}),
    ...(note ? { note } : {}),
  };
}
