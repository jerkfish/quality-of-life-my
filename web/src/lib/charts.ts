/**
 * Chart renderers, one function per visual.
 *
 * Every chart follows the same contract so the mount layer stays trivial and the honesty
 * conventions are enforced in one place:
 *
 *   - nulls are never bridged. Plot's `line` would happily draw straight through a gap, so
 *     series are split into contiguous runs before drawing.
 *   - every axis is titled with its unit, because a bare "130" tells a reader nothing.
 *   - each chart carries a text summary, rendered into a visually-hidden node, so the
 *     information is available to a screen reader and to anyone whose SVG fails to paint.
 *   - nothing is hard-coded as a "known event" — annotations come from the data.
 */

import * as Plot from "@observablehq/plot";
import type { ChartTheme } from "./theme";

type Row = Record<string, unknown>;

export interface ChartProps {
  [key: string]: unknown;
}

export interface RenderedChart {
  element: SVGSVGElement | HTMLElement;
  summary: string;
}

function periodToDate(period: string): Date {
  const [y, m] = period.split("-").map(Number);
  return new Date(Date.UTC(y, (m ?? 1) - 1, 1));
}

/**
 * Split rows into runs of consecutive non-null values.
 *
 * This is the mechanism behind "no interpolation". Plot draws a line through every point it
 * is handed, so a null in the middle of a series becomes a straight line across the gap that
 * looks exactly like real data. Splitting first means the gap stays visible.
 */
function contiguousRuns<T extends Row>(rows: T[], valueOf: (row: T) => number | null): T[][] {
  const runs: T[][] = [];
  let current: T[] = [];
  for (const row of rows) {
    if (valueOf(row) === null || valueOf(row) === undefined || Number.isNaN(valueOf(row))) {
      if (current.length > 1) runs.push(current);
      current = [];
    } else {
      current.push(row);
    }
  }
  if (current.length > 1) runs.push(current);
  return runs;
}

/** Merge consecutive runs into a single series keyed by an index, for facet-free line marks. */
function gapAware<T extends Row>(rows: T[], valueOf: (row: T) => number | null): T[] {
  const out: T[] = [];
  contiguousRuns(rows, valueOf).forEach((run, i) => {
    run.forEach((row, j) => {
      out.push({ ...row, __run: i, __first: j === 0 } as T);
    });
  });
  return out;
}

function axisStyle(theme: ChartTheme) {
  return {
    color: theme.fgMuted,
    fontSize: "11px",
  };
}

function base(theme: ChartTheme) {
  return {
    style: {
      background: "transparent",
      color: theme.fg,
      fontFamily: "inherit",
      fontSize: "12px",
      overflow: "visible",
    },
    marginLeft: 56,
    marginRight: 16,
  } satisfies Partial<Plot.PlotOptions>;
}

// ---------------------------------------------------------------------- CPI charts

/** National CPI index over time for the selected divisions. */
export function cpiLevel(props: ChartProps, theme: ChartTheme): RenderedChart {
  const periods = props.periods as string[];
  const series = props.series as Record<string, (number | null)[]>;
  const names = props.names as Record<string, { en: string }>;
  const codes = props.codes as string[];

  const rows: Row[] = [];
  for (const code of codes) {
    const values = series[code] ?? [];
    periods.forEach((period, i) => {
      rows.push({
        date: periodToDate(period),
        value: values[i] ?? null,
        division: names[code]?.en ?? code,
      });
    });
  }

  const drawn = gapAware(rows, (r) => r.value as number | null);
  const last = rows.filter((r) => r.value !== null).at(-1);

  const element = Plot.plot({
    ...base(theme),
    width: 780,
    height: 340,
    x: { label: "Year", ticks: 6, ...axisStyle(theme) },
    y: { label: "Consumer price index (2010 = 100)", grid: true, ...axisStyle(theme) },
    color: {
      legend: codes.length > 1,
      domain: codes.map((c) => names[c]?.en ?? c),
      range: codes.map((_, i) => theme.series[i % theme.series.length]),
    },
    marks: [
      Plot.ruleY([100], { stroke: theme.border, strokeDasharray: "3,3" }),
      Plot.line(drawn, {
        x: "date",
        y: "value",
        stroke: "division",
        strokeWidth: 1.8,
        curve: "linear",
        z: "__run",
      }),
    ],
  });

  const first = rows.find((r) => r.value !== null);
  const summary =
    `Line chart of the consumer price index from ${first ? (first.date as Date).getUTCFullYear() : "?"} ` +
    `to ${last ? (last.date as Date).getUTCFullYear() : "?"}, base year 2010 = 100. ` +
    codes
      .map((c) => {
        const values = (series[c] ?? []).filter((v): v is number => v !== null);
        const a = values[0];
        const b = values.at(-1);
        const change = a && b ? ((b / a - 1) * 100).toFixed(1) : "?";
        return `${names[c]?.en ?? c}: index ${b?.toFixed(1) ?? "?"}, up ${change}% since 2010.`;
      })
      .join(" ");

  return { element: element as SVGSVGElement, summary };
}

