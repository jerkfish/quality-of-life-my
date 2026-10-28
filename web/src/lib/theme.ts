/**
 * Theme-aware colours for charts.
 *
 * Charts are drawn to SVG on the client, so they cannot inherit CSS custom properties. They
 * read the computed values instead, which means a chart always matches the stylesheet rather
 * than carrying its own palette that drifts from it.
 */

export interface ChartTheme {
  fg: string;
  fgMuted: string;
  fgFaint: string;
  border: string;
  grid: string;
  bg: string;
  bgRaised: string;
  series: [string, string, string, string];
  muted: string;
  warn: string;
  good: string;
}

export function chartTheme(): ChartTheme {
  const styles = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => styles.getPropertyValue(name).trim() || fallback;
  return {
    fg: v("--fg", "#16181d"),
    fgMuted: v("--fg-muted", "#5a6172"),
    fgFaint: v("--fg-faint", "#848b9c"),
    border: v("--border", "#dfe3ea"),
    grid: v("--border", "#dfe3ea"),
    bg: v("--bg", "#ffffff"),
    bgRaised: v("--bg-raised", "#ffffff"),
    series: [
      v("--series-1", "#1b5fbf"),
      v("--series-2", "#d9531e"),
      v("--series-3", "#0f8a6a"),
      v("--series-4", "#8a4fbd"),
    ],
    muted: v("--series-muted", "#b6bcc9"),
    warn: v("--warn-border", "#e0a94a"),
    good: v("--good-border", "#4aa87a"),
  };
}

export function isDark(): boolean {
  const set = document.documentElement.dataset.theme;
  if (set === "dark") return true;
  if (set === "light") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}
