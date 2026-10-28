# Changelog

All notable changes to this project. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
the project aims at [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — Phase 2 (analyses)
- **Forecasting** (`transforms/forecast.py`): ETS and SARIMA against an inflation-persistence
  baseline, rolling-origin expanding-window backtesting, 80% prediction intervals reported in
  both index and rate space, and an explicit `beats_baseline` verdict.
- **Anomaly and regime detection** (`transforms/anomaly.py`): STL decomposition with MAD-scaled
  robust z-scores, plus binary segmentation on the year-on-year rate. Corroborating movement in
  independent series is reported as context, never as cause.
- New artifacts: `data/forecast.json`, `data/anomalies.json`, covering the national headline
  series and every CPI division.
- `docs/METHODOLOGY.md` — every formula, every assumption, and the places where a reasonable
  person could have chosen differently.
- ADRs 0001–0004 covering static artifacts, honest forecasting, PriceCatcher reduction, and the
  report-as-found policy.
- 36 new tests (131 offline total). The honesty machinery is a tested code path, not a comment.

### Notable results
- ETS beats the persistence baseline on headline CPI: **MAE 0.17 pp vs 0.46 pp** over 96
  backtested forecasts. SARIMA is statistically indistinguishable at 0.18 pp.
- **5 of 14 series do not beat the baseline** -- divisions 03 (Clothing & Footwear),
  05 (Furnishings), 07 (Transport), 11 (Restaurants & Accommodation) and 13 (Personal Care).
  The pipeline reports this rather than presenting a forecast anyway.
- Anomaly detection independently isolates the 2020 deflation (z = −25.9) and the 2022
  inflation surge without any hard-coded dates.

### Fixed — Phase 2
- **The forecast baseline was a straw man.** The textbook seasonal-naive baseline predicts
  exactly 0% inflation at every horizon under year-on-year scoring — it is an identity, not a
  forecast. Detected via a MAPE of exactly 100.00%. Replaced with persistence of the inflation
  rate, which is genuine competition.
- **Anomaly detector had a blind spot.** MAD is zero when more than half the residuals are
  identical, which happens whenever STL fits a seasonal series almost exactly — so the detector
  reported "no anomalies" in the one case it most needed to catch. Added a mean-absolute-
  deviation fallback.
- `household_income_summary` and `real_wage_summary` crashed with an opaque `KeyError` when no
  state had both survey years. Both now return correctly-shaped empty frames.
- `build_regional` no longer fails when a source has no overlapping years.

### Added — Phase 1 (pipeline)
- Data pipeline (`pipeline/`) — fetches DOSM parquet, validates it against declared
  contracts, and reduces it to the static artifacts under `data/`.
- Dataset registry covering 32 upstream datasets with tier, frequency, join keys and
  per-dataset notes (`pipeline/src/colcheck/registry.py`).
- Data contracts with error/warning severity: missing columns, unparsable dates, unexpected
  or missing states, CPI division domains, duplicate keys, null keys, non-numeric values.
- Transforms: CPI annual/ cumulative/YoY, real wages with base-year deflation, HIES
  household income, personal inflation calculator, PriceCatcher item-level reduction.
- Test suite: 95 offline tests plus 18 live upstream contract tests. Known-answer cases
  throughout; the transforms carry 92-100% coverage.
- Live contract test that asserts the CPI is still based at 2010 and that our deflation
  still reproduces DOSM's own published real-wage series.
- Scheduled GitHub Actions workflow: lint, format check, tests, upstream contract tests,
  artifact rebuild, commit-on-drift.
- `docs/DATA.md`, `docs/ASSUMPTIONS.md`, `docs/KNOWN-GAPS.md`.

### Notes
- The pipeline reads bulk parquet from `storage.dosm.gov.my`, not the REST API. The API is
  rate limited to 4 requests/minute and is missing five datasets we need entirely.
- Nothing is published until every validation passes; a failed run writes no artifacts.

### Added — Phase 0 (discovery)
- Confirmed API mechanics, response shapes and rate limits against the live service rather
  than the documentation.
- Established that CPI is based at 2010=100 and that the state wage series starts in the same
  year, so no rebasing is needed anywhere in the project.
- Validated the deflation method against DOSM's published real-wage series (max 0.77 RM).
- Documented three findings the data supports, with caveats.

[Unreleased]: https://github.com/jerkfish/quality-of-life-my/commits/HEAD