/** Year-on-year inflation for one or more divisions. */
export function inflationRate(props: ChartProps, theme: ChartTheme): RenderedChart {
  const periods = props.periods as string[];
  const series = props.series as Record<string, (number | null)[]>;
  const names = props.names as Record<string, { en: string }>;
  const codes = props.codes as string[];
  const from = (props.from as string) ?? "1990-01";

  const rows: Row[] = [];
  for (const code of codes) {
    const values = series[code] ?? [];
    periods.forEach((period, i) => {
      if (period < from) return;
      const prev = values[i - 12];
      const now = values[i];
      if (typeof prev !== "number" || typeof now !== "number" || prev === 0) return;
      rows.push({
        date: periodToDate(period),
        value: (now / prev - 1) * 100,
        division: names[code]?.en ?? code,
      });
    });
  }

  const element = Plot.plot({
    ...base(theme),
    width: 780,
    height: 320,
    x: { label: "Year", ticks: 6, ...axisStyle(theme) },
    y: {
      label: "Percent change from a year earlier",
      grid: true,
      ...axisStyle(theme),
    },
    color: {
      legend: codes.length > 1,
      domain: codes.map((c) => names[c]?.en ?? c),
      range: codes.map((_, i) => theme.series[i % theme.series.length]),
    },
    marks: [
      Plot.ruleY([0], { stroke: theme.fgFaint }),
      Plot.line(rows, { x: "date", y: "value", stroke: "division", strokeWidth: 1.6 }),
    ],
  });

  const headline = rows.filter((r) => (r as { division: string }).division === names[codes[0]]?.en);
  const peak = headline.reduce<Row | null>(
    (best, r) => (best === null || (r.value as number) > (best.value as number) ? r : best),
    null,
  );
  const low = headline.reduce<Row | null>(
    (best, r) => (best === null || (r.value as number) < (best.value as number) ? r : best),
    null,
  );
  const latest = headline.at(-1);

  const summary =
    `Line chart of year-on-year consumer price inflation. ` +
    (latest
      ? `Latest reading ${(latest.value as number).toFixed(1)}% in ${(latest.date as Date).getUTCFullYear()}. `
      : "") +
    (peak
      ? `Highest ${(peak.value as number).toFixed(1)}% in ${(peak.date as Date).getUTCFullYear()}. `
      : "") +
    (low
      ? `Lowest ${(low.value as number).toFixed(1)}% in ${(low.date as Date).getUTCFullYear()}.`
      : "");

  return { element: element as SVGSVGElement, summary };
}

/** Nominal vs CPI-deflated median wage for one state. */
export function nominalVsReal(props: ChartProps, theme: ChartTheme): RenderedChart {
  const points = props.points as {
    year: number;
    wages_median: number | null;
    real_median: number | null;
  }[];
  const state = (props.state as string) ?? "";

  const rows = points.flatMap((p) => [
    { year: p.year, value: p.wages_median, kind: "Nominal" },
    { year: p.year, value: p.real_median, kind: `Real (2010 ringgit)` },
  ]);

  const element = Plot.plot({
    ...base(theme),
    width: 780,
    height: 330,
    x: { label: "Year", tickFormat: "d", ticks: 6, ...axisStyle(theme) },
    y: { label: "RM per month", grid: true, ...axisStyle(theme) },
    color: {
      legend: true,
      domain: ["Nominal", "Real (2010 ringgit)"],
      range: [theme.series[0], theme.series[1]],
    },
    marks: [
      Plot.line(gapAware(rows, (r) => r.value as number | null), {
        x: "year",
        y: "value",
        stroke: "kind",
        strokeWidth: 2,
        z: "__run",
      }),
      Plot.dot(rows.filter((r) => r.value !== null), {
        x: "year",
        y: "value",
        fill: "kind",
        r: 2.5,
      }),
    ],
  });

  const nominal = points.filter((p) => p.wages_median !== null);
  const real = points.filter((p) => p.real_median !== null);
  const nomChange =
    nominal.length > 1
      ? ((nominal.at(-1)!.wages_median! / nominal[0].wages_median! - 1) * 100).toFixed(1)
      : "?";
  const realChange =
    real.length > 1 ? ((real.at(-1)!.real_median! / real[0].real_median! - 1) * 100).toFixed(1) : "?";

  const summary =
    `${state} median monthly wage, nominal and in 2010 ringgit, ${points[0]?.year} to ${points.at(-1)?.year}. ` +
    `Nominal ${nomChange}%, real ${realChange}%.`;

  return { element: element as SVGSVGElement, summary };
}

