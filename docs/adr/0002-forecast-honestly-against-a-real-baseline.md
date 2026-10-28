# ADR 0002 — Forecast the index, score the rate, and use a baseline that can win

- **Status:** Accepted
- **Date:** 2026-10-28
- **Phase:** 2 (analyses)

## Context

The brief requires a 6–12 month CPI forecast using at least two methods, rolling-origin
backtesting, MAE/MAPE against a baseline, prediction intervals, and — explicitly — *"If the
model doesn't beat the baseline, say so in the UI. That is a feature, not a bug."*

Three decisions had to be made, and the obvious answer to each turns out to be wrong.

## Decision 1 — Model the index, but score the year-on-year rate

Modelling the index level is the right choice statistically: it has clean trend and seasonal
structure, which is what SARIMA and ETS are designed for.

But scoring on the index is nearly meaningless. The index only rises; a model with a 0.5-point
MAE on a series sitting at 130 has demonstrated almost nothing, and percentage errors on a
level near 130 are uninformative for the same reason.

**So: fit on the level, score on the implied rate, in percentage points.**

This is computable without look-ahead for horizons up to 12 months, because the year-earlier
index needed for a year-on-year rate is always inside the training window. Beyond 12 months the
base would itself be a forecast, so we return null rather than a number with no honest interval.

## Decision 2 — The baseline is inflation persistence, not seasonal naive

The textbook seasonal-naive baseline is `level(t) = level(t-12)`. Under year-on-year scoring
that predicts **exactly 0.0% inflation at every horizon, by construction**. It is an identity,
not a forecast.

We built it that way first. The tell was a MAPE of exactly **100.00%** — the predicted rate was
always zero, so the error always equalled the actual and the floored denominator always bound.
Against that straw man, ETS "beat the baseline" by a factor of nine and the result was
worthless.

**So the baseline is persistence of the inflation rate:**
`level(t) = level(t-12) · (1 + yoy(t-12))`.

It is genuine competition. Measured on real headline CPI over 12 rolling-origin folds it scores
**MAE 0.47 pp**, against **0.17 pp** for ETS and **0.17 pp** for SARIMA. ETS beats it by
roughly 2.8×, which is a real result rather than an artefact of a weak benchmark.

## Decision 3 — The honesty requirement is a data field, not a rendering choice

`beats_baseline: false` plus a human-readable `verdict` and a caveat are written into the
artifact whenever the best model fails to beat the baseline. Encoding it in the schema rather
than leaving it to the UI means the frontend cannot omit it by accident, and
`test_forecast_series_says_so_when_nothing_beats_the_baseline` pins the behaviour so a future
refactor cannot quietly drop it.

## Consequences

**Good**

- The reported accuracy means something, because the baseline is not a straw man.
- Saying "no model beat the baseline" is a supported state, tested and rendered.
- Fixed SARIMA parameters make results reproducible and statable in the methodology, instead
  of depending on which day an auto-ARIMA search ran.

**Bad**

- Two extra concepts to explain (rate-space scoring, persistence baseline). Both are in
  METHODOLOGY.md §4.
- Fixed SARIMA parameters are not optimal for every division. A division where auto-ARIMA
  would pick a different order may be under-served. We accept this for reproducibility and
  report the backtest so the reader can judge.
- Forecasting 14 series takes ~12 s each, so the full pipeline needs minutes. Mitigated with
  `--skip-forecast` and `--forecast-limit` flags.

## Alternatives rejected

- **Score on the index level** — flatters every model, since the index only rises.
- **Seasonal-naive baseline** — degenerate under rate scoring; see Decision 2.
- **Auto-ARIMA (`pmdarima`)** — extra dependency, non-deterministic runtime, and the goal is
  to compare against a baseline, not to win.
- **Gradient boosting on lag features** — the brief offers it as an option and it would add
  ~30 MB of scikit-learn. Two statistical methods plus a hard baseline already answer the
  question the brief asks.
