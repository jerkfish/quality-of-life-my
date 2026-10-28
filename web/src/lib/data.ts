/**
 * Build-time access to the pipeline's artifacts.
 *
 * The site reads `data/*.json` and nothing else. No runtime fetch, no upstream call, no
 * database. If an artifact is missing the build fails loudly rather than rendering a page
 * with an empty chart, which is the failure mode a reader would never notice.
 *
 * Types here mirror the pydantic models in `pipeline/src/colcheck/artifacts.py`. They are
 * hand-written rather than generated so that a mismatch is a visible diff; a generated file
 * would drift silently every time the pipeline changed.
 */

import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const DATA_DIR = fileURLToPath(new URL("../../../data/", import.meta.url));

// ---------------------------------------------------------------------------- types

export interface ArtifactMeta {
  dataset?: string;
  datasets?: string[];
  source?: string[];
  as_of?: string | null;
  last_updated?: string | null;
  next_update?: string | null;
  frequency?: string | null;
  base_year?: number;
  unit?: string;
  caveat?: string;
  note?: string;
  [key: string]: unknown;
}

export interface SeriesArtifact {
  meta: ArtifactMeta;
  periods: string[];
  series: Record<string, (number | null)[]>;
}

export interface DivisionName {
  en: string;
  bm: string;
}

export interface NationalCpi extends SeriesArtifact {
  meta: ArtifactMeta & {
    division_names: Record<string, DivisionName>;
    base_year: number;
    unit: string;
  };
}

export interface StateCpi extends SeriesArtifact {
  meta: ArtifactMeta & {
    states: string[];
    division_names: Record<string, DivisionName>;
    base_year: number;
  };
}

export interface GroupCpi extends SeriesArtifact {
  meta: ArtifactMeta & {
    group_names: Record<string, DivisionName>;
    cumulative_from_base_pct: Record<string, number>;
  };
}

export interface WagePoint {
  state: string;
  year: number;
  wages_median: number | null;
  wages_mean: number | null;
  cpi: number | null;
  real_median: number | null;
  real_mean: number | null;
}

export interface PeriodSummary {
  state: string;
  start_year: number;
  end_year: number;
  wages_median_start?: number;
  wages_median_end?: number;
  income_median_start?: number;
  income_median_end?: number;
  nominal_growth_pct: number;
  cumulative_inflation_pct: number;
  real_growth_pct: number;
}

export interface RealWages {
  meta: ArtifactMeta & {
    population: string;
    currency: string;
    period: { start_year: number; end_year: number };
    validation_against_dosm: {
      n_compared: number;
      max_abs_diff_median: number;
      passed: boolean;
      tolerance_rm: number;
    };
  };
  series: WagePoint[];
  /** Nationwide series, deflated by the national CPI. `series` has no Malaysia row. */
  national: Omit<WagePoint, "state">[];
  summary: PeriodSummary[];
  caveats: string[];
}

export interface HouseholdIncome {
  meta: ArtifactMeta & {
    survey_years: number[];
    period: { start_year: number; end_year: number };
  };
  series: { state: string; year: number; income_median: number | null; income_mean: number | null; cpi: number | null }[];
  national: { year: number; income_median: number | null; income_mean: number | null; cpi: number | null }[];
  summary: PeriodSummary[];
  caveats: string[];
}

export interface SpreadStats {
  min_pct?: number;
  min_state?: string;
  max_pct?: number;
  max_state?: string;
  spread_pp?: number;
}