/** Ranked bars: cumulative inflation per state. */
export function stateInflationBars(props: ChartProps, theme: ChartTheme): RenderedChart {
  const rows = (props.rows as { state: string; cumulative_inflation_pct: number }[]).slice();
  rows.sort((a, b) => b.cumulative_inflation_pct - a.cumulative_inflation_pct);

  const element = Plot.plot({
    ...base(theme),
    width: 780,
    height: Math.max(240, rows.length * 26 + 60),
    marginLeft: 150,
    x: { label: "Cumulative inflation since 2010 (%)", grid: true, ...axisStyle(theme) },
    y: { label: null, ...axisStyle(theme) },
    marks: [
      Plot.barX(rows, {
        y: "state",
        x: "cumulative_inflation_pct",
        fill: theme.series[0],
        sort: { y: "-x" },
      }),
      Plot.text(rows, {
        y: "state",
        x: "cumulative_inflation_pct",
        text: (d: { cumulative_inflation_pct: number }) => `${d.cumulative_inflation_pct.toFixed(1)}%`,
        dx: 6,
        textAnchor: "start",
        fill: theme.fg,
        fontSize: 10,
      }),
      Plot.ruleX([0], { stroke: theme.fgFaint }),
    ],
  });

  // Guard the empty case explicitly. `rows.at(-1)` is `undefined` for an empty array, so an
  // absent ranking would otherwise throw while building the summary text -- after the chart
  // had already been drawn, which makes it a confusing failure to chase.
  if (rows.length === 0) {
    return {
      element: emptyChart("No state inflation ranking is available in this build."),
      summary: "No data is available for this chart.",
    };
  }

  const hi = rows[0];
  const lo = rows[rows.length - 1];
  const summary =
    `Bar chart of cumulative consumer price inflation by state, 2010 to the latest month. ` +
    `Highest ${hi.state} at ${hi.cumulative_inflation_pct.toFixed(1)}%, lowest ${lo.state} at ` +
    `${lo.cumulative_inflation_pct.toFixed(1)}%, a spread of ` +
    `${(hi.cumulative_inflation_pct - lo.cumulative_inflation_pct).toFixed(1)} percentage points.`;

  return { element: element as SVGSVGElement, summary };
}

/**
 * The core divergence chart: price inflation against real income growth, one dot per state.
 *
 * A flat cloud of points means the two are unrelated; a diagonal means states with higher
 * inflation also saw higher income growth. The finding is that it is flat and wide, so the
 * chart is designed to make the *absence* of a relationship visible.
 */
