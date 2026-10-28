/**
 * Formatting helpers.
 *
 * One rule governs this file: a missing value renders as an explicit gap, never as "0" or a
 * dash that could be mistaken for one. The pipeline goes to some trouble to preserve nulls,
 * and formatting them away here would undo that at the last step.
 */

const MONTHS_EN = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
] as const; // fmt: skip

export function isMissing(value: unknown): boolean {
  return value === null || value === undefined || (typeof value === "number" && !Number.isFinite(value));
}

/** "2026-09" -> "Sep 2026". Passes anything unparseable through unchanged. */
export function monthLabel(period: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(period);
  if (!match) return period;
  const month = Number(match[2]);
  return `${MONTHS_EN[month - 1] ?? match[2]} ${match[1]}`;
}

export function yearLabel(year: number | string): string {
  return `${year}`;
}

/** Signed percentage with a fixed number of decimals, e.g. "+12.4%" / "-3.1%". */
export function pct(value: number | null | undefined, digits = 1, signed = true): string {
  if (isMissing(value)) return "no data";
  const n = value as number;
  const sign = signed && n > 0 ? "+" : "";
  return `${sign}${n.toFixed(digits)}%`;
}

/** Percentage-point difference, e.g. "+1.2 pp". */
export function pp(value: number | null | undefined, digits = 1): string {
  if (isMissing(value)) return "no data";
  const n = value as number;
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)} pp`;
}

/** Ringgit, rounded to whole units — the data is not precise enough to justify decimals. */
export function myr(value: number | null | undefined): string {
  if (isMissing(value)) return "no data";
  return `RM ${Math.round(value as number).toLocaleString("en-MY")}`;
}

export function number(value: number | null | undefined, digits = 1): string {
  if (isMissing(value)) return "no data";
  return (value as number).toLocaleString("en-MY", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function integer(value: number | null | undefined): string {
  if (isMissing(value)) return "no data";
  return Math.round(value as number).toLocaleString("en-MY");
}

/** ISO timestamp -> "28 Oct 2026". */
export function dated(iso: string | null | undefined): string {
  if (!iso) return "unknown";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getUTCDate()} ${MONTHS_EN[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

/**
 * A yyyy-MM "as of" value -> "Sep 2026", or an ISO stamp -> "28 Oct 2026".
 * Dataset as-of values are either form depending on frequency, so this handles both.
 */
export function asOfLabel(value: string | null | undefined): string {
  if (!value) return "unknown";
  if (/^\d{4}-\d{2}$/.test(value)) return monthLabel(value);
  if (/^\d{4}$/.test(value)) return value;
  return dated(value);
}