export interface Regional {
  meta: ArtifactMeta & {
    wages_period: { start_year: number; end_year: number };
    income_period: { start_year: number; end_year: number };
    /** Older block, kept in the artifact but not used by the site. */
    inflation_by_state_wages_period?: { state: string; cumulative_pct: number }[];
  };
  wages_ranking: {
    state: string;
    cumulative_inflation_pct: number;
    nominal_wage_growth_pct: number;
    real_wage_growth_pct: number;
    wages_median_start: number;
    wages_median_end: number;
  }[];
  income_ranking: {
    state: string;
    cumulative_inflation_pct: number;
    nominal_income_growth_pct: number;
    real_income_growth_pct: number;
    income_median_start?: number;
    income_median_end?: number;
  }[];
  spreads: {
    wages: { inflation: SpreadStats; nominal_growth: SpreadStats; real_growth: SpreadStats };
    income: { inflation: SpreadStats; nominal_growth: SpreadStats; real_growth: SpreadStats };
  };
}

export interface ModelScore {
  model: string;
  mae_pp: number;
  mape_pct: number;
  rmse_pp: number;
  n_observations: number;
}

export interface ForecastPoint {
  period: string;
  point: number;
  lo80: number | null;
  hi80: number | null;
  implied_yoy: number | null;
  implied_yoy_lo80: number | null;
  implied_yoy_hi80: number | null;
}

export interface ForecastSeries {
  series_id: string;
  label: string;
  method: string;
  horizon: number;
  last_observed_period: string;
  last_observed_index: number;
  points: ForecastPoint[];
  scores: ModelScore[];
  baseline_model: string;
  beats_baseline: boolean;
  verdict: string;
  caveats: string[];
}

export interface ForecastArtifact {
  meta: ArtifactMeta & { baseline: string; scored_on: string; method_note: string };
  series: ForecastSeries[];
  caveats?: string[];
}

export interface AnomalyPoint {
  period: string;
  index: number;
  residual: number;
  z: number;
  direction: "above" | "below";
  yoy_at_period: number | null;
}

export interface RegimePoint {
  start_period: string;
  end_period: string;
  months: number;
  mean_yoy: number;
  min_yoy: number;
  max_yoy: number;
}

export interface AnomalySeries {
  series_id: string;
  label: string;
  first_period: string;
  last_period: string;
  threshold_z: number;
  anomalies: AnomalyPoint[];
  regimes: RegimePoint[];
  context: Record<string, number | string>;
  caveats: string[];
}

export interface AnomalyArtifact {
  meta: ArtifactMeta;
  series: AnomalySeries[];
}

export interface PricecatcherItem {
  item_code: number;
  item: string;
  unit: string | null;
  item_group: string | null;
  item_category: string | null;
}

export interface PricecatcherSeries {
  months: string[];
  median_price: (number | null)[];
  n_obs: number[];
  n_states: number[];
}

export interface PricecatcherArtifact {
  meta: ArtifactMeta & { editions: string[]; n_items: number };
  items: PricecatcherItem[];
  series: Record<string, PricecatcherSeries>;
  /** Latest month's median price per item per state, for the "near you" panel. */
  latest_by_state: {
    month: string | null;
    prices: Record<string, Record<string, number | null>>;
    counts: Record<string, Record<string, number>>;
  };
}

export interface DatasetReport {
  dataset_id: string;
  rows: number;
  date_min: string | null;
  date_max: string | null;
  issues: { level: "error" | "warning"; code: string; message: string }[];
}

export interface Manifest {
  generated_at: string;
  pipeline_version: string;
  source: string;
  datasets: Record<
    string,
    {
      tier: string;
      title: string;
      as_of: string | null;
      last_updated: string | null;
      next_update: string | null;
      upstream_source: string[];
      report: DatasetReport;
    }
  >;
  artifacts: Record<string, { bytes: number; sha256: string }>;
}

// ---------------------------------------------------------------------------- loading

/** Artifacts the site cannot render without. A missing one is a build failure, not a gap. */
const REQUIRED = [
  "manifest.json",
  "cpi_national.json",
  "cpi_state.json",
  "cpi_groups.json",
  "real_wages.json",
  "household_income.json",
  "regional.json",
] as const;

/** Present only when the pipeline's PriceCatcher stage has run. */
const OPTIONAL = ["forecast.json", "anomalies.json", "pricecatcher/items.json"] as const;