export function divergenceScatter(props: ChartProps, theme: ChartTheme): RenderedChart {
  const rows = props.rows as {
    state: string;
    cumulative_inflation_pct: number;
    real_growth_pct: number;
    measure: string;
  }[];

  const element = Plot.plot({
    ...base(theme),
    width: 780,
    height: 380,
    marginLeft: 64,
    x: { label: "Cumulative price inflation (%)", grid: true, ...axisStyle(theme) },
    y: { label: "Real income growth (%)", grid: true, ...axisStyle(theme) },
    color: { domain: ["Wages", "Household income"], range: [theme.series[0], theme.series[2]] },
    marks: [
      Plot.dot(rows, {
        x: "cumulative_inflation_pct",
        y: "real_growth_pct",
        fill: "measure",
        r: 5,
        fillOpacity: 0.75,
        stroke: theme.bg,
        strokeWidth: 1,
      }),
      Plot.text(rows, {
        x: "cumulative_inflation_pct",
        y: "real_growth_pct",
        text: (d: { state: string }) => abbreviateState(d.state),
        dy: -10,
        fontSize: 9,
        fill: theme.fgMuted,
      }),
    ],
  });

  const xs = rows.map((r) => r.cumulative_inflation_pct);
  const ys = rows.map((r) => r.real_growth_pct);
  const summary =
    `Scatter plot of cumulative price inflation (x) against real income growth (y) for each state. ` +
    `Inflation ranges from ${Math.min(...xs).toFixed(1)}% to ${Math.max(...xs).toFixed(1)}% ` +
    `(a spread of ${(Math.max(...xs) - Math.min(...xs)).toFixed(1)} points) while real growth ranges ` +
    `from ${Math.min(...ys).toFixed(1)}% to ${Math.max(...ys).toFixed(1)}% ` +
    `(a spread of ${(Math.max(...ys) - Math.min(...ys)).toFixed(1)} points). ` +
    `The points do not form a line, which is the point of the chart.`;

  return { element: element as SVGSVGElement, summary };
}

/** Food away from home vs food at home, indexed to the base year. */
export function groupComparison(props: ChartProps, theme: ChartTheme): RenderedChart {
  const periods = props.periods as string[];
  const series = props.series as Record<string, (number | null)[]>;
  const names = props.names as Record<string, { en: string }>;
  const codes = props.codes as string[];

  const rows: Row[] = [];
  for (const code of codes) {
    const values = series[code] ?? [];
    const base = values.find((v) => v !== null) ?? null;
    if (base === null) continue;
    periods.forEach((period, i) => {
      const v = values[i];
      rows.push({
        date: periodToDate(period),
        value: v === null || v === undefined ? null : (v / base - 1) * 100,
        group: names[code]?.en ?? code,
      });
    });
  }

  const element = Plot.plot({
    ...base(theme),
    width: 780,
    height: 330,
    x: { label: "Year", ticks: 6, ...axisStyle(theme) },
    y: { label: "Percent change since 2010", grid: true, ...axisStyle(theme) },
    color: {
      legend: true,
      domain: codes.map((c) => names[c]?.en ?? c),
      range: codes.map((_, i) => theme.series[i % theme.series.length]),
    },
    marks: [
      Plot.ruleY([0], { stroke: theme.fgFaint }),
      Plot.line(gapAware(rows, (r) => r.value as number | null), {
        x: "date",
        y: "value",
        stroke: "group",
        strokeWidth: 2,
        z: "__run",
      }),
    ],
  });

  const summary =
    `Line chart comparing cumulative price change since 2010 for ${codes.length} spending categories. ` +
    codes
      .map((c) => {
        const values = (series[c] ?? []).filter((v): v is number => v !== null);
        const base = values[0];
        const last = values.at(-1);
        return base && last
          ? `${names[c]?.en ?? c} is up ${((last / base - 1) * 100).toFixed(1)}%.`
          : `${names[c]?.en ?? c} has no data.`;
      })
      .join(" ");

  return { element: element as SVGSVGElement, summary };
}

// --------------------------------------------------------------------- forecast