function load<T>(name: string): T {
  const path = `${DATA_DIR}${name}`;
  if (!existsSync(path)) {
    throw new Error(
      `Missing data artifact ${name}. Run \`cd pipeline && uv run colcheck run\` before building the site.`,
    );
  }
  return JSON.parse(readFileSync(path, "utf8")) as T;
}

function loadOptional<T>(name: string): T | null {
  const path = `${DATA_DIR}${name}`;
  return existsSync(path) ? (JSON.parse(readFileSync(path, "utf8")) as T) : null;
}

/** Verified at module load so a broken artifact set fails `astro build`, not a visitor. */
export function assertArtifactsPresent(): string[] {
  const missing = REQUIRED.filter((name) => !existsSync(`${DATA_DIR}${name}`));
  if (missing.length) {
    throw new Error(
      `Missing required artifact(s): ${missing.join(", ")}.\n` +
        `Run the pipeline first: cd pipeline && uv run colcheck run`,
    );
  }
  return [...REQUIRED];
}

export const manifest = (): Manifest => load<Manifest>("manifest.json");
export const nationalCpi = (): NationalCpi => load<NationalCpi>("cpi_national.json");
export const stateCpi = (): StateCpi => load<StateCpi>("cpi_state.json");
export const groupCpi = (): GroupCpi => load<GroupCpi>("cpi_groups.json");
export const strataCpi = (): SeriesArtifact => load<SeriesArtifact>("cpi_strata.json");
export const realWages = (): RealWages => load<RealWages>("real_wages.json");
export const householdIncome = (): HouseholdIncome => load<HouseholdIncome>("household_income.json");
export const regional = (): Regional => load<Regional>("regional.json");

export const forecast = (): ForecastArtifact | null => loadOptional<ForecastArtifact>("forecast.json");
export const anomalies = (): AnomalyArtifact | null => loadOptional<AnomalyArtifact>("anomalies.json");
export const pricecatcher = (): PricecatcherArtifact | null =>
  loadOptional<PricecatcherArtifact>("pricecatcher/items.json");

export const ALL_ARTIFACTS = [...REQUIRED, ...OPTIONAL];

// ---------------------------------------------------------------------------- helpers

/**
 * Latest as-of date across the upstream datasets that feed the given artifacts.
 *
 * There is deliberately no single global "last updated" for the site: CPI refreshes monthly,
 * formal wages monthly, state wages annually and HIES biennially. One badge claiming a single
 * date would be wrong for most of the page, so each page derives its own.
 */
export function asOf(filenames: string[]): string | null {
  const m = manifest();
  const dates = filenames
    .flatMap(artifactDatasets)
    .map((d) => m.datasets[d]?.as_of)
    .filter((d): d is string => Boolean(d));
  return dates.sort().at(-1) ?? null;
}

const ARTIFACT_DATASETS: Record<string, string[]> = {
  "cpi_national.json": ["cpi_headline"],
  "cpi_state.json": ["cpi_state"],
  "cpi_groups.json": ["cpi_3d"],
  "cpi_strata.json": ["cpi_strata"],
  "real_wages.json": ["wages_state", "cpi_state"],
  "household_income.json": ["hh_income_state", "cpi_headline"],
  "regional.json": ["cpi_state", "wages_state"],
  "forecast.json": ["cpi_headline"],
  "anomalies.json": ["cpi_headline"],
  "pricecatcher/items.json": ["pricecatcher"],
  "manifest.json": [],
};

function artifactDatasets(filename: string): string[] {
  return ARTIFACT_DATASETS[filename] ?? [];
}

/** Warnings and errors the pipeline recorded, so the site can surface data confidence. */
export function issuesFor(filename: string): { level: string; code: string; message: string }[] {
  const m = manifest();
  return artifactDatasets(filename).flatMap((d) => m.datasets[d]?.report?.issues ?? []);
}

export function generatedAt(): string {
  return manifest().generated_at;
}

export function pipelineVersion(): string {
  return manifest().pipeline_version;
}