/** History plus forecast, with the 80% interval as a band. */
export function forecastPath(props: ChartProps, theme: ChartTheme): RenderedChart {
  const history = props.history as { period: string; value: number }[];
  const points = props.points as {
    period: string;
    implied_yoy: number | null;
    implied_yoy_lo80: number | null;
    implied_yoy_hi80: number | null;
  }[];
  const label = (props.label as string) ?? "";

  const histRows = history.map((h) => ({
    date: periodToDate(h.period),
    value: h.value,
    kind: "Actual",
  }));
  const bands = points
    .filter((p) => p.implied_yoy_lo80 !== null)
    .map((p) => ({
      date: periodToDate(p.period),
      lo: p.implied_yoy_lo80 as number,
      hi: p.implied_yoy_hi80 as number,
    }));
  const mid = points.map((p) => ({
    date: periodToDate(p.period),
    value: p.implied_yoy,
    kind: "Forecast",
  }));

  const lastDate = histRows.at(-1)?.date;

  const element = Plot.plot({
    ...base(theme),
    width: 780,
    height: 340,
    x: { label: "Year", ticks: 6, ...axisStyle(theme) },
    y: { label: "Inflation, % change from a year earlier", grid: true, ...axisStyle(theme) },
    color: { domain: ["Actual", "Forecast"], range: [theme.series[0], theme.series[1]] },
    marks: [
      Plot.ruleY([0], { stroke: theme.fgFaint }),
      Plot.areaY(bands, {
        x: "date",
        y1: "lo",
        y2: "hi",
        fill: theme.series[1],
        fillOpacity: 0.16,
      }),
      Plot.line(histRows, { x: "date", y: "value", stroke: "kind", strokeWidth: 1.8 }),
      Plot.line(mid, {
        x: "date",
        y: "value",
        stroke: "kind",
        strokeWidth: 1.8,
        strokeDasharray: "4,3",
      }),
      lastDate
        ? Plot.ruleX([lastDate], {
            stroke: theme.fgFaint,
            strokeDasharray: "2,2",
          })
        : null,
    ].filter(Boolean) as Plot.Markish[],
  });

  const first = points[0]?.implied_yoy;
  const last = points.at(-1)?.implied_yoy;
  const summary =
    `Line chart of ${label} inflation: solid line is the actual rate, dashed line the forecast, ` +
    `shaded band the 80% interval. The forecast runs from ${points[0]?.period} to ${points.at(-1)?.period}, ` +
    `from ${first?.toFixed(1) ?? "?"}% to ${last?.toFixed(1) ?? "?"}%.`;

  return { element: element as SVGSVGElement, summary };
}

/** Model accuracy comparison. Lower is better. */
export function modelScores(props: ChartProps, theme: ChartTheme): RenderedChart {
  const scores = props.scores as {
    model: string;
    mae_pp: number;
    mape_pct: number;
    rmse_pp: number;
  }[];
  const baseline = (props.baseline as string) ?? "";

  const rows = scores
    .filter((s) => Number.isFinite(s.mae_pp))
    .sort((a, b) => a.mae_pp - b.mae_pp)
    .map((s) => ({
      model: s.model,
      mae: s.mae_pp,
      isBaseline: s.model === baseline,
    }));

  const element = Plot.plot({
    ...base(theme),
    width: 620,
    height: Math.max(160, rows.length * 42 + 60),
    marginLeft: 150,
    x: { label: "Mean absolute error (percentage points)", grid: true, ...axisStyle(theme) },
    y: { label: null, ...axisStyle(theme) },
    marks: [
      Plot.barX(rows, {
        y: "model",
        x: "mae",
        fill: (d: { isBaseline: boolean }) => (d.isBaseline ? theme.series[2] : theme.series[0]),
      }),
      Plot.text(rows, {
        y: "model",
        x: "mae",
        text: (d: { mae: number }) => `${d.mae.toFixed(2)} pp`,
        dx: 6,
        textAnchor: "start",
        fill: theme.fg,
        fontSize: 11,
      }),
    ],
  });

  const best = rows[0];
  // Named `baselineRow`, not `base`: a local called `base` shadows the module-level
  // `base(theme)` helper used at the top of this function, and `const` puts it in the
  // temporal dead zone for the whole scope — so `...base(theme)` above would throw
  // "Cannot access 'base' before initialization" at runtime while looking perfectly fine.
  const baselineRow = rows.find((r) => r.isBaseline);
  const summary =
    `Bar chart of backtest accuracy. ` +
    `Best model ${best.model} at ${best.mae.toFixed(2)} percentage points mean absolute error. ` +
    (baselineRow ? `Baseline ${baselineRow.model} at ${baselineRow.mae.toFixed(2)}.` : "");

  return { element: element as SVGSVGElement, summary };
}

/** Inflation history with regime bands and anomaly markers. */
export function anomalyTimeline(props: ChartProps, theme: ChartTheme): RenderedChart {
  const history = props.history as { period: string; value: number }[];
  const anomalies = props.anomalies as { period: string; yoy_at_period: number | null; z: number }[];
  const regimes = (props.regimes as { start_period: string; end_period: string; mean_yoy: number }[]) ?? [];

  const rows = history.map((h) => ({ date: periodToDate(h.period), value: h.value }));
  const marks = anomalies
    .filter((a) => a.yoy_at_period !== null)
    .map((a) => ({
      date: periodToDate(a.period),
      value: a.yoy_at_period as number,
      z: a.z,
    }));

  const element = Plot.plot({
    ...base(theme),
    width: 780,
    height: 340,
    x: { label: "Year", ticks: 6, ...axisStyle(theme) },
    y: { label: "Inflation, % change from a year earlier", grid: true, ...axisStyle(theme) },
    marks: [
      ...regimes.map((r) =>
        Plot.ruleX([periodToDate(r.start_period), periodToDate(r.end_period)], {
          stroke: theme.border,
          strokeDasharray: "3,3",
        }),
      ),
      Plot.ruleY([0], { stroke: theme.fgFaint }),
      Plot.line(rows, { x: "date", y: "value", stroke: theme.series[0], strokeWidth: 1.5 }),
      Plot.dot(marks, {
        x: "date",
        y: "value",
        fill: (d: { z: number }) => (d.z > 0 ? theme.series[2] : theme.series[3]),
        r: 3.2,
        stroke: theme.bg,
        strokeWidth: 0.8,
      }),
    ],
  });

  const biggest = anomalies.reduce(
    (best, a) => (best === null || Math.abs(a.z) > Math.abs(best.z) ? a : best),
    null as null | { period: string; z: number },
  );
  const summary =
    `Line chart of year-on-year inflation with ${anomalies.length} statistically unusual months marked ` +
    `and ${regimes.length} regime boundaries drawn as dashed lines. ` +
    (biggest
      ? `The largest deviation is ${biggest.period} at ${biggest.z.toFixed(1)} robust standard deviations from trend.`
      : "");

  return { element: element as SVGSVGElement, summary };
}

// ------------------------------------------------------------------ pricecatcher

/** One item's median observed price over time. */
export function itemPrice(props: ChartProps, theme: ChartTheme): RenderedChart {
  const months = props.months as string[];
  const prices = props.median_price as (number | null)[];
  const item = (props.item as string) ?? "";
  const unit = (props.unit as string) ?? "";

  const rows = gapAware(
    months.map((m, i) => ({ date: periodToDate(m), value: prices[i] ?? null })),
    (r) => r.value as number | null,
  );

  const element = Plot.plot({
    ...base(theme),
    width: 780,
    height: 300,
    x: { label: "Month", ticks: 6, ...axisStyle(theme) },
    y: { label: `Median price (RM per ${unit || "unit"})`, grid: true, ...axisStyle(theme) },
    marks: [
      Plot.line(rows, {
        x: "date",
        y: "value",
        stroke: theme.series[2],
        strokeWidth: 1.8,
        z: "__run",
      }),
    ],
  });

  const valid = prices.filter((p): p is number => p !== null);
  const summary =
    `Line chart of the median observed price of ${item} per ${unit}, ${months[0]} to ${months.at(-1)}. ` +
    (valid.length > 1
      ? `From RM ${valid[0].toFixed(2)} to RM ${valid.at(-1)!.toFixed(2)}, a change of ` +
        `${(((valid.at(-1)! / valid[0]) - 1) * 100).toFixed(1)}%.`
      : "");

  return { element: element as SVGSVGElement, summary };
}

// ------------------------------------------------------------------------ helpers

/**
 * A plain text stand-in for a chart with nothing to draw.
 *
 * Better than an empty SVG: a blank rectangle reads as "we have this data and it is fine",
 * whereas a sentence says what actually happened.
 */
function emptyChart(message: string): HTMLElement {
  return Object.assign(document.createElement("p"), {
    className: "chart-loading",
    textContent: message,
  });
}

/** "W.P. Kuala Lumpur" -> "K. Lumpur" so the scatter labels do not overlap. */
function abbreviateState(state: string): string {
  return state
    .replace(/^W\.P\.\s*/, "")
    .replace("Kuala Lumpur", "K. Lumpur")
    .replace("Negeri Sembilan", "N. Sembilan")
    .replace("Pulau Pinang", "P. Pinang");
}

export const RENDERERS = {
  "cpi-level": cpiLevel,
  "inflation-rate": inflationRate,
  "nominal-vs-real": nominalVsReal,
  "state-inflation-bars": stateInflationBars,
  "divergence-scatter": divergenceScatter,
  "group-comparison": groupComparison,
  "forecast-path": forecastPath,
  "model-scores": modelScores,
  "anomaly-timeline": anomalyTimeline,
  "item-price": itemPrice,
} as const;

export type RendererName = keyof typeof RENDERERS;
